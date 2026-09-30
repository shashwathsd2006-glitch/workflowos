"""Pydantic contracts for the Execution module (Phase 6).

An execution is the auditable record of *running* an already-approved
workflow draft. It references the exact draft it ran, so history can never
drift from what was approved.

Nothing in this module decides that execution is allowed — the engine
requires ``status == "approved"`` on the draft first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ExecutionStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

EXECUTION_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
)

StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]

STEP_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
)


class ExecutionStepResult(BaseModel):
    """One executed step of a workflow run."""

    id: str
    execution_id: str
    step_number: int = Field(ge=1)
    application: str
    action: str
    purpose: str = ""
    status: StepStatus
    action_type: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ExecutionRecord(BaseModel):
    """A single run of an approved workflow draft."""

    id: str
    draft_id: str
    workflow_name: str
    status: ExecutionStatus
    current_step: int = Field(default=0, ge=0)
    total_steps: int = Field(ge=0)
    completed_steps: int = Field(default=0, ge=0)
    failed_step: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    retryable: Optional[bool] = None
    result_summary: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ExecutionDetail(ExecutionRecord):
    """An execution plus its ordered step results."""

    steps: List[ExecutionStepResult] = Field(default_factory=list)


class ExecutionList(BaseModel):
    executions: List[ExecutionRecord]
    count: int


class ExecutionStepList(BaseModel):
    execution_id: str
    steps: List[ExecutionStepResult]
    count: int


class ExecutionRequest(BaseModel):
    """Optional execute payload — reserved for future run parameters."""

    note: Optional[str] = Field(default=None, max_length=500)


class ExecuteResponse(BaseModel):
    """Result of an execute request, including the full ordered step trace."""

    execution: ExecutionRecord
    steps: List[ExecutionStepResult]
    count: int


class ActionDescriptor(BaseModel):
    """A registered, safe action the engine is allowed to run."""

    name: str
    description: str


class ExecutionRequest(BaseModel):
    """Optional run-time context for a manual execution.

    ``inputs`` is plain data merged into each integration step's payload. It
    lets a caller point a run at a specific real item — for example the Gmail
    ``message_id`` the user picked in the UI. It is data only: it can never
    introduce or change an action name, and it has no effect unless the draft
    is already approved.
    """

    inputs: Dict[str, Any] = Field(default_factory=dict)
