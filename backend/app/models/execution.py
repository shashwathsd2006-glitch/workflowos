"""Persistence helpers for workflow_executions and workflow_execution_steps.

Executions are append-only history: every run of an approved draft creates a
new row and never mutates an earlier one, so an audit trail survives reruns.

The step table carries a UNIQUE (execution_id, step_number) constraint, which
makes a double-insert of the same step impossible at the storage layer — not
just in the engine.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.execution import (
    ExecutionRecord,
    ExecutionStepResult,
    ExecutionStatus,
    StepStatus,
)

EXECUTIONS_TABLE = "workflow_executions"
STEPS_TABLE = "workflow_execution_steps"

EXECUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_executions (
    id               TEXT PRIMARY KEY,
    draft_id         TEXT NOT NULL,
    workflow_name    TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    current_step     INTEGER NOT NULL DEFAULT 0,
    total_steps      INTEGER NOT NULL DEFAULT 0,
    completed_steps  INTEGER NOT NULL DEFAULT 0,
    failed_step      TEXT,
    error            TEXT,
    error_type       TEXT,
    retryable        INTEGER,
    result_summary   TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    duration_ms      INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES workflow_drafts (id)
)
"""

STEPS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_execution_steps (
    id            TEXT PRIMARY KEY,
    execution_id  TEXT NOT NULL,
    step_number   INTEGER NOT NULL,
    application   TEXT NOT NULL,
    action        TEXT NOT NULL,
    purpose       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    action_type   TEXT,
    input         TEXT,
    output        TEXT,
    error         TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    duration_ms   INTEGER,
    FOREIGN KEY (execution_id) REFERENCES workflow_executions (id),
    UNIQUE (execution_id, step_number)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_execution_draft "
    "ON workflow_executions (draft_id)",
    "CREATE INDEX IF NOT EXISTS idx_execution_status "
    "ON workflow_executions (status)",
    "CREATE INDEX IF NOT EXISTS idx_execution_step_execution "
    "ON workflow_execution_steps (execution_id)",
)

#: Columns added after the table first shipped. ``CREATE TABLE IF NOT EXISTS``
#: cannot add a column to a table that already exists, so a database created
#: by an earlier phase would be missing these and every read would raise
#: ``IndexError: No item with that key``. This migration is what keeps an
#: existing database working across upgrades.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    (EXECUTIONS_TABLE, "error_type", "TEXT"),
    (EXECUTIONS_TABLE, "retryable", "INTEGER"),
)


def ensure_tables(connection: sqlite3.Connection) -> None:
    """Create both execution tables and indexes (idempotent)."""
    connection.execute(EXECUTIONS_DDL)
    connection.execute(STEPS_DDL)
    for statement in INDEXES:
        connection.execute(statement)
    apply_migrations(connection)


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Add any columns introduced after a table was first created."""
    for table, column, ddl_type in MIGRATIONS:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            # Table does not exist yet; the CREATE above handled it.
            continue
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
            )


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def next_execution_identifier(connection: sqlite3.Connection) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {EXECUTIONS_TABLE}"):
        raw = row["id"]
        if not raw or not raw.startswith("execution-"):
            continue
        suffix = raw[len("execution-") :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"execution-{highest + 1:04d}"


def next_step_identifier(connection: sqlite3.Connection) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {STEPS_TABLE}"):
        raw = row["id"]
        if not raw or not raw.startswith("execstep-"):
            continue
        suffix = raw[len("execstep-") :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"execstep-{highest + 1:05d}"


# ------------------------------------------------------------------ reads


def row_to_execution(row: sqlite3.Row) -> ExecutionRecord:
    return ExecutionRecord(
        id=row["id"],
        draft_id=row["draft_id"],
        workflow_name=row["workflow_name"],
        status=row["status"],
        current_step=row["current_step"],
        total_steps=row["total_steps"],
        completed_steps=row["completed_steps"],
        failed_step=row["failed_step"],
        error=row["error"],
        error_type=row["error_type"],
        retryable=None if row["error_type"] is None and row["retryable"] is None else bool(row["retryable"]),
        result_summary=row["result_summary"],
        started_at=parse_timestamp(row["started_at"]) if row["started_at"] else None,
        completed_at=(
            parse_timestamp(row["completed_at"]) if row["completed_at"] else None
        ),
        duration_ms=row["duration_ms"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )


def row_to_step(row: sqlite3.Row) -> ExecutionStepResult:
    return ExecutionStepResult(
        id=row["id"],
        execution_id=row["execution_id"],
        step_number=row["step_number"],
        application=row["application"],
        action=row["action"],
        purpose=row["purpose"],
        status=row["status"],
        action_type=row["action_type"],
        input=row["input"],
        output=row["output"],
        error=row["error"],
        started_at=parse_timestamp(row["started_at"]) if row["started_at"] else None,
        completed_at=(
            parse_timestamp(row["completed_at"]) if row["completed_at"] else None
        ),
        duration_ms=row["duration_ms"],
    )


def select_execution(
    connection: sqlite3.Connection, execution_id: str
) -> Optional[ExecutionRecord]:
    row = connection.execute(
        f"SELECT * FROM {EXECUTIONS_TABLE} WHERE id = ?", (execution_id,)
    ).fetchone()
    return row_to_execution(row) if row else None


def select_executions(
    connection: sqlite3.Connection,
    draft_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[ExecutionRecord]:
    clauses: List[str] = []
    params: List[Any] = []
    if draft_id:
        clauses.append("draft_id = ?")
        params.append(draft_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM {EXECUTIONS_TABLE} {where} "
        "ORDER BY created_at DESC, id DESC",
        params,
    ).fetchall()
    return [row_to_execution(row) for row in rows]


def select_steps(
    connection: sqlite3.Connection, execution_id: str
) -> List[ExecutionStepResult]:
    """Return steps in generated workflow order — never reordered."""
    rows = connection.execute(
        f"SELECT * FROM {STEPS_TABLE} WHERE execution_id = ? ORDER BY step_number ASC",
        (execution_id,),
    ).fetchall()
    return [row_to_step(row) for row in rows]


# ----------------------------------------------------------------- writes


def insert_execution(
    connection: sqlite3.Connection,
    execution_id: str,
    draft_id: str,
    workflow_name: str,
    total_steps: int,
) -> ExecutionRecord:
    now = format_timestamp(datetime.now(timezone.utc))
    connection.execute(
        f"""
        INSERT INTO {EXECUTIONS_TABLE}
            (id, draft_id, workflow_name, status, current_step, total_steps,
             completed_steps, created_at, updated_at)
        VALUES (?, ?, ?, 'queued', 0, ?, 0, ?, ?)
        """,
        (execution_id, draft_id, workflow_name, total_steps, now, now),
    )
    record = select_execution(connection, execution_id)
    assert record is not None
    return record


def update_execution(
    connection: sqlite3.Connection,
    execution_id: str,
    **fields: Any,
) -> ExecutionRecord:
    """Patch mutable execution fields and refresh ``updated_at``."""
    allowed = {
        "status",
        "current_step",
        "total_steps",
        "completed_steps",
        "failed_step",
        "error",
        "error_type",
        "retryable",
        "result_summary",
        "started_at",
        "completed_at",
        "duration_ms",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return select_execution(connection, execution_id)  # type: ignore[return-value]

    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values())
    params.append(format_timestamp(datetime.now(timezone.utc)))
    params.append(execution_id)
    connection.execute(
        f"UPDATE {EXECUTIONS_TABLE} SET {assignments}, updated_at = ? WHERE id = ?",
        params,
    )
    record = select_execution(connection, execution_id)
    assert record is not None
    return record


def insert_step(
    connection: sqlite3.Connection,
    step_id: str,
    execution_id: str,
    step_number: int,
    application: str,
    action: str,
    purpose: str,
) -> ExecutionStepResult:
    connection.execute(
        f"""
        INSERT INTO {STEPS_TABLE}
            (id, execution_id, step_number, application, action, purpose, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (step_id, execution_id, step_number, application, action, purpose),
    )
    step = select_step(connection, execution_id, step_number)
    assert step is not None
    return step


def select_step(
    connection: sqlite3.Connection, execution_id: str, step_number: int
) -> Optional[ExecutionStepResult]:
    row = connection.execute(
        f"SELECT * FROM {STEPS_TABLE} WHERE execution_id = ? AND step_number = ?",
        (execution_id, step_number),
    ).fetchone()
    return row_to_step(row) if row else None


def update_step(
    connection: sqlite3.Connection,
    step_id: str,
    status: StepStatus,
    *,
    action_type: Optional[str] = None,
    input: Optional[str] = None,
    output: Optional[str] = None,
    error: Optional[str] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> ExecutionStepResult:
    connection.execute(
        f"""
        UPDATE {STEPS_TABLE} SET
            status = ?, action_type = ?, input = ?, output = ?, error = ?,
            started_at = ?, completed_at = ?, duration_ms = ?
        WHERE id = ?
        """,
        (
            status,
            action_type,
            input,
            output,
            error,
            started_at,
            completed_at,
            duration_ms,
            step_id,
        ),
    )
    row = connection.execute(
        f"SELECT * FROM {STEPS_TABLE} WHERE id = ?", (step_id,)
    ).fetchone()
    return row_to_step(row)  # type: ignore[return-value]


def count_executions(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) AS total FROM {EXECUTIONS_TABLE}"
    ).fetchone()
    return int(row["total"]) if row else 0


__all__ = [
    "EXECUTIONS_TABLE",
    "STEPS_TABLE",
    "ExecutionStatus",
    "count_executions",
    "ensure_tables",
    "insert_execution",
    "insert_step",
    "next_execution_identifier",
    "next_step_identifier",
    "row_to_execution",
    "row_to_step",
    "select_execution",
    "select_executions",
    "select_step",
    "select_steps",
    "update_execution",
    "update_step",
]
