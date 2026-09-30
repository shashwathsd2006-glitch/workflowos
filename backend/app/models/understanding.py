"""Persistence helpers for the workflow_understandings table."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.ai import WorkflowUnderstanding, WorkflowUnderstandingRecord, WorkflowUnderstandingStep

TABLE = "workflow_understandings"

DDL = """
CREATE TABLE IF NOT EXISTS workflow_understandings (
    id                     TEXT PRIMARY KEY,
    workflow_candidate_id  TEXT NOT NULL UNIQUE,
    workflow_name          TEXT NOT NULL,
    intent                 TEXT NOT NULL,
    description            TEXT NOT NULL,
    trigger                TEXT NOT NULL,
    steps_json             TEXT NOT NULL DEFAULT '[]',
    applications_json      TEXT NOT NULL DEFAULT '[]',
    categories_json        TEXT NOT NULL DEFAULT '[]',
    inputs_json            TEXT NOT NULL DEFAULT '[]',
    outputs_json           TEXT NOT NULL DEFAULT '[]',
    dependencies_json      TEXT NOT NULL DEFAULT '[]',
    assumptions_json       TEXT NOT NULL DEFAULT '[]',
    confidence             REAL NOT NULL DEFAULT 0.0,
    suggested_automation   TEXT NOT NULL DEFAULT '',
    provider               TEXT NOT NULL,
    model                  TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_understanding_candidate "
    "ON workflow_understandings (workflow_candidate_id)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create the understanding table and indexes (idempotent)."""
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


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
        suffix = str(row["id"])[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{width}d}"


def row_to_record(row: sqlite3.Row) -> WorkflowUnderstandingRecord:
    steps = _loads(row["steps_json"], [])
    return WorkflowUnderstandingRecord(
        id=row["id"],
        workflow_candidate_id=row["workflow_candidate_id"],
        workflow_name=row["workflow_name"],
        intent=row["intent"],
        description=row["description"],
        trigger=row["trigger"],
        steps=[WorkflowUnderstandingStep(**step) for step in steps],
        applications=_loads(row["applications_json"], []),
        categories=_loads(row["categories_json"], []),
        inputs=_loads(row["inputs_json"], []),
        outputs=_loads(row["outputs_json"], []),
        dependencies=_loads(row["dependencies_json"], []),
        assumptions=_loads(row["assumptions_json"], []),
        confidence=row["confidence"],
        suggested_automation=row["suggested_automation"],
        provider=row["provider"],
        model=row["model"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )


def select_by_candidate(
    connection: sqlite3.Connection, candidate_id: str
) -> Optional[WorkflowUnderstandingRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE workflow_candidate_id = ?", (candidate_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_by_id(
    connection: sqlite3.Connection, understanding_id: str
) -> Optional[WorkflowUnderstandingRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE id = ?", (understanding_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_all(connection: sqlite3.Connection) -> List[WorkflowUnderstandingRecord]:
    rows = connection.execute(
        f"SELECT * FROM {TABLE} ORDER BY created_at ASC, id ASC"
    ).fetchall()
    return [row_to_record(row) for row in rows]


def _insert(
    connection: sqlite3.Connection,
    identifier: str,
    understanding: WorkflowUnderstanding,
    provider: str,
    model: str,
    created_at: str,
    updated_at: str,
) -> WorkflowUnderstandingRecord:
    connection.execute(
        """
        INSERT INTO workflow_understandings
            (id, workflow_candidate_id, workflow_name, intent, description, trigger,
             steps_json, applications_json, categories_json, inputs_json, outputs_json,
             dependencies_json, assumptions_json, confidence, suggested_automation,
             provider, model, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            understanding.workflow_id,
            understanding.workflow_name,
            understanding.intent,
            understanding.description,
            understanding.trigger,
            _dumps([step.model_dump() for step in understanding.steps]),
            _dumps(understanding.applications),
            _dumps(understanding.categories),
            _dumps(understanding.inputs),
            _dumps(understanding.outputs),
            _dumps(understanding.dependencies),
            _dumps(understanding.assumptions),
            understanding.confidence,
            understanding.suggested_automation,
            provider,
            model,
            created_at,
            updated_at,
        ),
    )
    record = select_by_id(connection, identifier)
    assert record is not None
    return record


def _update(
    connection: sqlite3.Connection,
    existing: WorkflowUnderstandingRecord,
    understanding: WorkflowUnderstanding,
    provider: str,
    model: str,
    updated_at: str,
) -> WorkflowUnderstandingRecord:
    connection.execute(
        """
        UPDATE workflow_understandings SET
            workflow_name = ?, intent = ?, description = ?, trigger = ?,
            steps_json = ?, applications_json = ?, categories_json = ?,
            inputs_json = ?, outputs_json = ?, dependencies_json = ?,
            assumptions_json = ?, confidence = ?, suggested_automation = ?,
            provider = ?, model = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            understanding.workflow_name,
            understanding.intent,
            understanding.description,
            understanding.trigger,
            _dumps([step.model_dump() for step in understanding.steps]),
            _dumps(understanding.applications),
            _dumps(understanding.categories),
            _dumps(understanding.inputs),
            _dumps(understanding.outputs),
            _dumps(understanding.dependencies),
            _dumps(understanding.assumptions),
            understanding.confidence,
            understanding.suggested_automation,
            provider,
            model,
            updated_at,
            existing.id,
        ),
    )
    record = select_by_id(connection, existing.id)
    assert record is not None
    return record


def upsert_understanding(
    connection: sqlite3.Connection,
    understanding: WorkflowUnderstanding,
    provider: str,
    model: str,
) -> WorkflowUnderstandingRecord:
    """Insert, or refresh in place, the understanding for a candidate.

    One row per workflow candidate keeps the table tidy when the user runs
    "Understand with AI" again.
    """
    from datetime import datetime, timezone

    now = format_timestamp(datetime.now(timezone.utc))
    existing = select_by_candidate(connection, understanding.workflow_id)
    if existing is None:
        identifier = next_identifier(connection, "understanding-")
        return _insert(
            connection, identifier, understanding, provider, model, now, now
        )
    return _update(connection, existing, understanding, provider, model, now)
