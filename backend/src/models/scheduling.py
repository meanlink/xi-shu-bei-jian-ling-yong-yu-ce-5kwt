from sqlalchemy import String, Double, Integer, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Workstation(Base):
    """A maintenance bay. ``blocked_days`` is the bay calendar (ISO dates)."""
    __tablename__ = 'workstations'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked_days: Mapped[list] = mapped_column(JSON, default=list)
    calendar_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Double)


class MaterialBook(Base):
    """Version counter for one part's daily releasable quantities."""
    __tablename__ = 'material_books'
    id: Mapped[int] = mapped_column(primary_key=True)
    part: Mapped[str] = mapped_column(String(40), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[float] = mapped_column(Double)


class MaterialDay(Base):
    __tablename__ = 'material_days'
    __table_args__ = (UniqueConstraint('part', 'day', name='uq_material_part_day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    part: Mapped[str] = mapped_column(String(40))
    day: Mapped[str] = mapped_column(String(10))
    releasable: Mapped[float] = mapped_column(Double)


class WorkOrder(Base):
    __tablename__ = 'work_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    part: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20), default='normal')
    quantity: Mapped[float] = mapped_column(Double)
    duration_days: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[str] = mapped_column(String(10))
    earliest_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    eligible_workstations: Mapped[list] = mapped_column(JSON, default=list)
    started: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)


class Schedule(Base):
    __tablename__ = 'schedules'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    part: Mapped[str] = mapped_column(String(40))
    forecast_job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    start_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default='draft')
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    entries: Mapped[list] = mapped_column(JSON, default=list)
    daily: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    replaces_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class Reservation(Base):
    """One effective reservation per work order. Unique key makes confirmation idempotent."""
    __tablename__ = 'reservations'
    __table_args__ = (UniqueConstraint('work_order_id', name='uq_reservation_order'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'))
    schedule_id: Mapped[int] = mapped_column(ForeignKey('schedules.id'))
    workstation_id: Mapped[int] = mapped_column(ForeignKey('workstations.id'))
    part: Mapped[str] = mapped_column(String(40))
    start_day: Mapped[str] = mapped_column(String(10))
    end_day: Mapped[str] = mapped_column(String(10))
    material_day: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[float] = mapped_column(Double)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Double)


class ReservationDay(Base):
    """Bay occupancy rows; unique bay/day prevents double booking at the database."""
    __tablename__ = 'reservation_days'
    __table_args__ = (UniqueConstraint('workstation_id', 'day', name='uq_reservation_bay_day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey('reservations.id'))
    workstation_id: Mapped[int] = mapped_column(ForeignKey('workstations.id'))
    day: Mapped[str] = mapped_column(String(10))
