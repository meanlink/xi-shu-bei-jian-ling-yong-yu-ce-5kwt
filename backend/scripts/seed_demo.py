import json
import time
from datetime import date, timedelta

from sqlalchemy import func, select

from src.config.database import Session
from migrations.initial import migrate
from src.models import (Audit, Dataset, Delivery, Job, MaterialBook, MaterialDay, Report,
                        Schedule, User, WorkOrder, Workstation)
from src.services.business import analyze, metadata, seed_records
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum

WORKSTATIONS = [("W1", "一号检修工位 · 立加"), ("W2", "二号检修工位 · 卧加"),
                ("W3", "三号检修工位 · 磨床")]
# Releasable stock per part and day, starting on the first forecast day.
MATERIAL = {"R17": 9, "G22": 7}
ORDERS = [
    ("LS-2609", "R17 主轴密封环更换", "R17", "line_stop", 5, 2, 3, 0),
    ("LS-2610", "R17 冷却泵密封环抢修", "R17", "line_stop", 4, 1, 5, 1),
    ("NM-2611", "R17 例行密封环更换", "R17", "normal", 6, 2, 9, 2),
    ("NM-2612", "R17 备件轮换", "R17", "normal", 3, 1, 11, 4),
    ("LS-2613", "G22 联轴片断裂抢修", "G22", "line_stop", 5, 2, 4, 1),
    ("NM-2614", "G22 例行联轴片更换", "G22", "normal", 4, 1, 10, 3),
    ("NM-2615", "G22 对中复查", "G22", "normal", 2, 1, 6, 1),
    # Deliberately more than any single day's releasable stock: demonstrates the shortage reason.
    ("LS-2616", "R17 全线抢修备料", "R17", "line_stop", 12, 1, 6, 0),
]


def seed_demo_parts():
    """Workstations, material calendar and work orders for the scheduling demo."""
    with Session() as db:
        for code, name in WORKSTATIONS:
            if not db.scalar(select(Workstation).where(Workstation.code == code)):
                db.add(Workstation(code=code, name=name, active=True, blocked_days=[],
                                   calendar_version=1, created_at=time.time()))
        db.flush()
        jobs = list(db.scalars(select(Job).where(Job.approved == True)))
        if not jobs:
            db.commit()
            return
        # Material days follow the approved forecast table so every demo day has a basis.
        for job in jobs:
            part = str(job.params.get("part"))
            days = [str(row["day"]) for row in (job.result or {}).get("table", {}).get("rows", [])]
            quantity = MATERIAL.get(part, 8)
            for index, day in enumerate(days):
                exists = db.scalar(select(MaterialDay).where(MaterialDay.part == part, MaterialDay.day == day))
                if not exists:
                    # One deliberately tight day shows the holdback and the shortage reason.
                    amount = 3 if index == 6 else quantity
                    db.add(MaterialDay(part=part, day=day, releasable=amount))
        for part in MATERIAL:
            if not db.scalar(select(MaterialBook).where(MaterialBook.part == part)):
                db.add(MaterialBook(part=part, version=1, updated_at=time.time()))
        if not db.scalar(select(WorkOrder).limit(1)):
            first_day = min(str(row["day"]) for job in jobs
                            for row in (job.result or {}).get("table", {}).get("rows", []))
            start = date.fromisoformat(first_day)
            for code, name, part, kind, quantity, duration, deadline_offset, earliest_offset in ORDERS:
                db.add(WorkOrder(
                    code=code, name=name, part=part, kind=kind, quantity=quantity,
                    duration_days=duration,
                    deadline=(start + timedelta(days=deadline_offset)).isoformat(),
                    earliest_start=(start + timedelta(days=earliest_offset)).isoformat(),
                    eligible_workstations=[], started=False, created_at=time.time()))
        db.commit()


def seed_demo_schedule():
    """One ready-to-review draft per part so the scheduling page is not empty."""
    from src.schemas.scheduling import ScheduleInput
    from src.services import scheduling
    with Session() as db:
        admin = db.scalar(select(User).where(User.role == 'admin'))
        for job in db.scalars(select(Job).where(Job.approved == True).order_by(Job.id)):
            part = str(job.params.get('part'))
            name = f'{part} 演示检修排程'
            if not admin or db.scalar(select(Schedule).where(Schedule.name == name)):
                continue
            rows = (job.result or {}).get('table', {}).get('rows', [])
            if not rows:
                continue
            try:
                scheduling.create(db, ScheduleInput(name=name, part=part, forecast_job_id=job.id,
                                                    start_date=str(rows[0]['day'])), admin)
            except Exception:
                # A demo draft must never stop the backend from starting.
                db.rollback()


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        if not db.scalar(select(Job).where(Job.name=='G22 预测基线')):
            rows=seed_records()
            admin=db.scalar(select(User).where(User.role=='admin'))
            params={'part':'G22','alpha':0.2,'horizon':14,'variant':'sba'}
            result=analyze(rows,params)
            j=Job(name='G22 预测基线',dataset_id=d.id,params=params,status='completed',result=result,
                  created_by=admin.id,created_at=time.time()-3500,finished_at=time.time()-3495,approved=True)
            db.add(j);db.flush()
        db.commit()
        seed_demo_parts()
        seed_demo_schedule()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Workstation,WorkOrder,MaterialDay,Schedule]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts


if __name__=='__main__':seed()
