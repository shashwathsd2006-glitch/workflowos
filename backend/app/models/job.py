"""Persistence for background_jobs.

The queue is durable: a job row is the single source of truth for its own
lifecycle, so a crash between "queued" and "running" is recoverable — see
``app.scheduler.worker.recover_stale_jobs``.

Exactly-once processing is enforced by a conditional UPDATE
(``queued -> running``) that only succeeds for the row still in ``queued``.
Two workers therefore cannot both claim the same job.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.activity import format_timestamp, parse_timestamp
from app.schemas.job import JobRecord, JobStatus, JobTriggerKind

TABLE = "background_jobs"

DDL = """
CREATE TABLE IF NOT EXISTS background_jobs (
    id               TEXT PRIMARY KEY,
    automation_id    TEXT NOT NULL,
    execution_id     TEXT,
    trigger          TEXT NOT NULL DEFAULT 'manual',
    event_id         TEXT,
    status           TEXT NOT NULL DEFAULT 'queued',
    payload_json     TEXT NOT NULL DEFAULT '{}',
    error            TEXT,
    error_type       TEXT,
    retryable        INTEGER,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    max_retries      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    completed_at     TEXT,
    duration_ms      INTEGER,
    FOREIGN KEY (automation_id) REFERENCES automations (id) ON DELETE CASCADE
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_job_automation ON background_jobs (automation_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_status ON background_jobs (status)",
    "CREATE INDEX IF NOT EXISTS idx_job_created ON background_jobs (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_job_next_attempt ON background_jobs (next_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_job_event ON background_jobs (event_id)",
)


def ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(DDL)
    for statement in INDEXES:
        connection.execute(statement)


def _now() -> str:
    return format_timestamp(datetime.now(timezone.utc))


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


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
        if raw and raw.startswith("job-"):
            suffix = raw[len("job-") :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"job-{highest + 1:06d}"


def row_to_record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        automation_id=row["automation_id"],
        execution_id=row["execution_id"],
        trigger=row["trigger"],
        event_id=row["event_id"],
        status=row["status"],
        payload=_loads(row["payload_json"], {}),
        error=row["error"],
        error_type=row["error_type"],
        retryable=None if row["retryable"] is None else bool(row["retryable"]),
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        next_attempt_at=(
            parse_timestamp(row["next_attempt_at"]) if row["next_attempt_at"] else None
        ),
        created_at=parse_timestamp(row["created_at"]),
        started_at=parse_timestamp(row["started_at"]) if row["started_at"] else None,
        completed_at=(
            parse_timestamp(row["completed_at"]) if row["completed_at"] else None
        ),
        duration_ms=row["duration_ms"],
    )


def select_by_id(
    connection: sqlite3.Connection, job_id: str
) -> Optional[JobRecord]:
    row = connection.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (job_id,)).fetchone()
    return row_to_record(row) if row else None


def select_all(
    connection: sqlite3.Connection,
    automation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[JobRecord]:
    clauses: List[str] = []
    params: List[Any] = []
    if automation_id:
        clauses.append("automation_id = ?")
        params.append(automation_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = connection.execute(
        f"SELECT * FROM {TABLE} {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [row_to_record(row) for row in rows]


def counts(connection: sqlite3.Connection) -> Dict[str, int]:
    """Per-status job counts, always including every known status."""
    result = {
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for row in connection.execute(
        f"SELECT status, COUNT(*) AS total FROM {TABLE} GROUP BY status"
    ):
        status = row["status"]
        if status in result:
            result[status] = int(row["total"])
    return result


def count_active_for_automation(
    connection: sqlite3.Connection, automation_id: str
) -> int:
    """Queued/running jobs for one automation — prevents duplicate pile-up."""
    row = connection.execute(
        f"SELECT COUNT(*) AS total FROM {TABLE} WHERE automation_id = ? "
        "AND status IN ('queued', 'running')",
        (automation_id,),
    ).fetchone()
    return int(row["total"]) if row else 0


def insert_job(
    connection: sqlite3.Connection,
    *,
    automation_id: str,
    trigger: JobTriggerKind = "manual",
    event_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    max_retries: int = 0,
) -> JobRecord:
    identifier = next_identifier(connection)
    now = _now()
    connection.execute(
        f"""
        INSERT INTO {TABLE}
            (id, automation_id, trigger, event_id, status, payload_json,
             retry_count, max_retries, next_attempt_at, created_at)
        VALUES (?, ?, ?, ?, 'queued', ?, 0, ?, ?, ?)
        """,
        (
            identifier,
            automation_id,
            trigger,
            event_id,
            _dumps(payload or {}),
            max_retries,
            now,
            now,
        ),
    )
    record = select_by_id(connection, identifier)
    assert record is not None
    return record


def claim_next_job(
    connection: sqlite3.Connection, now: Optional[datetime] = None
) -> Optional[JobRecord]:
    """Atomically claim the oldest ready job.

    The UPDATE is conditional on ``status = 'queued'``; if another worker won
    the race it changes zero rows and we try the next candidate. This is what
    makes the queue safe against double execution.
    """
    moment = format_timestamp(now or datetime.now(timezone.utc))
    candidates = connection.execute(
        f"SELECT * FROM {TABLE} WHERE status = 'queued' "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
        "ORDER BY created_at ASC, id ASC LIMIT 20",
        (moment,),
    ).fetchall()
    for row in candidates:
        cursor = connection.execute(
            f"UPDATE {TABLE} SET status = 'running', started_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (moment, row["id"]),
        )
        if cursor.rowcount == 1:
            return select_by_id(connection, row["id"])
    return None


def complete_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    execution_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Optional[JobRecord]:
    connection.execute(
        f"UPDATE {TABLE} SET status = 'completed', execution_id = ?, "
        "completed_at = ?, duration_ms = ?, error = NULL WHERE id = ?",
        (execution_id, _now(), duration_ms, job_id),
    )
    return select_by_id(connection, job_id)


def fail_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    error: str,
    error_type: str,
    retryable: bool,
    retry_count: int,
    max_retries: int,
    next_attempt_at: Optional[datetime],
    duration_ms: Optional[int] = None,
) -> Optional[JobRecord]:
    """Record a failure and either requeue for retry or finish as failed."""
    status: JobStatus = "queued" if retryable and retry_count < max_retries else "failed"
    connection.execute(
        f"""
        UPDATE {TABLE} SET
            status = ?, error = ?, error_type = ?, retryable = ?,
            retry_count = ?, next_attempt_at = ?, completed_at = ?, duration_ms = ?
        WHERE id = ?
        """,
        (
            status,
            error,
            error_type,
            1 if retryable else 0,
            retry_count,
            format_timestamp(next_attempt_at) if next_attempt_at else None,
            None if status == "queued" else _now(),
            duration_ms,
            job_id,
        ),
    )
    return select_by_id(connection, job_id)


def recover_stale_jobs(
    connection: sqlite3.Connection, stale_after_seconds: int
) -> int:
    """Requeue jobs stuck in ``running`` after a crash or restart.

    A job left ``running`` means the process died mid-execution. It goes back
    to ``queued`` so the worker can retry it, and its retry counter is bumped
    so a poison job cannot loop forever.
    """
    from datetime import timedelta

    cutoff = format_timestamp(
        datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    )
    rows = connection.execute(
        f"SELECT id, retry_count, max_retries FROM {TABLE} "
        "WHERE status = 'running' AND (started_at IS NULL OR started_at <= ?)",
        (cutoff,),
    ).fetchall()
    recovered = 0
    for row in rows:
        exhausted = row["retry_count"] >= row["max_retries"]
        if exhausted:
            connection.execute(
                f"UPDATE {TABLE} SET status = 'failed', error = ?, "
                "error_type = 'StaleJob', retryable = 0, completed_at = ? "
                "WHERE id = ?",
                (
                    "Job was interrupted and exhausted its retries",
                    _now(),
                    row["id"],
                ),
            )
        else:
            connection.execute(
                f"UPDATE {TABLE} SET status = 'queued', retry_count = ?, "
                "started_at = NULL, next_attempt_at = ? WHERE id = ?",
                (row["retry_count"] + 1, _now(), row["id"]),
            )
        recovered += 1
    return recovered


def cancel_jobs_for_automation(
    connection: sqlite3.Connection, automation_id: str
) -> int:
    cursor = connection.execute(
        f"UPDATE {TABLE} SET status = 'cancelled', completed_at = ? "
        "WHERE automation_id = ? AND status IN ('queued', 'running')",
        (_now(), automation_id),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def count_jobs(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS total FROM {TABLE}").fetchone()
    return int(row["total"]) if row else 0


__all__ = [
    "TABLE",
    "cancel_jobs_for_automation",
    "claim_next_job",
    "complete_job",
    "counts",
    "count_active_for_automation",
    "count_jobs",
    "ensure_table",
    "fail_job",
    "insert_job",
    "next_identifier",
    "recover_stale_jobs",
    "row_to_record",
    "select_all",
    "select_by_id",
]
