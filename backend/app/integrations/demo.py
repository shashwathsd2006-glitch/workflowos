"""Demo integrations — deterministic local stand-ins for real services.

These exist so the full architecture (scheduler → queue → worker → engine →
actions → audit → analytics) can be demonstrated on a machine with no Google
or Slack credentials.

The single most important rule here: a demo provider is **always** labelled
``is_mock = True`` and its status message says so in plain words. It never
reports ``connected`` in a way that could be mistaken for a real link, and it
never contacts a network. A judge must be able to tell instantly that no
Gmail/Slack/Calendar account is attached.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_connection
from app.integrations.base import (
    IntegrationProvider,
    IntegrationStatus,
    NotConfiguredError,
    ValidationError,
)
from app.models import integration as integration_model

logger = logging.getLogger("workflowos.integrations.demo")

DEMO_LABEL_SUFFIX = " (demo)"


def _stable_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def normalize_action(action: str) -> str:
    """Accept both ``gmail_read_email`` and ``gmail_demo_read_email``.

    The engine may route a real action name to a stand-in in DEMO_MODE, so a
    demo provider must recognise either spelling of the same action.
    """
    return (action or "").strip().lower().replace("_demo_", "_")


class DemoIntegration(IntegrationProvider):
    """A local, offline stand-in for one external service.

    Subclasses declare which real provider they stand in for so status text
    stays honest about what is and is not connected.
    """

    name = "demo"
    label = "Demo integration"
    is_mock = True
    #: The real provider this mocks, e.g. "gmail".
    stands_in_for = "an external service"

    def is_configured(self) -> bool:
        return settings.demo_mode

    def is_connected(self) -> bool:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            row = integration_model.select_account(connection, self.name)
        return row is not None and bool(row["is_mock"])

    def status(self) -> Dict[str, Any]:
        if not settings.demo_mode:
            return {
                "provider": self.name,
                "label": f"{self.label}{DEMO_LABEL_SUFFIX}",
                "is_mock": True,
                "configured": False,
                "connected": False,
                "state": IntegrationStatus.NOT_CONFIGURED,
                "message": (
                    "Demo mode is off. Set DEMO_MODE=true to use the local "
                    "demo integration."
                ),
            }
        connected = self.is_connected()
        return {
            "provider": self.name,
            "label": f"{self.label}{DEMO_LABEL_SUFFIX}",
            "is_mock": True,
            "configured": True,
            "connected": connected,
            "state": (
                IntegrationStatus.CONNECTED
                if connected
                else IntegrationStatus.DISCONNECTED
            ),
            "message": (
                f"DEMO MODE — simulated {self.stands_in_for}. No real account is "
                "connected and nothing leaves this machine."
                if connected
                else (
                    f"DEMO MODE available — simulated {self.stands_in_for} is "
                    "not activated yet."
                )
            ),
            "scopes": ["demo"],
            "stands_in_for": self.stands_in_for,
        }

    def connect(self, label: Optional[str] = None) -> Dict[str, Any]:
        """Activate the local demo integration."""
        if not settings.demo_mode:
            raise NotConfiguredError(self.label)
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.upsert_account(
                connection,
                self.name,
                encrypted_token=None,
                account_label=label or f"demo-{self.stands_in_for}",
                is_mock=True,
                verified=True,
            )
        return self.status()

    def disconnect(self) -> None:
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.delete_account(connection, self.name)

    def get_authorize_url(self, state: Optional[str] = None) -> str:
        raise NotConfiguredError(
            f"{self.label} (demo integrations have no OAuth flow)"
        )

    def exchange_code(self, code: str) -> Dict[str, Any]:
        raise NotConfiguredError(
            f"{self.label} (demo integrations have no OAuth flow)"
        )

    def test_connection(self) -> Dict[str, Any]:
        if not settings.demo_mode:
            raise NotConfiguredError(self.label)
        return {
            "ok": True,
            "provider": self.name,
            "is_mock": True,
            "message": (
                f"Demo integration active — {self.stands_in_for} is simulated "
                "locally, not contacted."
            ),
        }

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def _require_demo(self) -> None:
        if not settings.demo_mode or not self.is_connected():
            raise ValidationError(
                f"{self.label} demo integration is not activated"
            )


class DemoGmail(DemoIntegration):
    name = "gmail_demo"
    label = "Gmail"
    stands_in_for = "Gmail"

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_demo()
        action = normalize_action(action)
        action = normalize_action(action)
        action = normalize_action(action)
        if action == "gmail_read_email":
            message_id = str(payload.get("message_id") or "")
            if not message_id:
                raise ValidationError("message_id is required")
            seed = _stable_id(message_id)
            sender = str(payload.get("from") or f"customer-{seed[:4]}@example.com")
            subject = str(payload.get("subject") or "Demo customer request")
            return {
                "ok": True,
                "is_mock": True,
                "message_id": message_id,
                "from": sender,
                "subject": subject,
                "snippet": f"[demo] Simulated message body for {message_id}",
                "body": (
                    f"[demo] This body was generated locally for message "
                    f"{message_id}. No Gmail API was called."
                ),
            }
        if action == "gmail_send_email":
            to_address = str(payload.get("to") or "")
            if "@" not in to_address:
                raise ValidationError("A valid 'to' address is required")
            if not str(payload.get("subject") or "").strip():
                raise ValidationError("A subject is required")
            return {
                "ok": True,
                "is_mock": True,
                "message_id": f"demo-sent-{_stable_id(to_address + str(payload.get('subject')))}",
                "to": to_address,
                "message": "Simulated send — no email was delivered.",
            }
        if action == "gmail_fetch_messages":
            return {"ok": True, "is_mock": True, "messages": []}
        raise ValidationError(f"Demo Gmail does not support action '{action}'")

    def poll_events(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Emit a deterministic synthetic message per poll cycle.

        The id is derived from the current poll slot so repeated polls inside
        the same slot cannot create duplicate events, while a later slot
        produces a fresh message.
        """
        if not settings.demo_mode or not self.is_connected():
            return []
        slot = int(config.get("demo_slot") or 0)
        seed = _stable_id(f"gmail-demo-{slot}")
        message_id = f"demo-{seed}"
        return [
            {
                "external_event_id": f"gmail_demo:{message_id}",
                "provider": self.name,
                "message_id": message_id,
                "from": f"customer-{seed[:4]}@example.com",
                "subject": f"Demo support request #{slot + 1}",
                "snippet": "[demo] Simulated inbound customer email.",
                "unread": True,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "is_mock": True,
            }
        ]


class DemoSlack(DemoIntegration):
    name = "slack_demo"
    label = "Slack"
    stands_in_for = "Slack"

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_demo()
        action = normalize_action(action)
        action = normalize_action(action)
        action = normalize_action(action)
        if action == "slack_send_message":
            channel = str(payload.get("channel") or "")
            text = str(payload.get("text") or "")
            if not channel.strip():
                raise ValidationError("A Slack channel is required")
            if not text.strip():
                raise ValidationError("Message text is required")
            return {
                "ok": True,
                "is_mock": True,
                "channel": channel,
                "ts": f"demo-{_stable_id(channel + text)}",
                "message": text,
                "note": "Simulated Slack message — nothing was posted to Slack.",
            }
        if action == "slack_list_channels":
            return {
                "ok": True,
                "is_mock": True,
                "channels": [
                    {"id": "C0000DEMO", "name": "support", "is_private": False},
                    {"id": "C0000ALERTS", "name": "alerts", "is_private": False},
                ],
            }
        raise ValidationError(f"Demo Slack does not support action '{action}'")


class DemoCalendar(DemoIntegration):
    name = "calendar_demo"
    label = "Google Calendar"
    stands_in_for = "Google Calendar"

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_demo()
        action = normalize_action(action)
        action = normalize_action(action)
        action = normalize_action(action)
        if action == "calendar_create_event":
            from app.integrations.calendar import (
                MAX_TITLE_LENGTH,
                validate_calendar_id,
                validate_timezone,
            )
            from datetime import datetime as _dt

            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValidationError("Event title is required")
            if len(title) > MAX_TITLE_LENGTH:
                raise ValidationError("Event title too long")
            calendar_id = validate_calendar_id(str(payload.get("calendar_id") or "primary"))
            validate_timezone(str(payload.get("timezone") or "UTC"))
            try:
                start = _dt.fromisoformat(str(payload.get("start")).replace("Z", "+00:00"))
                end = _dt.fromisoformat(str(payload.get("end")).replace("Z", "+00:00"))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError("start/end must be ISO-8601 datetimes") from exc
            if end <= start:
                raise ValidationError("Event end must be after its start")
            return {
                "ok": True,
                "is_mock": True,
                "id": f"demo-event-{_stable_id(title + start.isoformat())}",
                "summary": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "calendar_id": calendar_id,
                "note": "Simulated event — no calendar was modified.",
            }
        if action == "calendar_list_events":
            now = datetime.now(timezone.utc)
            return {
                "ok": True,
                "is_mock": True,
                "events": [
                    {
                        "id": "demo-upcoming-1",
                        "summary": "Demo standup",
                        "start": (now + timedelta(hours=2)).isoformat(),
                        "end": (now + timedelta(hours=2, minutes=30)).isoformat(),
                    }
                ],
            }
        if action == "calendar_update_event":
            if not str(payload.get("event_id") or "").strip():
                raise ValidationError("event_id is required")
            return {
                "ok": True,
                "is_mock": True,
                "id": str(payload["event_id"]),
                "note": "Simulated update — no calendar was modified.",
            }
        raise ValidationError(f"Demo Calendar does not support action '{action}'")

    def poll_events(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not settings.demo_mode or not self.is_connected():
            return []
        slot = int(config.get("demo_slot") or 0)
        event_id = f"demo-cal-{_stable_id(str(slot))}"
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        return [
            {
                "external_event_id": f"calendar_demo:{event_id}",
                "provider": self.name,
                "event_id": event_id,
                "summary": f"Demo calendar event #{slot + 1}",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "is_mock": True,
            }
        ]


__all__ = [
    "DEMO_LABEL_SUFFIX",
    "DemoCalendar",
    "DemoGmail",
    "DemoIntegration",
    "DemoSlack",
]
