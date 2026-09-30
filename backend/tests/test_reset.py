"""Reset safety: ``POST /api/system/reset`` must be narrow and harmless.

The demo depends on being able to clear local history at any moment. These
tests pin the guarantees that make that safe — above all that a reset can never
disturb an authorised Gmail connection, and can never reach a real mailbox.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.database import get_connection
from app.models import integration as integration_model
from app.services import reset_service


def _create_execution(client, candidate_id: str = "workflow-reset"):
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model

    draft = WorkflowDraft(
        name="Reset fixture",
        description="Local record to be cleared",
        trigger=WorkflowTrigger(type="manual", application="WorkFlowOS", action="x"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="WorkFlowOS", action="log", purpose="p"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id=candidate_id,
        )
        draft_model.set_status(connection, record, "approved")
    client.post(f"/api/workflows/drafts/{record.id}/execute")
    return record.id


def test_reset_clears_local_records(client):
    _create_execution(client)
    assert client.get("/api/workflows/executions").json()["executions"]

    body = client.post("/api/system/reset").json()
    assert body["reset"] is True
    assert body["scope"] == "local-records-only"
    assert body["external_calls_made"] == 0

    assert client.get("/api/workflows/executions").json()["executions"] == []
    assert client.get("/api/activity").json()["events"] == []
    assert client.get("/api/background-jobs").json()["jobs"] == []
    assert client.get("/api/workflows/drafts").json()["drafts"] == []


def test_reset_preserves_a_connected_gmail_account(client):
    """The single most important guarantee: OAuth state survives a reset."""
    from app.integrations.credentials import encrypt_token

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "secret", "expires_at": "2099-01-01T00:00:00+00:00"}
            ),
            account_label="judge@gmail.com",
            is_mock=False,
            verified=True,
        )

    _create_execution(client)
    client.post("/api/system/reset")

    with get_connection() as connection:
        row = integration_model.select_account(connection, "gmail")
    assert row is not None
    assert row["account_label"] == "judge@gmail.com"
    assert row["encrypted_token"]
    assert row["is_mock"] == 0

    assert client.get("/api/integrations/gmail/status").json()["connected"] is True


def test_reset_makes_no_external_call(client, monkeypatch):
    """No HTTP request may be issued by a reset."""
    import httpx

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("reset attempted an external HTTP call")

    monkeypatch.setattr(httpx, "request", explode)
    _create_execution(client)
    assert client.post("/api/system/reset").status_code == 200


def test_reset_leaves_the_credential_files_alone(client):
    """The OAuth client file and token key must not be touched."""
    from pathlib import Path

    import app.config as config_module

    credentials = Path(config_module.BACKEND_DIR) / "credentials"
    before = sorted(p.name for p in credentials.glob("*")) if credentials.exists() else []
    client.post("/api/system/reset")
    after = sorted(p.name for p in credentials.glob("*")) if credentials.exists() else []
    assert before == after


def test_reset_preview_changes_nothing(client):
    _create_execution(client)
    before = client.get("/api/system/reset/preview").json()["counts"]
    assert before["executions"] >= 1
    after = client.get("/api/system/reset/preview").json()["counts"]
    assert after == before


def test_reset_is_safe_to_repeat(client):
    assert client.post("/api/system/reset").status_code == 200
    body = client.post("/api/system/reset").json()
    assert body["reset"] is True
    assert all(value == 0 for value in body["counts"].values())


def test_reset_service_never_lists_integration_accounts():
    """Structural guard: the account table is not a delete target.

    The provider-event ledger *is* cleared — it only holds the dedupe keys of
    messages already observed, and keeping it made a reset unrecoverable. The
    authorised account and its encrypted token live in a different table,
    which is why they survive.
    """
    tables = {table for _, table in reset_service._TABLES_IN_DELETE_ORDER}
    assert integration_model.ACCOUNTS_TABLE not in tables
    assert integration_model.EVENTS_TABLE in tables
    # And nothing token-shaped is a delete target.
    assert integration_model.ACCOUNTS_TABLE != integration_model.EVENTS_TABLE


@pytest.mark.parametrize("path", ["/api/system/reset/preview"])
def test_reset_endpoints_expose_no_secrets(client, path):
    body = client.get(path).text
    for needle in ("access_token", "refresh_token", "client_secret", "encrypted_token"):
        assert needle not in body


def test_reset_clears_the_provider_event_ledger(client):
    """The dedupe ledger must be cleared with the activity it produced.

    Leaving ``integration_events`` behind made a reset a one-way door: the
    messages were already recorded as seen, so the observation pass skipped
    them and Activity stayed permanently empty after every later reset.
    """
    from app.database import get_connection
    from app.models import integration as integration_model

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.record_event(
            connection,
            provider="gmail",
            external_event_id="gmail:reset-probe-1",
            automation_id=None,
            payload={"message_id": "reset-probe-1"},
        )
        before = connection.execute(
            "SELECT COUNT(*) AS n FROM integration_events"
        ).fetchone()["n"]
    assert before >= 1

    client.post("/api/system/reset")

    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS n FROM integration_events"
        ).fetchone()["n"]
    assert after == 0


def test_reset_still_preserves_the_connected_account(client):
    from app.database import get_connection
    from app.models import integration as integration_model

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        before = connection.execute(
            "SELECT COUNT(*) AS n FROM integration_accounts"
        ).fetchone()["n"]

    client.post("/api/system/reset")

    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS n FROM integration_accounts"
        ).fetchone()["n"]
    assert after == before


def test_reset_clears_the_observation_throttle(client):
    """A reset must not leave observation waiting out a poll interval."""
    from app.scheduler.service import scheduler

    scheduler._poll_state["observe:gmail"] = {"last": datetime(2999, 1, 1)}
    client.post("/api/system/reset")
    assert scheduler._poll_state == {}
