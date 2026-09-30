"""Execution engine — runs an *approved* workflow draft, step by step.

Guarantees:

- Only ``approved`` drafts run; every other state is refused before any step
  is created (see ``app.api.routes_executions``).
- Steps run in the exact generated order and are never reordered or retried.
- Every run is a new execution record; history is never overwritten.
- Each step is dispatched through ``app.automation.actions.ACTION_REGISTRY``,
  so model output cannot choose a callable.
- A failing step stops the run and marks the execution failed; the first
  failure is recorded and the remaining steps stay ``pending``.
- The AI provider is not involved: execution consumes the persisted draft.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.automation.actions import (
    ActionContext,
    NonRetryableActionError,
    RetryableActionError,
    UnsupportedActionError,
    registry_names,
    resolve_action,
    run_action,
)
from app.database import get_connection
from app.models import activity as activity_model
from app.models import execution as execution_model
from app.models import generator as draft_model
from app.schemas.activity import ActivityEvent
from app.schemas.execution import (
    ExecutionDetail,
    ExecutionRecord,
    ExecutionStepResult,
)
from app.services import activity_service

logger = logging.getLogger("workflowos.execution")

#: Only this draft status may be executed.
EXECUTABLE_STATUS = "approved"

APPLICATION = "WorkFlowOS"
CATEGORY = "system"


class DraftNotFoundError(Exception):
    """Raised when a draft id does not exist."""


class DraftNotApprovedError(Exception):
    """Raised when a draft exists but is not approved."""

    def __init__(self, draft_id: str, status: str) -> None:
        super().__init__(
            f"Workflow draft '{draft_id}' is '{status}', not 'approved' — "
            "only approved workflows may be executed"
        )
        self.draft_id = draft_id
        self.status = status


class ExecutionNotFoundError(Exception):
    """Raised when an execution id does not exist."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    connection,
    execution_id: str,
    action: str,
    description: str,
    metadata: Optional[dict] = None,
) -> None:
    """Append one execution event to the existing activity timeline.

    Reuses the activity service so execution shows up on the same timeline as
    observed activity, rather than in a parallel logging system.
    """
    payload = dict(metadata or {})
    payload.setdefault("execution_id", execution_id)
    payload.setdefault("source", "execution")
    event = ActivityEvent(
        id=activity_model.next_identifier(connection, activity_service.EVENT_PREFIX),
        timestamp=_now(),
        application=APPLICATION,
        category=CATEGORY,
        action=action,
        description=description,
        metadata=payload,
        session_id=f"execution-{execution_id}",
    )
    activity_model.insert_event(connection, event)


def execute_draft(
    draft_id: str,
    *,
    trigger_payload: Optional[Dict[str, Any]] = None,
    source: str = "manual",
) -> ExecutionDetail:
    """Run an approved draft and return the full execution trace.

    ``trigger_payload`` carries context from whatever fired the run (a Gmail
    message id, a calendar event id). It is merged into integration step
    payloads so a step can act on the event that triggered it. It is data
    only — it can never introduce or alter an action name.
    """
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        draft = draft_model.select_by_id(connection, draft_id)
        if draft is None:
            raise DraftNotFoundError(f"Workflow draft '{draft_id}' not found")
        if draft.status != EXECUTABLE_STATUS:
            raise DraftNotApprovedError(draft_id, draft.status)

        execution_model.ensure_tables(connection)
        execution_id = execution_model.next_execution_identifier(connection)
        execution = execution_model.insert_execution(
            connection,
            execution_id,
            draft_id=draft.id,
            workflow_name=draft.name,
            total_steps=len(draft.steps),
        )
        _audit(
            connection,
            execution_id,
            "execution_created",
            f"Execution {execution_id} created for approved draft {draft_id}",
            {"draft_id": draft_id, "total_steps": len(draft.steps)},
        )

        # Materialise every step as `pending` up front so the UI can show the
        # full plan immediately, in generated order.
        for step in draft.steps:
            execution_model.insert_step(
                connection,
                execution_model.next_step_identifier(connection),
                execution_id,
                step.step_number,
                step.application,
                step.action,
                step.purpose,
            )

    started = _now()
    with get_connection() as connection:
        execution_model.update_execution(
            connection,
            execution_id,
            status="running",
            started_at=activity_model.format_timestamp(started),
        )
        _audit(
            connection,
            execution_id,
            "execution_started",
            f"Execution {execution_id} started",
        )

        previous_output: Optional[str] = None
        failure: Optional[str] = None
        failure_type: Optional[str] = None
        failure_retryable: bool = False
        failed_step: Optional[str] = None
        failed_step_number: Optional[int] = None
        completed = 0
        # Which real services this run actually touched, so the completion
        # summary can state the truth instead of assuming a mock run.
        used_providers: set = set()

        for step in draft.steps:
            step_row = execution_model.select_step(
                connection, execution_id, step.step_number
            )
            assert step_row is not None
            step_start = _now()
            execution_model.update_step(
                connection,
                step_row.id,
                "running",
                started_at=activity_model.format_timestamp(step_start),
            )
            execution_model.update_execution(
                connection, execution_id, current_step=step.step_number
            )
            _audit(
                connection,
                execution_id,
                "step_started",
                f"Step {step.step_number} started: {step.application} {step.action}",
                {"step_number": step.step_number, "application": step.application},
            )

            action_type = resolve_action(step.action, application=step.application)
            if action_type is None:
                message = (
                    f"Unsupported action '{step.action}'. No registered "
                    f"implementation — allowed actions: "
                    f"{', '.join(registry_names())}"
                )
                execution_model.update_step(
                    connection,
                    step_row.id,
                    "failed",
                    action_type=None,
                    error=message,
                    completed_at=activity_model.format_timestamp(_now()),
                    duration_ms=_elapsed_ms(step_start),
                )
                _audit(
                    connection,
                    execution_id,
                    "step_failed",
                    f"Step {step.step_number} failed: {step.action}",
                    {"step_number": step.step_number, "error": message},
                )
                failure = message
                failure_type = "UnsupportedAction"
                failure_retryable = False
                failed_step = f"{step.step_number}. {step.action}"
                failed_step_number = step.step_number
                break

            context = ActionContext(
                execution_id=execution_id,
                step_number=step.step_number,
                application=step.application,
                action=step.action,
                purpose=step.purpose,
                step_input=step.input,
                previous_output=previous_output,
                parameters=dict(step.parameters or {}),
                trigger_payload=dict(trigger_payload or {}),
                action_type=action_type,
            )
            try:
                # Release the write lock before the action runs. An action may
                # perform external I/O *and* write to the database itself — an
                # expiring OAuth token is refreshed and re-stored on a second
                # connection. Holding this transaction open across the call
                # would block that write and fail the step with
                # "database is locked".
                connection.commit()
                output = run_action(action_type, context)
                if isinstance(output, str):
                    try:
                        import json as _json

                        decoded = _json.loads(output)
                    except (TypeError, ValueError):
                        decoded = None
                    if isinstance(decoded, dict):
                        used_providers.add(
                            str(decoded.get("provider") or action_type)
                        )
            except (RetryableActionError, NonRetryableActionError) as exc:
                # Integration actions classify their own failures so the
                # background worker can decide about retries.
                output = None
                failure = str(exc)
                failure_type = "UnsupportedAction"
                failure_retryable = False
                failed_step = f"{step.step_number}. {step.action}"
                failed_step_number = step.step_number
                failure_type = type(exc).__name__
                failure_retryable = isinstance(exc, RetryableActionError)
                execution_model.update_step(
                    connection,
                    step_row.id,
                    "failed",
                    action_type=action_type,
                    error=failure,
                    completed_at=activity_model.format_timestamp(_now()),
                    duration_ms=_elapsed_ms(step_start),
                )
                _audit(
                    connection,
                    execution_id,
                    "step_failed",
                    f"Step {step.step_number} failed: {step.action}",
                    {
                        "step_number": step.step_number,
                        "error": failure,
                        "error_type": failure_type,
                        "retryable": failure_retryable,
                    },
                )
                break
            except UnsupportedActionError as exc:  # pragma: no cover - defensive
                output = None
                failure = str(exc)
                failure_type = "UnsupportedAction"
                failure_retryable = False
                failed_step = f"{step.step_number}. {step.action}"
                failed_step_number = step.step_number
                execution_model.update_step(
                    connection,
                    step_row.id,
                    "failed",
                    action_type=action_type,
                    error=failure,
                    completed_at=activity_model.format_timestamp(_now()),
                    duration_ms=_elapsed_ms(step_start),
                )
                _audit(
                    connection,
                    execution_id,
                    "step_failed",
                    f"Step {step.step_number} failed",
                    {"step_number": step.step_number, "error": failure},
                )
                break

            previous_output = output
            completed += 1
            execution_model.update_step(
                connection,
                step_row.id,
                "completed",
                action_type=action_type,
                input=step.input,
                output=output,
                completed_at=activity_model.format_timestamp(_now()),
                duration_ms=_elapsed_ms(step_start),
            )
            execution_model.update_execution(
                connection, execution_id, completed_steps=completed
            )
            _audit(
                connection,
                execution_id,
                "step_completed",
                f"Step {step.step_number} completed: {step.action}",
                {"step_number": step.step_number, "action_type": action_type},
            )

        finished = _now()
        if failure is None:
            summary = _completion_summary(
                completed, len(draft.steps), used_providers, registry_names()
            )
            execution_model.update_execution(
                connection,
                execution_id,
                status="completed",
                completed_at=activity_model.format_timestamp(finished),
                duration_ms=_elapsed_ms(started),
                result_summary=summary,
            )
            _audit(
                connection,
                execution_id,
                "execution_completed",
                f"Execution {execution_id} completed "
                f"({completed}/{len(draft.steps)} steps)",
                {"completed_steps": completed},
            )
        else:
            execution_model.update_execution(
                connection,
                execution_id,
                status="failed",
                completed_at=activity_model.format_timestamp(finished),
                duration_ms=_elapsed_ms(started),
                error=failure,
                error_type=failure_type,
                retryable=failure_retryable,
                failed_step=failed_step,
                result_summary=(
                    f"Failed at step {failed_step_number} of "
                    f"{len(draft.steps)} after {completed} completed."
                ),
            )
            _audit(
                connection,
                execution_id,
                "execution_failed",
                f"Execution {execution_id} failed at step {failed_step_number}",
                {"error": failure, "failed_step": failed_step},
            )

    return get_execution(execution_id)


def _completion_summary(
    completed: int,
    total: int,
    used_providers,
    available_actions,
) -> str:
    """Describe a successful run truthfully.

    The previous wording claimed every run used mock actions and contacted no
    external service. That was false whenever the workflow really read or sent
    Gmail, which made a genuine run look simulated on screen.
    """
    providers = sorted(item for item in used_providers if item)
    if not providers:
        return (
            f"Completed {completed} of {total} step(s) using the allowlisted "
            f"action registry ({', '.join(available_actions)}). "
            "No external service was contacted."
        )
    real = [item for item in providers if not item.endswith("_demo")]
    if not real:
        return (
            f"Completed {completed} of {total} step(s) using local demo "
            f"stand-ins ({', '.join(providers)}). No real account was contacted."
        )
    return (
        f"Completed {completed} of {total} step(s) using real "
        f"{', '.join(real)}."
    )


def _elapsed_ms(start: datetime) -> int:
    return max(0, int((_now() - start).total_seconds() * 1000))


def get_execution(execution_id: str) -> ExecutionDetail:
    """Load an execution with its ordered step results."""
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        record = execution_model.select_execution(connection, execution_id)
        if record is None:
            raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
        steps = execution_model.select_steps(connection, execution_id)
    return ExecutionDetail(**record.model_dump(), steps=steps)


def list_executions(
    draft_id: Optional[str] = None, status: Optional[str] = None
) -> List[ExecutionRecord]:
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        return execution_model.select_executions(connection, draft_id, status)


def get_steps(execution_id: str) -> List[ExecutionStepResult]:
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        if execution_model.select_execution(connection, execution_id) is None:
            raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
        return execution_model.select_steps(connection, execution_id)
