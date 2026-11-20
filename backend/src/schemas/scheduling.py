from pydantic import BaseModel, Field
from typing import Literal


class WorkOrderInput(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=160)
    part: str = Field(max_length=40)
    kind: Literal['line_stop', 'normal'] = 'normal'
    quantity: float = Field(gt=0, le=1_000_000_000)
    duration_days: int = Field(ge=1, le=60)
    deadline: str = Field(max_length=10)
    earliest_start: str | None = Field(default=None, max_length=10)
    eligible_workstations: list[str] = Field(default_factory=list, max_length=50)


class ScheduleInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    part: str = Field(max_length=40)
    forecast_job_id: int
    start_date: str = Field(max_length=10)


class WorkstationInput(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=120)


class WorkstationCalendarInput(BaseModel):
    blocked_days: list[str] = Field(default_factory=list, max_length=400)


class MaterialDayInput(BaseModel):
    part: str = Field(max_length=40)
    day: str = Field(max_length=10)
    releasable: float = Field(ge=0, le=1_000_000_000)
