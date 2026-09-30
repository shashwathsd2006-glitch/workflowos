"""SQLite connection helpers and database initialisation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.config import settings
from app.models.activity import ensure_table as ensure_activity_table
from app.models.automation import ensure_table as ensure_automation_table
from app.models.execution import ensure_tables as ensure_execution_tables
from app.models.generator import ensure_table as ensure_generator_table
from app.models.integration import ensure_tables as ensure_integration_tables
from app.models.job import ensure_table as ensure_job_table
from app.models.schedule import ensure_table as ensure_schedule_table
from app.models.understanding import ensure_table as ensure_understanding_table
from app.models.workflow_candidate import (
    ensure_table as ensure_workflow_candidate_table,
)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(settings.database_path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Yield a connection with automatic commit/rollback/close handling."""
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create the database file, bookkeeping tables and business tables."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        row = connection.execute("SELECT COUNT(*) AS total FROM schema_version").fetchone()
        if row is not None and row["total"] == 0:
            connection.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (1, datetime.now(timezone.utc).isoformat()),
            )
        ensure_activity_table(connection)
        ensure_generator_table(connection)
        ensure_workflow_candidate_table(connection)
        ensure_understanding_table(connection)
        ensure_execution_tables(connection)
        ensure_automation_table(connection)
        ensure_schedule_table(connection)
        ensure_job_table(connection)
        ensure_integration_tables(connection)


def database_ready() -> bool:
    """Return True when the database file exists and responds to a query."""
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    return True
