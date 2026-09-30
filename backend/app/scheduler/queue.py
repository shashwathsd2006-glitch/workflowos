"""Job queue service — the durable hand-off between triggers and execution.

Nothing here executes a workflow. Enqueueing only writes a row; the worker
picks it up and calls the existing execution engine. Keeping the two apart is
what makes the queue safe to inspect and safe to restart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_connection
from app.models import automation as automation_model
from app.models import job as job_model
from app.models import schedule as schedule_model
from app.schemas.job import JobRecord, JobTriggerKind, ScheduleCreate, ScheduleRecord

logger = logging.getLogger("workflowos.scheduler.queue")


class AutomationNotFoundError(Exception):
    """Raised when an automation id does not exist."""


class AutomationDisabledError(Exception):
    """Raised when work is requested for a disabled automation."""


class DuplicateJobError(Exception):
    """Raised when an identical pending job already exists."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_automation_row(connection, automation_id: str):
    automation_model.ensure_table(connection)
    return automation_model.select_by_id(connection, automation_id)


def enqueue_job(
    *,
    automation_id: str,
    trigger: JobTriggerKind = "manual",
    event_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    max_retries: Optional[int] = None,
    skip_duplicates: bool = True,
) -> JobRecord:
    """Queue work for an automation.

    ``skip_duplicates`` prevents a second pending job for the same
    automation + event, so a burst of polls cannot pile up duplicate runs.
    """
    with get_connection() as connection:
        job_model.ensure_table(connection)
        row = get_automation_row(connection, automation_id)
        if row is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found")
        if not row["enabled"]:
            raise AutomationDisabledError(
                f"Automation '{automation_id}' is disabled"
            )
        if (
            skip_duplicates
            and event_id
            and job_model.count_active_for_automation(connection, automation_id) > 0
        ):
            active = job_model.select_all(
                connection, automation_id=automation_id, limit=50
            )
            if any(job.event_id == event_id and job.status in {"queued", "running"} for job in active):
                raise DuplicateJobError(
                    f"A job for event {event_id} is already pending"
                )
        return job_model.insert_job(
            connection,
            automation_id=automation_id,
            trigger=trigger,
            event_id=event_id,
            payload=payload,
            max_retries=(
                settings.job_max_retries if max_retries is None else max_retries
            ),
        )


def create_schedule(
    automation_id: str, payload: ScheduleCreate
) -> ScheduleRecord:
    """Attach (or replace) the schedule for an automation."""
    from app.scheduler.recurrence import compute_next_run

    with get_connection() as connection:
        row = get_automation_row(connection, automation_id)
        if row is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found")
        schedule_model.ensure_table(connection)
        schedule_model.delete_for_automation(connection, automation_id)
        next_run = compute_next_run(payload)
        return schedule_model.insert_schedule(
            connection, automation_id, payload, next_run
        )


def get_schedule(automation_id: str) -> Optional[ScheduleRecord]:
    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        return schedule_model.select_by_automation(connection, automation_id)


def list_schedules() -> List[ScheduleRecord]:
    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        return schedule_model.select_all(connection)


def delete_schedule(automation_id: str) -> int:
    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        return schedule_model.delete_for_automation(connection, automation_id)


def record_schedule_run(
    connection, schedule: ScheduleRecord, *, failed: bool
) -> Optional[ScheduleRecord]:
    """Advance a schedule after a fire and recompute its next run.

    Called from inside the ticker's transaction so ``next_run`` moves forward
    even if the enqueue fails — otherwise a failing automation would spin on
    the same due time.
    """
    from app.scheduler.recurrence import compute_next_run

    payload = ScheduleCreate(
        frequency=schedule.frequency,
        timezone=schedule.timezone,
        run_at=schedule.run_at,
        interval_seconds=schedule.interval_seconds,
        time_of_day=schedule.time_of_day,
        days_of_week=schedule.days_of_week,
    )
    nxt = compute_next_run(payload)
    updates: Dict[str, Any] = {
        "last_run": _now(),
        "run_count": schedule.run_count + 1,
    }
    if failed:
        updates["failure_count"] = schedule.failure_count + 1
    if schedule.frequency == "once":
        # A one-time schedule retires after firing.
        updates["enabled"] = False
        updates["next_run"] = None
    else:
        updates["next_run"] = nxt
    return schedule_model.update_schedule(connection, schedule.id, **updates)


# ------------------------------------------------------------------ reads

def list_jobs(
    automation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[JobRecord]:
    with get_connection() as connection:
        job_model.ensure_table(connection)
        return job_model.select_all(
            connection, automation_id=automation_id, status=status, limit=limit
        )


def get_job(job_id: str) -> Optional[JobRecord]:
    with get_connection() as connection:
        job_model.ensure_table(connection)
        return job_model.select_by_id(connection, job_id)


def job_counts() -> Dict[str, int]:
    with get_connection() as connection:
        job_model.ensure_table(connection)
        return job_model.counts(connection)


__all__ = [
    "AutomationDisabledError",
    "AutomationNotFoundError",
    "DuplicateJobError",
    "create_schedule",
    "delete_schedule",
    "enqueue_job",
    "get_job",
    "get_schedule",
    "job_counts",
    "list_jobs",
    "list_schedules",
    "record_schedule_run",
]
