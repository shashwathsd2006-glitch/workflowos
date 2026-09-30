"""Integration API: connection status, OAuth handshake and connection tests.

Status is always measured, never assumed. A provider with no credentials in
the environment reports ``not_configured`` and the UI says exactly that — it
is never presented as connected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.config import demo_read_query
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.credentials import CredentialError
from app.integrations.demo import DemoIntegration
from app.integrations.google_oauth import (
    OAuthStateError,
    consume_state,
    frontend_url,
    issue_state,
)
from app.integrations.registry import (
    action_provider,
    get_provider,
    integration_actions,
    provider_names,
)
from app.models import integration as integration_model
from app.database import get_connection

logger = logging.getLogger("workflowos.integrations")

router = APIRouter(prefix="/integrations", tags=["integrations"])


class OAuthCallback(BaseModel):
    code: str = Field(min_length=1)
    state: Optional[str] = None


class ConnectRequest(BaseModel):
    """Label an OAuth-less (demo) connection, or pass a code directly."""

    code: Optional[str] = None
    state: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=120)


def _all_statuses() -> List[Dict[str, Any]]:
    """Status of every provider that is real in the current configuration.

    Mock stand-ins are test infrastructure: when ``DEMO_MODE`` is off they are
    omitted entirely, so the product never presents a "Gmail (demo)" account as
    if it were a mailbox. They can only appear when an operator has explicitly
    enabled demo mode, and are always labelled there.
    """
    from app.config import settings

    statuses = []
    for name in provider_names():
        try:
            if not settings.demo_mode and get_provider(name).is_mock:
                continue
        except Exception:  # noqa: BLE001 - fall through to the status call
            pass
        try:
            statuses.append(get_provider(name).status())
        except Exception as exc:  # noqa: BLE001 - one bad provider must not 500
            logger.exception("Status failed for %s", name)
            statuses.append(
                {
                    "provider": name,
                    "label": name,
                    "connected": False,
                    "configured": False,
                    "state": "error",
                    "is_mock": False,
                    "message": f"Status unavailable: {exc}",
                }
            )
    return statuses


@router.get("")
@router.get("/", include_in_schema=False)
def list_integrations() -> Dict[str, Any]:
    """Return every known provider with its measured status."""
    statuses = _all_statuses()
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        event_counts: Dict[str, int] = {}
        for item in statuses:
            event_counts[item["provider"]] = integration_model.count_events(
                connection, item["provider"]
            )
    for item in statuses:
        item["events_seen"] = event_counts.get(item["provider"], 0)
    return {
        "integrations": statuses,
        "count": len(statuses),
        "actions": [
            action
            for action in integration_actions()
            if not action.endswith("_demo")
        ],
    }


@router.get("/actions")
def list_actions() -> Dict[str, Any]:
    """Expose the action allowlist so the UI can show what is runnable.

    Mock actions are omitted unless demo mode is explicitly enabled, so the
    product never advertises a simulated action as runnable.
    """
    from app.config import settings

    actions = []
    for action in integration_actions():
        provider = action_provider(action)
        is_mock = (provider or "").endswith("_demo")
        if is_mock and not settings.demo_mode:
            continue
        actions.append(
            {"action": action, "provider": provider, "is_mock": is_mock}
        )
    return {"actions": actions, "count": len(actions)}


@router.get("/{provider}")
def get_integration(provider: str) -> Dict[str, Any]:
    """Status for one provider."""
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return instance.status()


@router.post("/{provider}/connect")
def connect(provider: str, payload: Optional[ConnectRequest] = None) -> Dict[str, Any]:
    """Begin (or complete) a connection.

    Demo providers activate locally. Real providers return an ``authorize_url``
    for the browser to visit, or exchange a code when one is supplied.
    """
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    body = payload or ConnectRequest()
    if isinstance(instance, DemoIntegration):
        if body.code:
            raise HTTPException(
                status_code=422,
                detail="Demo integrations connect locally and take no OAuth code.",
            )
        try:
            return instance.connect(label=body.label)
        except NotConfiguredError as exc:
            # DEMO_MODE is off: say so plainly instead of failing with a 500.
            raise HTTPException(status_code=409, detail=str(exc))

    try:
        if body.code:
            metadata = instance.exchange_code(body.code)
            return {"provider": provider, "connected": True, "token": metadata}
        authorize_url = instance.get_authorize_url(body.state)
        return {
            "provider": provider,
            "connected": False,
            "authorize_url": authorize_url,
            "message": "Open authorize_url, then post the code back here.",
        }
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except CredentialError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ------------------------------------------------------- Gmail OAuth (browser)


def _settings_redirect(reason: str, detail: str) -> RedirectResponse:
    """Send the user back to Settings with a human-readable reason.

    The token/secret is never placed in the URL — only a short reason code and
    a plain-language message.
    """
    from urllib.parse import urlencode

    return RedirectResponse(
        f"{frontend_url()}/settings?{urlencode({'gmail': reason, 'detail': detail})}"
    )


@router.get("/{provider}/connect")
def connect_redirect(provider: str):
    """Start the OAuth flow by redirecting to Google's consent screen.

    Browser-facing counterpart of ``POST /{provider}/connect``; the callback
    lands on the backend, which then redirects to the Settings page.
    """
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(instance, DemoIntegration):
        return _settings_redirect(
            "demo_only",
            f"{provider} demo integrations connect locally and use no OAuth.",
        )
    try:
        return RedirectResponse(instance.get_authorize_url(issue_state()))
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Complete the OAuth flow: validate state, swap the code, store tokens."""
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # 1. The user pressed Cancel, or Google refused the request outright.
    if error:
        if error == "access_denied":
            return _settings_redirect(
                "cancelled",
                "Authorization was cancelled — nothing was connected.",
            )
        if error in {"redirect_uri_mismatch", "invalid_request"}:
            expected = instance.redirect_uri()
            logger.error(
                "Gmail OAuth refused by Google: %s (expected redirect_uri=%s)",
                error,
                expected,
            )
            return _settings_redirect(
                "redirect_uri_mismatch",
                f"Google rejected the request ({error}). Register this exact "
                f"string as an Authorized redirect URI in the Google Cloud "
                f"Console: {expected}",
            )
        return _settings_redirect(
            "denied", f"Google returned an error: {error}"
        )

    # 2. CSRF / replay / stale-tab protection.
    try:
        consume_state(state)
    except OAuthStateError as exc:
        # A restart between clicking Connect and finishing consent loses the
        # in-memory nonce, which looks identical to a CSRF failure. Say so,
        # because the fix is simply to press the button again.
        logger.warning("Gmail OAuth state rejected: %s", exc)
        return _settings_redirect(
            "invalid_state",
            f"{exc} This also happens if the backend restarted while the "
            "consent screen was open — press Connect Gmail again.",
        )

    # 3. No code arrived.
    if not code:
        return _settings_redirect(
            "missing_code", "Google did not return an authorization code."
        )

    # 4. Swap the code for tokens and persist them encrypted.
    try:
        instance.exchange_code(code)
    except NotConfiguredError as exc:
        return _settings_redirect("not_configured", str(exc))
    except AuthenticationError as exc:
        return _settings_redirect("exchange_failed", str(exc))
    except IntegrationError as exc:
        return _settings_redirect("unavailable", str(exc))
    except CredentialError as exc:
        logger.exception("Credential storage failed")
        return _settings_redirect("storage_failed", str(exc))

    # 5. Verify with a real Gmail API call before claiming success. A token was
    #    issued, but only users.getProfile returning the address proves the
    #    grant actually works, so that is the only path to "connected".
    diagnosis = instance.diagnose()
    if not diagnosis.get("ok"):
        logger.error(
            "%s OAuth completed but the Gmail profile call failed: %s",
            provider,
            diagnosis.get("message"),
        )
        return _settings_redirect(
            "profile_failed",
            f"Authorised, but the Gmail API call failed: "
            f"{diagnosis.get('message')}",
        )

    account = diagnosis.get("account_email") or "unknown"
    logger.info(
        "%s OAuth connected, verified via Gmail profile API: %s",
        provider,
        account,
    )
    return _settings_redirect(
        "connected", f"{provider} connected as {account}."
    )


@router.get("/{provider}/status")
def integration_status(provider: str) -> Dict[str, Any]:
    """Browser-friendly status for one provider (same payload as GET /{provider})."""
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return instance.status()


@router.post("/{provider}/disconnect")
def disconnect(provider: str) -> Dict[str, Any]:
    """Revoke and delete stored credentials."""
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    instance.disconnect()
    return {"provider": provider, "connected": False, "disconnected": True}


@router.post("/{provider}/test")
def test_connection(provider: str) -> Dict[str, Any]:
    """Prove the link works with a cheap authenticated call."""
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        return instance.test_connection()
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AuthenticationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/{provider}/test")
def test_connection_get(provider: str) -> Dict[str, Any]:
    """Browser-friendly connection test (mirrors POST /{provider}/test).

    Returns a six-state diagnosis so the UI never has to guess.
    """
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    diagnose = getattr(instance, "diagnose", None)
    if callable(diagnose):
        result = diagnose()
    else:  # demo providers expose test_connection only
        try:
            result = instance.test_connection()
        except NotConfiguredError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    if hasattr(instance, "is_connected"):
        result["connected"] = bool(instance.is_connected())
    result.setdefault("tested_at", datetime.now(timezone.utc).isoformat())
    return result


@router.get("/{provider}/events")
def list_provider_events(provider: str, limit: int = 20) -> Dict[str, Any]:
    """Recent external events seen for a provider (idempotency ledger)."""
    try:
        get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        rows = integration_model.select_events(
            connection, provider=provider, limit=max(1, min(limit, 100))
        )
    events = [integration_model.row_to_event(row) for row in rows]
    return {
        "provider": provider,
        "events": events,
        "count": len(events),
    }


__all__ = ["router"]


@router.get("/{provider}/messages")
def list_recent_messages(
    provider: str,
    limit: int = 10,
    include_body: bool = False,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """List recent messages from the authenticated account's real mailbox.

    Powers the "Select Email" list in the UI so a run can be pointed at an
    actual message the user chooses. Every field comes from the Gmail API; no
    message is ever synthesised, and no token or secret is returned.
    """
    try:
        instance = get_provider(provider)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not instance.is_configured():
        raise HTTPException(
            status_code=409,
            detail=f"{instance.label} is not configured on this server.",
        )
    if not instance.is_connected():
        raise HTTPException(
            status_code=409,
            detail=(
                f"{instance.label} authentication required. "
                f"Connect {instance.label} in Settings."
            ),
        )

    try:
        messages = instance.search_emails(
            query=query or None,
            max_results=max(1, min(int(limit), 25)),
            include_body=bool(include_body),
        )
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AuthenticationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # The authenticated address, so the UI can tell an incoming message apart
    # from one WorkFlowOS sent itself.
    own_address = ""
    diagnose = getattr(instance, "diagnose", None)
    if callable(diagnose):
        try:
            own_address = str(diagnose().get("account_email") or "")
        except Exception:  # noqa: BLE001 - labelling must never fail the list
            own_address = ""

    def _address_only(value: str) -> str:
        text = (value or "").strip()
        if "<" in text and ">" in text:
            text = text[text.rfind("<") + 1 : text.rfind(">")]
        return text.strip().strip(",").strip().lower()

    own = _address_only(own_address)

    items = []
    for message in messages:
        if not message.get("id"):
            continue
        sender = message.get("from", "")
        items.append(
            {
                "message_id": message.get("id"),
                "thread_id": message.get("thread_id"),
                "sender": sender,
                "recipient": message.get("to", ""),
                "subject": message.get("subject", ""),
                "date": message.get("received_at"),
                "snippet": message.get("snippet", ""),
                "unread": bool(message.get("unread")),
                "body": message.get("body") if include_body else None,
                # True when this account sent the message, i.e. a reply
                # WorkFlowOS produced rather than genuine incoming mail.
                "is_self_sent": bool(own) and _address_only(sender) == own,
                "authenticated_account": own_address,
            }
        )
    return {
        "provider": provider,
        "messages": items,
        "count": len(items),
        "query": query or demo_read_query(),
        "include_body": bool(include_body),
    }
