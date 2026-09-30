"""Automation API: bind approved workflow drafts to triggers and run them.

Runs delegate to the existing execution engine — this router never executes
steps itself, so there is a single execution path in the system.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.automation.engine import DraftNotApprovedError, DraftNotFoundError
from app.scheduler.recurrence import ScheduleValidationError
from app.schemas.automation import TriggerValidationError
from app.schemas.automation import (
    AutomationCreate,
    AutomationExecutionResult,
    AutomationList,
    AutomationRecord,
    AutomationUpdate,
)
from app.integrations.demo import DemoCalendar, DemoGmail, DemoSlack
from app.services import automation_service as service

DEMO_PROVIDER_FOR_TRIGGER = {
    "demo_gmail": "gmail_demo",
    "demo_slack": "slack_demo",
    "demo_calendar": "calendar_demo",
}

_JOB_TRIGGER = {
    "demo_gmail": "gmail",
    "demo_slack": "slack",
    "demo_calendar": "calendar",
}

logger = logging.getLogger("workflowos.automations")

router = APIRouter(prefix="/automations", tags=["automations"])


@router.get("", response_model=AutomationList)
def list_automations(enabled: Optional[bool] = None) -> AutomationList:
    """Return automations with live draft and execution metadata."""
    records = service.list_automations(enabled)
    return AutomationList(automations=records, count=len(records))


@router.post("", response_model=AutomationRecord, status_code=201)
def create_automation(payload: AutomationCreate) -> AutomationRecord:
    """Create an automation bound to an approved workflow draft.

    Demo trigger types are refused unless ``DEMO_MODE`` is on. They drive the
    local mock stand-ins, so accepting them in a real deployment would let a
    simulated integration be scheduled as though it were real.
    """
    if payload.trigger_type in DEMO_PROVIDER_FOR_TRIGGER:
        from app.config import settings

        if not settings.demo_mode:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Trigger '{payload.trigger_type}' needs the local demo "
                    "integrations, which are disabled. Use the real Gmail, "
                    "Slack or Calendar trigger instead."
                ),
            )
    try:
        return service.create_automation(payload)
    except service.LinkedDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except service.LinkedDraftNotApprovedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (TriggerValidationError, ScheduleValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{automation_id}", response_model=AutomationRecord)
def get_automation(automation_id: str) -> AutomationRecord:
    """Return one automation."""
    try:
        return service.get_automation(automation_id)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{automation_id}", response_model=AutomationRecord)
def update_automation(
    automation_id: str, payload: AutomationUpdate
) -> AutomationRecord:
    """Partially update an automation.

    Demo trigger types are refused unless ``DEMO_MODE`` is on, for the same
    reason as on create.
    """
    if payload.trigger_type in DEMO_PROVIDER_FOR_TRIGGER:
        from app.config import settings

        if not settings.demo_mode:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Trigger '{payload.trigger_type}' needs the local demo "
                    "integrations, which are disabled."
                ),
            )
    try:
        return service.update_automation(automation_id, payload)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (TriggerValidationError, ScheduleValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{automation_id}/enable", response_model=AutomationRecord)
def enable_automation(automation_id: str) -> AutomationRecord:
    """Enable an automation so it may be run."""
    try:
        return service.set_enabled(automation_id, True)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{automation_id}/disable", response_model=AutomationRecord)
def disable_automation(automation_id: str) -> AutomationRecord:
    """Disable an automation. Existing execution history is preserved."""
    try:
        return service.set_enabled(automation_id, False)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{automation_id}/run", response_model=AutomationExecutionResult)
def run_automation(automation_id: str) -> AutomationExecutionResult:
    """Run an enabled automation now via the existing execution engine."""
    try:
        return service.run_now(automation_id)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except service.AutomationDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DraftNotApprovedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{automation_id}/demo", response_model=AutomationExecutionResult)
def run_demo(automation_id: str) -> AutomationExecutionResult:
    """Run a demo-integration automation through the real queue + worker.

    This is the manual demo fallback: it produces a synthetic event, records
    it in the idempotency ledger, enqueues a job and lets the worker execute
    it. The response labels the result as a demo so nothing looks real.
    """
    from app.scheduler import queue
    from app.scheduler.worker import worker

    try:
        record = service.get_automation(automation_id)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not record.enabled:
        raise HTTPException(
            status_code=409,
            detail=f"Automation '{automation_id}' is disabled — enable it first",
        )

    provider = DEMO_PROVIDER_FOR_TRIGGER.get(record.trigger_type)
    if provider is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "The demo path needs a demo trigger type (demo_gmail, "
                "demo_slack or demo_calendar)"
            ),
        )
    payload = service.demo_event_payload(provider, automation_id)
    if not payload.get("is_new_event"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This demo event has already been processed for this "
                "automation — replaying it would duplicate the run."
            ),
        )
    try:
        job = queue.enqueue_job(
            automation_id=automation_id,
            trigger=_JOB_TRIGGER.get(record.trigger_type, "manual"),
            event_id=payload.get("event_row_id"),
            payload=payload,
            skip_duplicates=False,
        )
    except queue.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except queue.AutomationDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    worker.drain(limit=1)
    finished = queue.get_job(job.id)
    # Report the execution's real progress rather than placeholders.
    total_steps = 0
    completed_steps = 0
    if finished is not None and finished.execution_id:
        from app.database import get_connection as _conn
        from app.models import execution as _exec_model

        with _conn() as connection:
            _exec_model.ensure_tables(connection)
            record = _exec_model.select_execution(
                connection, finished.execution_id
            )
        if record is not None:
            total_steps = record.total_steps
            completed_steps = record.completed_steps
    return AutomationExecutionResult(
        automation_id=automation_id,
        execution_id=(finished.execution_id if finished else None) or job.id,
        execution_status=(finished.status if finished else "queued"),
        total_steps=total_steps,
        completed_steps=completed_steps,
    )


@router.delete("/{automation_id}")
def delete_automation(automation_id: str) -> dict:
    """Delete an automation. Execution history is intentionally kept."""
    try:
        deleted = service.delete_automation(automation_id)
    except service.AutomationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"deleted": deleted, "id": automation_id}
