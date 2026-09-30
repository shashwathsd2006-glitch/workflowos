"""Pydantic contracts for scheduling and background jobs (Phase 8).

A schedule says *when* an automation should run; a background job is one
durable unit of work handed to the worker. Both are persisted, so a restart
resumes rather than loses state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ScheduleFrequency = Literal["once", "interval", "daily", "weekly"]

SCHEDULE_FREQUENCIES: tuple[str, ...] = ("once", "interval", "daily", "weekly")

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
)

JobTriggerKind = Literal[
    "manual",
    "schedule",
    "gmail",
    "slack",
    "calendar",
    "demo",
    "retry",
]

JOB_TRIGGER_KINDS: tuple[str, ...] = (
    "manual",
    "schedule",
    "gmail",
    "slack",
    "calendar",
    "demo",
    "retry",
)


# ------------------------------------------------------------- schedules

class ScheduleCreate(BaseModel):
    """Create/attach a schedule to an automation.

    ``run_at`` is required for ``once``; ``interval_seconds`` for
    ``interval``; ``time_of_day`` (HH:MM) for ``daily``; ``time_of_day`` plus
    ``days_of_week`` for ``weekly``.
    """

    frequency: ScheduleFrequency
    timezone: str = "UTC"
    run_at: Optional[datetime] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=86400)
    time_of_day: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days_of_week: Optional[List[int]] = Field(default=None)

    model_config = {"extra": "ignore"}


class ScheduleRecord(BaseModel):
    id: str
    automation_id: str
    frequency: ScheduleFrequency
    timezone: str
    run_at: Optional[datetime] = None
    interval_seconds: Optional[int] = None
    time_of_day: Optional[str] = None
    days_of_week: List[int] = Field(default_factory=list)
    enabled: bool = True
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None
    run_count: int = 0
    failure_count: int = 0
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- jobs

class JobCreate(BaseModel):
    """Enqueue work for an automation."""

    automation_id: str
    trigger: JobTriggerKind = "manual"
    event_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    max_retries: Optional[int] = Field(default=None, ge=0, le=10)


class JobRecord(BaseModel):
    id: str
    automation_id: str
    execution_id: Optional[str] = None
    trigger: JobTriggerKind
    event_id: Optional[str] = None
    status: JobStatus
    payload: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[str] = None
    retryable: Optional[bool] = None
    retry_count: int = 0
    max_retries: int = 0
    next_attempt_at: Optional[datetime] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class JobList(BaseModel):
    jobs: List[JobRecord]
    count: int


class JobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class SchedulerStatus(BaseModel):
    """Live scheduler state. Every field is measured, never hardcoded."""

    running: bool
    enabled: bool
    tick_seconds: float
    started_at: Optional[datetime] = None
    active_schedules: int
    next_due: Optional[datetime] = None
    last_tick_at: Optional[datetime] = None
    ticks: int = 0
    jobs_enqueued: int = 0


class WorkerStatus(BaseModel):
    """Live worker state."""

    running: bool
    enabled: bool
    poll_seconds: float
    started_at: Optional[datetime] = None
    last_processed_job_id: Optional[str] = None
    last_processed_at: Optional[datetime] = None
    jobs_processed: int = 0
    queue: JobCounts = Field(default_factory=JobCounts)
