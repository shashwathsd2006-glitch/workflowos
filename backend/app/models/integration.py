"""Persistence for integration_accounts and integration_events.

``integration_accounts`` holds one row per provider with an *encrypted* token
bundle. ``integration_events`` is the idempotency ledger: a
``UNIQUE (provider, external_event_id, automation_id)`` constraint is what
stops the same Gmail message triggering the same automation twice, even
across a worker restart.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.activity import format_timestamp, parse_timestamp

ACCOUNTS_TABLE = "integration_accounts"
EVENTS_TABLE = "integration_events"

ACCOUNTS_DDL = """
CREATE TABLE IF NOT EXISTS integration_accounts (
    provider        TEXT PRIMARY KEY,
    encrypted_token TEXT,
    account_label   TEXT,
    account_email   TEXT,
    scopes          TEXT NOT NULL DEFAULT '[]',
    is_mock         INTEGER NOT NULL DEFAULT 0,
    last_verified   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS integration_events (
    id                 TEXT PRIMARY KEY,
    provider           TEXT NOT NULL,
    automation_id      TEXT,
    external_event_id  TEXT NOT NULL,
    dedupe_key         TEXT NOT NULL,
    payload_json       TEXT NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'received',
    created_at         TEXT NOT NULL,
    processed_at       TEXT,
    UNIQUE (dedupe_key)
)
"""

INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_integration_events_automation "
    f"ON {EVENTS_TABLE} (automation_id)",
    f"CREATE INDEX IF NOT EXISTS idx_integration_events_status "
    f"ON {EVENTS_TABLE} (status)",
    f"CREATE INDEX IF NOT EXISTS idx_integration_events_provider "
    f"ON {EVENTS_TABLE} (provider)",
    f"CREATE INDEX IF NOT EXISTS idx_integration_events_created "
    f"ON {EVENTS_TABLE} (created_at)",
)


def ensure_tables(connection: sqlite3.Connection) -> None:
    """Create both integration tables and indexes (idempotent)."""
    connection.execute(ACCOUNTS_DDL)
    connection.execute(EVENTS_DDL)
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


def next_event_identifier(connection: sqlite3.Connection) -> str:
    highest = 0
    for row in connection.execute(f"SELECT id FROM {EVENTS_TABLE}"):
        raw = row["id"]
        if not raw or not raw.startswith("intevent-"):
            continue
        suffix = raw[len("intevent-") :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"intevent-{highest + 1:05d}"


# ------------------------------------------------------------- accounts

def upsert_account(
    connection: sqlite3.Connection,
    provider: str,
    *,
    encrypted_token: Optional[str],
    account_label: Optional[str] = None,
    account_email: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    is_mock: Optional[bool] = None,
    verified: bool = False,
) -> None:
    """Insert or update one provider's stored credential.

    Descriptive fields are preserved when the caller does not supply them.
    A routine token refresh only re-encrypts the token, and must not blank the
    verified account email or the granted scopes the Settings page shows.
    """
    now = _now()
    existing = connection.execute(
        f"SELECT provider, created_at FROM {ACCOUNTS_TABLE} WHERE provider = ?",
        (provider,),
    ).fetchone()
    last_verified = now if verified else None
    if existing is None:
        connection.execute(
            f"""
            INSERT INTO {ACCOUNTS_TABLE}
                (provider, encrypted_token, account_label, account_email, scopes,
                 is_mock, last_verified, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                encrypted_token,
                account_label,
                account_email,
                _dumps(scopes or []),
                1 if is_mock else 0,
                last_verified,
                now,
                now,
            ),
        )
        return
    connection.execute(
        f"""
        UPDATE {ACCOUNTS_TABLE} SET
            encrypted_token = ?,
            account_label = COALESCE(?, account_label),
            account_email = COALESCE(?, account_email),
            scopes = COALESCE(?, scopes),
            is_mock = COALESCE(?, is_mock),
            last_verified = COALESCE(?, last_verified),
            updated_at = ?
        WHERE provider = ?
        """,
        (
            encrypted_token,
            account_label,
            account_email,
            _dumps(scopes) if scopes is not None else None,
            None if is_mock is None else (1 if is_mock else 0),
            last_verified,
            now,
            provider,
        ),
    )


def delete_account(connection: sqlite3.Connection, provider: str) -> int:
    cursor = connection.execute(
        f"DELETE FROM {ACCOUNTS_TABLE} WHERE provider = ?", (provider,)
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def select_account(
    connection: sqlite3.Connection, provider: str
) -> Optional[sqlite3.Row]:
    return connection.execute(
        f"SELECT * FROM {ACCOUNTS_TABLE} WHERE provider = ?", (provider,)
    ).fetchone()


def select_accounts(connection: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        connection.execute(f"SELECT * FROM {ACCOUNTS_TABLE} ORDER BY provider ASC")
    )


# --------------------------------------------------------------- events

def record_event(
    connection: sqlite3.Connection,
    *,
    provider: str,
    external_event_id: str,
    automation_id: Optional[str],
    payload: Optional[Dict[str, Any]] = None,
    status: str = "received",
) -> Optional[str]:
    """Insert an external event, or return None if it was already recorded.

    Idempotency is enforced by the UNIQUE(dedupe_key) constraint, so a
    duplicate insert is a no-op regardless of which code path attempted it.
    """
    dedupe_key = f"{provider}:{external_event_id}:{automation_id or '-'}"
    existing = connection.execute(
        f"SELECT id FROM {EVENTS_TABLE} WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    if existing is not None:
        return None
    identifier = next_event_identifier(connection)
    connection.execute(
        f"""
        INSERT INTO {EVENTS_TABLE}
            (id, provider, automation_id, external_event_id, dedupe_key,
             payload_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier,
            provider,
            automation_id,
            external_event_id,
            dedupe_key,
            _dumps(payload or {}),
            status,
            _now(),
        ),
    )
    return identifier


def mark_event_processed(
    connection: sqlite3.Connection, event_id: str, status: str = "processed"
) -> None:
    connection.execute(
        f"UPDATE {EVENTS_TABLE} SET status = ?, processed_at = ? WHERE id = ?",
        (status, _now(), event_id),
    )


def select_events(
    connection: sqlite3.Connection,
    provider: Optional[str] = None,
    automation_id: Optional[str] = None,
    limit: int = 50,
) -> List[sqlite3.Row]:
    clauses: List[str] = []
    params: List[Any] = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if automation_id:
        clauses.append("automation_id = ?")
        params.append(automation_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    return list(
        connection.execute(
            f"SELECT * FROM {EVENTS_TABLE} {where} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        )
    )


def event_exists(
    connection: sqlite3.Connection, provider: str, external_event_id: str, automation_id: Optional[str]
) -> bool:
    dedupe_key = f"{provider}:{external_event_id}:{automation_id or '-'}"
    return (
        connection.execute(
            f"SELECT 1 FROM {EVENTS_TABLE} WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        is not None
    )


def count_events(
    connection: sqlite3.Connection, provider: Optional[str] = None
) -> int:
    if provider:
        row = connection.execute(
            f"SELECT COUNT(*) AS total FROM {EVENTS_TABLE} WHERE provider = ?",
            (provider,),
        ).fetchone()
    else:
        row = connection.execute(f"SELECT COUNT(*) AS total FROM {EVENTS_TABLE}").fetchone()
    return int(row["total"]) if row else 0


def row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "automation_id": row["automation_id"],
        "external_event_id": row["external_event_id"],
        "status": row["status"],
        "created_at": parse_timestamp(row["created_at"]),
        "processed_at": (
            parse_timestamp(row["processed_at"]) if row["processed_at"] else None
        ),
        "payload": _loads(row["payload_json"], {}),
    }


__all__ = [
    "ACCOUNTS_TABLE",
    "EVENTS_TABLE",
    "count_events",
    "delete_account",
    "ensure_tables",
    "event_exists",
    "mark_event_processed",
    "next_event_identifier",
    "record_event",
    "row_to_event",
    "select_account",
    "select_accounts",
    "select_events",
    "upsert_account",
]
