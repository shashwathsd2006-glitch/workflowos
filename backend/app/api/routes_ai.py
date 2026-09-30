"""AI Understanding API: generate and retrieve structured workflow intent.

Phase 5 is understanding only — no approval, no execution, no automation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.ai import service
from app.ai.errors import AIProviderError, AIResponseError
from app.schemas.ai import WorkflowUnderstandingList, WorkflowUnderstandingRecord

logger = logging.getLogger("workflowos.ai")

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/understandings", response_model=WorkflowUnderstandingList)
def list_understandings() -> WorkflowUnderstandingList:
    """Return every stored AI understanding."""
    records = service.list_understandings()
    return WorkflowUnderstandingList(understandings=records, count=len(records))


@router.get(
    "/understandings/{understanding_id}", response_model=WorkflowUnderstandingRecord
)
def get_understanding(understanding_id: str) -> WorkflowUnderstandingRecord:
    """Return one stored understanding by its id."""
    record = service.get_understanding(understanding_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Understanding '{understanding_id}' not found",
        )
    return record


@router.post(
    "/understand/{workflow_candidate_id}", response_model=WorkflowUnderstandingRecord
)
async def understand_workflow(workflow_candidate_id: str) -> WorkflowUnderstandingRecord:
    """Understand a workflow candidate with the configured AI provider.

    Reads the candidate, calls the provider, validates the response and
    persists it. Never executes or approves anything.
    """
    try:
        return await service.understand_workflow(workflow_candidate_id)
    except service.WorkflowCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AIProviderError as exc:
        logger.warning("AI provider failure for %s: %s", workflow_candidate_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"AI understanding unavailable: {exc}",
        )
    except AIResponseError as exc:
        logger.warning("AI response failure for %s: %s", workflow_candidate_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"AI returned an invalid response: {exc}",
        )
