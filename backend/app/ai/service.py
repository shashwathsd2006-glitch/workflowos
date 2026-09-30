"""AI Understanding service — the single orchestration point for Phase 5.

Flow:  Workflow Candidate → build AI input → provider → raw response →
       JSON extraction → Pydantic validation → persistence → record

Nothing here executes, approves or automates anything: the result is data
that later phases may *propose* to the user, never act on by itself.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from app.ai.errors import AIProviderError, AIResponseError
from app.ai.parsing import parse_understanding
from app.ai.provider import get_provider
from app.database import get_connection
from app.models import understanding as understanding_model
from app.models import workflow_candidate as candidate_model
from app.schemas.ai import (
    WorkflowUnderstanding,
    WorkflowUnderstandingInput,
    WorkflowUnderstandingInputStep,
    WorkflowUnderstandingRecord,
)
from app.schemas.discovery import WorkflowCandidate

logger = logging.getLogger("workflowos.ai")


class WorkflowCandidateNotFoundError(Exception):
    """Raised when a workflow candidate id does not exist."""


def build_input(candidate: WorkflowCandidate) -> WorkflowUnderstandingInput:
    """Compact the candidate into evidence a model can reason over.

    Event ids, timestamps, sessions list and other database internals stay
    out; only the observed sequence and its discovery scores go in.
    """
    categories: List[str] = []
    steps: List[WorkflowUnderstandingInputStep] = []
    for order, step in enumerate(candidate.sequence, start=1):
        if step.category and step.category not in categories:
            categories.append(step.category)
        steps.append(
            WorkflowUnderstandingInputStep(
                order=order,
                application=step.application,
                category=step.category,
                action=step.action,
                description=step.description,
            )
        )

    applications = candidate.applications or list(
        dict.fromkeys(step.application for step in candidate.sequence)
    )

    return WorkflowUnderstandingInput(
        workflow_id=candidate.id,
        workflow_name=candidate.name,
        occurrence_count=candidate.occurrence_count,
        similarity_score=candidate.similarity_score,
        confidence=candidate.confidence,
        session_count=len(candidate.session_ids),
        applications=applications,
        categories=categories,
        steps=steps,
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


async def understand_workflow(candidate_id: str) -> WorkflowUnderstandingRecord:
    """Run the full understanding pipeline for one candidate."""
    candidate = get_candidate(candidate_id)
    payload = build_input(candidate)
    provider = get_provider()

    logger.info(
        "Understanding %s with provider=%s model=%s",
        candidate.id,
        provider.name,
        provider.model,
    )
    raw = await provider.generate_workflow_understanding(payload)
    understanding: WorkflowUnderstanding = parse_understanding(raw, candidate.id)

    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        record = understanding_model.upsert_understanding(
            connection, understanding, provider.name, provider.model
        )
    logger.info("Persisted understanding %s for %s", record.id, candidate.id)
    return record


def list_understandings() -> List[WorkflowUnderstandingRecord]:
    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        return understanding_model.select_all(connection)


def get_understanding(understanding_id: str) -> Optional[WorkflowUnderstandingRecord]:
    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        return understanding_model.select_by_id(connection, understanding_id)


__all__ = [
    "WorkflowCandidateNotFoundError",
    "AIProviderError",
    "AIResponseError",
    "build_input",
    "get_candidate",
    "understand_workflow",
    "list_understandings",
    "get_understanding",
]
