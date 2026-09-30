"""Pydantic contracts for the AI Understanding module (module 3, Phase 5).

Three layers:

- ``WorkflowUnderstandingInput``   — compact evidence sent to the provider
- ``WorkflowUnderstanding``        — strict structured model output
- ``WorkflowUnderstandingRecord``  — persisted row (understanding + metadata)

AI output is data only: nothing here is executable.
"""

from __future__ import annotations

from enum import Enum

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

AIProviderName = Literal["ollama", "mock"]


class WorkflowUnderstandingStep(BaseModel):
    """One ordered, AI-interpreted step of a workflow."""

    order: int = Field(ge=1)
    application: str
    category: str = ""
    action: str
    purpose: str = ""
    input: Optional[str] = None
    output: Optional[str] = None


class WorkflowUnderstandingInputStep(BaseModel):
    """Observed evidence for a single step (no ids, timestamps or metadata)."""

    order: int = Field(ge=1)
    application: str
    category: str
    action: str
    description: str = ""


class WorkflowUnderstandingInput(BaseModel):
    """Compact, normalized payload handed to an AI provider."""

    workflow_id: str
    workflow_name: str
    occurrence_count: int = Field(ge=0)
    similarity_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    session_count: int = Field(ge=0)
    applications: List[str]
    categories: List[str]
    steps: List[WorkflowUnderstandingInputStep]


class WorkflowUnderstanding(BaseModel):
    """Strict schema for AI output. Extra fields are ignored, missing
    required fields fail validation."""

    workflow_id: str
    workflow_name: str
    intent: str = Field(min_length=1)
    description: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    steps: List[WorkflowUnderstandingStep] = Field(min_length=1)
    applications: List[str]
    categories: List[str]
    inputs: List[str]
    outputs: List[str]
    dependencies: List[str]
    assumptions: List[str]
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_automation: str = Field(min_length=1)

    model_config = {"extra": "ignore"}


class WorkflowUnderstandingRecord(BaseModel):
    """A persisted understanding, including storage metadata."""

    id: str
    workflow_candidate_id: str
    workflow_name: str
    intent: str
    description: str
    trigger: str
    steps: List[WorkflowUnderstandingStep]
    applications: List[str]
    categories: List[str]
    inputs: List[str]
    outputs: List[str]
    dependencies: List[str]
    assumptions: List[str]
    confidence: float
    suggested_automation: str
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


class WorkflowUnderstandingList(BaseModel):
    understandings: List[WorkflowUnderstandingRecord]
    count: int


class Priority(str, Enum):
    """The only priority values the model may return.

    Declared as an enum rather than free text so the local model is genuinely
    constrained by the JSON schema handed to Ollama, and so an unexpected value
    fails validation instead of being stored.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ContentAnalysis(BaseModel):
    """Structured verdict returned by the local model for one input.

    Used by the ``ai_analyze_content`` workflow step, which feeds real content
    (for example a real Gmail message) to the configured local provider and
    stores what came back. Every field is required so a truncated or
    malformed model response fails loudly instead of being stored as a
    partial result.
    """

    summary: str = Field(description="One-sentence summary of the content.")
    category: str = Field(description="Short category label, e.g. billing.")
    priority: Priority = Field(description="Assigned priority.")
    reasoning: str = Field(description="Why that priority was chosen.")
    recommended_action: str = Field(description="The next action to take.")
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence.")


class ContentAnalysisRequest(BaseModel):
    """What to analyse, and what the model should produce."""

    content: str
    task: str
