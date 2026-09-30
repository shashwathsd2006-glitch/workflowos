"""Activity service: processing layer between sources/API and SQLite.

Flow:  Activity Source → ActivityEventCreate → ActivityService → SQLite → REST
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.agents.activity_source import (
    DEFAULT_REPETITION_GAP_SECONDS,
    ActivitySource,
    SimulatedActivitySource,
    UnknownWorkflowError,
)
from app.database import get_connection
from app.models import activity as activity_model
from app.schemas.activity import (
    ActivityClearResponse,
    ActivityEvent,
    ActivityEventCreate,
    ActivityEventList,
    ActivityStats,
    SimulateResponse,
)

EVENT_PREFIX = "event-"
SESSION_PREFIX = "session-"
SESSION_WIDTH = 3

default_source: ActivitySource = SimulatedActivitySource()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_event(
    payload: ActivityEventCreate, connection=None
) -> ActivityEvent:
    """Persist a single event, filling in id/timestamp/session when absent.

    ``connection`` lets a caller that already holds an open write transaction
    reuse it. Opening a second connection while the first is mid-write makes
    SQLite raise "database is locked", which is what silently dropped observed
    activity events during a provider poll.
    """
    if connection is not None:
        return _record_with(payload, connection)
    with get_connection() as owned:
        return _record_with(payload, owned)


def _record_with(payload: ActivityEventCreate, connection) -> ActivityEvent:
    activity_model.ensure_table(connection)
    event_id = payload.id or activity_model.next_identifier(connection, EVENT_PREFIX)
    session_id = payload.session_id or _resolve_session(connection)
    timestamp = payload.timestamp or _now()

    event = ActivityEvent(
        id=event_id,
        timestamp=timestamp,
        application=payload.application,
        category=payload.category,
        action=payload.action,
        description=payload.description,
        metadata=payload.metadata,
        session_id=session_id,
    )
    activity_model.insert_event(connection, event)
    return event


def _resolve_session(connection) -> str:
    latest = activity_model.latest_session(connection)
    if latest:
        return latest
    return activity_model.next_identifier(
        connection, SESSION_PREFIX, SESSION_WIDTH, column="session_id"
    )


def list_events(
    *,
    limit: int = 50,
    application: Optional[str] = None,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> ActivityEventList:
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        rows = activity_model.select_events(
            connection,
            limit=limit,
            application=application,
            category=category,
            session_id=session_id,
        )
        total = activity_model.count_events(
            connection,
            application=application,
            category=category,
            session_id=session_id,
        )
    # rows arrive newest-first; expose chronological order for timelines
    events = [activity_model.row_to_event(row) for row in reversed(rows)]
    return ActivityEventList(events=events, count=len(events), total=total)


def clear_events() -> ActivityClearResponse:
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        cleared = activity_model.delete_all(connection)
    return ActivityClearResponse(cleared=cleared)


def get_stats() -> ActivityStats:
    today = _now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        total = activity_model.count_events(connection)
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM activity_events "
            "WHERE substr(timestamp, 1, 10) = ?",
            (today,),
        ).fetchone()
        total_today = int(row["total"]) if row else 0
        applications = [
            item["application"]
            for item in connection.execute(
                "SELECT DISTINCT application FROM activity_events ORDER BY application"
            ).fetchall()
        ]
        latest = activity_model.latest_session(connection)
        session_count = (
            activity_model.count_events(connection, session_id=latest)
            if latest
            else 0
        )
        last_row = connection.execute(
            "SELECT * FROM activity_events ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone()
        last_activity = (
            activity_model.row_to_event(last_row).timestamp if last_row else None
        )

    return ActivityStats(
        total_events=total,
        total_today=total_today,
        applications=applications,
        application_count=len(applications),
        last_activity=last_activity,
        current_session=latest,
        session_event_count=session_count,
    )


def simulate(
    workflow: str,
    repetitions: int,
    source: Optional[ActivitySource] = None,
    gap_seconds: int = DEFAULT_REPETITION_GAP_SECONDS,
) -> SimulateResponse:
    """Generate one or more predefined sequences and store them.

    Every repetition gets its own session (session-001, session-002, ...) and
    is placed in the recent past, oldest first, ``gap_seconds`` apart.
    """
    active_source = source or default_source
    if workflow not in active_source.workflows:
        raise UnknownWorkflowError(workflow)

    now = _now()
    created: List[ActivityEvent] = []
    session_ids: List[str] = []

    with get_connection() as connection:
        activity_model.ensure_table(connection)
        for index in range(repetitions):
            session_id = activity_model.next_identifier(
                connection, SESSION_PREFIX, SESSION_WIDTH, column="session_id"
            )
            session_ids.append(session_id)
            end_at = now - timedelta(seconds=(repetitions - 1 - index) * gap_seconds)

            for payload in active_source.generate(workflow, session_id, end_at):
                event = ActivityEvent(
                    id=activity_model.next_identifier(connection, EVENT_PREFIX),
                    timestamp=payload.timestamp or now,
                    application=payload.application,
                    category=payload.category,
                    action=payload.action,
                    description=payload.description,
                    metadata=payload.metadata,
                    session_id=payload.session_id or session_id,
                )
                activity_model.insert_event(connection, event)
                created.append(event)

    return SimulateResponse(
        workflow=workflow,
        repetitions=repetitions,
        generated=len(created),
        session_ids=session_ids,
        events=created,
    )
