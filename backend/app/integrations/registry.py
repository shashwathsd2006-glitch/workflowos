"""Integration provider registry.

One place maps a provider name to its implementation, and one place maps an
action name to the provider that owns it. The execution engine asks this
registry to perform an action; it never imports a vendor SDK itself.

Action-name resolution is *code-controlled*. An AI-generated step can only
ever name an action that already exists here — it cannot introduce a new one,
and an unknown name still fails the step safely.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.integrations.base import IntegrationProvider, ValidationError
from app.integrations.calendar import GoogleCalendarIntegration
from app.integrations.demo import DemoCalendar, DemoGmail, DemoSlack
from app.integrations.gmail import GmailIntegration
from app.integrations.slack import SlackIntegration

logger = logging.getLogger("workflowos.integrations.registry")

#: Real providers, keyed by their stable name.
_PROVIDERS: Dict[str, type] = {
    "gmail": GmailIntegration,
    "slack": SlackIntegration,
    "calendar": GoogleCalendarIntegration,
}

#: Local demo stand-ins, available when DEMO_MODE=true.
_DEMO_PROVIDERS: Dict[str, type] = {
    "gmail_demo": DemoGmail,
    "slack_demo": DemoSlack,
    "calendar_demo": DemoCalendar,
}

#: action prefix -> provider that owns it.
_ACTION_OWNERS: Dict[str, str] = {
    "gmail_": "gmail",
    "slack_": "slack",
    "calendar_": "calendar",
}

#: Explicit action -> provider map (the authoritative allowlist).
ACTION_PROVIDERS: Dict[str, str] = {
    "gmail_search_emails": "gmail",
    "gmail_read_email": "gmail",
    "gmail_send_email": "gmail",
    "gmail_fetch_messages": "gmail",
    "slack_send_message": "slack",
    "slack_list_channels": "slack",
    "calendar_list_events": "calendar",
    "calendar_create_event": "calendar",
    "calendar_update_event": "calendar",
    # Demo equivalents
    "gmail_demo_read_email": "gmail_demo",
    "gmail_demo_send_email": "gmail_demo",
    "gmail_demo_fetch_messages": "gmail_demo",
    "slack_demo_send_message": "slack_demo",
    "slack_demo_list_channels": "slack_demo",
    "calendar_demo_list_events": "calendar_demo",
    "calendar_demo_create_event": "calendar_demo",
    "calendar_demo_update_event": "calendar_demo",
}

_instances: Dict[str, IntegrationProvider] = {}


def provider_names() -> List[str]:
    """All known provider names, real first then demo."""
    return list(_PROVIDERS) + list(_DEMO_PROVIDERS)


def get_provider(name: str) -> IntegrationProvider:
    """Return a provider instance by name (cached)."""
    key = (name or "").strip().lower()
    if key in _instances:
        return _instances[key]
    factory = _PROVIDERS.get(key) or _DEMO_PROVIDERS.get(key)
    if factory is None:
        raise ValidationError(f"Unknown integration provider '{name}'")
    instance = factory()
    _instances[key] = instance
    return instance


def action_provider(action: str) -> Optional[str]:
    """Return the provider that owns ``action``, or None if unknown.

    An unknown action is never guessed at — the caller fails the step.
    """
    normalized = (action or "").strip().lower()
    if normalized in ACTION_PROVIDERS:
        return ACTION_PROVIDERS[normalized]
    for prefix, provider in _ACTION_OWNERS.items():
        if normalized.startswith(prefix):
            return provider
    return None


def integration_actions() -> List[str]:
    return sorted(ACTION_PROVIDERS)


__all__ = [
    "ACTION_PROVIDERS",
    "action_provider",
    "get_provider",
    "integration_actions",
    "provider_names",
]
