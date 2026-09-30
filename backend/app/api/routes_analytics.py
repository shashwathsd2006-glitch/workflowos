"""Analytics API: aggregate real execution/workflow/automation records."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from app.services.analytics_service import get_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
@router.get("/", include_in_schema=False)
def analytics(days: Optional[int] = None) -> dict:
    """Return the analytics payload for the trailing ``days`` window."""
    return get_analytics(14 if days is None else days)
