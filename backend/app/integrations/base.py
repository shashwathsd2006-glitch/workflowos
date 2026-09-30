"""Integration provider abstraction.

Every external system (Gmail, Slack, Google Calendar) is reached through an
``IntegrationProvider``. The execution engine never talks to a vendor API
directly — the action registry resolves an action name to a provider, and the
provider decides how to carry it out.

Two failure modes are distinguished on purpose:

- ``NotConfiguredError`` — no credentials in the environment. The UI must say
  "not configured"; it must never pretend to be connected.
- ``IntegrationError``   — the provider was reachable but the call failed.
  Retryable by default, because a network blip should not fail a workflow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IntegrationError(Exception):
    """A provider call failed. Usually retryable."""

    retryable = True


class NotConfiguredError(IntegrationError):
    """The provider has no credentials configured in this environment."""

    retryable = False

    def __init__(self, provider: str) -> None:
        super().__init__(f"{provider} integration is not configured")
        self.provider = provider


class AuthenticationError(IntegrationError):
    """Credentials were rejected or have expired and cannot refresh."""

    retryable = False


class ValidationError(IntegrationError):
    """Caller-supplied arguments were invalid. Never retryable."""

    retryable = False


class IntegrationStatus:
    """Connection status vocabulary shared by all providers."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


class IntegrationProvider(ABC):
    """Common surface for every external integration."""

    #: Stable identifier used in URLs and the registry.
    name: str = "provider"
    #: Human label for the UI.
    label: str = "Provider"
    #: True when this provider is a local mock rather than a real service.
    is_mock: bool = False

    @abstractmethod
    def is_configured(self) -> bool:
        """True when credentials exist in the environment."""

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Connection status. Must never fabricate a connected state."""

    @abstractmethod
    def get_authorize_url(self, state: Optional[str] = None) -> str:
        """OAuth 2.0 authorization URL the browser should visit."""

    @abstractmethod
    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange an OAuth authorization code for tokens."""

    @abstractmethod
    def disconnect(self) -> None:
        """Revoke/clear stored credentials for this provider."""

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Perform a cheap authenticated call to prove the link works."""

    @abstractmethod
    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a provider action by name."""

    def poll_events(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return new external events matching ``config``.

        Providers that do not support polling return an empty list.
        """
        return []


__all__ = [
    "AuthenticationError",
    "IntegrationError",
    "IntegrationProvider",
    "IntegrationStatus",
    "NotConfiguredError",
    "ValidationError",
]
