"""Pydantic contracts for the Discovery module (module 2)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, Field

WorkflowStatus = Literal["detected", "reviewed", "approved", "rejected"]

WORKFLOW_STATUSES: tuple[str, ...] = (
    "detected",
    "reviewed",
    "approved",
    "rejected",
)

ConfidenceLabel = Literal["low", "medium", "high"]


class WorkflowStep(BaseModel):
    """One displayable step of a discovered workflow."""

    application: str
    category: str
    action: str
    description: str = ""


class WorkflowCandidate(BaseModel):
    """A repeated sequence detected in the activity stream."""

    id: str
    name: str
    sequence: List[WorkflowStep]
    occurrence_count: int
    session_ids: List[str]
    similarity_score: float
    confidence: float
    confidence_label: ConfidenceLabel
    applications: List[str]
    first_seen: datetime
    last_seen: datetime
    status: WorkflowStatus = "detected"


class DiscoveredWorkflowsResponse(BaseModel):
    workflows: List[WorkflowCandidate]
    count: int


class DiscoverResponse(DiscoveredWorkflowsResponse):
    sequences_analyzed: int
    threshold: float = Field(ge=0.0, le=1.0)
