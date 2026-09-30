"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import database_ready

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness probe used by the frontend connection indicator."""
    return {"status": "ok", "service": "WorkFlowOS"}


@router.get("/health/ready")
def readiness() -> JSONResponse:
    """Readiness probe: confirms the app and SQLite database are usable."""
    database_ok = database_ready()
    payload = {
        "status": "ok" if database_ok else "degraded",
        "service": "WorkFlowOS",
        "environment": settings.environment,
        "database": "ok" if database_ok else "unavailable",
    }
    return JSONResponse(content=payload, status_code=200 if database_ok else 503)
