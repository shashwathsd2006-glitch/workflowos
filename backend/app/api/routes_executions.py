"""Execution API: run approved workflow drafts and inspect execution history.

Execution is gated on human approval. A draft in any other state is refused
with 409 before a single step is created, so a non-approved workflow can
never run a step.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.automation import engine
from app.automation.engine import (
    DraftNotApprovedError,
    DraftNotFoundError,
    ExecutionNotFoundError,
)
from app.schemas.execution import (
    ExecutionDetail,
    ExecutionList,
    ExecutionRequest,
    ExecutionStepList,
)

logger = logging.getLogger("workflowos.execution")

router = APIRouter(prefix="/workflows", tags=["executions"])


@router.post("/drafts/{draft_id}/execute", response_model=ExecutionDetail)
def execute_draft(
    draft_id: str, payload: Optional[ExecutionRequest] = None
) -> ExecutionDetail:
    """Execute an approved workflow draft and return the full step trace.

    Refuses any draft that is not ``approved``: 409 for draft,
    pending_approval and rejected, 404 when the draft does not exist. The
    approval check happens before any input is looked at.

    ``payload.inputs`` supplies run-time context (for example the Gmail
    ``message_id`` selected in the UI) and is passed through to the steps.
    """
    inputs = (payload.inputs if payload else None) or {}
    try:
        return engine.execute_draft(draft_id, trigger_payload=inputs)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DraftNotApprovedError as exc:
        logger.info("Refused execution of %s (status=%s)", draft_id, exc.status)
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected engine failure
        logger.exception("Unexpected execution failure for %s", draft_id)
        raise HTTPException(
            status_code=500, detail="Workflow execution failed unexpectedly"
        ) from exc


@router.get("/executions", response_model=ExecutionList)
def list_executions(
    draft_id: Optional[str] = None, status: Optional[str] = None
) -> ExecutionList:
    """Return execution history, newest first. History is never rewritten."""
    records = engine.list_executions(draft_id=draft_id, status=status)
    return ExecutionList(executions=records, count=len(records))


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
def get_execution(execution_id: str) -> ExecutionDetail:
    """Return one execution with its ordered step results."""
    try:
        return engine.get_execution(execution_id)
    except ExecutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/executions/{execution_id}/steps", response_model=ExecutionStepList)
def get_execution_steps(execution_id: str) -> ExecutionStepList:
    """Return the step results of an execution in generated workflow order."""
    try:
        steps = engine.get_steps(execution_id)
    except ExecutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ExecutionStepList(
        execution_id=execution_id, steps=steps, count=len(steps)
    )
