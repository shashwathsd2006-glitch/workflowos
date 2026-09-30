"""Persistence for automation_schedules.

Schedules are the durable source of truth for *when* an automation should
run. The in-process ticker only reads this table, so a restart recovers every
schedule and recomputes the next run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.job import ScheduleCreate, ScheduleRecord

TABLE = "automation_schedules"

DDL = """
CREATE TABLE IF NOT EXISTS automation_schedules (
    id                TEXT PRIMARY KEY,
    automation_id     TEXT NOT NULL,
    frequency         TEXT NOT NULL,
    timezone          TEXT NOT NULL DEFAULT 'UTC',
    run_at            TEXT,
    interval_seconds  INTEGER,
    time_of_day       TEXT,
    days_of_week      TEXT NOT NULL DEFAULT '[]',
    enabled           INTEGER NOT NULL DEFAULT 1,
    next_run          TEXT,
    last_run          TEXT,
    run_count         INTEGER NOT NULL DEFAULT 0,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (automation_id),
    FOREIGN KEY (automation_id) REFERENCES automations (id) ON DELETE CASCADE
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_schedule_automation "
    "ON automation_schedules (automation_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_next_run "
    "ON automation_schedules (next_run)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_enabled "
    "ON automation_schedules (enabled)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def _now() -> str:
    return format_timestamp(datetime.now(timezone.utc))


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def next_identifier(connection: sqlite3.Connection) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {TABLE}"):
        raw = row["id"]
        if raw and raw.startswith("schedule-"):
            suffix = raw[len("schedule-") :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"schedule-{highest + 1:04d}"


def row_to_record(row: sqlite3.Row) -> ScheduleRecord:
    return ScheduleRecord(
        id=row["id"],
        automation_id=row["automation_id"],
        frequency=row["frequency"],
        timezone=row["timezone"],
        run_at=parse_timestamp(row["run_at"]) if row["run_at"] else None,
        interval_seconds=row["interval_seconds"],
        time_of_day=row["time_of_day"],
        days_of_week=_loads(row["days_of_week"], []),
        enabled=bool(row["enabled"]),
        next_run=parse_timestamp(row["next_run"]) if row["next_run"] else None,
        last_run=parse_timestamp(row["last_run"]) if row["last_run"] else None,
        run_count=row["run_count"],
        failure_count=row["failure_count"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )


def select_by_id(
    connection: sqlite3.Connection, schedule_id: str
) -> Optional[ScheduleRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE id = ?", (schedule_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_by_automation(
    connection: sqlite3.Connection, automation_id: str
) -> Optional[ScheduleRecord]:
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE automation_id = ?", (automation_id,)
    ).fetchone()
    return row_to_record(row) if row else None


def select_all(connection: sqlite3.Connection) -> List[ScheduleRecord]:
    rows = connection.execute(
        f"SELECT * FROM {TABLE} ORDER BY next_run ASC, id ASC"
    ).fetchall()
    return [row_to_record(row) for row in rows]


def select_due(
    connection: sqlite3.Connection, now: Optional[datetime] = None
) -> List[ScheduleRecord]:
    """Schedules whose next_run has arrived and that are still enabled."""
    moment = format_timestamp(now or datetime.now(timezone.utc))
    rows = connection.execute(
        f"SELECT * FROM {TABLE} WHERE enabled = 1 AND next_run IS NOT NULL "
        "AND next_run <= ? ORDER BY next_run ASC",
        (moment,),
    ).fetchall()
    return [row_to_record(row) for row in rows]


def insert_schedule(
    connection: sqlite3.Connection,
    automation_id: str,
    payload: ScheduleCreate,
    next_run: Optional[datetime],
) -> ScheduleRecord:
    identifier = next_identifier(connection)
    now = _now()
    connection.execute(
        f"""
        INSERT INTO {TABLE}
            (id, automation_id, frequency, timezone, run_at, interval_seconds,
             time_of_day, days_of_week, enabled, next_run, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            identifier,
            automation_id,
            payload.frequency,
            payload.timezone,
            format_timestamp(payload.run_at) if payload.run_at else None,
            payload.interval_seconds,
            payload.time_of_day,
            _dumps(payload.days_of_week or []),
            format_timestamp(next_run) if next_run else None,
            now,
            now,
        ),
    )
    record = select_by_id(connection, identifier)
    assert record is not None
    return record


def update_schedule(
    connection: sqlite3.Connection, schedule_id: str, **fields: Any
) -> Optional[ScheduleRecord]:
    allowed = {
        "next_run",
        "last_run",
        "run_count",
        "failure_count",
        "enabled",
        "frequency",
        "timezone",
        "run_at",
        "interval_seconds",
        "time_of_day",
        "days_of_week",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return select_by_id(connection, schedule_id)
    for key in ("run_at", "next_run", "last_run"):
        if key in updates and isinstance(updates[key], datetime):
            updates[key] = format_timestamp(updates[key])
    if "days_of_week" in updates:
        updates["days_of_week"] = _dumps(updates["days_of_week"])
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values())
    params.append(_now())
    params.append(schedule_id)
    connection.execute(
        f"UPDATE {TABLE} SET {assignments}, updated_at = ? WHERE id = ?", params
    )
    return select_by_id(connection, schedule_id)


def delete_schedule(connection: sqlite3.Connection, schedule_id: str) -> int:
    cursor = connection.execute(f"DELETE FROM {TABLE} WHERE id = ?", (schedule_id,))
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def delete_for_automation(
    connection: sqlite3.Connection, automation_id: str
) -> int:
    cursor = connection.execute(
        f"DELETE FROM {TABLE} WHERE automation_id = ?", (automation_id,)
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def count_schedules(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS total FROM {TABLE}").fetchone()
    return int(row["total"]) if row else 0


__all__ = [
    "TABLE",
    "count_schedules",
    "delete_for_automation",
    "delete_schedule",
    "ensure_table",
    "insert_schedule",
    "next_identifier",
    "row_to_record",
    "select_all",
    "select_by_automation",
    "select_by_id",
    "select_due",
    "update_schedule",
]
