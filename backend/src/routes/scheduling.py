import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import MaterialBook, MaterialDay, Schedule, WorkOrder, Workstation
from src.schemas.scheduling import (MaterialDayInput, ScheduleInput, WorkOrderInput,
                                    WorkstationCalendarInput, WorkstationInput)
from src.services import scheduling
from src.services.audit import record

router = APIRouter()


@router.get('/workstations')
def workstations(db=Depends(get_db), u=Depends(current_user)):
    return [scheduling.workstation_dict(w) for w in db.scalars(select(Workstation).order_by(Workstation.code))]


@router.post('/workstations', status_code=201)
def create_workstation(data: WorkstationInput, db=Depends(get_db), u=Depends(admin_only)):
    code = data.code.strip()
    if db.scalar(select(Workstation).where(Workstation.code == code)):
        raise HTTPException(409, '工位编号已存在')
    w = Workstation(code=code, name=data.name.strip(), active=True, blocked_days=[],
                    calendar_version=1, created_at=time.time())
    db.add(w); db.flush()
    record(db, u.email, 'workstation.create', {'id': w.id, 'code': w.code})
    db.commit()
    return scheduling.workstation_dict(w)


@router.post('/workstations/{id}/toggle')
def toggle_workstation(id: int, db=Depends(get_db), u=Depends(admin_only)):
    w = db.get(Workstation, id)
    if not w:
        raise HTTPException(404, '工位不存在')
    return scheduling.workstation_dict(scheduling.toggle_workstation(db, w, u))


@router.post('/workstations/{id}/calendar')
def set_calendar(id: int, data: WorkstationCalendarInput, db=Depends(get_db), u=Depends(admin_only)):
    w = db.get(Workstation, id)
    if not w:
        raise HTTPException(404, '工位不存在')
    return scheduling.workstation_dict(scheduling.update_blocked_days(db, w, data.blocked_days, u))


@router.get('/material-days')
def material_days(part: str, db=Depends(get_db), u=Depends(current_user)):
    if part not in scheduling.part_codes():
        raise HTTPException(422, '备件不在支持范围')
    rows = db.scalars(select(MaterialDay).where(MaterialDay.part == part).order_by(MaterialDay.day))
    book = db.scalar(select(MaterialBook).where(MaterialBook.part == part))
    return {'part': part, 'version': book.version if book else 0,
            'days': [{'day': row.day, 'releasable': row.releasable} for row in rows]}


@router.post('/material-days')
def set_material(data: MaterialDayInput, db=Depends(get_db), u=Depends(can_write)):
    row = scheduling.set_material(db, data.part, data.day, data.releasable, u)
    book = db.scalar(select(MaterialBook).where(MaterialBook.part == data.part))
    return {'part': row.part, 'day': row.day, 'releasable': row.releasable,
            'version': book.version if book else 0}


@router.get('/work-orders')
def work_orders(part: str | None = None, db=Depends(get_db), u=Depends(current_user)):
    statement = select(WorkOrder).order_by(WorkOrder.deadline, WorkOrder.id)
    if part:
        if part not in scheduling.part_codes():
            raise HTTPException(422, '备件不在支持范围')
        statement = statement.where(WorkOrder.part == part)
    return [scheduling.work_order_dict(o) for o in db.scalars(statement)]


@router.post('/work-orders', status_code=201)
def create_work_order(data: WorkOrderInput, db=Depends(get_db), u=Depends(can_write)):
    return scheduling.work_order_dict(scheduling.create_work_order(db, data, u))


@router.post('/work-orders/{id}/start')
def start_work_order(id: int, db=Depends(get_db), u=Depends(can_write)):
    order = db.get(WorkOrder, id)
    if not order:
        raise HTTPException(404, '工单不存在')
    return scheduling.work_order_dict(scheduling.start_work_order(db, order, u))


@router.get('/schedules')
def schedules(db=Depends(get_db), u=Depends(current_user)):
    return [scheduling.schedule_dict(s) for s in
            db.scalars(select(Schedule).order_by(Schedule.id.desc()).limit(50))]


@router.post('/schedules', status_code=201)
def create_schedule(data: ScheduleInput, db=Depends(get_db), u=Depends(can_write)):
    return scheduling.schedule_dict(scheduling.create(db, data, u))


@router.get('/schedules/{id}')
def schedule_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    s = db.get(Schedule, id)
    if not s:
        raise HTTPException(404, '排程不存在')
    return scheduling.schedule_dict(s)


@router.post('/schedules/{id}/confirm')
def confirm_schedule(id: int, db=Depends(get_db), u=Depends(admin_only)):
    s = db.get(Schedule, id)
    if not s:
        raise HTTPException(404, '排程不存在')
    return scheduling.schedule_dict(scheduling.confirm(db, s, u))
