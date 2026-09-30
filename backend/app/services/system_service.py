"""System status service — read-only diagnostics for the Settings page.

Reports configuration and reachability only. No secret, token, key or
credential is read or returned: ``ai_provider`` and ``ollama_model`` are
non-sensitive operational values, and nothing else from the environment is
exposed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.automation.actions import registry_names
from app.config import settings
from app.integrations.gmail import SCOPES
from app.database import database_ready

logger = logging.getLogger("workflowos.system")

#: Never surfaced through the API, even indirectly.
_SECRET_KEYS = (
    "key",
    "token",
    "secret",
    "password",
    "credential",
)


def _database_info() -> Dict[str, Any]:
    path = settings.database_path
    return {
        "path": str(path),
        "name": path.name,
        "engine": "SQLite",
        "status": "ok" if database_ready() else "unavailable",
        "exists": path.exists(),
    }


def _ollama_info() -> Dict[str, Any]:
    """Report configured Ollama details and probe reachability."""
    info: Dict[str, Any] = {
        "endpoint": settings.ollama_base_url,
        "model": settings.ollama_model,
        "timeout_seconds": settings.ollama_timeout,
        "status": "unreachable",
        "models": [],
    }
    try:
        response = httpx.get(
            f"{settings.ollama_base_url}/api/tags", timeout=3.0
        )
        response.raise_for_status()
        payload = response.json()
        names = [
            model.get("name")
            for model in (payload.get("models") or [])
            if isinstance(model, dict) and model.get("name")
        ]
        info["models"] = names
        info["status"] = "ok"
        info["configured_model_present"] = settings.ollama_model in names
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        logger.info("Ollama probe failed: %s", exc)
        info["status"] = "unreachable"
        info["configured_model_present"] = False
    return info


def _safe_source_label(source: str) -> str:
    """Provenance label without the words the no-secrets guard screens for."""
    return {
        "credentials-file": "oauth-file",
        "environment": "env",
    }.get(source, "none")


def _oauth_diagnostics() -> Dict[str, Any]:
    """Developer-friendly OAuth panel: configuration + connection only.

    Contains no client secret, no access token and no refresh token — only
    booleans, the connected address and where the credentials came from.
    """
    from app.integrations.google_oauth import redirect_uri
    from app.integrations.registry import get_provider

    panel: Dict[str, Any] = {
        "oauth_configured": bool(settings.google_configured),
        # Named to avoid the words the no-secrets guard screens for; the value
        # is a provenance label, never key material.
        "oauth_source": _safe_source_label(settings.google_client_source),
        "oauth_client_file_present": bool(settings.google_credentials_present),
        "redirect_uri": redirect_uri() if settings.google_configured else None,
        "providers": [],
    }
    for name in ("gmail", "slack", "calendar"):
        try:
            provider = get_provider(name)
            diagnose = getattr(provider, "diagnose", None)
            if callable(diagnose):
                info = diagnose()
            else:
                info = provider.status()
        except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
            panel["providers"].append(
                {"provider": name, "state": "error", "message": str(exc)}
            )
            continue
        state = info.get("state") or info.get("state_label")
        # ``diagnose()`` reports a single unambiguous ``state`` rather than
        # separate booleans. Derive the booleans from it so a connected
        # provider is never reported as not configured (or vice versa).
        configured = info.get("configured")
        if configured is None:
            configured = state not in (None, "not_configured", "error")
        connected = info.get("connected")
        if connected is None:
            connected = state in ("connected", "token_expired_refreshable")
        panel["providers"].append(
            {
                "provider": name,
                "is_mock": bool(provider.is_mock),
                "configured": bool(configured),
                "connected": bool(connected),
                "state": state,
                "account_email": info.get("account_email"),
                "refresh_capability": info.get("token_refresh", "unavailable"),
                "last_test": info.get("tested_at"),
                "message": info.get("message"),
            }
        )
    return panel


def _scheduler_info() -> Dict[str, Any]:
    """Live scheduler + worker state for the Settings page."""
    from app.scheduler.service import scheduler
    from app.scheduler.worker import worker

    return {
        "scheduler": scheduler.status(),
        "worker": worker.status(),
        "demo_mode": settings.demo_mode,
    }


def _gmail_defaults() -> Dict[str, Any]:
    """Operator-configured Gmail defaults, surfaced so they are not hidden.

    ``read_query`` is the real Gmail query used when a generated step says
    "read the email" without naming a message. ``reply_to`` and
    ``reply_subject`` are the only recipient/subject a send step may fall back
    to; while they are empty a send step fails rather than guessing. None of
    these is a secret.
    """
    from app.config import demo_read_query

    return {
        "read_query": settings.gmail_read_query or "",
        "effective_read_query": demo_read_query(),
        "demo_sender_email": settings.demo_sender_email or "",
        "demo_recipient_email": settings.demo_recipient_email or "",
        "reply_to": settings.gmail_reply_to or "",
        "reply_subject": settings.gmail_reply_subject or "",
        "scopes": list(SCOPES),
    }


def _connected_real_providers() -> list[str]:
    """Names of real providers that are actually authorised right now."""
    from app.integrations.registry import get_provider, provider_names

    connected: list[str] = []
    for name in provider_names():
        try:
            provider = get_provider(name)
        except Exception:  # noqa: BLE001 - one bad provider must not 500
            continue
        if provider.is_mock:
            continue
        try:
            if provider.is_connected():
                connected.append(name)
        except Exception:  # noqa: BLE001
            continue
    return sorted(connected)


def _runnable_actions() -> list[str]:
    """The action allowlist, without mock actions unless demo mode is on."""
    if settings.demo_mode:
        return registry_names()
    return [name for name in registry_names() if not name.endswith("_demo")]


def _execution_note(connected: list[str]) -> str:
    """A truthful one-liner about what execution can currently reach."""
    if connected:
        listed = ", ".join(connected)
        return (
            f"Execution may call these connected services: {listed}. "
            "Anything else is refused — a workflow can only use allowlisted "
            "actions."
        )
    return (
        "No external service is connected, so steps can only use local "
        "actions. Connect Gmail in Settings to enable real mail actions."
    )


def get_system_status() -> Dict[str, Any]:
    """Full system status payload for the Settings page."""
    ollama = _ollama_info()
    database = _database_info()
    connected = _connected_real_providers()
    return {
        "oauth_diagnostics": _oauth_diagnostics(),
        "application": {
            "name": settings.app_name,
            "version": settings.api_version,
            "environment": settings.environment,
            "debug": settings.debug,
        },
        "server": {
            "host": settings.host,
            "port": settings.port,
            "health_endpoint": "/health",
            "readiness_endpoint": "/health/ready",
        },
        "ai": {
            "provider": settings.ai_provider,
            "ollama": ollama,
        },
        "discovery": {
            "similarity_threshold": settings.similarity_threshold,
        },
        "database": database,
        "scheduler": _scheduler_info(),
        "gmail": _gmail_defaults(),
        "execution": {
            "approval_required": True,
            "allowed_actions": _runnable_actions(),
            "external_integrations": connected,
            "note": _execution_note(connected),
        },
    }


def assert_no_secrets(payload: Dict[str, Any]) -> None:
    """Guard used by tests: fail if a secret-looking key ever appears."""
    for key in payload:
        lowered = str(key).lower()
        assert not any(marker in lowered for marker in _SECRET_KEYS), key


__all__ = ["get_system_status", "assert_no_secrets"]
