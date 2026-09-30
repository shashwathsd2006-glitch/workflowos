"""Shared Google OAuth 2.0 plumbing for Gmail and Google Calendar.

Both Google integrations use the same authorization-code flow against Google's
official OAuth endpoints and the same token storage, so the flow lives here
once. HTTP is done with ``httpx`` against documented Google REST APIs — no
scraping, and no new heavy dependency.
"""

from __future__ import annotations

import logging
import secrets
import threading
from urllib.parse import urlencode
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.database import get_connection
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    NotConfiguredError,
)
from app.integrations.credentials import CredentialError, decrypt_token, encrypt_token
from app.models import integration as integration_model

logger = logging.getLogger("workflowos.integrations.google")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Serialises access-token refreshes across every thread and provider, because
#: a Google refresh token can be single-use.
_REFRESH_LOCK = threading.Lock()
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

#: Where the browser lands after the user approves. This is a top-level
#: navigation from Google, not an XHR, so it targets the BACKEND, not the
#: frontend. It must be registered verbatim in Google Cloud Console.
BACKEND_CALLBACK_PATH = "/api/integrations/gmail/callback"
DEFAULT_REDIRECT_URI = f"http://localhost:8000{BACKEND_CALLBACK_PATH}"

#: Where the user is sent once the backend has handled the callback.
FRONTEND_CALLBACK_PATH = "/settings"
DEFAULT_FRONTEND_URL = "http://localhost:3000"


def redirect_uri() -> str:
    """The exact redirect URI sent to Google. Never guessed at call sites."""
    return settings.google_redirect_uri or DEFAULT_REDIRECT_URI


def frontend_url() -> str:
    return settings.frontend_url or DEFAULT_FRONTEND_URL


class OAuthStateError(Exception):
    """The ``state`` parameter did not match the value we issued.

    Covers a tampered link, a replayed callback and a stale browser tab.
    """


#: Short-lived issued OAuth states, so a callback can only be completed by the
#: same browser session that started the flow. Process-local by design: a
#: restart simply asks the user to click Connect again.
_OAUTH_STATES: "OrderedDict[str, float]" = OrderedDict()
_STATE_TTL_SECONDS = 600.0
_MAX_STATES = 32


def issue_state() -> str:
    """Create and remember a one-time OAuth state nonce."""
    _prune_states()
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = time.monotonic()
    return state


def consume_state(state: Optional[str]) -> None:
    """Validate and burn an OAuth state. Raises ``OAuthStateError``."""
    _prune_states()
    if not state:
        raise OAuthStateError("Missing OAuth state parameter")
    issued = _OAUTH_STATES.pop(state, None)
    if issued is None:
        raise OAuthStateError("OAuth state is invalid or has already been used")
    if time.monotonic() - issued > _STATE_TTL_SECONDS:
        raise OAuthStateError("OAuth state has expired — please try again")


def _prune_states() -> None:
    now = time.monotonic()
    for key in [k for k, v in _OAUTH_STATES.items() if now - v > _STATE_TTL_SECONDS]:
        _OAUTH_STATES.pop(key, None)
    while len(_OAUTH_STATES) > _MAX_STATES:
        _OAUTH_STATES.popitem(last=False)


def _google_error_detail(response) -> str:
    """Best-effort extraction of Google's human-readable error message."""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        payload = None
    detail = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "").strip()
        elif isinstance(error, str):
            detail = error.strip()
    if not detail:
        detail = (getattr(response, "text", "") or "").strip()[:200]
    return detail or "no further detail returned by Google"


class GoogleOAuthMixin:
    """Token lifecycle shared by Gmail and Calendar."""

    provider_name = "google"
    default_scopes: tuple[str, ...] = ()

    # ------------------------------------------------------------ helpers

    def _client_id(self) -> str:
        if not settings.google_client_id:
            raise NotConfiguredError(self.label)
        return settings.google_client_id

    def _client_secret(self) -> str:
        if not settings.google_client_secret:
            raise NotConfiguredError(self.label)
        return settings.google_client_secret

    def redirect_uri(self) -> str:
        return redirect_uri()

    def is_configured(self) -> bool:
        return settings.google_configured

    def credential_source(self) -> str:
        return settings.google_client_source

    # -------------------------------------------------------------- oauth

    def get_authorize_url(self, state: Optional[str] = None) -> str:
        """Build the Google consent-screen URL for the browser to visit.

        ``access_type=offline`` + ``prompt=consent`` is what makes Google issue
        a refresh token, which is the only credential that survives an access
        token expiring.
        """
        if not self.is_configured():
            raise NotConfiguredError(self.label)
        params = {
            "client_id": self._client_id(),
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": " ".join(self.default_scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state or issue_state(),
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Swap an authorization code for tokens and persist them encrypted."""
        if not self.is_configured():
            raise NotConfiguredError(self.label)
        if not code:
            raise AuthenticationError("Missing authorization code")
        try:
            response = httpx.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id(),
                    "client_secret": self._client_secret(),
                    "redirect_uri": self.redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=settings.integration_request_timeout,
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(
                f"{self.label}: could not reach Google to exchange the code"
            ) from exc
        if response.status_code != 200:
            detail = ""
            try:
                detail = str(response.json().get("error_description") or "")
            except ValueError:
                detail = response.text[:200]
            raise AuthenticationError(
                f"{self.label}: authorization code was rejected by Google"
                + (f" ({detail})" if detail else f" (HTTP {response.status_code})")
            )
        tokens = response.json()
        if not tokens.get("access_token"):
            raise AuthenticationError(
                f"{self.label}: Google returned no access token"
            )
        return self._store_tokens(tokens)

    def _store_tokens(self, tokens: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt and persist a token bundle. Returns metadata only."""
        expires_in = tokens.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            ).isoformat()
        payload = {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "scope": tokens.get("scope", ""),
            "expires_at": expires_at,
            "token_type": tokens.get("token_type", "Bearer"),
        }
        encrypted = encrypt_token(payload)
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.upsert_account(
                connection,
                self.name,
                encrypted_token=encrypted,
                scopes=payload["scope"].split() if payload["scope"] else [],
                is_mock=False,
            )
        return integration_model_token_metadata(self.name)

    # ------------------------------------------------------------- tokens

    def _load_token(self) -> Optional[Dict[str, Any]]:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            row = integration_model.select_account(connection, self.name)
        if row is None or not row["encrypted_token"]:
            return None
        try:
            return decrypt_token(row["encrypted_token"])
        except CredentialError:
            logger.warning("Stored credential for %s unreadable", self.name)
            return None

    def access_token(self) -> str:
        """Return a valid access token, refreshing it when expired.

        The refresh is serialised. A Google refresh token may be single-use,
        so two threads refreshing at the same moment invalidate each other and
        one of them fails with "authorization expired and could not be
        refreshed". The scheduler's observation poll, the background worker and
        the API request handlers all reach this method, so without a lock the
        connection failed intermittently for no visible reason.
        """
        token = self._load_token()
        if not token or not token.get("access_token"):
            raise AuthenticationError(
                f"{self.label} authentication required. "
                f"Connect {self.label} in Settings."
            )
        if not self._is_expired(token):
            return str(token["access_token"])
        with _REFRESH_LOCK:
            # Re-read inside the lock: another thread may have refreshed the
            # stored token while this one waited, in which case there is
            # nothing left to do.
            token = self._load_token()
            if not token or not token.get("access_token"):
                raise AuthenticationError(
                    f"{self.label} authentication required. "
                    f"Connect {self.label} in Settings."
                )
            if not self._is_expired(token):
                return str(token["access_token"])
            refreshed = self._refresh(token)
            if not refreshed:
                raise AuthenticationError(
                    f"{self.label} authorization expired and could not be "
                    f"refreshed. Reconnect {self.label} in Settings."
                )
            return str(refreshed["access_token"])

    @staticmethod
    def _is_expired(token: Dict[str, Any]) -> bool:
        raw = token.get("expires_at")
        if not raw:
            return False
        try:
            expiry = datetime.fromisoformat(str(raw))
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc) + timedelta(seconds=30)

    def _refresh(self, token: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        try:
            response = httpx.post(
                TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": self._client_id(),
                    "client_secret": self._client_secret(),
                    "grant_type": "refresh_token",
                },
                timeout=settings.integration_request_timeout,
            )
        except httpx.HTTPError:
            # Network blip: retrying later may still work.
            return None
        if response.status_code in (400, 401):
            # Google says the grant is dead (revoked or invalid_grant).
            logger.warning(
                "%s refresh token rejected by Google; reconnection required",
                self.name,
            )
            return None
        if response.status_code != 200:
            return None
        fresh = response.json()
        # Google may omit the refresh token on refresh; keep the old one.
        merged = dict(token)
        merged.update({k: v for k, v in fresh.items() if v is not None})
        encrypted = encrypt_token(merged)
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.upsert_account(
                connection,
                self.name,
                encrypted_token=encrypted,
            )
        return merged

    def disconnect(self) -> None:
        """Revoke remotely when possible, then delete local credentials."""
        token = self._load_token()
        if token and token.get("access_token"):
            try:
                httpx.post(
                    REVOKE_URL,
                    data={"token": token["access_token"]},
                    timeout=settings.integration_request_timeout,
                )
            except httpx.HTTPError:
                logger.info("Remote revoke failed for %s (ignored)", self.name)
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.delete_account(connection, self.name)

    def is_connected(self) -> bool:
        token = self._load_token()
        return bool(token and token.get("access_token"))

    def _google_request(
        self, method: str, url: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """Perform an authenticated Google REST call with clear errors."""
        token = self.access_token()
        headers = {"Authorization": f"Bearer {token}"}
        headers.update(kwargs.pop("headers", {}) or {})
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                timeout=settings.integration_request_timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(
                f"Gmail API request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code in (401, 403):
            # Surface Google's own reason: "insufficient permission" and
            # "re-authorise" look identical to the user otherwise.
            reason = _google_error_detail(response)
            if response.status_code == 401:
                raise AuthenticationError(
                    f"{self.label} API request failed: HTTP 401 — {reason} "
                    f"Reconnect {self.label} in Settings to re-authorise."
                )
            raise IntegrationError(
                f"{self.label} API request failed: HTTP 403 — {reason} "
                f"The granted scopes may be insufficient; reconnect "
                f"{self.label} in Settings if a scope is missing."
            )
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(
                    (payload.get("error") or {}).get("message") or ""
                ).strip()
            except (ValueError, AttributeError):
                detail = ""
            reason = f": {detail}" if detail else ""
            raise IntegrationError(
                f"{self.label} API request failed: HTTP "
                f"{response.status_code}{reason}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError(f"{self.label}: malformed API response") from exc


def integration_model_token_metadata(provider: str) -> Dict[str, Any]:
    """Safe token metadata for a provider (never includes secrets)."""
    from app.integrations.credentials import token_metadata

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        row = integration_model.select_account(connection, provider)
    blob = row["encrypted_token"] if row else None
    meta = token_metadata(blob)
    if row is not None:
        meta["is_mock"] = bool(row["is_mock"])
        meta["account_label"] = row["account_label"]
        meta["account_email"] = row["account_email"]
        meta["last_verified"] = row["last_verified"]
    else:
        meta["is_mock"] = False
    return meta


__all__ = [
    "AUTHORIZE_URL",
    "BACKEND_CALLBACK_PATH",
    "DEFAULT_REDIRECT_URI",
    "OAuthStateError",
    "consume_state",
    "frontend_url",
    "issue_state",
    "redirect_uri",
    "FRONTEND_CALLBACK_PATH",
    "GoogleOAuthMixin",
    "TOKEN_URL",
    "frontend_callback_url",
    "integration_model_token_metadata",
]
