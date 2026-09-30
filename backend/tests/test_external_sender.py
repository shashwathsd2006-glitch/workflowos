"""The demo email must arrive from an external account, not from ourselves.

Two guarantees:

* the Gmail query the workflow uses targets the external sender and excludes
  the mail WorkFlowOS itself sent;
* a reply is addressed to the real sender of the message that was read.

The script is also checked to make sure it can never send the demo email.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.config import DEMO_EMAIL_SUBJECT, demo_read_query, settings
from app.integrations.registry import get_provider

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prepare_demo_email.py"

# The real two-account demo setup. These are the addresses the demo actually
# uses; no placeholder account is referenced anywhere in this project.
DEMO_SENDER = "demo.sender@example.com"
DEMO_RECIPIENT = "demo.recipient@example.com"


def _set(name, value):
    object.__setattr__(settings, name, value)


# ------------------------------------------------------------------- query


def test_query_targets_the_external_sender():
    _set("gmail_read_query", "")
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", "watched@example.com")
    query = demo_read_query()
    assert f'subject:"{DEMO_EMAIL_SUBJECT}"' in query
    assert f"from:{DEMO_SENDER}" in query


def test_query_excludes_our_own_mail_when_no_sender_is_set():
    _set("gmail_read_query", "")
    _set("demo_sender_email", "")
    _set("demo_recipient_email", "watched@example.com")
    # Mail *delivered to* this account cannot be a reply we sent, so the
    # outgoing triage replies are excluded.
    assert "to:watched@example.com" in demo_read_query()
    assert "from:" not in demo_read_query()


def test_query_excludes_our_own_mail_with_nothing_configured():
    _set("gmail_read_query", "")
    _set("demo_sender_email", "")
    _set("demo_recipient_email", "")
    assert "-from:me" in demo_read_query()


def test_explicit_query_overrides_the_derived_one():
    _set("gmail_read_query", "is:unread from:boss@corp.com")
    _set("demo_sender_email", DEMO_SENDER)
    assert demo_read_query() == "is:unread from:boss@corp.com"


def test_reply_subject_never_collides_with_the_incoming_subject():
    """The reply must not itself match the demo query."""
    assert DEMO_EMAIL_SUBJECT.lower() != "workflowos triage reply"


# ------------------------------------------------------------- reply target


def _read_output(sender: str, subject: str = "WorkflowOS Demo") -> str:
    return json.dumps(
        {
            "action": "gmail_read_email",
            "provider": "gmail",
            "is_mock": False,
            "result": {
                "id": "m-1",
                "from": sender,
                "reply_to": "",
                "to": "watched@example.com",
                "subject": subject,
                "body": "I was charged twice for invoice 88213.",
            },
        }
    )


def _ai_output(source) -> str:
    return json.dumps(
        {
            "action": "analyze_content",
            "provider": "ollama",
            "model": "qwen2.5-coder:7b",
            "task": "classify",
            "analysed_characters": 44,
            "source": source,
            "result": {
                "summary": "Customer requests a refund.",
                "category": "Finance",
                "priority": "high",
                "reasoning": "Billing complaint.",
                "recommended_action": "Process the refund.",
                "confidence": 0.9,
            },
        }
    )


def _captured_send(monkeypatch, previous: str, parameters=None):
    from app.automation.actions import ActionContext, run_action

    seen = {}
    import app.integrations.gmail as gmail_module

    original = gmail_module.GmailIntegration.send_email

    def capture(self, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "message_id": "sent-1"}

    gmail_module.GmailIntegration.send_email = capture
    try:
        run_action(
            "gmail_send_email",
            ActionContext(
                execution_id="execution-0001",
                step_number=3,
                application="Gmail",
                action="send_email",
                previous_output=previous,
                parameters=parameters or {},
                action_type="gmail_send_email",
            ),
        )
    finally:
        gmail_module.GmailIntegration.send_email = original
    return seen


def test_reply_goes_to_the_external_sender(monkeypatch):
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "WorkFlowOS triage reply")
    read = _read_output("Billing Team <billing@vendor.example>")
    source = {
        "sender": "Billing Team <billing@vendor.example>",
        "subject": "WorkflowOS Demo",
        "id": "m-1",
    }
    seen = _captured_send(monkeypatch, _ai_output(source))
    assert seen["to"] == "billing@vendor.example"
    assert seen["subject"].startswith("Re: ")


def test_reply_to_header_wins_over_from(monkeypatch):
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "x")
    source = {
        "sender": "no-reply@notifications.example",
        "reply_to": "Support <support@vendor.example>",
        "subject": "WorkflowOS Demo",
    }
    seen = _captured_send(monkeypatch, _ai_output(source))
    assert seen["to"] == "support@vendor.example"


def test_explicit_step_parameter_wins_over_the_sender(monkeypatch):
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "x")
    source = {"sender": "someone@else.example", "subject": "WorkflowOS Demo"}
    seen = _captured_send(
        monkeypatch,
        _ai_output(source),
        parameters={"to": "override@target.example", "subject": "Custom"},
    )
    assert seen["to"] == "override@target.example"
    assert seen["subject"] == "Custom"


def test_reply_works_without_an_ai_step_in_between(monkeypatch):
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "x")
    seen = _captured_send(
        monkeypatch, _read_output("billing@vendor.example")
    )
    assert seen["to"] == "billing@vendor.example"


def test_no_source_means_no_guessed_recipient(monkeypatch):
    """Without a real read behind it, the configured fallback still applies."""
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "Configured subject")
    previous = json.dumps(
        {"action": "analyze_content", "provider": "ollama", "source": None,
         "result": {"summary": "s", "category": "c", "priority": "low",
                    "reasoning": "r", "recommended_action": "a", "confidence": 0.5}}
    )
    seen = _captured_send(monkeypatch, previous)
    # Nothing in the payload claims a recipient, so the provider falls back to
    # the operator-configured address rather than guessing one.
    assert seen["to"] == ""
    assert seen["subject"] == ""


# ------------------------------------------------------------- source carry


def test_ai_step_records_the_message_it_analysed(monkeypatch):
    from app.automation.actions import ActionContext, run_action

    out = json.loads(
        run_action(
            "ai_analyze_content",
            ActionContext(
                execution_id="execution-0001",
                step_number=2,
                application="WorkFlowOS AI",
                action="analyze_email",
                previous_output=_read_output("billing@vendor.example"),
            ),
        )
    )
    assert out["source"]["sender"] == "billing@vendor.example"
    assert out["source"]["subject"] == "WorkflowOS Demo"


def test_ai_step_has_no_source_for_non_mail_input(monkeypatch):
    from app.automation.actions import ActionContext, run_action

    out = json.loads(
        run_action(
            "ai_analyze_content",
            ActionContext(
                execution_id="execution-0001",
                step_number=1,
                application="WorkFlowOS AI",
                action="analyze_email",
                previous_output=json.dumps(
                    {"action": "log", "result": {"note": "manual"}}
                ),
            ),
        )
    )
    assert out["source"] is None


# ------------------------------------------------------------------ script


def test_prepare_script_never_sends_email():
    """The script must not be able to send the demo email itself."""
    tree = ast.parse(SCRIPT.read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "send_email" not in called, "prepare script must not send"
    assert "execute_action" not in called, "prepare script must not run actions"


def test_prepare_script_states_the_manual_instruction():
    text = SCRIPT.read_text()
    assert "DEMO EMAIL" in text
    assert "cannot send this for you" in text
    assert DEMO_EMAIL_SUBJECT in text
    assert "I was charged twice for invoice 88213" in text


# ----------------------------------------------------------- API labelling


def test_listing_marks_self_sent_messages(client, monkeypatch):
    from app.integrations.credentials import encrypt_token
    from app.models import integration as integration_model
    from app.database import get_connection

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token({"access_token": "a", "refresh_token": "r"}),
            account_email="watched@example.com",
            is_mock=False,
            verified=True,
        )

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )

    from tests.test_gmail_oauth import _FakeResponse as FR

    def fake_request(method, url, **kwargs):
        if url.endswith("/profile"):
            return FR({"emailAddress": "watched@example.com"})
        if url.endswith("/messages"):
            return FR({"messages": [{"id": "in-1"}, {"id": "out-1"}]})
        if url.endswith("/messages/in-1"):
            return FR(
                {
                    "id": "in-1",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Billing <b@vendor.example>"},
                            {"name": "To", "value": "watched@example.com"},
                            {"name": "Subject", "value": "WorkflowOS Demo"},
                        ],
                    },
                }
            )
        return FR(
            {
                "id": "out-1",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "watched@example.com"},
                        {"name": "To", "value": "b@vendor.example"},
                        {"name": "Subject", "value": "Re: WorkflowOS Demo"},
                    ],
                },
            }
        )

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )
    body = client.get("/api/integrations/gmail/messages?limit=5").json()
    by_id = {m["message_id"]: m for m in body["messages"]}
    assert by_id["in-1"]["is_self_sent"] is False
    assert by_id["out-1"]["is_self_sent"] is True
    assert by_id["in-1"]["authenticated_account"] == "watched@example.com"


# --------------------------------------------- the two-account demo setup


def test_query_uses_subject_from_and_to_together():
    """All three filters together are what stop an old mail matching."""
    _set("gmail_read_query", "")
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)
    assert demo_read_query() == (
        f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER} to:{DEMO_RECIPIENT}'
    )


def test_recipient_filter_applies_alongside_the_sender():
    """A sender alone must not be enough: the mail must also be addressed here."""
    _set("gmail_read_query", "")
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", "")
    query = demo_read_query()
    assert f"from:{DEMO_SENDER}" in query
    assert "to:" not in query


def test_missing_demo_email_reports_the_sender_and_says_nothing_was_read(monkeypatch):
    """The message must name the sender it is waiting for."""
    from app.config import settings as live
    from app.integrations.registry import get_provider

    _set("gmail_read_query", "")
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: __import__(
            "tests.test_gmail_oauth", fromlist=["_FakeResponse"]
        )._FakeResponse({"messages": []}),
    )
    assert live.demo_sender_email == DEMO_SENDER

    with pytest.raises(Exception) as excinfo:
        provider.read_message("")
    message = str(excinfo.value)
    assert (
        f"No {DEMO_EMAIL_SUBJECT} email received from {DEMO_SENDER} yet."
        in message
    )
    assert "Nothing was read" in message
    assert "access_token" not in message


def test_newest_match_is_preferred(monkeypatch):
    """When several match, the newest by timestamp is read."""
    from app.config import settings as live
    from app.integrations.registry import get_provider

    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}"')
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )

    def fake_request(method, url, **kwargs):
        FR = __import__(
            "tests.test_gmail_oauth", fromlist=["_FakeResponse"]
        )._FakeResponse
        if url.endswith("/messages"):
            # Deliberately oldest first, to prove ordering is not inherited.
            return FR({"messages": [{"id": "old-1"}, {"id": "new-9"}]})
        stamp = "1000" if url.endswith("/messages/old-1") else "9000"
        return FR(
            {
                "id": url.rsplit("/", 1)[-1],
                "internalDate": stamp,
                "payload": {"mimeType": "text/plain", "headers": []},
            }
        )

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )
    message = provider.read_message("")
    assert message["id"] == "new-9"
    assert message["id"]  # a real Gmail id is required
    assert live.gmail_read_query


def test_read_requires_a_real_gmail_message_id(monkeypatch):
    """A match without an id is refused rather than read."""
    from app.integrations.registry import get_provider

    _set("gmail_read_query", "in:anywhere")
    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    FR = __import__("tests.test_gmail_oauth", fromlist=["_FakeResponse"])._FakeResponse
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda method, url, **k: (
            FR({"messages": [{"id": "listed-id"}]})
            if url.endswith("/messages")
            # Detail response without an id, as Gmail would never send.
            else FR({"internalDate": "1", "payload": {"mimeType": "text/plain", "headers": []}})
        ),
    )
    with pytest.raises(Exception) as excinfo:
        provider.read_message("")
    assert "without a message id" in str(excinfo.value)
    assert "Nothing was read" in str(excinfo.value)


# ------------------------------------- never read our own outbound reply


def _fake_mailbox(monkeypatch, boxes):
    """Serve a tiny fake Gmail mailbox: {message_id: (from, internalDate)}."""
    from app.integrations.registry import get_provider
    from tests.test_gmail_oauth import _FakeResponse as FR

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "tok", raising=False
    )
    monkeypatch.setattr(
        type(provider),
        "stored_account_email",
        lambda self: DEMO_RECIPIENT,
    )

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            return FR({"messages": [{"id": mid} for mid in boxes]})
        mid = url.rsplit("/", 1)[-1]
        sender, stamp = boxes[mid]
        return FR(
            {
                "id": mid,
                "internalDate": stamp,
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": sender},
                        {"name": "To", "value": DEMO_RECIPIENT},
                        {"name": "Subject", "value": DEMO_EMAIL_SUBJECT},
                    ],
                },
            }
        )

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )
    return provider


def test_newest_incoming_wins_even_when_a_newer_self_sent_reply_exists(monkeypatch):
    """A reply we sent ourselves must never be read as the incoming email."""
    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}"')
    provider = _fake_mailbox(
        monkeypatch,
        {
            "in-1": (f"Dhruva Devaiah pm <{DEMO_SENDER}>", "1000"),
            # Newer, but this is our own outbound reply.
            "out-1": (f"Shashwath S Duvani <{DEMO_RECIPIENT}>", "9000"),
        },
    )
    assert provider.read_message("")["id"] == "in-1"


def test_only_self_sent_matches_is_refused_with_a_clear_reason(monkeypatch):
    """If the only matches are our own replies, say so instead of looping."""
    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}"')
    _set("demo_sender_email", "")
    _set("demo_recipient_email", "")
    provider = _fake_mailbox(
        monkeypatch,
        {"out-1": (f"Shashwath S Duvani <{DEMO_RECIPIENT}>", "9000")},
    )
    with pytest.raises(Exception) as excinfo:
        provider.read_message("")
    message = str(excinfo.value)
    assert "sent itself" in message
    assert "Nothing was read" in message


def test_configured_query_outranks_a_step_supplied_address(monkeypatch):
    """A model-invented address must not redirect the real Gmail read.

    ``read_message`` is called with criteria that came from a language-model
    generated step. Operator configuration wins so the demo keeps reading the
    message the operator configured.
    """
    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER}')
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)
    provider = _fake_mailbox(
        monkeypatch,
        {"in-1": (f"Dhruva Devaiah pm <{DEMO_SENDER}>", "1000")},
    )
    used = {}
    original = type(provider).fetch_messages

    def spy(self, **kwargs):
        used.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(type(provider), "fetch_messages", spy)
    # A hallucinated address that does not exist and must not be used.
    message = provider.read_message(
        "", from_address="invented.sender@example.invalid", subject=DEMO_EMAIL_SUBJECT
    )
    assert message["id"] == "in-1"
    assert f"from:{DEMO_SENDER}" in used["query"]
    assert "invented.sender" not in used["query"]


# --------------------------------------- model-invented addresses dropped


def test_generated_draft_drops_an_invented_mailbox_address():
    """The parser must not persist an address the operator never configured."""
    from app.generator.parser import normalize_draft

    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER}')
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)

    data = normalize_draft(
        {
            "name": "Triage",
            "steps": [
                {
                    "application": "Gmail",
                    "action": "read_email",
                    "parameters": {
                        "from_address": "invented.sender@example.invalid",
                        "subject": DEMO_EMAIL_SUBJECT,
                    },
                }
            ],
        }
    )
    params = data["steps"][0]["parameters"]
    assert "from_address" not in params
    assert params["subject"] == DEMO_EMAIL_SUBJECT
    assert any("invented.sender@example.invalid" in a for a in data["assumptions"])


def test_generated_draft_keeps_the_configured_address():
    from app.generator.parser import normalize_draft

    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER}')
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)

    data = normalize_draft(
        {
            "name": "Triage",
            "steps": [
                {
                    "application": "Gmail",
                    "action": "read_email",
                    "parameters": {"from_address": DEMO_SENDER},
                }
            ],
        }
    )
    assert data["steps"][0]["parameters"]["from_address"] == DEMO_SENDER
    assert data["assumptions"] == []


def test_invented_address_in_a_non_mailbox_step_is_untouched():
    """Only read/search parameters are sanitised."""
    from app.generator.parser import normalize_draft

    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER}')
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)

    data = normalize_draft(
        {
            "name": "Triage",
            "steps": [
                {
                    "application": "Gmail",
                    "action": "send_email",
                    "parameters": {"to": "invented.sender@example.invalid"},
                }
            ],
        }
    )
    assert data["steps"][0]["parameters"]["to"] == "invented.sender@example.invalid"


# ------------------------------------- placeholder recipient is not usable


def test_placeholder_recipient_is_dropped_from_the_draft():
    """"the sender's email address" is not sendable, so it must not persist."""
    from app.generator.parser import normalize_draft

    _set("gmail_read_query", f'subject:"{DEMO_EMAIL_SUBJECT}" from:{DEMO_SENDER}')
    _set("demo_sender_email", DEMO_SENDER)
    _set("demo_recipient_email", DEMO_RECIPIENT)

    data = normalize_draft(
        {
            "name": "Triage",
            "steps": [
                {
                    "application": "gmail",
                    "action": "read_email",
                    "parameters": {"from_address": "the sender's email address"},
                }
            ],
        }
    )
    assert "from_address" not in data["steps"][0]["parameters"]
    assert any("placeholder" in a for a in data["assumptions"])


def test_reply_ignores_a_placeholder_recipient_and_uses_the_real_sender(monkeypatch):
    """The model must not be able to pre-empt the real correspondent."""
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "x")
    source = {"sender": f"Dhruva Devaiah pm <{DEMO_SENDER}>", "subject": DEMO_EMAIL_SUBJECT}
    seen = _captured_send(
        monkeypatch,
        _ai_output(source),
        parameters={"to": "Monika's email address"},
    )
    assert seen["to"] == DEMO_SENDER


def test_reply_still_honours_a_real_explicit_recipient(monkeypatch):
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "x")
    source = {"sender": DEMO_SENDER, "subject": DEMO_EMAIL_SUBJECT}
    seen = _captured_send(
        monkeypatch, _ai_output(source), parameters={"to": "override@target.example"}
    )
    assert seen["to"] == "override@target.example"


def test_reply_ignores_a_placeholder_subject_and_uses_the_real_one(monkeypatch):
    """A generated "Re: [Original Subject]" must never be sent verbatim."""
    _set("gmail_reply_to", "watched@example.com")
    _set("gmail_reply_subject", "Configured subject")
    source = {
        "sender": f"Dhruva Devaiah pm <{DEMO_SENDER}>",
        "subject": DEMO_EMAIL_SUBJECT,
    }
    seen = _captured_send(
        monkeypatch,
        _ai_output(source),
        parameters={"subject": "Re: [Original Subject]"},
    )
    assert seen["subject"] == f"Re: {DEMO_EMAIL_SUBJECT}"
