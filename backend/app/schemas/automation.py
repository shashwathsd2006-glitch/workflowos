"""Pydantic contracts for Automations (module 6, Phase 7).

An automation is a *scheduled binding* between an approved workflow draft and
a trigger. It owns no execution logic: running an automation delegates to the
existing execution engine in ``app.automation.engine``, so there is exactly
one execution path in the system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

AutomationStatus = Literal["enabled", "disabled"]

#: Phase 8 trigger types. ``gmail``/``calendar`` poll their provider;
#: ``schedule`` uses the persisted recurrence; the ``demo_*`` variants use the
#: local stand-ins so the whole pipeline can be shown without credentials.
AutomationTriggerType = Literal[
    "manual",
    "schedule",
    "gmail",
    "slack",
    "calendar",
    "demo_gmail",
    "demo_slack",
    "demo_calendar",
]

SUPPORTED_TRIGGER_TYPES: tuple[str, ...] = (
    "manual",
    "schedule",
    "gmail",
    "slack",
    "calendar",
    "demo_gmail",
    "demo_slack",
    "demo_calendar",
)

#: Triggers that fire from a polled provider rather than a time or a button.
EVENT_TRIGGER_TYPES: tuple[str, ...] = (
    "gmail",
    "slack",
    "calendar",
    "demo_gmail",
    "demo_slack",
    "demo_calendar",
)

#: Triggers served by a local demo integration.
DEMO_TRIGGER_TYPES: tuple[str, ...] = (
    "demo_gmail",
    "demo_slack",
    "demo_calendar",
)


class TriggerValidationError(Exception):
    """Raised when a trigger definition is unusable for its type."""


def validate_trigger(trigger_type: str, trigger_config: str) -> Dict[str, Any]:
    """Validate a trigger configuration for its type.

    Returns the parsed config. Raises ``TriggerValidationError`` with a
    human-readable reason rather than letting a bad schedule reach the
    scheduler.
    """
    import json

    parsed: Dict[str, Any] = {}
    if trigger_config:
        try:
            loaded = json.loads(trigger_config)
        except (TypeError, ValueError) as exc:
            raise TriggerValidationError(
                "Trigger configuration must be valid JSON"
            ) from exc
        if not isinstance(loaded, dict):
            raise TriggerValidationError(
                "Trigger configuration must be a JSON object"
            )
        parsed = loaded

    if trigger_type == "schedule":
        if not parsed.get("frequency"):
            raise TriggerValidationError(
                "A schedule trigger needs a 'frequency' "
                "(once, interval, daily or weekly)"
            )
    elif trigger_type in {"gmail", "demo_gmail"}:
        interval = parsed.get("poll_interval_seconds")
        if interval is not None:
            try:
                if int(interval) < 5:
                    raise TriggerValidationError(
                        "Gmail poll interval must be at least 5 seconds"
                    )
            except (TypeError, ValueError) as exc:
                raise TriggerValidationError(
                    "Gmail poll interval must be a number"
                ) from exc
        if parsed.get("from") and "@" not in str(parsed["from"]):
            raise TriggerValidationError("Gmail 'from' must be an email address")
    elif trigger_type in {"calendar", "demo_calendar"}:
        if parsed.get("timezone"):
            from app.scheduler.recurrence import resolve_timezone

            try:
                resolve_timezone(str(parsed["timezone"]))
            except Exception as exc:  # noqa: BLE001
                raise TriggerValidationError(
                    f"Unknown timezone '{parsed['timezone']}'"
                ) from exc
    elif trigger_type not in SUPPORTED_TRIGGER_TYPES:
        raise TriggerValidationError(f"Unsupported trigger type '{trigger_type}'")
    return parsed


class AutomationCore(BaseModel):
    """The user-editable content of an automation."""

    name: str = Field(min_length=1, max_length=120)
    draft_id: str = Field(min_length=1)
    description: str = ""
    trigger_type: AutomationTriggerType = "manual"
    #: Human-readable trigger detail, e.g. a cron expression. Never executed.
    trigger_config: str = Field(default="", max_length=200)
    enabled: bool = True

    model_config = {"extra": "ignore"}


class AutomationRecord(AutomationCore):
    """A persisted automation plus denormalised display metadata.

    ``workflow_name``/``draft_status``/``execution_count``/``last_execution``
    are derived on read from the draft and execution tables so the list view
    needs a single request.
    """

    id: str
    workflow_name: str
    draft_status: str
    execution_count: int = 0
    last_execution: Optional["ExecutionSummaryView"] = None
    # Phase 8 scheduling counters, read from the schedule row.
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None
    run_count: int = 0
    failure_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"extra": "ignore"}


class ExecutionSummaryView(BaseModel):
    """The most recent execution of an automation, for list display."""

    id: str
    status: str
    completed_steps: int
    total_steps: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AutomationList(BaseModel):
    automations: List[AutomationRecord]
    count: int


class AutomationCreate(AutomationCore):
    """Create payload. The linked draft must exist and be approved."""


class AutomationUpdate(BaseModel):
    """Partial update payload — every field optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    trigger_type: Optional[AutomationTriggerType] = None
    trigger_config: Optional[str] = Field(default=None, max_length=200)
    enabled: Optional[bool] = None


class AutomationExecutionResult(BaseModel):
    """Result of a manual "Run now" — delegates to the existing engine."""

    automation_id: str
    execution_id: str
    execution_status: str
    total_steps: int
    completed_steps: int
