"""Persistence helpers for the workflow_candidates table."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from app.schemas.discovery import WorkflowCandidate, WorkflowStep

TABLE = "workflow_candidates"

DDL = """
CREATE TABLE IF NOT EXISTS workflow_candidates (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    sequence          TEXT NOT NULL DEFAULT '[]',
    occurrence_count  INTEGER NOT NULL DEFAULT 0,
    session_ids       TEXT NOT NULL DEFAULT '[]',
    similarity_score  REAL NOT NULL DEFAULT 0.0,
    confidence        REAL NOT NULL DEFAULT 0.0,
    confidence_label  TEXT NOT NULL DEFAULT 'low',
    applications      TEXT NOT NULL DEFAULT '[]',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'detected'
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_candidates (status)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_occurrences ON workflow_candidates (occurrence_count)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create the candidate table and indexes (idempotent, safe to restart)."""
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def next_identifier(connection: sqlite3.Connection, prefix: str, width: int = 3) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {TABLE}").fetchall():
        raw = row["id"]
        if not raw.startswith(prefix):
            continue
        suffix = raw[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{width}d}"


def insert_candidate(connection: sqlite3.Connection, candidate: WorkflowCandidate) -> None:
    connection.execute(
        """
        INSERT INTO workflow_candidates
            (id, name, sequence, occurrence_count, session_ids, similarity_score,
             confidence, confidence_label, applications, first_seen, last_seen, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.id,
            candidate.name,
            _dumps([step.model_dump() for step in candidate.sequence]),
            candidate.occurrence_count,
            _dumps(candidate.session_ids),
            candidate.similarity_score,
            candidate.confidence,
            candidate.confidence_label,
            _dumps(candidate.applications),
            candidate.first_seen.isoformat(),
            candidate.last_seen.isoformat(),
            candidate.status,
        ),
    )


def row_to_candidate(row: sqlite3.Row) -> WorkflowCandidate:
    from app.models.activity import parse_timestamp

    sequence = _loads(row["sequence"], [])
    return WorkflowCandidate(
        id=row["id"],
        name=row["name"],
        sequence=[WorkflowStep(**step) for step in sequence],
        occurrence_count=row["occurrence_count"],
        session_ids=_loads(row["session_ids"], []),
        similarity_score=row["similarity_score"],
        confidence=row["confidence"],
        confidence_label=row["confidence_label"],
        applications=_loads(row["applications"], []),
        first_seen=parse_timestamp(row["first_seen"]),
        last_seen=parse_timestamp(row["last_seen"]),
        status=row["status"],
    )


def select_candidates(
    connection: sqlite3.Connection,
    status: Optional[str] = None,
) -> List[WorkflowCandidate]:
    if status:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} WHERE status = ? "
            "ORDER BY occurrence_count DESC, first_seen ASC, id ASC",
            (status,),
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} "
            "ORDER BY occurrence_count DESC, first_seen ASC, id ASC"
        ).fetchall()
    return [row_to_candidate(row) for row in rows]


def delete_detected(connection: sqlite3.Connection) -> int:
    """Remove previously detected candidates so a re-run stays deterministic.

    Only rows with status ``detected`` are cleared; reviewed/approved/rejected
    candidates (Phase 5+) are preserved.
    """
    cursor = connection.execute(f"DELETE FROM {TABLE} WHERE status = 'detected'")
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def count_candidates(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS total FROM {TABLE}").fetchone()
    return int(row["total"]) if row else 0
