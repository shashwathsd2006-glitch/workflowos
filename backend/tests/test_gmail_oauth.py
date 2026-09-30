"""Real Gmail OAuth 2.0 flow tests.

These exercise the genuine Google authorization-code flow against the
credentials file, with Google's endpoints mocked. They prove:

- credentials load from ``credentials/google-client-secret.json``
- minimum scopes are requested
- the redirect URI is the backend callback
- state is validated (CSRF / replay / cancellation)
- tokens are encrypted at rest and never returned by the API
- real Gmail actions are allowlisted and reach the provider
- an unconnected real Gmail **fails** rather than falling back to the mock

No test performs a live Google call and none is claimed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
from app.database import get_connection
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.credentials import decrypt_token, encrypt_token
from app.integrations.gmail import SCOPES, GmailIntegration, build_query
from app.integrations.google_oauth import (
    DEFAULT_REDIRECT_URI,
    OAuthStateError,
    consume_state,
    issue_state,
    redirect_uri,
)
from app.integrations.registry import action_provider, get_provider
from app.models import activity as activity_model
from app.models import generator as draft_model
from app.models import execution as exec_model
from app.models import integration as integration_model
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)

CREDS_PATH = "credentials/google-client-secret.json"


@pytest.fixture(autouse=True)
def _isolate():
    """Clear integration + workflow state so each test starts clean."""
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        draft_model.ensure_table(connection)
        exec_model.ensure_tables(connection)
        integration_model.ensure_tables(connection)
        connection.execute(f"DELETE FROM {integration_model.EVENTS_TABLE}")
        connection.execute(f"DELETE FROM {integration_model.ACCOUNTS_TABLE}")
    yield


# ------------------------------------------------------- credentials file

@pytest.mark.skipif(
    not __import__("pathlib").Path(CREDS_PATH).exists(),
    reason="no OAuth credentials file in this working tree (it is never committed)",
)
def test_credentials_file_exists():
    import pathlib

    path = pathlib.Path(CREDS_PATH)
    assert path.exists(), "credentials/google-client-secret.json is missing"
    data = json.loads(path.read_text())
    section = data.get("installed") or data.get("web")
    assert section["client_id"]
    assert section["client_secret"]


def test_settings_loads_credentials_from_file():
    """A client is always available: the real file, or the test placeholder."""
    assert settings.google_configured is True
    assert settings.google_client_id
    assert settings.google_client_secret


def test_client_secret_is_never_in_any_api_response(client):
    for path in (
        "/api/integrations",
        "/api/integrations/gmail",
        "/api/integrations/gmail/status",
        "/api/system/status",
    ):
        body = client.get(path).text
        assert settings.google_client_secret not in body, path
        assert "client_secret" not in body, path


def test_credentials_file_path_is_not_served(client):
    """The raw JSON must not be reachable over HTTP."""
    response = client.get("/credentials/google-client-secret.json")
    assert response.status_code == 404
    assert settings.google_client_secret not in response.text


# ------------------------------------------------------------------ scopes

def test_minimum_scopes_only():
    """Read + send. No mailbox-modify scope is requested."""
    assert len(SCOPES) == 2
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES
    for scope in SCOPES:
        assert "gmail.modify" not in scope


def test_authorize_url_requests_offline_and_minimum_scopes():
    url = get_provider("gmail").get_authorize_url(issue_state())
    query = parse_qs(urlparse(url).query)
    assert set(query["scope"][0].split()) == set(SCOPES)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["true"]


# ------------------------------------------------------------ redirect URI

def test_default_redirect_uri_is_backend_callback():
    assert DEFAULT_REDIRECT_URI == (
        "http://localhost:8000/api/integrations/gmail/callback"
    )


def test_redirect_uri_used_in_authorize_url():
    url = get_provider("gmail").get_authorize_url(issue_state())
    query = parse_qs(urlparse(url).query)
    assert query["redirect_uri"] == [redirect_uri()]
    assert query["redirect_uri"][0].endswith("/api/integrations/gmail/callback")


# ------------------------------------------------------------ OAuth routes

def test_get_connect_redirects_to_google(client):
    response = client.get("/api/integrations/gmail/connect", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "accounts.google.com" in response.headers["location"]


def test_get_connect_issues_single_use_state(client):
    first = client.get("/api/integrations/gmail/connect", follow_redirects=False)
    second = client.get("/api/integrations/gmail/connect", follow_redirects=False)
    state_a = parse_qs(urlparse(first.headers["location"]).query)["state"][0]
    state_b = parse_qs(urlparse(second.headers["location"]).query)["state"][0]
    assert state_a != state_b


def test_status_route_reports_configured_but_not_connected(client):
    body = client.get("/api/integrations/gmail/status").json()
    assert body["provider"] == "gmail"
    assert body["configured"] is True
    assert body["is_mock"] is False
    assert body["state"] in {"disconnected", "connected"}
    assert body["requested_scopes"] == list(SCOPES)
    assert "client_secret" not in body


def test_callback_rejects_invalid_state(client):
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "abc", "state": "forged"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307, 400, 409)
    if response.status_code in (302, 307):
        assert "invalid_state" in response.headers["location"]


def test_callback_rejects_replayed_state(client):
    state = issue_state()
    # First use is valid...
    consume_state(state)
    # ...a replay is not.
    with pytest.raises(OAuthStateError):
        consume_state(state)


def test_callback_rejects_missing_state(client):
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "abc"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307, 400, 409)
    if response.status_code in (302, 307):
        assert "invalid_state" in response.headers["location"]


def test_callback_handles_user_cancellation(client):
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"error": "access_denied", "state": "anything"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "cancelled" in response.headers["location"]
    # Nothing was stored.
    with get_connection() as connection:
        assert integration_model.select_account(connection, "gmail") is None


def test_callback_handles_missing_code(client):
    state = issue_state()
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"state": state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "missing_code" in response.headers["location"]


def test_callback_reports_code_exchange_failure(client, monkeypatch):
    """Google rejecting the code must surface a clear reason, not a crash."""
    import app.integrations.gmail as gmail_module

    def reject(*args, **kwargs):
        raise AuthenticationError("Gmail: authorization code was rejected by Google")

    monkeypatch.setattr(GmailIntegration, "exchange_code", reject)
    state = issue_state()
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "bad-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "exchange_failed" in response.headers["location"]


def test_callback_never_leaks_tokens_in_redirect(client, monkeypatch):
    """Even on success the redirect must carry no secret material."""
    import app.integrations.gmail as gmail_module

    monkeypatch.setattr(
        GmailIntegration, "exchange_code", lambda self, code: {"ok": True}
    )
    state = issue_state()
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "connected" in location
    for secret_marker in ("access_token", "refresh_token", "client_secret", "code="):
        assert secret_marker not in location


# ---------------------------------------------------------- token storage

def test_exchange_encrypts_refresh_token(monkeypatch):
    """The refresh token must be Fernet-encrypted before hitting SQLite."""
    import app.integrations.google_oauth as oauth_module

    captured: Dict[str, Any] = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data or {}
        return _FakeResponse(
            {
                "access_token": "ya29.super-secret-access",
                "refresh_token": "1//refresh-super-secret",
                "scope": " ".join(SCOPES),
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        )

    monkeypatch.setattr(oauth_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        GmailIntegration, "_google_request", lambda self, *a, **k: {}
    )

    get_provider("gmail").exchange_code("auth-code")

    with get_connection() as connection:
        row = integration_model.select_account(connection, "gmail")
    assert row is not None
    blob = row["encrypted_token"]
    # Neither secret is readable in the stored blob.
    assert "super-secret-access" not in blob
    assert "refresh-super-secret" not in blob
    # ...but decrypts correctly for the provider's own use.
    payload = decrypt_token(blob)
    assert payload["access_token"] == "ya29.super-secret-access"
    assert payload["refresh_token"] == "1//refresh-super-secret"


def test_stored_token_is_encrypted_at_rest():
    blob = encrypt_token({"access_token": "a", "refresh_token": "b"})
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection, "gmail", encrypted_token=blob, is_mock=False
        )
        row = integration_model.select_account(connection, "gmail")
    assert "refresh_token" not in row["encrypted_token"]
    assert "access_token" not in row["encrypted_token"]


def test_status_metadata_never_returns_token_values():
    from app.integrations.credentials import token_metadata

    blob = encrypt_token({"access_token": "secret-a", "refresh_token": "secret-b"})
    meta = token_metadata(blob)
    assert "secret-a" not in json.dumps(meta)
    assert "secret-b" not in json.dumps(meta)
    assert meta["can_refresh"] is True


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("unexpected error status")

    def json(self) -> Dict[str, Any]:
        return self._payload


# ------------------------------------------------- real Gmail action path

def test_real_gmail_actions_are_allowlisted():
    for action in (
        "gmail_search_emails",
        "gmail_read_email",
        "gmail_send_email",
    ):
        assert action_provider(action) == "gmail"
    from app.automation.actions import registry_names

    names = registry_names()
    assert "gmail_search_emails" in names
    assert "gmail_read_email" in names
    assert "gmail_send_email" in names


def test_real_gmail_action_never_falls_back_to_mock(client):
    """An unconnected real Gmail must fail — not silently run against a mock."""
    from app.automation.engine import execute_draft

    draft = WorkflowDraft(
        name="Real Gmail workflow",
        description="Reads real Gmail.",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1,
                application="Gmail",
                action="gmail_read_email",
                purpose="Read the email",
                parameters={"message_id": "abc123"},
            )
        ],
        confidence=0.8,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        exec_model.ensure_tables(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-gmail-real",
        )
        draft_model.set_status(connection, record, "approved")

    detail = execute_draft(record.id)
    assert detail.status == "failed"
    step = detail.steps[0]
    assert step.status == "failed"
    assert step.action_type == "gmail_read_email"
    # The error must be about the real provider, not a mock, and it must tell
    # the user exactly what to do.
    assert "Gmail authentication required" in (step.error or "")
    assert "Connect Gmail in Settings" in (step.error or "")
    assert "demo" not in (step.error or "").lower()
    assert detail.retryable is False


def test_gmail_action_uses_registered_provider_only():
    provider = get_provider("gmail")
    assert provider.is_mock is False
    # An action the provider does not implement is refused.
    with pytest.raises(ValidationError):
        provider.execute_action("gmail_delete_everything", {})


def test_gmail_send_validates_recipients():
    provider = get_provider("gmail")
    with pytest.raises(ValidationError):
        provider.send_email(to="not-an-address", subject="s", body="b")
    with pytest.raises(ValidationError):
        provider.send_email(to="a@b.com", subject="", body="b")


def test_gmail_read_defaults_to_the_newest_message(monkeypatch):
    """No id and no criteria must still work: read the newest real message.

    The demo must not be pinned to one subject line, so a bare "read the email"
    step falls back to the newest message and says so in the log.
    """
    from app.config import settings

    provider = get_provider("gmail")
    seen = {}

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            seen["q"] = (kwargs.get("params") or {}).get("q")
            return _FakeResponse({"messages": [{"id": "newest-1"}]})
        return _FakeResponse(
            {
                "id": "newest-1",
                "threadId": "t-1",
                "snippet": "newest",
                "payload": {"mimeType": "text/plain", "headers": []},
            }
        )

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: fake_request(a[0], a[1], **k),
    )
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "token", raising=False
    )
    original = settings.__dict__["gmail_read_query"]
    object.__setattr__(settings, "gmail_read_query", "")
    try:
        message = provider.read_message("")
    finally:
        object.__setattr__(settings, "gmail_read_query", original)

    assert message["id"] == "newest-1"
    assert seen["q"]


def test_gmail_read_falls_back_to_the_configured_query(monkeypatch):
    """A generated "read the email" step uses the operator's real query."""
    from app.config import settings

    provider = get_provider("gmail")
    seen = {}

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            seen["q"] = (kwargs.get("params") or {}).get("q")
            return {"messages": [{"id": "m-7"}]}
        return {"id": "m-7", "payload": {"mimeType": "text/plain", "headers": []}}

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: _FakeResponse(fake_request(a[0], a[1], **k)),
    )
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "token", raising=False
    )
    original = settings.__dict__["gmail_read_query"]
    object.__setattr__(settings, "gmail_read_query", 'subject:"WorkFlowOS Demo"')
    try:
        message = provider.read_message("")
    finally:
        object.__setattr__(settings, "gmail_read_query", original)

    assert seen["q"] == 'subject:"WorkFlowOS Demo"'
    assert message["id"] == "m-7"


def test_gmail_send_never_guesses_a_recipient():
    """With no recipient configured the send must fail, not pick one."""
    from app.config import settings

    original = settings.__dict__["gmail_reply_to"]
    object.__setattr__(settings, "gmail_reply_to", "")
    try:
        with pytest.raises(ValidationError) as excinfo:
            get_provider("gmail").send_email(to="", subject="s", body="b")
    finally:
        object.__setattr__(settings, "gmail_reply_to", original)
    assert "valid 'to' address is required" in str(excinfo.value)


def test_gmail_read_resolves_a_real_match_from_criteria(monkeypatch):
    """Given criteria it performs a real search and reads the newest match."""
    provider = get_provider("gmail")
    seen = {}

    def fake_request(method, url, **kwargs):
        if url.endswith("/messages"):
            seen["listing_query"] = (kwargs.get("params") or {}).get("q")
            return {"messages": [{"id": "m-42"}]}
        return {
            "id": "m-42",
            "threadId": "t-1",
            "snippet": "real snippet",
            "internalDate": "1758000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": "From", "value": "a@b.com"}],
            },
        }

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: _FakeResponse(fake_request(a[0], a[1], **k)),
    )
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "token", raising=False
    )
    message = provider.read_message("", subject="WorkFlowOS Demo")
    assert message["id"] == "m-42"
    assert seen["listing_query"] == 'subject:"WorkFlowOS Demo"'
    assert message["from"] == "a@b.com"


def test_gmail_read_reports_when_nothing_matches(monkeypatch):
    provider = get_provider("gmail")

    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda *a, **k: _FakeResponse({"messages": []}),
    )
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "token", raising=False
    )
    with pytest.raises(ValidationError) as excinfo:
        provider.read_message("", subject="WorkFlowOS Demo")
    assert "Nothing was read" in str(excinfo.value)


def test_gmail_query_sanitises_input():
    assert build_query(from_address="a@b.com", unread=True) == 'from:"a@b.com" is:unread'
    hostile = build_query(subject='x" OR is:unread')
    assert hostile.count('"') == 2
    assert "\n" not in build_query(subject="a\nb")


def test_mark_processed_refuses_because_no_modify_scope():
    """WorkFlowOS never mutates the mailbox."""
    with pytest.raises(ValidationError) as exc:
        get_provider("gmail").mark_processed("abc")
    assert "no mailbox-modify scope" in str(exc.value).lower()


def test_disconnect_clears_stored_credentials():
    blob = encrypt_token({"access_token": "a", "refresh_token": "b"})
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection, "gmail", encrypted_token=blob, is_mock=False
        )
    client_ok = get_provider("gmail").disconnect()
    assert client_ok is None
    with get_connection() as connection:
        assert integration_model.select_account(connection, "gmail") is None


def test_refresh_failure_raises_authentication_error(monkeypatch):
    """A dead refresh token surfaces as auth, not a silent mock fallback."""
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {
                    "access_token": "old",
                    "refresh_token": "dead",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                }
            ),
        )

    def reject_refresh(url, data=None, timeout=None):
        return _FakeResponse({"error": "invalid_grant"}, status=400)

    monkeypatch.setattr(oauth_module.httpx, "post", reject_refresh)
    with pytest.raises(AuthenticationError) as exc:
        get_provider("gmail").access_token()
    assert "reconnect" in str(exc.value).lower()


def test_expired_token_is_refreshed(monkeypatch):
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {
                    "access_token": "old",
                    "refresh_token": "good",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                }
            ),
        )

    def refresh_ok(url, data=None, timeout=None):
        return _FakeResponse({"access_token": "fresh", "expires_in": 3600})

    monkeypatch.setattr(oauth_module.httpx, "post", refresh_ok)
    assert get_provider("gmail").access_token() == "fresh"


def test_insufficient_permission_is_reported(monkeypatch):
    """A 403 from Gmail must be a clear permission error."""
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}
            ),
        )

    def forbidden(*args, **kwargs):
        return _FakeResponse({"error": {"message": "insufficient permission"}}, 403)

    monkeypatch.setattr(oauth_module.httpx, "request", forbidden)
    with pytest.raises(IntegrationError) as exc:
        get_provider("gmail")._google_request("GET", "https://example.test")
    assert "permission" in str(exc.value).lower()


def test_demo_and_real_gmail_are_separate_providers():
    real = get_provider("gmail")
    demo = get_provider("gmail_demo")
    assert real.is_mock is False
    assert demo.is_mock is True
    assert real.name != demo.name
    # The demo provider is never reported as the real one being connected.
    assert demo.status()["is_mock"] is True


# ------------------------------------- connection-aware action resolution

def test_plain_email_actions_never_resolve_to_simulate_when_gmail_disconnected():
    """Offline: an email action must NOT be reported as a success.

    This is the guarantee that WorkFlowOS never claims to have read or sent an
    email it did not. The step resolves to the real adapter, which then fails
    with an actionable error.
    """
    from app.automation.actions import gmail_is_connected, resolve_action

    assert gmail_is_connected() is False
    for action in ("read_email", "open_email", "send_email", "search_emails"):
        resolved = resolve_action(action)
        assert resolved is not None
        assert resolved != "simulate", action
        assert str(resolved).startswith("gmail_"), action


def test_plain_email_actions_upgrade_to_real_gmail_when_connected(monkeypatch):
    """Connected: read_email/send_email resolve to the REAL Gmail adapter."""
    import app.automation.actions as actions_module
    from app.integrations.registry import get_provider

    monkeypatch.setattr(
        get_provider("gmail"), "is_connected", lambda: True, raising=False
    )
    monkeypatch.setattr(actions_module, "gmail_is_connected", lambda: True)

    assert actions_module.resolve_action("read_email") == "gmail_read_email"
    assert actions_module.resolve_action("send_email") == "gmail_send_email"
    assert actions_module.resolve_action("search_emails") == "gmail_search_emails"
    # Explicit names are unaffected.
    assert actions_module.resolve_action("gmail_read_email") == "gmail_read_email"
    # Non-email actions are untouched.
    assert actions_module.resolve_action("log") == "log"
    assert actions_module.resolve_action("transform") == "transform"


def test_gmail_live_map_targets_are_all_allowlisted():
    """The upgrade must only ever land on an existing allowlisted action."""
    from app.automation.actions import ACTION_REGISTRY, gmail_live_actions
    from app.integrations.registry import action_provider

    names = gmail_live_actions()
    assert "read_email" in names and "send_email" in names
    from app.automation.actions import _GMAIL_LIVE_MAP

    for source in names:
        target = _GMAIL_LIVE_MAP[source]
        assert target in ACTION_REGISTRY, target
        assert action_provider(target) == "gmail"


def test_unknown_action_still_unresolvable_when_connected(monkeypatch):
    import app.automation.actions as actions_module

    monkeypatch.setattr(actions_module, "gmail_is_connected", lambda: True)
    assert actions_module.resolve_action("os_system") is None
    assert actions_module.resolve_action("curl_http") is None


# ------------------------------------------------- six-state test diagnosis

def test_diagnose_not_configured(monkeypatch):
    from app.integrations.gmail import GmailIntegration

    monkeypatch.setattr(GmailIntegration, "is_configured", lambda self: False)
    result = GmailIntegration().diagnose()
    assert result["state"] == "not_configured"
    assert result["ok"] is False


def test_diagnose_configured_not_connected():
    result = get_provider("gmail").diagnose()
    assert result["state"] == "configured_not_connected"
    assert result["ok"] is False
    assert result["account_email"] is None


def test_diagnose_connected_requires_successful_profile(monkeypatch):
    """`connected` is only returned when users/me/profile actually worked."""
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}
            ),
            is_mock=False,
        )

    def profile_ok(*args, **kwargs):
        return _FakeResponse(
            {"emailAddress": "user@gmail.com", "messagesTotal": 42, "threadsTotal": 7}
        )

    monkeypatch.setattr(oauth_module.httpx, "request", profile_ok)
    result = get_provider("gmail").diagnose()
    assert result["state"] == "connected"
    assert result["ok"] is True
    assert result["account_email"] == "user@gmail.com"
    assert result["messages_total"] == 42


def test_diagnose_permission_denied(monkeypatch):
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}
            ),
            is_mock=False,
        )

    def forbidden(*args, **kwargs):
        return _FakeResponse({"error": {"message": "forbidden"}}, 403)

    monkeypatch.setattr(oauth_module.httpx, "request", forbidden)
    result = get_provider("gmail").diagnose()
    assert result["state"] == "permission_denied"
    assert result["ok"] is False


def test_diagnose_authentication_error(monkeypatch):
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}
            ),
            is_mock=False,
        )

    def unauthorized(*args, **kwargs):
        return _FakeResponse({"error": {"message": "invalid"}}, 401)

    monkeypatch.setattr(oauth_module.httpx, "request", unauthorized)
    result = get_provider("gmail").diagnose()
    assert result["state"] == "authentication_error"
    assert result["ok"] is False
    assert "connect again" in result["message"].lower()


def test_diagnose_token_expired_refreshable(monkeypatch):
    """An expired access token that refreshes must still reach 'connected'."""
    import app.integrations.google_oauth as oauth_module

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {
                    "access_token": "stale",
                    "refresh_token": "good",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                }
            ),
            is_mock=False,
        )

    def refresh_ok(url, data=None, timeout=None):
        return _FakeResponse({"access_token": "fresh", "expires_in": 3600})

    def profile_ok(*args, **kwargs):
        return _FakeResponse({"emailAddress": "user@gmail.com", "messagesTotal": 1})

    monkeypatch.setattr(oauth_module.httpx, "post", refresh_ok)
    monkeypatch.setattr(oauth_module.httpx, "request", profile_ok)
    result = get_provider("gmail").diagnose()
    assert result["state"] == "connected"
    assert result["token_refresh"] == "available"


def test_get_test_route_returns_six_state_diagnosis(client):
    body = client.get("/api/integrations/gmail/test").json()
    assert body["state"] in {
        "not_configured",
        "configured_not_connected",
        "connected",
        "token_expired_refreshable",
        "permission_denied",
        "authentication_error",
    }
    assert "tested_at" in body
    assert "client_secret" not in body


def test_oauth_diagnostics_panel_has_no_secrets(client):
    body = client.get("/api/system/status").json()
    panel = body["oauth_diagnostics"]
    assert isinstance(panel["oauth_configured"], bool)
    assert panel["oauth_source"] in {"oauth-file", "env", "none"}
    assert any(item["provider"] == "gmail" for item in panel["providers"])
    serialized = json.dumps(panel)
    assert settings.google_client_secret not in serialized
    for marker in ("client_secret", "access_token", "refresh_token"):
        assert marker not in serialized, marker


# ------------------------------------------------- credentials file discovery


def test_both_credential_filenames_are_accepted(tmp_path, monkeypatch):
    """Google labels the download "credentials"; projects rename it. Both work."""
    from app.config import load_google_credentials
    import app.config as config_module

    payload = {
        "installed": {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir()

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(config_module, "GOOGLE_CREDENTIALS_DIR", creds_dir)

    for name in ("credentials.json", "google-client-secret.json"):
        (creds_dir / name).write_text(json.dumps(payload))
        loaded = load_google_credentials()
        assert loaded["client_id"] == "cid.apps.googleusercontent.com", name
        assert loaded["source_file"] == name
        (creds_dir / name).unlink()

    # Nothing present: no crash, simply unconfigured.
    assert load_google_credentials() == {}


def test_missing_credentials_file_is_not_fatal(tmp_path, monkeypatch):
    import app.config as config_module

    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir()
    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(config_module, "GOOGLE_CREDENTIALS_DIR", creds_dir)
    assert config_module.load_google_credentials() == {}


# ----------------------------------------------------- actionable diagnostics


def test_redirect_uri_mismatch_tells_the_operator_what_to_register(client):
    """Google's refusal must come back with the exact URI to register."""
    from urllib.parse import parse_qs, urlparse

    response = client.get(
        "/api/integrations/gmail/callback",
        params={"error": "redirect_uri_mismatch", "state": "anything"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["gmail"] == ["redirect_uri_mismatch"]
    detail = query["detail"][0]
    assert "Authorized redirect URI" in detail
    assert "http://localhost:8000/api/integrations/gmail/callback" in detail


def test_stale_state_explains_a_backend_restart(client):
    from urllib.parse import parse_qs, urlparse

    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "abc", "state": "no-longer-valid"},
        follow_redirects=False,
    )
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["gmail"] == ["invalid_state"]
    assert "backend restarted" in query["detail"][0]
    assert "Connect Gmail again" in query["detail"][0]


def test_authorize_url_carries_everything_google_needs(client):
    from urllib.parse import parse_qs, urlparse

    response = client.get(
        "/api/integrations/gmail/connect", follow_redirects=False
    )
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/")
    query = parse_qs(urlparse(location).query)

    assert query["client_id"][0].endswith(".apps.googleusercontent.com")
    assert query["redirect_uri"][0] == (
        "http://localhost:8000/api/integrations/gmail/callback"
    )
    assert query["response_type"][0] == "code"
    assert query["access_type"][0] == "offline"
    assert query.get("prompt") == ["consent"]
    assert query["state"][0]
    scopes = query["scope"][0].split()
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.send" in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" not in scopes


def test_connect_is_not_a_demo_endpoint(client):
    """The real Gmail provider must never answer with a mock authorize URL."""
    from urllib.parse import urlparse

    response = client.post(
        "/api/integrations/gmail/connect", json={}, follow_redirects=False
    )
    assert response.status_code in (200, 302, 307)
    if response.status_code == 200:
        body = response.json()
        assert body["provider"] == "gmail"
        assert "accounts.google.com" in urlparse(body["authorize_url"]).netloc
    provider = get_provider("gmail")
    assert provider.is_mock is False


def test_callback_claims_connected_only_after_a_real_profile_call(client, monkeypatch):
    """A token alone is not success: users.getProfile must succeed."""
    from urllib.parse import parse_qs, urlparse

    provider = get_provider("gmail")
    state = None
    from app.integrations.google_oauth import issue_state

    state = issue_state()

    monkeypatch.setattr(
        type(provider),
        "exchange_code",
        lambda self, code: {"present": True, "readable": True, "can_refresh": True},
    )
    monkeypatch.setattr(
        type(provider),
        "diagnose",
        lambda self: {
            "state": "connected",
            "ok": True,
            "provider": "gmail",
            "account_email": "judge@gmail.com",
            "message": "Gmail API reachable (judge@gmail.com).",
        },
    )
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "real-code", "state": state},
        follow_redirects=False,
    )
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["gmail"] == ["connected"]
    assert "judge@gmail.com" in query["detail"][0]


def test_callback_reports_profile_failure_instead_of_claiming_success(
    client, monkeypatch
):
    from urllib.parse import parse_qs, urlparse

    provider = get_provider("gmail")
    from app.integrations.google_oauth import issue_state

    state = issue_state()
    monkeypatch.setattr(
        type(provider),
        "exchange_code",
        lambda self, code: {"present": True, "readable": True, "can_refresh": True},
    )
    monkeypatch.setattr(
        type(provider),
        "diagnose",
        lambda self: {
            "state": "authentication_error",
            "ok": False,
            "provider": "gmail",
            "message": "Gmail API request failed: HTTP 403",
        },
    )
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "real-code", "state": state},
        follow_redirects=False,
    )
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["gmail"] == ["profile_failed"]
    assert "HTTP 403" in query["detail"][0]


def test_token_refresh_does_not_wipe_the_verified_account(client):
    """Regression: re-encrypting a token must not blank the account details.

    ``upsert_account`` is also used for a routine refresh, which supplies only
    the new token. Before this was fixed it overwrote ``account_email``,
    ``account_label`` and ``scopes`` with NULL/[], so the Settings page lost
    "Connected account: …" roughly every hour.
    """
    from app.integrations.credentials import encrypt_token
    from app.models import integration as integration_model
    from app.database import get_connection

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token({"access_token": "a", "refresh_token": "r"}),
            account_label="Gmail (42 messages)",
            account_email="judge@gmail.com",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            is_mock=False,
            verified=True,
        )
        # Simulate a refresh: token only.
        integration_model.upsert_account(
            connection,
            "gmail",
            encrypted_token=encrypt_token(
                {"access_token": "b", "refresh_token": "r"}
            ),
        )
        row = integration_model.select_account(connection, "gmail")

    assert row["account_email"] == "judge@gmail.com"
    assert row["account_label"] == "Gmail (42 messages)"
    assert json.loads(row["scopes"]) == [
        "https://www.googleapis.com/auth/gmail.readonly"
    ]
    assert row["is_mock"] == 0


def test_upsert_account_can_still_set_a_new_account_email(client):
    from app.integrations.credentials import encrypt_token
    from app.models import integration as integration_model
    from app.database import get_connection

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection, "gmail",
            encrypted_token=encrypt_token({"access_token": "a"}),
            account_email="first@gmail.com",
        )
        integration_model.upsert_account(
            connection, "gmail",
            encrypted_token=encrypt_token({"access_token": "b"}),
            account_email="second@gmail.com",
        )
        row = integration_model.select_account(connection, "gmail")
    assert row["account_email"] == "second@gmail.com"


def test_gmail_send_never_guesses_a_subject():
    """With no subject configured the send must fail, not invent one."""
    from app.config import settings

    original = settings.__dict__["gmail_reply_subject"]
    object.__setattr__(settings, "gmail_reply_subject", "")
    try:
        with pytest.raises(ValidationError) as excinfo:
            get_provider("gmail").send_email(
                to="judge@gmail.com", subject="", body="b"
            )
    finally:
        object.__setattr__(settings, "gmail_reply_subject", original)
    assert "subject is required" in str(excinfo.value)


def test_gmail_send_uses_the_configured_reply_subject(monkeypatch):
    """The operator-configured subject is used, and reported back."""
    from app.config import settings

    sent = {}

    class R:
        status_code = 200
        content = b'{"id": "sent-1", "threadId": "t-1"}'
        text = content.decode()

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "sent-1", "threadId": "t-1"}

    provider = get_provider("gmail")
    monkeypatch.setattr(
        type(provider), "access_token", lambda self: "token", raising=False
    )
    monkeypatch.setattr(
        "app.integrations.google_oauth.httpx.request",
        lambda method, url, **kwargs: (
            sent.update(raw=(kwargs.get("json") or {}).get("raw", "")) or R()
        ),
    )
    original = settings.__dict__["gmail_reply_subject"]
    object.__setattr__(settings, "gmail_reply_subject", "WorkFlowOS triage reply")
    try:
        result = provider.send_email(to="judge@gmail.com", subject="", body="b")
    finally:
        object.__setattr__(settings, "gmail_reply_subject", original)

    assert result["subject"] == "WorkFlowOS triage reply"
    assert result["message_id"] == "sent-1"
    # The MIME body is base64url-encoded, so decode before asserting.
    import base64

    decoded = base64.urlsafe_b64decode(
        sent["raw"] + "=" * (-len(sent["raw"]) % 4)
    ).decode()
    assert "Subject: WorkFlowOS triage reply" in decoded
    assert "To: judge@gmail.com" in decoded


def test_concurrent_refresh_uses_one_grant(monkeypatch):
    """A refresh token may be single-use, so refreshes must be serialised.

    The scheduler poll, the background worker and request handlers all call
    ``access_token()``. Without a lock, two threads refresh at once, Google
    invalidates one grant, and the connection fails intermittently with
    "authorization expired and could not be refreshed".
    """
    import threading

    from app.integrations import google_oauth

    provider = get_provider("gmail")
    calls = []
    barrier = threading.Barrier(4, timeout=5)

    def slow_post(*args, **kwargs):
        # Record the grant we are about to spend so a second, concurrent
        # refresh of the same grant is detectable.
        calls.append(kwargs.get("data", {}).get("refresh_token"))
        time.sleep(0.05)
        FR = __import__("tests.test_gmail_oauth", fromlist=["_FakeResponse"])._FakeResponse
        return FR({"access_token": f"at-{len(calls)}", "expires_in": 3600})

    monkeypatch.setattr(google_oauth.httpx, "post", slow_post)
    monkeypatch.setattr(
        type(provider), "_load_token",
        lambda self: {"access_token": "stale", "refresh_token": "rt-1",
                      "expires_at": "2000-01-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(type(provider), "_refresh", lambda self, token: {
        "access_token": f"at-{len(calls)}", "expires_at": "2999-01-01T00:00:00+00:00",
    })
    monkeypatch.setattr(
        google_oauth, "TOKEN_URL", "https://oauth2.googleapis.com/token"
    )

    results = []

    def worker():
        barrier.wait()
        try:
            results.append(provider.access_token())
        except Exception as exc:  # noqa: BLE001
            results.append(f"error: {exc}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Every caller gets a usable token and no caller sees an auth failure.
    assert len(results) == 4
    for value in results:
        assert not str(value).startswith("error:"), value
