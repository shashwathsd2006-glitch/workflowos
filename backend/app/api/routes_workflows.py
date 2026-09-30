"""Discovery API: list detected workflow candidates and run discovery."""

from __future__ import annotations

from fastapi import APIRouter

from app.discovery import discovery_service
from app.schemas.discovery import DiscoverResponse, DiscoveredWorkflowsResponse

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/discovered", response_model=DiscoveredWorkflowsResponse)
def list_discovered_workflows() -> DiscoveredWorkflowsResponse:
    """Return the currently stored workflow candidates."""
    return discovery_service.get_discovered()


@router.post("/discover", response_model=DiscoverResponse)
def discover_workflows() -> DiscoverResponse:
    """Analyse stored activity events and detect repeated sequences."""
    return discovery_service.run_discovery()
