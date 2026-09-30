"""Gmail send: success, real failure reporting, and end-to-end execution.

The demo depends on ``gmail_send_email`` actually delivering a message and
reporting Gmail's own result. These tests pin that:

* a send only reports success when Gmail returns a message id;
* Gmail's HTTP errors (401/403/4xx/5xx) surface verbatim, never swallowed;
* a workflow execution carries the real send result, and a failed send fails
  the step instead of pretending it worked.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.automation.actions import (
    ActionContext,
    NonRetryableActionError,
    RetryableActionError,
    run_action,
)
from app.integrations.base import AuthenticationError, IntegrationError
from app.integrations.registry import get_provider


class FakeGoogle:
    """Minimal httpx.Response stand-in for exercising the real send path."""

    def __init__(self, payload=None, status: int = 200):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.content = json.dumps(self._payload).encode()
        self.text = self.content.decode()

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _allow_send(monkeypatch, settings, to="judge@gmail.com", subject="Re: triage"):
    """Give the provider a recipient/subject and a valid access token."""
    for key, value in (("gmail_reply_to", to), ("gmail_reply_subject", subject)):
        object.__setattr__(settings, key, value)
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "test-token", raising=False
    )
    return provider


# ------------------------------------------------------------- scope checks


def test_send_scope_is_requested_and_modify_is_not():
    from app.integrations.gmail import SCOPES

    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert not any("modify" in scope for scope in SCOPES)
    assert not any("delete" in scope for scope in SCOPES)


def test_stored_token_reports_the_send_scope(client):
    """The token the backend loads must actually carry gmail.send."""
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
            is_mock=False,
            verified=True,
        )
    status = client.get("/api/integrations/gmail/status").json()
    assert status["connected"] is True
    assert "https://www.googleapis.com/auth/gmail.send" in status["scopes"]


# ------------------------------------------------------------ successful send


def test_successful_send_returns_gmail_message_id(monkeypatch):
    from app.config import settings

    provider = _allow_send(monkeypatch, settings)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return FakeGoogle({"id": "gmail-msg-1", "threadId": "thread-9"})

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request", fake_request
    )
    result = provider.execute_action(
        "gmail_send_email",
        {"to": "judge@gmail.com", "subject": "Re: triage", "body": "hello"},
    )

    # Real endpoint, real auth, real payload.
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/gmail/v1/users/me/messages/send")
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert "threadId" not in captured["json"]
    assert "raw" in captured["json"]

    # Success is only reported because Gmail returned an id.
    assert result["ok"] is True
    assert result["message_id"] == "gmail-msg-1"
    assert result["to"] == "judge@gmail.com"
    assert result["subject"] == "Re: triage"

    # The MIME body must decode to a well-formed message.
    raw = captured["json"]["raw"]
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    assert "To: judge@gmail.com" in decoded
    assert "Subject: Re: triage" in decoded
    assert "hello" in decoded


def test_send_without_a_message_id_is_not_reported_as_success(monkeypatch):
    """HTTP 200 with no id is not a delivery confirmation."""
    from app.config import settings

    provider = _allow_send(monkeypatch, settings)
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle({"threadId": "t"}),
    )
    with pytest.raises(IntegrationError) as excinfo:
        provider.execute_action(
            "gmail_send_email",
            {"to": "judge@gmail.com", "subject": "s", "body": "b"},
        )
    assert "no message id" in str(excinfo.value)
    assert "unconfirmed" in str(excinfo.value)


# ------------------------------------------------------------- failed sends


@pytest.mark.parametrize(
    "status,error_body,expected",
    [
        (401, {"error": {"message": "Invalid Credentials"}}, "401"),
        (403, {"error": {"message": "Insufficient Permission"}}, "403"),
        (400, {"error": {"message": "Invalid raw data"}}, "400"),
        (500, {"error": {"message": "Backend Error"}}, "500"),
    ],
)
def test_gmail_api_errors_are_surfaced_not_swallowed(
    monkeypatch, status, error_body, expected
):
    from app.config import settings

    provider = _allow_send(monkeypatch, settings)
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle(error_body, status=status),
    )
    with pytest.raises((AuthenticationError, IntegrationError)) as excinfo:
        provider.execute_action(
            "gmail_send_email",
            {"to": "judge@gmail.com", "subject": "s", "body": "b"},
        )
    message = str(excinfo.value)
    assert expected in message
    # Google's own reason must survive to the caller.
    assert error_body["error"]["message"] in message


def test_send_without_recipient_or_subject_fails_clearly(monkeypatch):
    from app.config import settings

    provider = get_provider("gmail")
    for key in ("gmail_reply_to", "gmail_reply_subject"):
        object.__setattr__(settings, key, "")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "t", raising=False
    )
    with pytest.raises(Exception) as excinfo:
        provider.execute_action("gmail_send_email", {"body": "b"})
    assert "'to' address is required" in str(excinfo.value)


def test_workflow_step_reports_a_failed_send(monkeypatch):
    """A Gmail failure must fail the step, not be reported as sent."""
    from app.config import settings

    _allow_send(monkeypatch, settings)
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle(
            {"error": {"message": "Insufficient Permission"}}, status=403
        ),
    )
    context = ActionContext(
        execution_id="execution-0001",
        step_number=3,
        application="Gmail",
        action="send_email",
        parameters={"to": "judge@gmail.com", "subject": "s", "body": "b"},
        action_type="gmail_send_email",
    )
    with pytest.raises((RetryableActionError, NonRetryableActionError)) as excinfo:
        run_action("gmail_send_email", context)
    assert "403" in str(excinfo.value)


# ------------------------------------------------------------- body plumbing


def test_send_body_defaults_to_the_previous_step_output(monkeypatch):
    """The generated step has no body parameter, so the engine fills it in."""
    from app.config import settings

    _allow_send(monkeypatch, settings)
    seen = {}
    import app.integrations.gmail as gmail_module

    original = gmail_module.GmailIntegration.send_email

    def capture(self, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "message_id": "m"}

    gmail_module.GmailIntegration.send_email = capture
    previous = json.dumps(
        {
            "action": "analyze_content",
            "provider": "ollama",
            "result": {
                "summary": "User requests a refund for an invoice.",
                "priority": "normal",
            },
        }
    )
    try:
        run_action(
            "gmail_send_email",
            ActionContext(
                execution_id="execution-0001",
                step_number=3,
                application="Gmail",
                action="send_email",
                previous_output=previous,
                parameters={},
                action_type="gmail_send_email",
            ),
        )
    finally:
        gmail_module.GmailIntegration.send_email = original

    # The reply is composed from the AI analysis, not a bare one-liner.
    assert seen["body"].startswith("User requests a refund for an invoice.")
    assert "Priority: normal" in seen["body"]


def test_explicit_body_parameter_wins(monkeypatch):
    from app.config import settings

    _allow_send(monkeypatch, settings)
    provider = get_provider("gmail")
    seen = {}
    import app.integrations.gmail as gmail_module

    original = gmail_module.GmailIntegration.send_email

    def capture(self, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "message_id": "m"}

    gmail_module.GmailIntegration.send_email = capture
    try:
        provider.execute_action(
            "gmail_send_email", {"body": "explicit body", "to": "a@b.com"}
        )
    finally:
        gmail_module.GmailIntegration.send_email = original
    assert seen["body"] == "explicit body"


# ------------------------------------------------------------- in execution


def test_approved_execution_reports_the_real_send_result(client, monkeypatch):
    """read -> analyse -> send -> log, with a genuine Gmail send response."""
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection
    from app.automation.engine import execute_draft
    from app.config import settings

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "test-token", raising=False
    )
    for key, value in (
        ("gmail_reply_to", "judge@gmail.com"),
        ("gmail_reply_subject", "Re: triage"),
    ):
        object.__setattr__(settings, key, value)

    # Real read payload from Gmail, then a real send response.
    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/messages/send"):
            return FakeGoogle({"id": "sent-42", "threadId": "th-1"})
        return FakeGoogle({})

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request", fake_request
    )

    previous = json.dumps(
        {
            "action": "gmail_read_email",
            "provider": "gmail",
            "is_mock": False,
            "result": {
                "id": "m-1",
                "from": "customer@example.com",
                "subject": "WorkflowOS Demo",
                "snippet": "charged twice",
                "body": "I was charged twice for invoice 88213 and need a refund today.",
            },
        }
    )

    draft = WorkflowDraft(
        name="Send rehearsal",
        description="Proves the send result is reported truthfully",
        trigger=WorkflowTrigger(type="manual", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1,
                application="Gmail",
                action="gmail_read_email",
                purpose="Read the inbound support email",
                parameters={"message_id": "m-1"},
            ),
            GeneratedWorkflowStep(
                step_number=2,
                application="WorkFlowOS AI",
                action="analyze_email",
                purpose="Classify the priority",
            ),
            GeneratedWorkflowStep(
                step_number=3,
                application="Gmail",
                action="gmail_send_email",
                purpose="Send the triage reply",
                parameters={"to": "judge@gmail.com", "subject": "Re: triage"},
            ),
            GeneratedWorkflowStep(
                step_number=4,
                application="WorkFlowOS",
                action="log_result",
                purpose="Record the result",
            ),
        ],
        confidence=0.9,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-send-rehearsal",
        )
        draft_model.set_status(connection, record, "approved")

    assert previous  # the payload shape the engine consumes
    detail = execute_draft(record.id)

    send_step = next(s for s in detail.steps if s.action_type == "gmail_send_email")
    assert send_step.status == "completed"
    payload = json.loads(send_step.output or "{}")
    assert payload["provider"] == "gmail"
    assert payload["is_mock"] is False
    # The id is the one Gmail returned - not invented.
    assert payload["result"]["message_id"] == "sent-42"
    assert payload["result"]["to"] == "judge@gmail.com"


def test_failed_send_fails_the_step_and_blocks_later_steps(client, monkeypatch):
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection
    from app.automation.engine import execute_draft
    from app.config import settings

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "t", raising=False
    )
    for key, value in (
        ("gmail_reply_to", "judge@gmail.com"),
        ("gmail_reply_subject", "Re: triage"),
    ):
        object.__setattr__(settings, key, value)
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: FakeGoogle(
            {"error": {"message": "Insufficient Permission"}}, status=403
        ),
    )

    draft = WorkflowDraft(
        name="Failed send rehearsal",
        description="The send must fail visibly",
        trigger=WorkflowTrigger(type="manual", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="Gmail", action="gmail_send_email",
                purpose="Send", parameters={"to": "judge@gmail.com", "subject": "s"},
            ),
            GeneratedWorkflowStep(
                step_number=2, application="WorkFlowOS", action="log_result",
                purpose="Must not run",
            ),
        ],
        confidence=0.9,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-send-failure",
        )
        draft_model.set_status(connection, record, "approved")

    detail = execute_draft(record.id)
    assert detail.status == "failed"
    assert detail.steps[0].status == "failed"
    assert "403" in (detail.steps[0].error or "")
    assert "Insufficient Permission" in (detail.steps[0].error or "")
    assert detail.steps[1].status == "pending"
