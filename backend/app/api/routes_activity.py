"""Activity API: ingestion, querying, clearing and simulation."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.agents.activity_source import UnknownWorkflowError
from app.schemas.activity import (
    ACTIVITY_CATEGORIES,
    ActivityCategory,
    ActivityClearResponse,
    ActivityEvent,
    ActivityEventCreate,
    ActivityEventList,
    ActivityStats,
    SimulateRequest,
    SimulateResponse,
)
from app.services import activity_service

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=ActivityEventList)
def list_activity(
    limit: int = Query(default=50, ge=1, le=500),
    application: Optional[str] = Query(default=None, max_length=120),
    category: Optional[ActivityCategory] = Query(default=None),
    session_id: Optional[str] = Query(default=None, max_length=120),
) -> ActivityEventList:
    """Return recent activity events, newest collected, chronological order."""
    return activity_service.list_events(
        limit=limit,
        application=application,
        category=category,
        session_id=session_id,
    )


@router.post("", response_model=ActivityEvent, status_code=201)
def create_activity(payload: ActivityEventCreate) -> ActivityEvent:
    """Submit a single structured activity event."""
    return activity_service.record_event(payload)


@router.delete("", response_model=ActivityClearResponse)
def clear_activity() -> ActivityClearResponse:
    """Remove all stored activity events."""
    return activity_service.clear_events()


@router.post("/simulate", response_model=SimulateResponse)
def simulate_activity(payload: SimulateRequest) -> SimulateResponse:
    """Generate a predefined activity sequence and persist it.

    Test/discovery tooling only. It writes *synthetic* events into the audit
    trail, so it is refused unless demo mode is explicitly enabled. The
    product UI never calls it: Activity shows only events produced by real
    executions.
    """
    from app.config import settings

    if not settings.demo_mode:
        raise HTTPException(
            status_code=409,
            detail=(
                "Simulated activity is disabled. It writes synthetic events "
                "into the audit trail and is available only with "
                "DEMO_MODE=true (used by the automated tests)."
            ),
        )
    try:
        return activity_service.simulate(payload.workflow, payload.repetitions)
    except UnknownWorkflowError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown workflow '{payload.workflow}'. "
                f"Available: {', '.join(activity_service.default_source.workflows)}"
            ),
        )


@router.get("/stats", response_model=ActivityStats)
def activity_stats() -> ActivityStats:
    """Aggregated counters used by the dashboard."""
    return activity_service.get_stats()


__all__ = ["router", "ACTIVITY_CATEGORIES"]
