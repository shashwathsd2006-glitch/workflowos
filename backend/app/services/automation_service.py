"""Automation service — bindings between approved drafts and triggers.

Deliberately thin. An automation stores *what* to run and *when* it is
allowed to run; the actual step-by-step work is delegated to
``app.automation.engine.execute_draft`` so the system keeps exactly one
execution path and one audit trail.

No scheduler runs here: ``trigger_type`` is recorded and displayed, but
nothing fires on a timer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.automation.engine import (
    DraftNotApprovedError,
    DraftNotFoundError,
    execute_draft,
)
from app.database import get_connection
from app.models import automation as automation_model
from app.models import execution as execution_model
from app.models import generator as draft_model
from app.schemas.automation import (
    AutomationCore,
    AutomationCreate,
    AutomationExecutionResult,
    AutomationRecord,
    AutomationUpdate,
    ExecutionSummaryView,
)

logger = logging.getLogger("workflowos.automations")


class AutomationNotFoundError(Exception):
    """Raised when an automation id does not exist."""


class LinkedDraftNotFoundError(Exception):
    """Raised when the draft an automation points at does not exist."""

    def __init__(self, draft_id: str) -> None:
        super().__init__(f"Workflow draft '{draft_id}' not found")
        self.draft_id = draft_id


class LinkedDraftNotApprovedError(Exception):
    """Raised when the linked draft is not approved."""

    def __init__(self, draft_id: str, status: str) -> None:
        super().__init__(
            f"Workflow draft '{draft_id}' is '{status}', not 'approved' — "
            "only approved workflows can be automated"
        )
        self.draft_id = draft_id
        self.status = status


class AutomationDisabledError(Exception):
    """Raised when a disabled automation is asked to run."""

    def __init__(self, automation_id: str) -> None:
        super().__init__(
            f"Automation '{automation_id}' is disabled — enable it before running"
        )
        self.automation_id = automation_id


def _hydrate(connection, row) -> AutomationRecord:
    """Attach draft, execution and schedule state to a raw automation row."""
    from app.models import schedule as schedule_model

    draft_model.ensure_table(connection)
    schedule_model.ensure_table(connection)
    # The execution table is read for the counters, so guarantee it exists
    # rather than assuming init_db() ran first.
    execution_model.ensure_tables(connection)
    schedule = schedule_model.select_by_automation(connection, row["id"])
    draft = draft_model.select_by_id(connection, row["draft_id"])
    executions = execution_model.select_executions(
        connection, draft_id=row["draft_id"]
    )
    latest = executions[0] if executions else None
    summary = (
        ExecutionSummaryView(
            id=latest.id,
            status=latest.status,
            completed_steps=latest.completed_steps,
            total_steps=latest.total_steps,
            started_at=latest.started_at,
            completed_at=latest.completed_at,
        )
        if latest
        else None
    )
    return automation_model.row_to_record(
        row,
        workflow_name=draft.name if draft else "(missing draft)",
        draft_status=draft.status if draft else "missing",
        execution_count=len(executions),
        last_execution=summary,
        next_run=schedule.next_run if schedule else None,
        last_run=schedule.last_run if schedule else None,
        run_count=schedule.run_count if schedule else 0,
        failure_count=schedule.failure_count if schedule else 0,
    )


def _maybe_create_schedule(automation_id: str, payload) -> None:
    """Persist a recurrence when the trigger is a schedule."""
    if payload.trigger_type != "schedule":
        return
    from app.scheduler.queue import create_schedule
    from app.schemas.job import ScheduleCreate

    import json

    config = json.loads(payload.trigger_config or "{}")
    definition = ScheduleCreate(
        frequency=config.get("frequency", "interval"),
        timezone=config.get("timezone", "UTC"),
        run_at=config.get("run_at"),
        interval_seconds=config.get("interval_seconds"),
        time_of_day=config.get("time_of_day"),
        days_of_week=config.get("days_of_week"),
    )
    create_schedule(automation_id, definition)


def _on_automation_disabled(automation_id: str) -> None:
    """A disabled automation must not keep a live schedule or queued work."""
    from app.models import job as job_model
    from app.models import schedule as schedule_model

    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        job_model.ensure_table(connection)
        schedule = schedule_model.select_by_automation(connection, automation_id)
        if schedule is not None:
            schedule_model.update_schedule(
                connection, schedule.id, enabled=False, next_run=None
            )
        job_model.cancel_jobs_for_automation(connection, automation_id)


def _cancel_pending_work(connection, automation_id: str) -> None:
    """Drop schedule and queued jobs before an automation is deleted."""
    from app.models import job as job_model
    from app.models import schedule as schedule_model

    schedule_model.ensure_table(connection)
    job_model.ensure_table(connection)
    schedule_model.delete_for_automation(connection, automation_id)
    job_model.cancel_jobs_for_automation(connection, automation_id)


def list_automations(enabled: Optional[bool] = None) -> List[AutomationRecord]:
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        rows = automation_model.select_all(connection, enabled)
        return [_hydrate(connection, row) for row in rows]


def get_automation(automation_id: str) -> AutomationRecord:
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
        if row is None:
            raise AutomationNotFoundError(
                f"Automation '{automation_id}' not found"
            )
        return _hydrate(connection, row)


def create_automation(payload: AutomationCreate) -> AutomationRecord:
    """Create an automation bound to an existing approved draft."""
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        draft_model.ensure_table(connection)
        draft = draft_model.select_by_id(connection, payload.draft_id)
        if draft is None:
            raise LinkedDraftNotFoundError(payload.draft_id)
        if draft.status != "approved":
            raise LinkedDraftNotApprovedError(payload.draft_id, draft.status)
        row = automation_model.insert_automation(connection, payload)
        identifier = row["id"]
    _maybe_create_schedule(identifier, payload)
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, identifier)
        assert row is not None
        return _hydrate(connection, row)


def update_automation(
    automation_id: str, payload: AutomationUpdate
) -> AutomationRecord:
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
        if row is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found")
        changes = {
            key: value
            for key, value in payload.model_dump(exclude_unset=True).items()
            if value is not None
        }
        row = automation_model.update_automation(connection, automation_id, changes)
        trigger_type = changes.get("trigger_type")
    if trigger_type == "schedule":
        # Re-point the schedule at the new definition (replaces any existing).
        from app.models import schedule as schedule_model

        with get_connection() as connection:
            automation_model.ensure_table(connection)
            automation_model.ensure_table(connection)
            fresh = automation_model.select_by_id(connection, automation_id)
            assert fresh is not None
            core = automation_model.row_to_core(fresh)
            schedule_model.ensure_table(connection)
            schedule_model.delete_for_automation(connection, automation_id)
        _maybe_create_schedule(automation_id, core)
    elif trigger_type is not None:
        from app.models import schedule as schedule_model

        with get_connection() as connection:
            schedule_model.ensure_table(connection)
            schedule_model.delete_for_automation(connection, automation_id)
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
        assert row is not None
        return _hydrate(connection, row)


def set_enabled(automation_id: str, enabled: bool) -> AutomationRecord:
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
        if row is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found")
        row = automation_model.set_enabled(connection, automation_id, enabled)
    if not enabled:
        _on_automation_disabled(automation_id)
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
        assert row is not None
        return _hydrate(connection, row)


def delete_automation(automation_id: str) -> int:
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        if automation_model.select_by_id(connection, automation_id) is None:
            raise AutomationNotFoundError(f"Automation '{automation_id}' not found")
        _cancel_pending_work(connection, automation_id)
        return automation_model.delete_automation(connection, automation_id)


def automation_executions(automation_id: str):
    """Execution history for the draft an automation points at."""
    record = get_automation(automation_id)
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        return execution_model.select_executions(connection, draft_id=record.draft_id)



def latest_event_payload(
    trigger_type: str, automation_id: str
) -> Dict[str, Any]:
    """The most recent recorded provider event for an automation, if any.

    Returns the event's own payload (for example a Gmail ``message_id``) so a
    manual run acts on real data. Returns ``{}`` when nothing has been
    recorded yet, which leaves the workflow to its normal search behaviour.
    """
    from app.database import get_connection
    from app.models import integration as integration_model
    from app.scheduler.service import TRIGGER_PROVIDERS

    provider_name = TRIGGER_PROVIDERS.get(trigger_type)
    if not provider_name or provider_name.endswith("_demo"):
        return {}
    try:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            events = integration_model.select_events(
                connection,
                provider=provider_name,
                automation_id=automation_id,
                limit=1,
            )
    except Exception:  # noqa: BLE001 - a missing event is not a failure
        return {}
    if not events:
        return {}
    import json as _json

    try:
        payload = _json.loads(events[0]["payload_json"] or "{}")
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_now(automation_id: str) -> AutomationExecutionResult:
    """Run an enabled automation through the existing execution engine.

    This creates a *new* execution record; prior executions are untouched.
    """
    record = get_automation(automation_id)
    if not record.enabled:
        raise AutomationDisabledError(automation_id)
    # Replay the most recent real provider event for this automation, so a
    # manual run behaves exactly like the background path the worker performs
    # (it passes the recorded event payload too). Without this a Gmail step
    # would have no message id and fall back to a mailbox search.
    trigger_payload = latest_event_payload(record.trigger_type, automation_id)
    detail = execute_draft(
        record.draft_id,
        trigger_payload=trigger_payload,
        source=f"manual:{record.trigger_type}",
    )
    return AutomationExecutionResult(
        automation_id=automation_id,
        execution_id=detail.id,
        execution_status=detail.status,
        total_steps=detail.total_steps,
        completed_steps=detail.completed_steps,
    )


def demo_event_payload(provider_name: str, automation_id: str) -> Dict[str, Any]:
    """Build (and idempotently record) a synthetic event for the demo path.

    The event is written to ``integration_events`` exactly like a real polled
    event, so the demo exercises the same dedupe, queue and worker code.
    """
    from app.models import integration as integration_model
    from app.integrations.registry import get_provider

    provider = get_provider(provider_name)
    events = provider.poll_events({"demo_slot": 0}) or [
        {
            "external_event_id": f"{provider_name}:demo-manual",
            "provider": provider_name,
            "is_mock": True,
        }
    ]
    event = events[0]
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        event_row_id = integration_model.record_event(
            connection,
            provider=provider_name,
            external_event_id=str(event.get("external_event_id")),
            automation_id=automation_id,
            payload=event,
        )
    payload = dict(event)
    payload["is_demo"] = True
    payload["provider"] = provider_name
    payload["event_row_id"] = event_row_id
    # False means this exact event was already recorded for this automation,
    # so re-running it would violate idempotency.
    payload["is_new_event"] = event_row_id is not None
    return payload


__all__ = [
    "AutomationCreate",
    "AutomationDisabledError",
    "AutomationNotFoundError",
    "AutomationUpdate",
    "DraftNotApprovedError",
    "DraftNotFoundError",
    "LinkedDraftNotApprovedError",
    "LinkedDraftNotFoundError",
    "automation_executions",
    "create_automation",
    "demo_event_payload",
    "delete_automation",
    "get_automation",
    "list_automations",
    "run_now",
    "set_enabled",
    "update_automation",
]
