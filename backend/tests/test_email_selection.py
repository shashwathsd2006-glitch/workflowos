"""Choosing which real Gmail message a run processes.

Covers the "Select Email" path end to end:

* listing recent messages from the real mailbox shape;
* pointing a run at a specific message id;
* reading exactly that message;
* replying to it through Gmail;
* the approval gate still refusing an unapproved draft;
* honest failures when Gmail is not authenticated or the id is unknown;
* and no token ever reaching the API response.
"""

from __future__ import annotations

import json

import pytest

from app.automation.engine import DraftNotApprovedError, execute_draft
from app.integrations.registry import get_provider
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)


class FakeGoogle:
    def __init__(self, payload=None, status: int = 200):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.content = json.dumps(self._payload).encode()
        self.text = self.content.decode()

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _message(mid: str, subject: str, sender: str, body: str = "") -> dict:
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "snippet": f"snippet for {subject}",
        "internalDate": "1758000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _b64(body)},
        },
    }


def _b64(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _connect_gmail(client) -> None:
    """Store a usable credential so the provider reports itself connected."""
    from app.integrations.credentials import encrypt_token
    from app.models import integration as integration_model
    from app.database import get_connection

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "scope": "https://www.googleapis.com/auth/gmail.readonly "
                    "https://www.googleapis.com/auth/gmail.send",
                }
            ),
            account_email="me@example.com",
            is_mock=False,
            verified=True,
        )


# ------------------------------------------------------------- listing


def test_lists_recent_messages_with_the_expected_fields(client, monkeypatch):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            return FakeGoogle({"messages": [{"id": "m-1"}, {"id": "m-2"}]})
        if url.endswith("/messages/m-1"):
            return FakeGoogle(_message("m-1", "Invoice 88213", "c@d.com", "charged twice"))
        return FakeGoogle(_message("m-2", "Hello", "x@y.com", "hi"))

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )

    body = client.get("/api/integrations/gmail/messages?limit=5").json()
    assert body["count"] == 2
    first = body["messages"][0]
    for field in ("message_id", "thread_id", "sender", "subject", "date", "snippet"):
        assert field in first, field
    assert first["message_id"] == "m-1"
    assert first["subject"] == "Invoice 88213"
    assert first["sender"] == "c@d.com"
    assert first["date"]
    # Body is withheld unless asked for.
    assert first["body"] is None

    with_body = client.get(
        "/api/integrations/gmail/messages?limit=1&include_body=true"
    ).json()
    assert with_body["messages"][0]["body"] == "charged twice"


def test_message_listing_never_exposes_a_token(client, monkeypatch):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "super-secret", raising=False
    )

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            return FakeGoogle({"messages": [{"id": "m-1"}]})
        return FakeGoogle(_message("m-1", "S", "a@b.com", "body"))

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )
    raw = client.get("/api/integrations/gmail/messages?limit=1&include_body=true").text
    for needle in ("super-secret", "access_token", "refresh_token", "client_secret"):
        assert needle not in raw


def test_listing_requires_authentication(client):
    """No credential means a real, actionable error - not an empty list."""
    response = client.get("/api/integrations/gmail/messages")
    assert response.status_code == 409
    assert "authentication required" in response.json()["error"]["detail"]
    assert "Settings" in response.json()["error"]["detail"]


def test_listing_surfaces_a_gmail_api_failure(client, monkeypatch):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle(
            {"error": {"message": "Rate Limit Exceeded"}}, status=429
        ),
    )
    response = client.get("/api/integrations/gmail/messages")
    assert response.status_code == 502
    assert "429" in response.json()["error"]["detail"]


def test_listing_reports_an_empty_mailbox_without_inventing(client, monkeypatch):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle({"messages": []}),
    )
    body = client.get("/api/integrations/gmail/messages").json()
    assert body["messages"] == []
    assert body["count"] == 0


# ------------------------------------------------- selection drives execution


def _triage_draft(candidate_id: str = "workflow-selection"):
    from app.models import generator as draft_model
    from app.database import get_connection

    draft = WorkflowDraft(
        name="Selection triage",
        description="Reads the message the user picked",
        trigger=WorkflowTrigger(type="manual", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="Gmail", action="read_email",
                purpose="Read the selected email",
            ),
            GeneratedWorkflowStep(
                step_number=2, application="WorkFlowOS AI", action="analyze_email",
                purpose="Classify it",
            ),
            GeneratedWorkflowStep(
                step_number=3, application="Gmail", action="send_email",
                purpose="Reply",
            ),
            GeneratedWorkflowStep(
                step_number=4, application="WorkFlowOS", action="log_result",
                purpose="Record",
            ),
        ],
        confidence=0.9,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id=candidate_id,
        )
        return record


def test_selected_message_id_is_read_by_step_one(client, monkeypatch):
    from app.config import settings
    from app.database import get_connection
    from app.models import generator as draft_model

    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    for key, value in (
        ("gmail_reply_to", "me@example.com"),
        ("gmail_reply_subject", "Re: your email"),
    ):
        object.__setattr__(settings, key, value)

    requested = []

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/messages/send"):
            return FakeGoogle({"id": "sent-77", "threadId": "t"})
        requested.append(url)
        if "/messages/picked-42" in url:
            return FakeGoogle(
                _message("picked-42", "Picked subject", "boss@corp.com",
                         "please refund this invoice")
            )
        if url.endswith("/messages"):
            return FakeGoogle({"messages": [{"id": "picked-42"}]})
        return FakeGoogle({})

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )

    record = _triage_draft()
    with get_connection() as connection:
        draft_model.set_status(connection, record, "approved")

    response = client.post(
        f"/api/workflows/drafts/{record.id}/execute",
        json={"inputs": {"message_id": "picked-42"}},
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "completed"
    assert detail["completed_steps"] == 4

    read = json.loads(detail["steps"][0]["output"])
    assert read["result"]["id"] == "picked-42"
    assert read["result"]["subject"] == "Picked subject"
    # The exact message was fetched, not a newest-message fallback.
    assert any("/messages/picked-42" in url for url in requested)

    analysis = json.loads(detail["steps"][1]["output"])
    assert analysis["analysed_characters"] > 0
    assert analysis["result"]["priority"] in {"low", "normal", "high", "urgent"}

    sent = json.loads(detail["steps"][2]["output"])
    assert sent["result"]["message_id"] == "sent-77"
    # The reply is addressed to the sender of the message that was read, not
    # to the monitored account or a configured fallback.
    assert sent["result"]["to"] == "boss@corp.com"
    assert sent["result"]["subject"].startswith("Re: ")


def test_no_selection_falls_back_to_the_newest_message(client, monkeypatch):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda method, url, **kw: (
            FakeGoogle({"id": "sent-1", "threadId": "t"})
            if url.endswith("/messages/send")
            else (
                FakeGoogle({"messages": [{"id": "newest-9"}]})
                if url.endswith("/messages")
                else FakeGoogle(_message("newest-9", "Newest", "n@e.com", "body"))
            )
        ),
    )
    from app.config import settings

    for key, value in (
        ("gmail_read_query", ""),
        ("gmail_reply_to", "me@example.com"),
        ("gmail_reply_subject", "Re"),
    ):
        object.__setattr__(settings, key, value)

    from app.database import get_connection
    from app.models import generator as draft_model

    record = _triage_draft("workflow-selection-default")
    with get_connection() as connection:
        draft_model.set_status(connection, record, "approved")

    detail = client.post(
        f"/api/workflows/drafts/{record.id}/execute", json={"inputs": {}}
    ).json()
    read = json.loads(detail["steps"][0]["output"])
    assert read["result"]["id"] == "newest-9"


def test_selected_message_that_cannot_be_read_reports_the_real_error(
    client, monkeypatch
):
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle(
            {"error": {"message": "Requested entity was not found."}}, status=404
        ),
    )
    from app.database import get_connection
    from app.models import generator as draft_model

    record = _triage_draft("workflow-selection-missing")
    with get_connection() as connection:
        draft_model.set_status(connection, record, "approved")

    detail = client.post(
        f"/api/workflows/drafts/{record.id}/execute",
        json={"inputs": {"message_id": "does-not-exist"}},
    ).json()
    assert detail["status"] == "failed"
    error = detail["steps"][0]["error"]
    assert "404" in error
    assert "not found" in error.lower()
    # Nothing was invented, and later steps stayed pending.
    assert detail["steps"][1]["status"] == "pending"


# ------------------------------------------------------------ approval gate


def test_selection_does_not_bypass_the_approval_gate(client):
    """Inputs are ignored entirely until a human approves."""
    _connect_gmail(client)
    record = _triage_draft("workflow-selection-gate")
    assert record.status == "pending_approval"

    response = client.post(
        f"/api/workflows/drafts/{record.id}/execute",
        json={"inputs": {"message_id": "anything"}},
    )
    assert response.status_code == 409
    assert "only approved workflows may be executed" in response.json()["error"]["detail"]
    assert client.get("/api/workflows/executions").json()["executions"] == []


def test_execute_without_a_body_still_works(client, monkeypatch):
    """The endpoint stays backwards compatible for callers with no inputs."""
    _connect_gmail(client)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda method, url, **kw: (
            FakeGoogle({"id": "s1", "threadId": "t"})
            if url.endswith("/messages/send")
            else (
                FakeGoogle({"messages": [{"id": "n1"}]})
                if url.endswith("/messages")
                else FakeGoogle(_message("n1", "S", "a@b.com", "b"))
            )
        ),
    )
    from app.config import settings
    from app.database import get_connection
    from app.models import generator as draft_model

    for key, value in (
        ("gmail_read_query", ""),
        ("gmail_reply_to", "me@example.com"),
        ("gmail_reply_subject", "Re"),
    ):
        object.__setattr__(settings, key, value)

    record = _triage_draft("workflow-selection-nobody")
    with get_connection() as connection:
        draft_model.set_status(connection, record, "approved")

    response = client.post(f"/api/workflows/drafts/{record.id}/execute")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
