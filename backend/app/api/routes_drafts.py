"""Workflow draft API: generate, review, approve and reject workflow drafts.

Phase 5 generates proposals and records human decisions. It never executes a
draft: approval only changes the persisted status so a later phase can act on
an explicitly authorised plan.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.generator import service
from app.generator.service import (
    AIProviderError,
    AIResponseError,
    UnderstandingNotFoundError,
    WorkflowCandidateNotFoundError,
)
from app.models import generator as draft_model
from app.models.generator import InvalidTransitionError
from app.schemas.generator import (
    RejectWorkflowRequest,
    WorkflowDraftList,
    WorkflowDraftRecord,
    WorkflowDraftStatus,
)

logger = logging.getLogger("workflowos.generator")

router = APIRouter(prefix="/workflows/drafts", tags=["workflow-drafts"])


@router.get("", response_model=WorkflowDraftList)
def list_drafts(status: Optional[str] = None) -> WorkflowDraftList:
    """Return stored workflow drafts, optionally filtered by status."""
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        records = draft_model.select_all(connection, status)
    return WorkflowDraftList(drafts=records, count=len(records))


@router.get("/{draft_id}", response_model=WorkflowDraftRecord)
def get_draft(draft_id: str) -> WorkflowDraftRecord:
    """Return one workflow draft by its id."""
    record = _load(draft_id)
    return record


@router.post("/{workflow_candidate_id}/generate", response_model=WorkflowDraftRecord)
async def generate_draft(workflow_candidate_id: str) -> WorkflowDraftRecord:
    """Generate a workflow draft from an understood workflow candidate.

    Requires a stored AI understanding: the generator plans from the
    interpreted workflow so the user reviews meaning before structure.
    """
    try:
        return await service.generate_workflow_draft(workflow_candidate_id)
    except WorkflowCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except UnderstandingNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AIProviderError as exc:
        logger.warning("AI provider failure for %s: %s", workflow_candidate_id, exc)
        raise HTTPException(
            status_code=502, detail=f"Workflow generation unavailable: {exc}"
        )
    except AIResponseError as exc:
        logger.warning("AI response failure for %s: %s", workflow_candidate_id, exc)
        raise HTTPException(
            status_code=502, detail=f"AI returned an invalid draft: {exc}"
        )


@router.post("/{draft_id}/approve", response_model=WorkflowDraftRecord)
def approve_draft(draft_id: str) -> WorkflowDraftRecord:
    """Approve a pending draft. Records the decision; executes nothing."""
    return _transition(draft_id, "approved")


@router.post("/{draft_id}/reject", response_model=WorkflowDraftRecord)
def reject_draft(
    draft_id: str, payload: Optional[RejectWorkflowRequest] = None
) -> WorkflowDraftRecord:
    """Reject a pending draft, optionally recording why."""
    return _transition(
        draft_id, "rejected", payload.reason if payload else None
    )


@router.delete("/{draft_id}")
def delete_draft(draft_id: str) -> dict:
    """Delete a draft. Approved and rejected drafts are kept for the record."""
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.select_by_id(connection, draft_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Workflow draft '{draft_id}' not found"
            )
        draft_model.delete_draft(connection, draft_id)
    return {"deleted": 1, "id": draft_id}


def _load(draft_id: str) -> WorkflowDraftRecord:
    """Fetch a draft or raise 404."""
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.select_by_id(connection, draft_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow draft '{draft_id}' not found"
        )
    return record


def _transition(
    draft_id: str, target: WorkflowDraftStatus, rejection_reason: Optional[str] = None
) -> WorkflowDraftRecord:
    """Apply a lifecycle transition, mapping failures onto HTTP codes."""
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.select_by_id(connection, draft_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Workflow draft '{draft_id}' not found"
            )
        try:
            return draft_model.set_status(
                connection, record, target, rejection_reason
            )
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
