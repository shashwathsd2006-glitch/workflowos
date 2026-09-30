"""Persistence helpers for the workflow_drafts table.

One row per workflow candidate (UNIQUE) so re-generating refreshes the
existing draft instead of accumulating duplicates. Draft content is stored
in typed columns — not a single opaque JSON blob — so status transitions
and listing stay indexable and inspectable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowDraftRecord,
    WorkflowDraftStatus,
    WorkflowTrigger,
)

TABLE = "workflow_drafts"

#: Human review is the only gate into ``approved``/``rejected``.
INITIAL_STATUS: WorkflowDraftStatus = "pending_approval"

#: Allowed lifecycle transitions. Anything else is a 409 at the API layer.
ALLOWED_TRANSITIONS: dict[WorkflowDraftStatus, tuple[WorkflowDraftStatus, ...]] = {
    "draft": ("pending_approval",),
    "pending_approval": ("approved", "rejected"),
    "approved": (),
    "rejected": (),
}

DDL = """
CREATE TABLE IF NOT EXISTS workflow_drafts (
    id                     TEXT PRIMARY KEY,
    workflow_candidate_id  TEXT NOT NULL UNIQUE,
    understanding_id       TEXT NOT NULL DEFAULT '',
    name                   TEXT NOT NULL,
    description            TEXT NOT NULL,
    trigger_json           TEXT NOT NULL DEFAULT '{}',
    steps_json             TEXT NOT NULL DEFAULT '[]',
    inputs_json            TEXT NOT NULL DEFAULT '[]',
    outputs_json           TEXT NOT NULL DEFAULT '[]',
    applications_json      TEXT NOT NULL DEFAULT '[]',
    conditions_json        TEXT NOT NULL DEFAULT '[]',
    dependencies_json      TEXT NOT NULL DEFAULT '[]',
    assumptions_json       TEXT NOT NULL DEFAULT '[]',
    confidence             REAL NOT NULL DEFAULT 0.0,
    status                 TEXT NOT NULL DEFAULT 'pending_approval',
    rejection_reason       TEXT,
    generated_by           TEXT NOT NULL DEFAULT '',
    model                  TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    approved_at            TEXT
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_draft_status ON workflow_drafts (status)",
    "CREATE INDEX IF NOT EXISTS idx_draft_candidate "
    "ON workflow_drafts (workflow_candidate_id)",
)


class InvalidTransitionError(Exception):
    """Raised when a draft status change is not part of the lifecycle."""

    def __init__(self, current: str, target: str) -> None:
        allowed = ALLOWED_TRANSITIONS.get(current, ())  # type: ignore[arg-type]
        detail = (
            f"allowed: {', '.join(allowed)}"
            if allowed
            else "no further transitions are allowed"
        )
        super().__init__(
            f"Cannot move draft from '{current}' to '{target}' ({detail})"
        )
        self.current = current
        self.target = target


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create the workflow_drafts table and indexes (idempotent)."""
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


def next_identifier(connection: sqlite3.Connection, prefix: str, width: int = 4) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {TABLE}").fetchall():
        raw = row["id"]
        if not raw.startswith(prefix):
            continue
        suffix = raw[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{width}d}"


def row_to_record(row: sqlite3.Row) -> WorkflowDraftRecord:
    trigger = _loads(row["trigger_json"], {})
    steps = _loads(row["steps_json"], [])
    return WorkflowDraftRecord(
        id=row["id"],
        workflow_candidate_id=row["workflow_candidate_id"],
        understanding_id=row["understanding_id"],
        name=row["name"],
        description=row["description"],
        trigger=WorkflowTrigger(
            type=trigger.get("type", "event"),
            application=trigger.get("application") or "Manual",
            action=trigger.get("action") or "start_workflow",
        ),
        steps=[GeneratedWorkflowStep(**step) for step in steps],
        inputs=_loads(row["inputs_json"], []),
        outputs=_loads(row["outputs_json"], []),
        applications=_loads(row["applications_json"], []),
        conditions=_loads(row["conditions_json"], []),
        dependencies=_loads(row["dependencies_json"], []),
        assumptions=_loads(row["assumptions_json"], []),
        confidence=row["confidence"],
        status=row["status"],
        rejection_reason=row["rejection_reason"],
        generated_by=row["generated_by"],
        model=row["model"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
        approved_at=parse_timestamp(row["approved_at"]) if row["approved_at"] else None,
    )


def select_by_candidate(
    connection: sqlite3.Connection, candidate_id: str
) -> Optional[WorkflowDraftRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE workflow_candidate_id = ?", (candidate_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_by_id(
    connection: sqlite3.Connection, draft_id: str
) -> Optional[WorkflowDraftRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE id = ?", (draft_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_all(
    connection: sqlite3.Connection, status: Optional[str] = None
) -> List[WorkflowDraftRecord]:
    if status:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} WHERE status = ? ORDER BY created_at ASC, id ASC",
            (status,),
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return [row_to_record(row) for row in rows]


def _content_values(draft: WorkflowDraft) -> tuple:
    return (
        draft.name,
        draft.description,
        _dumps(draft.trigger.model_dump()),
        _dumps([step.model_dump() for step in draft.steps]),
        _dumps(draft.inputs),
        _dumps(draft.outputs),
        _dumps(draft.applications),
        _dumps(draft.conditions),
        _dumps(draft.dependencies),
        _dumps(draft.assumptions),
        draft.confidence,
    )


def _insert(
    connection: sqlite3.Connection,
    identifier: str,
    draft: WorkflowDraft,
    provider: str,
    model: str,
    candidate_id: str,
    understanding_id: str,
    now: str,
) -> WorkflowDraftRecord:
    connection.execute(
        f"""
        INSERT INTO {TABLE}
            (id, workflow_candidate_id, understanding_id, name, description,
             trigger_json, steps_json, inputs_json, outputs_json,
             applications_json, conditions_json, dependencies_json,
             assumptions_json, confidence, status, generated_by, model,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            candidate_id,
            understanding_id,
            *_content_values(draft),
            INITIAL_STATUS,
            provider,
            model,
            now,
            now,
        ),
    )
    record = select_by_id(connection, identifier)
    assert record is not None
    return record


def _update(
    connection: sqlite3.Connection,
    existing: WorkflowDraftRecord,
    draft: WorkflowDraft,
    provider: str,
    model: str,
    now: str,
) -> WorkflowDraftRecord:
    """Refresh draft content in place, preserving id and lifecycle status.

    Regeneration must not silently undo a human decision, so an approved or
    rejected draft keeps its status; only the content is replaced.
    """
    connection.execute(
        f"""
        UPDATE {TABLE} SET
            understanding_id = ?, name = ?, description = ?, trigger_json = ?,
            steps_json = ?, inputs_json = ?, outputs_json = ?,
            applications_json = ?, conditions_json = ?, dependencies_json = ?,
            assumptions_json = ?, confidence = ?, generated_by = ?, model = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            existing.understanding_id,
            *_content_values(draft),
            provider,
            model,
            now,
            existing.id,
        ),
    )
    record = select_by_id(connection, existing.id)
    assert record is not None
    return record


def upsert_draft(
    connection: sqlite3.Connection,
    draft: WorkflowDraft,
    provider: str,
    model: str,
    workflow_candidate_id: str,
    understanding_id: str = "",
) -> WorkflowDraftRecord:
    """Insert, or refresh in place, the single draft for a candidate."""
    from datetime import datetime, timezone

    now = format_timestamp(datetime.now(timezone.utc))
    existing = select_by_candidate(connection, workflow_candidate_id)
    if existing is None:
        return _insert(
            connection,
            next_identifier(connection, "draft-"),
            draft,
            provider,
            model,
            workflow_candidate_id,
            understanding_id,
            now,
        )
    return _update(connection, existing, draft, provider, model, now)


def set_status(
    connection: sqlite3.Connection,
    draft: WorkflowDraftRecord,
    target: WorkflowDraftStatus,
    rejection_reason: Optional[str] = None,
) -> WorkflowDraftRecord:
    """Move a draft to ``target``, rejecting invalid transitions."""
    if target not in ALLOWED_TRANSITIONS.get(draft.status, ()):  # type: ignore[arg-type]
        raise InvalidTransitionError(draft.status, target)

    from datetime import datetime, timezone

    now = format_timestamp(datetime.now(timezone.utc))
    approved_at = now if target == "approved" else None
    connection.execute(
        f"""
        UPDATE {TABLE} SET
            status = ?, rejection_reason = ?, approved_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (target, rejection_reason, approved_at, now, draft.id),
    )
    record = select_by_id(connection, draft.id)
    assert record is not None
    return record


def delete_draft(connection: sqlite3.Connection, draft_id: str) -> int:
    cursor = connection.execute(f"DELETE FROM {TABLE} WHERE id = ?", (draft_id,))
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def count_drafts(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS total FROM {TABLE}").fetchone()
    return int(row["total"]) if row else 0
