"""Shift-maintenance scheduling with daily material holdback.

Rules implemented here:

* every day keeps ``ceil(approved daily forecast)`` pieces out of the releasable quantity;
* a work order is placed as a whole: the bay must be free for the whole duration and the
  remaining material on the picking day must cover the whole quantity;
* the end date never passes the work-order deadline;
* first maximise the number of line-stop orders, then normal orders (lexicographic MILP);
* started orders stay where they are, even when a bay is switched off.
"""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
import time

import numpy as np
from fastapi import HTTPException
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models import (Job, MaterialBook, MaterialDay, Reservation, ReservationDay,
                        Schedule, WorkOrder, Workstation)
from src.services.audit import record
from src.services.business import metadata

KIND_LABEL = {"line_stop": "停线单", "normal": "普通单"}


def part_codes():
    for parameter in metadata().get("parameters", []):
        if parameter["key"] == "part":
            return [str(option["value"]) for option in parameter.get("options", [])]
    return []


def _iso(value, label):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label}须为 YYYY-MM-DD 日期") from None


def _days(first, last):
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def _ceil(value):
    # Forecasts are rounded to 4 decimals; the 1e-9 guard keeps 3.0 from becoming 4.
    return max(0, math.ceil(float(value) - 1e-9))


def _forecast_map(job, part):
    if job is None:
        raise HTTPException(404, "预测任务不存在")
    if job.status != "completed" or not job.approved:
        raise HTTPException(409, "请先选择已完成且获批准的预测结果")
    if str(job.params.get("part")) != part:
        raise HTTPException(422, "预测任务的备件与排程备件不一致")
    rows = (job.result or {}).get("table", {}).get("rows", [])
    forecast = {}
    for row in rows:
        day = _iso(row.get("day"), "预测日期").isoformat()
        expected = float(row.get("expected", 0))
        if not math.isfinite(expected) or expected < 0:
            raise HTTPException(409, "预测结果包含非法期望值，请重新计算预测")
        forecast[day] = {"expected": expected, "reserved": _ceil(expected)}
    if not forecast:
        raise HTTPException(409, "预测结果没有逐日明细，无法留出日常领用")
    return forecast


def _forecast_checksum(forecast):
    return hashlib.sha256(json.dumps(forecast, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _workstation_fingerprint(db):
    rows = list(db.scalars(select(Workstation).order_by(Workstation.id)))
    payload = [[w.id, w.code, w.active, w.calendar_version, sorted(w.blocked_days or [])] for w in rows]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _material_version(db, part):
    book = db.scalar(select(MaterialBook).where(MaterialBook.part == part))
    return book.version if book else 0


def workstation_dict(w):
    return {"id": w.id, "code": w.code, "name": w.name, "active": w.active,
            "blocked_days": list(w.blocked_days or []), "calendar_version": w.calendar_version}


def _material_rows(db, part):
    return list(db.scalars(select(MaterialDay).where(MaterialDay.part == part).order_by(MaterialDay.day)))


def _reservation_days(reservation):
    return list(_days(_iso(reservation.start_day, "占位开始日"), _iso(reservation.end_day, "占位结束日")))


def _partition_reservations(db, part, all_orders):
    """A part has one effective confirmed schedule.

    Started orders keep their reservation forever; every other reservation of this part
    belongs to the schedule that is about to be replaced and is therefore only a candidate
    for deletion. Reservations of other parts are always held: a bay is shared.
    """
    reservations = list(db.scalars(select(Reservation)))
    locked, stale, other_parts = [], [], []
    for reservation in reservations:
        order = all_orders.get(reservation.work_order_id)
        if reservation.part != part:
            other_parts.append(reservation)
        elif reservation.locked or (order is not None and order.started):
            locked.append(reservation)
        else:
            stale.append(reservation)
    return locked + other_parts, stale


def _latest_confirmed(db, part):
    return db.scalar(select(Schedule).where(Schedule.part == part, Schedule.status == "confirmed")
                     .order_by(Schedule.confirmed_at.desc(), Schedule.id.desc()).limit(1))


def _capacity(mat, forecast, committed, day_text):
    """Releasable minus the daily holdback and minus material already promised."""
    if day_text not in mat or day_text not in forecast:
        return None
    return max(0.0, float(mat[day_text]) - forecast[day_text]["reserved"] - float(committed.get(day_text, 0)))


def _window(order, start):
    earliest = start
    if order.earliest_start:
        earliest = max(earliest, _iso(order.earliest_start, "工单最早开工日"))
    latest = _iso(order.deadline, "工单期限") - timedelta(days=max(1, int(order.duration_days)) - 1)
    return earliest, latest


def _span(order, day):
    return list(_days(day, day + timedelta(days=max(1, int(order.duration_days)) - 1)))


def _reason_for(order, start, active_workstations, blocked, forecast, mat, committed, occupied):
    eligible = [w for w in active_workstations
                if not order.eligible_workstations or w.code in order.eligible_workstations]
    if not eligible:
        return "无可用工位"
    earliest, latest = _window(order, start)
    if latest < earliest:
        return "工单期限不足一个完整工期"
    calendar = []
    for day in _days(earliest, latest):
        for workstation in eligible:
            span = _span(order, day)
            if all(item.isoformat() not in blocked[workstation.id] for item in span):
                calendar.append((workstation, day, span))
    if not calendar:
        return "工位日历无连续可用档期"
    covered = [item for item in calendar if item[1].isoformat() in forecast]
    if not covered:
        return "开工日不在已批准预测覆盖范围"
    with_material = [item for item in covered if item[1].isoformat() in mat]
    if not with_material:
        return "开工日缺少可发量记录"
    fit = [item for item in with_material
           if (_capacity(mat, forecast, committed, item[1].isoformat()) or 0) + 1e-9 >= float(order.quantity)]
    if not fit:
        return "扣除日常领用和既有占位后余料不足整单"
    free = [item for item in fit if all((item[0].id, day) not in occupied for day in item[2])]
    if not free:
        return "工位档期已被既有占位占用"
    return "工位或余料被更高优先级工单占用，本轮未排入"


def _solve(orders, candidates, capacities):
    """Maximise line-stop orders first, then normal orders. Returns order_id -> candidate."""
    variables = []
    order_variables = defaultdict(list)
    for order in orders:
        for candidate in candidates.get(order.id, []):
            order_variables[order.id].append(len(variables))
            variables.append((order, candidate))
    if not variables:
        return {}

    normal_count = sum(1 for order in orders if order.kind != "line_stop")
    last_ordinal = max((candidate[1].toordinal() for items in candidates.values() for candidate in items), default=0)
    first_ordinal = min((candidate[1].toordinal() for items in candidates.values() for candidate in items), default=0)
    span = max(1, last_ordinal - first_ordinal + 1)
    # Strict lexicographic weights: one more line stop beats every date preference,
    # one more normal order beats every date preference, and dates only break ties.
    date_term = lambda day: span - (day.toordinal() - first_ordinal)
    base = len(variables) * (span + 1) + 1
    weights = np.array([base * (normal_count + 1 if order.kind == "line_stop" else 1) + date_term(candidate[1])
                        for order, candidate in variables], dtype=float)

    rows, cols, values, upper = [], [], [], []
    row = 0
    for order in orders:
        for variable in order_variables.get(order.id, []):
            rows.append(row); cols.append(variable); values.append(1.0)
        upper.append(1.0)
        row += 1

    bay_columns = defaultdict(list)
    material_columns = defaultdict(list)
    for variable, (order, candidate) in enumerate(variables):
        workstation, day, span = candidate
        material_columns[day.isoformat()].append(variable)
        for item in span:
            bay_columns[(workstation.id, item.isoformat())].append(variable)

    for key in sorted(bay_columns):
        for variable in bay_columns[key]:
            rows.append(row); cols.append(variable); values.append(1.0)
        upper.append(1.0)
        row += 1

    for day_text in sorted(material_columns):
        capacity = capacities.get(day_text)
        if capacity is None:
            for variable in material_columns[day_text]:
                rows.append(row); cols.append(variable); values.append(1.0)
            upper.append(0.0)
        else:
            for variable in material_columns[day_text]:
                rows.append(row); cols.append(variable); values.append(float(variables[variable][0].quantity))
            upper.append(float(capacity))
        row += 1

    matrix = coo_matrix((values, (rows, cols)), shape=(row, len(variables))).tocsr()
    result = milp(c=-weights,
                  constraints=LinearConstraint(matrix, -np.inf, np.array(upper)),
                  integrality=np.ones(len(variables)),
                  bounds=Bounds(np.zeros(len(variables)), np.ones(len(variables))),
                  options={"time_limit": 20, "mip_rel_gap": 0})
    if result.x is None:
        return {}
    chosen = {}
    for index, (order, candidate) in enumerate(variables):
        if float(result.x[index]) > 0.5 and order.id not in chosen:
            chosen[order.id] = candidate
    return chosen


def create(db, data, user):
    part = str(data.part)
    if part not in part_codes():
        raise HTTPException(422, "备件不在支持范围")
    job = db.get(Job, data.forecast_job_id)
    forecast = _forecast_map(job, part)
    start = _iso(data.start_date, "排程开始日")
    forecast_days = sorted(forecast)
    if not (forecast_days[0] <= start.isoformat() <= forecast_days[-1]):
        raise HTTPException(422, "排程开始日必须落在已批准预测的日期范围内")
    workstations = list(db.scalars(select(Workstation).order_by(Workstation.code)))
    if not workstations:
        raise HTTPException(409, "尚未维护工位，无法排程")
    active = [w for w in workstations if w.active]
    blocked = {w.id: {str(item) for item in (w.blocked_days or [])} for w in workstations}
    orders = list(db.scalars(select(WorkOrder).where(WorkOrder.part == part)
                             .order_by(WorkOrder.deadline, WorkOrder.id)))
    all_orders = {order.id: order for order in db.scalars(select(WorkOrder))}

    mat = {row.day: float(row.releasable) for row in _material_rows(db, part)
           if row.day >= start.isoformat()}
    replaced = _latest_confirmed(db, part)
    replaced_id = replaced.id if replaced else None

    kept, _stale = _partition_reservations(db, part, all_orders)

    occupied = set()
    committed = defaultdict(float)
    locked = {}
    for reservation in kept:
        for day in _reservation_days(reservation):
            occupied.add((reservation.workstation_id, day.isoformat()))
        if reservation.part == part:
            committed[reservation.material_day] += float(reservation.quantity)
        if reservation.part == part and (reservation.work_order_id in all_orders):
            locked[reservation.work_order_id] = reservation

    capacities = {day: _capacity(mat, forecast, committed, day) for day in mat}

    candidates = {}
    for order in orders:
        if order.id in locked or order.started:
            continue
        quantity = float(order.quantity)
        earliest, latest = _window(order, start)
        if latest < earliest:
            continue
        eligible = [w for w in active if not order.eligible_workstations or w.code in order.eligible_workstations]
        for workstation in eligible:
            for day in _days(earliest, latest):
                day_text = day.isoformat()
                if day_text not in forecast or day_text not in mat:
                    continue
                span = _span(order, day)
                if any(item.isoformat() in blocked[workstation.id] for item in span):
                    continue
                if any((workstation.id, item.isoformat()) in occupied for item in span):
                    continue
                capacity = capacities.get(day_text)
                if capacity is None or capacity + 1e-9 < quantity:
                    continue
                candidates.setdefault(order.id, []).append((workstation, day, span))
        candidates.setdefault(order.id, []).sort(key=lambda item: (item[1], item[0].code))

    chosen = _solve(orders, candidates, capacities)

    entries, scheduled_by_day, locked_by_day = [], defaultdict(float), defaultdict(float)
    for order in orders:
        entry = {
            "work_order_id": order.id, "code": order.code, "name": order.name,
            "kind": order.kind, "kind_label": KIND_LABEL.get(order.kind, order.kind),
            "quantity": float(order.quantity), "duration_days": int(order.duration_days),
            "deadline": order.deadline, "started": bool(order.started), "locked": False,
            "status": "unscheduled", "workstation_id": None, "workstation_code": None,
            "start_day": None, "end_day": None, "material_day": None, "reason": None, "material": None,
        }
        reservation = locked.get(order.id)
        if reservation is not None:
            workstation = db.get(Workstation, reservation.workstation_id)
            entry.update({
                "locked": True, "status": "scheduled", "workstation_id": reservation.workstation_id,
                "workstation_code": workstation.code if workstation else "?",
                "start_day": reservation.start_day, "end_day": reservation.end_day,
                "material_day": reservation.material_day,
                "reason": "已开工，保留原位" if order.started else "已确认占位，保持在原位",
            })
        elif order.started:
            entry["reason"] = "已开工但缺少占位记录，需人工核对"
        elif order.id in chosen:
            workstation, day, span = chosen[order.id]
            entry.update({"status": "scheduled", "workstation_id": workstation.id,
                          "workstation_code": workstation.code, "start_day": day.isoformat(),
                          "end_day": span[-1].isoformat(), "material_day": day.isoformat()})
        if entry["status"] == "scheduled" and entry["material_day"]:
            scheduled_by_day[entry["material_day"]] += entry["quantity"]
            if entry["locked"]:
                locked_by_day[entry["material_day"]] += entry["quantity"]
        entries.append(entry)

    # Reasons must reflect the finished plan, not the empty board.
    planned_bays = set(occupied)
    for order_id, (workstation, _day, span) in chosen.items():
        for item in span:
            planned_bays.add((workstation.id, item.isoformat()))
    for entry in entries:
        if entry["status"] == "scheduled":
            continue
        if entry["reason"]:
            continue
        order = all_orders[entry["work_order_id"]]
        entry["reason"] = _reason_for(order, start, active, blocked, forecast, mat,
                                      scheduled_by_day, planned_bays)

    last_needed = max(start, _iso(forecast_days[-1], "预测末日"))
    for entry in entries:
        if entry["end_day"]:
            last_needed = max(last_needed, _iso(entry["end_day"], "完工日"))
        if entry["material_day"]:
            last_needed = max(last_needed, _iso(entry["material_day"], "用料日"))
    daily = []
    for day in _days(start, last_needed):
        day_text = day.isoformat()
        releasable = mat.get(day_text)
        reserved = forecast.get(day_text, {}).get("reserved")
        total = scheduled_by_day.get(day_text, 0.0)
        if day_text not in forecast:
            note = "不在已批准预测覆盖范围"
        elif day_text not in mat:
            note = "缺少可发量记录"
        elif releasable - reserved - total < -1e-9:
            note = "占位超出可发量"
        else:
            note = ""
        daily.append({
            "day": day_text, "releasable": releasable, "forecast_reserved": reserved,
            "locked_material": locked_by_day.get(day_text, 0.0), "scheduled_material": total,
            "remaining": None if releasable is None or reserved is None else round(releasable - reserved - total, 4),
            "note": note,
        })

    basis = {row["day"]: {key: row[key] for key in
                          ("releasable", "forecast_reserved", "locked_material",
                           "scheduled_material", "remaining", "note")} for row in daily}
    for entry in entries:
        if entry["material_day"]:
            entry["material"] = {"required": entry["quantity"], **basis.get(entry["material_day"], {})}

    summary = {
        "orders": len(orders),
        "scheduled": sum(entry["status"] == "scheduled" for entry in entries),
        "unscheduled": sum(entry["status"] != "scheduled" for entry in entries),
        "line_stop_scheduled": sum(entry["status"] == "scheduled" and entry["kind"] == "line_stop" for entry in entries),
        "normal_scheduled": sum(entry["status"] == "scheduled" and entry["kind"] == "normal" for entry in entries),
        "line_stop_unscheduled": sum(entry["status"] != "scheduled" and entry["kind"] == "line_stop" for entry in entries),
        "normal_unscheduled": sum(entry["status"] != "scheduled" and entry["kind"] == "normal" for entry in entries),
        "quantity_scheduled": round(sum(entry["quantity"] for entry in entries if entry["status"] == "scheduled"), 4),
        "workstations": len(active),
        "forecast_first_day": forecast_days[0], "forecast_last_day": forecast_days[-1],
        "replaced_id": replaced_id,
    }

    snapshot = {
        "workstation_fingerprint": _workstation_fingerprint(db),
        "material_version": _material_version(db, part),
        "forecast_checksum": _forecast_checksum(forecast),
        "workstations": [workstation_dict(w) for w in workstations],
        "material": mat, "forecast": forecast,
    }
    schedule = Schedule(
        name=data.name.strip(), part=part, forecast_job_id=job.id, start_date=start.isoformat(),
        status="draft", snapshot=snapshot, entries=entries, daily=daily, summary=summary,
        replaces_id=replaced_id, created_by=user.id, created_at=time.time(),
    )
    db.add(schedule)
    db.flush()
    record(db, user.email, "schedule.create", {"id": schedule.id, "part": part, "replaced_id": replaced_id})
    db.commit()
    return schedule


def confirm(db, schedule, user):
    if schedule.status == "confirmed":
        return schedule
    if schedule.status != "draft":
        raise HTTPException(409, "只有草稿排程可以确认")
    snapshot = schedule.snapshot or {}
    if snapshot.get("workstation_fingerprint") != _workstation_fingerprint(db):
        raise HTTPException(409, "工位日历已更新，请重新计算排程")
    if snapshot.get("material_version") != _material_version(db, schedule.part):
        raise HTTPException(409, "领料余量已更新，请重新计算排程")
    job = db.get(Job, schedule.forecast_job_id)
    forecast = _forecast_map(job, schedule.part)
    if snapshot.get("forecast_checksum") != _forecast_checksum(forecast):
        raise HTTPException(409, "已批准预测已变化，请重新计算排程")
    latest = _latest_confirmed(db, schedule.part)
    if (latest.id if latest else None) != schedule.replaces_id:
        raise HTTPException(409, "已有更新的已确认排程，请基于最新版本重新计算")

    workstations = {w.id: w for w in db.scalars(select(Workstation))}
    all_orders = {order.id: order for order in db.scalars(select(WorkOrder))}
    entries = list(schedule.entries or [])
    for entry in entries:
        order = all_orders.get(entry["work_order_id"])
        if order is None:
            raise HTTPException(409, "工单已不存在，请重新计算排程")
        if order.started and not entry.get("locked"):
            raise HTTPException(409, "工单已开工，请重新计算排程以保留原位")

    kept, to_delete = _partition_reservations(db, schedule.part, all_orders)

    occupied = set()
    committed = defaultdict(float)
    by_order = {}
    for reservation in kept:
        for day in _reservation_days(reservation):
            occupied.add((reservation.workstation_id, day.isoformat()))
        if reservation.part != schedule.part:
            continue  # other parts share bays but have their own material books
        by_order[reservation.work_order_id] = reservation
        committed[reservation.material_day] += float(reservation.quantity)

    mat = {row.day: float(row.releasable) for row in _material_rows(db, schedule.part)}
    new_by_day = defaultdict(float)
    for entry in entries:
        if entry["status"] != "scheduled":
            continue
        if entry.get("locked"):
            reservation = by_order.get(entry["work_order_id"])
            if reservation is None:
                raise HTTPException(409, "已开工工单的占位记录缺失，请重新计算排程")
            if (reservation.workstation_id != entry["workstation_id"]
                    or reservation.start_day != entry["start_day"]
                    or reservation.end_day != entry["end_day"]
                    or reservation.material_day != entry["material_day"]):
                raise HTTPException(409, "已开工工单的占位已变化，请重新计算排程")
            continue
        workstation = workstations.get(entry["workstation_id"])
        if workstation is None or not workstation.active:
            raise HTTPException(409, "排程使用的工位已停用，请重新计算排程")
        blocked = {str(item) for item in (workstation.blocked_days or [])}
        span = list(_days(_iso(entry["start_day"], "开工日"), _iso(entry["end_day"], "完工日")))
        if any(item.isoformat() in blocked for item in span):
            raise HTTPException(409, "工位日历已更新，请重新计算排程")
        if any((workstation.id, item.isoformat()) in occupied for item in span):
            raise HTTPException(409, "工位档期已被占用，请重新计算排程")
        for item in span:
            occupied.add((workstation.id, item.isoformat()))
        material_day = entry["material_day"]
        new_by_day[material_day] += float(entry["quantity"])
        capacity = _capacity(mat, forecast, committed, material_day)
        if capacity is None or new_by_day[material_day] > capacity + 1e-9:
            raise HTTPException(409, "领料余量已更新，请重新计算排程")

    try:
        for reservation in to_delete:
            db.execute(delete(ReservationDay).where(ReservationDay.reservation_id == reservation.id))
            db.delete(reservation)
        db.flush()
        created = []
        for entry in entries:
            if entry["status"] != "scheduled" or entry.get("locked"):
                continue
            reservation = Reservation(
                work_order_id=entry["work_order_id"], schedule_id=schedule.id,
                workstation_id=entry["workstation_id"], part=schedule.part,
                start_day=entry["start_day"], end_day=entry["end_day"],
                material_day=entry["material_day"], quantity=float(entry["quantity"]),
                locked=False, created_at=time.time(),
            )
            db.add(reservation); db.flush()
            for item in _days(_iso(entry["start_day"], "开工日"), _iso(entry["end_day"], "完工日")):
                db.add(ReservationDay(reservation_id=reservation.id,
                                      workstation_id=entry["workstation_id"], day=item.isoformat()))
            created.append(reservation.id)
        schedule.status = "confirmed"
        schedule.confirmed_at = time.time()
        record(db, user.email, "schedule.confirm",
               {"id": schedule.id, "reservations": created, "replaced_id": schedule.replaces_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "排程占位发生冲突，已保留旧版，请重新计算") from None
    return schedule


def toggle_workstation(db, workstation, user):
    workstation.active = not workstation.active
    workstation.calendar_version = int(workstation.calendar_version or 0) + 1
    record(db, user.email, "workstation.toggle",
           {"id": workstation.id, "active": workstation.active})
    db.commit()
    return workstation


def update_blocked_days(db, workstation, days, user):
    parsed = sorted({_iso(day, "停用日期").isoformat() for day in days})
    if parsed == sorted(str(item) for item in (workstation.blocked_days or [])):
        return workstation  # A no-op save must not invalidate existing drafts.
    workstation.blocked_days = parsed
    workstation.calendar_version = int(workstation.calendar_version or 0) + 1
    record(db, user.email, "workstation.calendar", {"id": workstation.id, "days": len(parsed)})
    db.commit()
    return workstation


def set_material(db, part, day, releasable, user):
    if part not in part_codes():
        raise HTTPException(422, "备件不在支持范围")
    day_text = _iso(day, "用料日").isoformat()
    try:
        amount = float(releasable)
    except (TypeError, ValueError):
        raise HTTPException(422, "可发量必须为数值")
    if not math.isfinite(amount) or not 0 <= amount <= 1_000_000_000:
        raise HTTPException(422, "可发量必须为 0 至 10 亿之间的有限数值")
    row = db.scalar(select(MaterialDay).where(MaterialDay.part == part, MaterialDay.day == day_text))
    if row and abs(float(row.releasable) - amount) < 1e-9:
        return row  # Unchanged value must not invalidate existing drafts.
    if row:
        row.releasable = amount
    else:
        row = MaterialDay(part=part, day=day_text, releasable=amount)
        db.add(row)
    book = db.scalar(select(MaterialBook).where(MaterialBook.part == part))
    if book:
        book.version = int(book.version or 0) + 1
        book.updated_at = time.time()
    else:
        db.add(MaterialBook(part=part, version=1, updated_at=time.time()))
    record(db, user.email, "material.set",
           {"part": part, "day": day_text, "releasable": amount})
    db.commit()
    return row


def schedule_dict(schedule):
    snapshot = schedule.snapshot or {}
    return {
        "id": schedule.id, "name": schedule.name, "part": schedule.part,
        "forecast_job_id": schedule.forecast_job_id, "start_date": schedule.start_date,
        "status": schedule.status, "entries": schedule.entries or [], "daily": schedule.daily or [],
        "summary": schedule.summary or {}, "replaces_id": schedule.replaces_id,
        "created_at": schedule.created_at, "confirmed_at": schedule.confirmed_at,
        "basis_versions": {"workstation_fingerprint": snapshot.get("workstation_fingerprint"),
                           "material_version": snapshot.get("material_version")},
    }


def work_order_dict(order):
    return {"id": order.id, "code": order.code, "name": order.name, "part": order.part,
            "kind": order.kind, "kind_label": KIND_LABEL.get(order.kind, order.kind),
            "quantity": order.quantity, "duration_days": order.duration_days,
            "deadline": order.deadline, "earliest_start": order.earliest_start,
            "eligible_workstations": order.eligible_workstations or [], "started": order.started,
            "started_at": order.started_at}


def start_work_order(db, order, user):
    if order.started:
        return order
    reservation = db.scalar(select(Reservation).where(Reservation.work_order_id == order.id))
    if reservation is None:
        raise HTTPException(409, "工单尚无已确认占位，不能标记开工")
    order.started = True
    order.started_at = time.time()
    reservation.locked = True
    record(db, user.email, "work_order.start", {"id": order.id, "reservation": reservation.id})
    db.commit()
    return order


def create_work_order(db, data, user):
    part = str(data.part)
    if part not in part_codes():
        raise HTTPException(422, "备件不在支持范围")
    if db.scalar(select(WorkOrder).where(WorkOrder.code == data.code.strip())):
        raise HTTPException(409, "工单编号已存在")
    duration = int(data.duration_days)
    if duration < 1 or duration > 60:
        raise HTTPException(422, "工期须为 1 至 60 天")
    deadline = _iso(data.deadline, "工单期限")
    earliest = _iso(data.earliest_start, "最早开工日") if data.earliest_start else None
    if earliest and earliest > deadline:
        raise HTTPException(422, "最早开工日不能晚于工单期限")
    try:
        quantity = float(data.quantity)
    except (TypeError, ValueError):
        raise HTTPException(422, "整单数量必须为数值")
    if not math.isfinite(quantity) or not 0 < quantity <= 1_000_000_000:
        raise HTTPException(422, "整单数量必须为大于 0 的有限数值")
    order = WorkOrder(code=data.code.strip(), name=data.name.strip(), part=part,
                      kind=data.kind, quantity=quantity, duration_days=duration,
                      deadline=deadline.isoformat(),
                      earliest_start=earliest.isoformat() if earliest else None,
                      eligible_workstations=list(data.eligible_workstations),
                      started=False, created_at=time.time())
    db.add(order); db.flush()
    record(db, user.email, "work_order.create", {"id": order.id, "code": order.code, "part": part})
    db.commit()
    return order
