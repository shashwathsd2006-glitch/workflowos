"""Persistence helpers for the activity_events table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.schemas.activity import ActivityEvent

TABLE = "activity_events"

DDL = """
CREATE TABLE IF NOT EXISTS activity_events (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    application TEXT NOT NULL,
    category    TEXT NOT NULL,
    action      TEXT NOT NULL,
    description TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    session_id  TEXT NOT NULL
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_events (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_activity_session ON activity_events (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_activity_application ON activity_events (application)",
    "CREATE INDEX IF NOT EXISTS idx_activity_category ON activity_events (category)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create the activity table and its indexes (idempotent)."""
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def format_timestamp(value: datetime) -> str:
    """Store all timestamps as UTC ISO-8601 with an explicit offset."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def row_to_event(row: sqlite3.Row) -> ActivityEvent:
    return ActivityEvent(
        id=row["id"],
        timestamp=parse_timestamp(row["timestamp"]),
        application=row["application"],
        category=row["category"],
        action=row["action"],
        description=row["description"],
        metadata=_load_metadata(row["metadata"]),
        session_id=row["session_id"],
    )


def _load_metadata(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def dumps_metadata(metadata: Dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"), default=str)


def next_identifier(
    connection: sqlite3.Connection,
    prefix: str,
    width: int = 4,
    column: str = "id",
) -> str:
    """Return the next free ``<prefix><n>`` identifier (event-0001, session-001).

    ``column`` selects which values are scanned: event ids live in ``id``,
    session ids live in ``session_id``.
    """
    highest = 0
    for row in connection.execute(f"SELECT {column} AS value FROM {TABLE}"):
        raw = row["value"]
        if not raw or not raw.startswith(prefix):
            continue
        suffix = raw[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{width}d}"


def session_exists(connection: sqlite3.Connection, session_id: str) -> bool:
    row = connection.execute(
        f"SELECT 1 FROM {TABLE} WHERE session_id = ? LIMIT 1", (session_id,)
    ).fetchone()
    return row is not None


def latest_session(connection: sqlite3.Connection) -> Optional[str]:
    row = connection.execute(
        f"SELECT session_id FROM {TABLE} ORDER BY timestamp DESC, id DESC LIMIT 1"
    ).fetchone()
    return row["session_id"] if row is not None else None


def insert_event(
    connection: sqlite3.Connection,
    event: ActivityEvent,
) -> None:
    connection.execute(
        """
        INSERT INTO activity_events
            (id, timestamp, application, category, action, description, metadata, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            format_timestamp(event.timestamp),
            event.application,
            event.category,
            event.action,
            event.description,
            dumps_metadata(event.metadata),
            event.session_id,
        ),
    )


def select_events(
    connection: sqlite3.Connection,
    *,
    limit: int,
    application: Optional[str] = None,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[sqlite3.Row]:
    where, params = build_filter(application, category, session_id)
    sql = f"SELECT * FROM {TABLE} {where} ORDER BY timestamp DESC, id DESC LIMIT ?"
    return connection.execute(sql, (*params, limit)).fetchall()


def select_all_events(connection: sqlite3.Connection) -> List[sqlite3.Row]:
    """Every stored event in chronological order (used by discovery)."""
    return connection.execute(
        f"SELECT * FROM {TABLE} ORDER BY timestamp ASC, id ASC"
    ).fetchall()


def count_events(
    connection: sqlite3.Connection,
    *,
    application: Optional[str] = None,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    where, params = build_filter(application, category, session_id)
    row = connection.execute(
        f"SELECT COUNT(*) AS total FROM {TABLE} {where}", params
    ).fetchone()
    return int(row["total"]) if row is not None else 0


def delete_all(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(f"DELETE FROM {TABLE}")
    return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0


def build_filter(
    application: Optional[str],
    category: Optional[str],
    session_id: Optional[str],
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if application:
        clauses.append("application = ? COLLATE NOCASE")
        params.append(application)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params
