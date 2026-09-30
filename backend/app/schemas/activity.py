"""Pydantic contracts for the Activity module (module 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ActivityCategory = Literal[
    "application",
    "browser",
    "file",
    "communication",
    "crm",
    "ui",
    "system",
]

ACTIVITY_CATEGORIES: tuple[str, ...] = (
    "application",
    "browser",
    "file",
    "communication",
    "crm",
    "ui",
    "system",
)


class ActivityEvent(BaseModel):
    """A single structured activity event as stored and returned by the API."""

    id: str
    timestamp: datetime
    application: str
    category: ActivityCategory
    action: str
    description: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: str


class ActivityEventCreate(BaseModel):
    """Ingestion payload; id/timestamp/session_id are filled in when absent."""

    id: Optional[str] = None
    timestamp: Optional[datetime] = None
    application: str = Field(min_length=1, max_length=120)
    category: ActivityCategory
    action: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = Field(default=None, max_length=120)


class ActivityEventList(BaseModel):
    events: List[ActivityEvent]
    count: int
    total: int


class ActivityStats(BaseModel):
    total_events: int
    total_today: int
    applications: List[str]
    application_count: int
    last_activity: Optional[datetime] = None
    current_session: Optional[str] = None
    session_event_count: int = 0


class ActivityClearResponse(BaseModel):
    cleared: int


class SimulateRequest(BaseModel):
    workflow: str = "customer_request"
    repetitions: int = Field(default=1, ge=1, le=20)


class SimulateResponse(BaseModel):
    workflow: str
    repetitions: int
    generated: int
    session_ids: List[str]
    events: List[ActivityEvent]
