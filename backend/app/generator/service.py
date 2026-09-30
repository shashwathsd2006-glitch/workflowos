"""Workflow draft generation service — the orchestration point for Phase 5.

Flow:  WorkflowCandidate + WorkflowUnderstanding → build generation input →
       provider → raw response → JSON extraction → Pydantic validation →
       normalization → persistence → record

Generation produces a *proposal* only. Nothing here executes, schedules or
approves anything: approval is a separate, explicit human act performed
through the draft endpoints.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from app.ai.errors import AIProviderError, AIResponseError
from app.ai.provider import get_provider
from app.database import get_connection
from app.generator.parser import parse_draft
from app.models import generator as draft_model
from app.models import understanding as understanding_model
from app.models import workflow_candidate as candidate_model
from app.schemas.ai import WorkflowUnderstandingRecord
from app.schemas.discovery import WorkflowCandidate
from app.schemas.generator import (
    WorkflowDraftRecord,
    WorkflowGenerationInput,
)

logger = logging.getLogger("workflowos.generator")


class WorkflowCandidateNotFoundError(Exception):
    """Raised when a workflow candidate id does not exist."""


class UnderstandingNotFoundError(Exception):
    """Raised when a candidate has no AI understanding to generate from."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(
            f"Workflow candidate '{candidate_id}' has no AI understanding — "
            "run 'Understand with AI' first"
        )
        self.candidate_id = candidate_id


def build_generation_input(
    candidate: WorkflowCandidate,
    understanding: WorkflowUnderstandingRecord,
) -> WorkflowGenerationInput:
    """Compact candidate + understanding into evidence a model can plan from.

    Database internals (row ids, timestamps, session lists) stay out; the
    model sees the interpreted sequence and the discovery scores behind it.
    """
    steps: List[dict] = [
        {
            "order": step.order,
            "application": step.application,
            "action": step.action,
            "purpose": step.purpose,
        }
        for step in understanding.steps
    ]

    return WorkflowGenerationInput(
        workflow_id=candidate.id,
        workflow_name=understanding.workflow_name,
        understanding_id=understanding.id,
        intent=understanding.intent,
        description=understanding.description,
        trigger=understanding.trigger,
        applications=list(understanding.applications),
        categories=list(understanding.categories),
        steps=steps,
        inputs=list(understanding.inputs),
        outputs=list(understanding.outputs),
        dependencies=list(understanding.dependencies),
        assumptions=list(understanding.assumptions),
        understanding_confidence=understanding.confidence,
        occurrence_count=candidate.occurrence_count,
    )


def get_candidate(candidate_id: str) -> WorkflowCandidate:
    """Load a workflow candidate or raise a not-found error."""
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        for candidate in candidate_model.select_candidates(connection):
            if candidate.id == candidate_id:
                return candidate
    raise WorkflowCandidateNotFoundError(
        f"Workflow candidate '{candidate_id}' not found"
    )


def get_understanding_for_candidate(
    candidate_id: str,
) -> WorkflowUnderstandingRecord:
    """Return the stored understanding for a candidate, or raise."""
    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        record = understanding_model.select_by_candidate(connection, candidate_id)
    if record is None:
        raise UnderstandingNotFoundError(candidate_id)
    return record


async def generate_workflow_draft(candidate_id: str) -> WorkflowDraftRecord:
    """Run the full generation pipeline for one candidate.

    Requires an existing AI understanding: the generator plans from the
    interpreted workflow, so the user must review understanding first.
    """
    candidate = get_candidate(candidate_id)
    understanding = get_understanding_for_candidate(candidate_id)
    payload = build_generation_input(candidate, understanding)
    provider = get_provider()

    logger.info(
        "Generating draft for %s with provider=%s model=%s",
        candidate.id,
        provider.name,
        provider.model,
    )
    raw = await provider.generate_workflow_draft(payload)
    draft = parse_draft(raw)

    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection,
            draft,
            provider=provider.name,
            model=provider.model,
            workflow_candidate_id=candidate.id,
            understanding_id=understanding.id,
        )
    logger.info("Persisted draft %s for %s", record.id, candidate.id)
    return record


def list_drafts() -> List[WorkflowDraftRecord]:
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        return draft_model.select_all(connection)


def get_draft(draft_id: str) -> Optional[WorkflowDraftRecord]:
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        return draft_model.select_by_id(connection, draft_id)


__all__ = [
    "WorkflowCandidateNotFoundError",
    "UnderstandingNotFoundError",
    "AIProviderError",
    "AIResponseError",
    "build_generation_input",
    "get_candidate",
    "get_understanding_for_candidate",
    "generate_workflow_draft",
    "list_drafts",
    "get_draft",
]
