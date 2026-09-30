"""Slack integration — official Slack Web API.

Uses documented endpoints (``oauth.v2.access``, ``auth.test``,
``chat.postMessage``, ``conversations.list``) over HTTPS. Credentials come
from the environment only, and tokens are stored encrypted.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.database import get_connection
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    IntegrationProvider,
    IntegrationStatus,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.credentials import (
    CredentialError,
    decrypt_token,
    encrypt_token,
    token_metadata,
)
from app.models import integration as integration_model

logger = logging.getLogger("workflowos.integrations.slack")

SLACK_API = "https://slack.com/api"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"

SCOPES = ("chat:write", "channels:read", "groups:read", "im:read")


class SlackIntegration(IntegrationProvider):
    name = "slack"
    label = "Slack"
    is_mock = False

    # ------------------------------------------------------------ helpers

    def redirect_uri(self) -> str:
        return settings.slack_redirect_uri or "http://localhost:3000/settings"

    def is_configured(self) -> bool:
        return settings.slack_configured

    def _require_config(self) -> None:
        if not self.is_configured():
            raise NotConfiguredError(self.label)

    # -------------------------------------------------------------- oauth

    def get_authorize_url(self, state: Optional[str] = None) -> str:
        self._require_config()
        from urllib.parse import urlencode
        import secrets

        params = {
            "client_id": settings.slack_client_id,
            "scope": ",".join(SCOPES),
            "redirect_uri": self.redirect_uri(),
            "state": state or secrets.token_urlsafe(16),
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> Dict[str, Any]:
        self._require_config()
        if not code:
            raise AuthenticationError("Missing authorization code")
        try:
            response = httpx.post(
                f"{SLACK_API}/oauth.v2.access",
                data={
                    "code": code,
                    "client_id": settings.slack_client_id,
                    "client_secret": settings.slack_client_secret,
                    "redirect_uri": self.redirect_uri(),
                },
                timeout=settings.integration_request_timeout,
            )
        except httpx.HTTPError as exc:
            raise IntegrationError("Slack: token exchange failed") from exc
        payload = response.json()
        if not payload.get("ok"):
            raise AuthenticationError(
                f"Slack: token exchange rejected ({payload.get('error', 'unknown')})"
            )
        return self._store(payload)

    def _store(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        authed = payload.get("authed_user") or {}
        access_token = payload.get("access_token") or authed.get("access_token")
        if not access_token:
            raise AuthenticationError("Slack did not return an access token")
        team = payload.get("team") or {}
        bundle = {
            "access_token": access_token,
            "refresh_token": payload.get("refresh_token"),
            "scope": payload.get("scope", ""),
            "team_id": team.get("id"),
            "team_name": team.get("name"),
            "bot_user_id": payload.get("bot_user_id"),
        }
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.upsert_account(
                connection,
                self.name,
                encrypted_token=encrypt_token(bundle),
                account_label=team.get("name"),
                scopes=str(bundle["scope"] or "").split(),
                is_mock=False,
            )
        return self._metadata()

    def _load_token(self) -> Optional[Dict[str, Any]]:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            row = integration_model.select_account(connection, self.name)
        if row is None or not row["encrypted_token"]:
            return None
        try:
            return decrypt_token(row["encrypted_token"])
        except CredentialError:
            return None

    def access_token(self) -> str:
        token = self._load_token()
        if not token or not token.get("access_token"):
            raise AuthenticationError("Slack is not connected")
        return str(token["access_token"])

    def is_connected(self) -> bool:
        return bool((self._load_token() or {}).get("access_token"))

    def disconnect(self) -> None:
        token = self._load_token()
        if token and token.get("access_token"):
            try:
                httpx.post(
                    f"{SLACK_API}/auth.revoke",
                    headers={
                        "Authorization": f"Bearer {token['access_token']}"
                    },
                    timeout=settings.integration_request_timeout,
                )
            except httpx.HTTPError:
                logger.info("Slack revoke failed (ignored)")
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.delete_account(connection, self.name)

    def _metadata(self) -> Dict[str, Any]:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            row = integration_model.select_account(connection, self.name)
        meta = token_metadata(row["encrypted_token"] if row else None)
        if row is not None:
            meta["is_mock"] = bool(row["is_mock"])
            meta["account_label"] = row["account_label"]
        return meta

    # -------------------------------------------------------------- status

    def status(self) -> Dict[str, Any]:
        if not self.is_configured():
            return {
                "provider": self.name,
                "label": self.label,
                "is_mock": False,
                "configured": False,
                "connected": False,
                "state": IntegrationStatus.NOT_CONFIGURED,
                "message": "Slack integration is not configured.",
                "scopes": list(SCOPES),
            }
        meta = self._metadata()
        connected = bool(meta.get("present")) and bool(meta.get("readable"))
        return {
            "provider": self.name,
            "label": self.label,
            "is_mock": False,
            "configured": True,
            "connected": connected,
            "state": (
                IntegrationStatus.CONNECTED
                if connected
                else IntegrationStatus.DISCONNECTED
            ),
            "message": (
                "Connected via Slack OAuth."
                if connected
                else "Not connected. Install the Slack app to enable messaging."
            ),
            "scopes": meta.get("scopes") or [],
            "account_label": meta.get("account_label"),
            "token": {
                key: meta.get(key) for key in ("present", "readable", "expires_at")
            },
        }

    def _api(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = self.access_token()
        try:
            response = httpx.post(
                f"{SLACK_API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload or {},
                timeout=settings.integration_request_timeout,
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Slack: {method} request failed") from exc
        if response.status_code in (401, 403):
            raise AuthenticationError(f"Slack: authorization rejected for {method}")
        try:
            body = response.json()
        except ValueError as exc:
            raise IntegrationError("Slack: malformed response") from exc
        if not body.get("ok"):
            error = body.get("error", "unknown_error")
            if error in {"invalid_auth", "token_revoked", "account_inactive"}:
                raise AuthenticationError(f"Slack: {error}")
            raise IntegrationError(f"Slack: {error}")
        return body

    def test_connection(self) -> Dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError(self.label)
        if not self.is_connected():
            return {
                "ok": False,
                "provider": self.name,
                "message": "Not connected — install the Slack app first.",
            }
        body = self._api("auth.test")
        return {
            "ok": True,
            "provider": self.name,
            "message": "Slack API reachable.",
            "team": body.get("team"),
            "user": body.get("user"),
        }

    # ------------------------------------------------------------- actions

    def send_message(
        self, *, channel: str, text: str, thread_ts: Optional[str] = None
    ) -> Dict[str, Any]:
        """Post a message to a channel."""
        target = (channel or "").strip()
        if not target:
            raise ValidationError("A Slack channel is required")
        if not (text or "").strip():
            raise ValidationError("Message text is required")
        payload: Dict[str, Any] = {"channel": target, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        body = self._api("chat.postMessage", payload)
        return {
            "ok": True,
            "channel": body.get("channel", target),
            "ts": body.get("ts"),
            "message": body.get("message", {}).get("text", text),
        }

    def list_channels(self, limit: int = 20) -> List[Dict[str, Any]]:
        body = self._api("conversations.list", {"limit": max(1, min(limit, 100))})
        return [
            {
                "id": channel.get("id"),
                "name": channel.get("name"),
                "is_private": bool(channel.get("is_private")),
            }
            for channel in body.get("channels", [])
        ]

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action == "slack_send_message":
            return self.send_message(
                channel=str(payload.get("channel") or ""),
                text=str(payload.get("text") or ""),
                thread_ts=payload.get("thread_ts"),
            )
        if action == "slack_list_channels":
            return {"ok": True, "channels": self.list_channels()}
        raise ValidationError(f"Slack does not support action '{action}'")


__all__ = ["SCOPES", "SlackIntegration"]
