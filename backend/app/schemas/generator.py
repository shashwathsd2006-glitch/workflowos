"""Pydantic contracts for the Workflow Generator (module 4, Phase 6).

A ``WorkflowDraft`` is a *plan*, never an execution command: it describes
intended actions as data that a future Automation Engine (Phase 7) may one
day propose — nothing here runs.

Lifecycle: ``review`` (freshly generated or edited) → ``approved`` /
``rejected`` only through explicit human endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

WorkflowDraftStatus = Literal["draft", "pending_approval", "approved", "rejected"]

WORKFLOW_DRAFT_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
)

TriggerType = Literal["event", "schedule", "manual"]


class WorkflowTrigger(BaseModel):
    """What starts the workflow — described, never subscribed to."""

    type: TriggerType = "event"
    application: str = Field(min_length=1)
    action: str = Field(min_length=1)


class GeneratedWorkflowStep(BaseModel):
    """One intended step of a generated workflow."""

    step_number: int = Field(ge=1)
    application: str = Field(min_length=1)
    action: str = Field(min_length=1)
    purpose: str = ""
    input: Optional[str] = None
    output: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class WorkflowDraftCore(BaseModel):
    """The editable content of a draft — shared by generation and edits."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    trigger: WorkflowTrigger
    steps: List[GeneratedWorkflowStep] = Field(min_length=1)
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    applications: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    model_config = {"extra": "ignore"}


class WorkflowDraft(WorkflowDraftCore):
    """A generated workflow draft — not yet persisted.

    This is the in-memory representation returned by the generator service.
    It mirrors WorkflowDraftCore but can be used independently of the
    persistence layer.
    """

    model_config = {"extra": "ignore"}


class WorkflowDraftRecord(WorkflowDraftCore):
    """A persisted draft including storage and lifecycle metadata."""

    id: str
    workflow_candidate_id: str
    understanding_id: str
    generated_by: str
    model: str
    status: WorkflowDraftStatus
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None


class WorkflowDraftList(BaseModel):
    drafts: List[WorkflowDraftRecord]
    count: int


class WorkflowDraftEdit(BaseModel):
    """Partial update payload — every field optional, validated on merge."""

    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, min_length=1)
    trigger: Optional[WorkflowTrigger] = None
    steps: Optional[List[GeneratedWorkflowStep]] = Field(default=None, min_length=1)
    inputs: Optional[List[str]] = None
    outputs: Optional[List[str]] = None
    conditions: Optional[List[str]] = None
    assumptions: Optional[List[str]] = None


class RejectWorkflowRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class WorkflowGenerationInput(BaseModel):
    """Compact evidence handed to the provider for draft generation."""

    workflow_id: str
    workflow_name: str
    understanding_id: str
    intent: str
    description: str
    trigger: str
    applications: List[str]
    categories: List[str]
    steps: List[Dict[str, Any]]
    inputs: List[str]
    outputs: List[str]
    dependencies: List[str]
    assumptions: List[str]
    understanding_confidence: float = Field(ge=0.0, le=1.0)
    occurrence_count: int = Field(ge=0)
