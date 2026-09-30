"""Persistence helpers for the automations table.

One row per automation, keyed by its own id, pointing at an approved
``workflow_drafts`` row. Execution history is never copied here — it is read
live from ``workflow_executions`` so an automation view can never drift from
the real execution record.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.automation import (
    AutomationCore,
    AutomationRecord,
    AutomationStatus,
    AutomationTriggerType,
    ExecutionSummaryView,
)

TABLE = "automations"

DDL = """
CREATE TABLE IF NOT EXISTS automations (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    draft_id        TEXT NOT NULL,
    trigger_type    TEXT NOT NULL DEFAULT 'manual',
    trigger_config  TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES workflow_drafts (id)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_automation_draft ON automations (draft_id)",
    "CREATE INDEX IF NOT EXISTS idx_automation_enabled ON automations (enabled)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create the automations table and indexes (idempotent)."""
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def next_identifier(connection: sqlite3.Connection, prefix: str = "automation-") -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {TABLE}"):
        raw = row["id"]
        if not raw or not raw.startswith(prefix):
            continue
        suffix = raw[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:03d}"


def row_to_core(row: sqlite3.Row) -> AutomationCore:
    return AutomationCore(
        name=row["name"],
        description=row["description"],
        draft_id=row["draft_id"],
        trigger_type=row["trigger_type"],
        trigger_config=row["trigger_config"],
        enabled=bool(row["enabled"]),
    )


def row_to_record(
    row: sqlite3.Row,
    workflow_name: str = "",
    draft_status: str = "",
    execution_count: int = 0,
    last_execution: Optional[ExecutionSummaryView] = None,
    next_run: Optional[datetime] = None,
    last_run: Optional[datetime] = None,
    run_count: int = 0,
    failure_count: int = 0,
) -> AutomationRecord:
    return AutomationRecord(
        **row_to_core(row).model_dump(),
        id=row["id"],
        workflow_name=workflow_name,
        draft_status=draft_status,
        execution_count=execution_count,
        last_execution=last_execution,
        next_run=next_run,
        last_run=last_run,
        run_count=run_count,
        failure_count=failure_count,
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )


def select_by_id(
    connection: sqlite3.Connection, automation_id: str
) -> Optional[sqlite3.Row]:
    return connection.execute(
        f"SELECT * FROM {TABLE} WHERE id = ?", (automation_id,)
    ).fetchone()


def select_all(
    connection: sqlite3.Connection, enabled: Optional[bool] = None
) -> List[sqlite3.Row]:
    if enabled is None:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} ORDER BY created_at ASC, id ASC"
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT * FROM {TABLE} WHERE enabled = ? ORDER BY created_at ASC, id ASC",
            (1 if enabled else 0,),
        ).fetchall()
    return list(rows)


def insert_automation(
    connection: sqlite3.Connection, core: AutomationCore
) -> sqlite3.Row:
    now = format_timestamp(datetime.now(timezone.utc))
    identifier = next_identifier(connection)
    connection.execute(
        f"""
        INSERT INTO {TABLE}
            (id, name, description, draft_id, trigger_type, trigger_config,
             enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            core.name,
            core.description,
            core.draft_id,
            core.trigger_type,
            core.trigger_config,
            1 if core.enabled else 0,
            now,
            now,
        ),
    )
    row = select_by_id(connection, identifier)
    assert row is not None
    return row


def update_automation(
    connection: sqlite3.Connection, automation_id: str, changes: dict[str, Any]
) -> sqlite3.Row:
    allowed = {"name", "description", "trigger_type", "trigger_config", "enabled"}
    updates = {key: value for key, value in changes.items() if key in allowed}
    if updates:
        if "enabled" in updates:
            updates["enabled"] = 1 if updates["enabled"] else 0
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = list(updates.values())
        params.append(format_timestamp(datetime.now(timezone.utc)))
        params.append(automation_id)
        connection.execute(
            f"UPDATE {TABLE} SET {assignments}, updated_at = ? WHERE id = ?", params
        )
    row = select_by_id(connection, automation_id)
    assert row is not None
    return row


def set_enabled(
    connection: sqlite3.Connection, automation_id: str, enabled: bool
) -> sqlite3.Row:
    return update_automation(connection, automation_id, {"enabled": enabled})


def delete_automation(connection: sqlite3.Connection, automation_id: str) -> int:
    cursor = connection.execute(
        f"DELETE FROM {TABLE} WHERE id = ?", (automation_id,)
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def count_automations(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS total FROM {TABLE}").fetchone()
    return int(row["total"]) if row else 0


__all__ = [
    "TABLE",
    "AutomationStatus",
    "AutomationTriggerType",
    "count_automations",
    "delete_automation",
    "ensure_table",
    "insert_automation",
    "next_identifier",
    "row_to_core",
    "row_to_record",
    "select_all",
    "select_by_id",
    "set_enabled",
    "update_automation",
]
