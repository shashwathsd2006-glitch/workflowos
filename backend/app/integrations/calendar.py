"""Google Calendar integration — official Calendar REST API v3.

Shares Google's OAuth flow with Gmail. Inputs are validated before any
request so a malformed event is rejected locally rather than creating junk
in the user's calendar.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings
from app.integrations.base import (
    IntegrationError,
    IntegrationProvider,
    IntegrationStatus,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.google_oauth import (
    GoogleOAuthMixin,
    integration_model_token_metadata,
)

logger = logging.getLogger("workflowos.integrations.calendar")

CALENDAR_API = "https://www.googleapis.com/calendar/v3"

SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
)

#: Guardrails: refuse absurd or unbounded events.
MAX_TITLE_LENGTH = 500
MAX_DURATION_HOURS = 24 * 7


def validate_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone, rejecting unknown names."""
    candidate = (name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValidationError(f"Unknown timezone '{candidate}'") from exc


def validate_calendar_id(calendar_id: str) -> str:
    """Accept only a plausible calendar id or the 'primary' alias."""
    candidate = (calendar_id or "primary").strip()
    if not candidate:
        return "primary"
    if len(candidate) > 256 or any(ch in candidate for ch in "\n\r"):
        raise ValidationError("Invalid calendar id")
    return candidate


def _parse_moment(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid {field}: expected ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class GoogleCalendarIntegration(GoogleOAuthMixin, IntegrationProvider):
    name = "calendar"
    label = "Google Calendar"
    is_mock = False
    provider_name = "calendar"
    default_scopes = SCOPES

    def is_configured(self) -> bool:
        return settings.google_configured

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
                "message": "Google integration is not configured.",
                "scopes": list(SCOPES),
                "poll_interval_seconds": settings.calendar_poll_interval_seconds,
            }
        meta = integration_model_token_metadata(self.name)
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
                "Connected via Google OAuth."
                if connected
                else "Not connected. Authorise with Google to enable Calendar."
            ),
            "scopes": meta.get("scopes") or [],
            "account_email": meta.get("account_email"),
            "poll_interval_seconds": settings.calendar_poll_interval_seconds,
            "token": {
                key: meta.get(key)
                for key in ("present", "readable", "expires_at", "can_refresh")
            },
        }

    def test_connection(self) -> Dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError(self.label)
        if not self.is_connected():
            return {
                "ok": False,
                "provider": self.name,
                "message": "Not connected — authorise with Google first.",
            }
        body = self._google_request("GET", f"{CALENDAR_API}/users/me/calendarList")
        return {
            "ok": True,
            "provider": self.name,
            "message": "Calendar API reachable.",
            "calendars": len(body.get("items", [])),
        }

    # ------------------------------------------------------------ calendar

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        max_results: int = 10,
        time_zone: str = "UTC",
    ) -> List[Dict[str, Any]]:
        """List upcoming events."""
        target = validate_calendar_id(calendar_id)
        zone = validate_timezone(time_zone)
        params: Dict[str, Any] = {
            "calendarId": target,
            "maxResults": max(1, min(max_results, 50)),
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeZone": str(zone),
        }
        if time_min:
            params["timeMin"] = time_min
        else:
            params["timeMin"] = datetime.now(timezone.utc).isoformat()
        body = self._google_request(
            "GET", f"{CALENDAR_API}/calendars/{target}/events", params=params
        )
        return [self._summarize(item) for item in body.get("items", [])]

    @staticmethod
    def _summarize(event: Dict[str, Any]) -> Dict[str, Any]:
        start = (event.get("start") or {}).get("dateTime") or (
            event.get("start") or {}
        ).get("date")
        end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get(
            "date"
        )
        return {
            "id": event.get("id"),
            "summary": event.get("summary", ""),
            "description": event.get("description", ""),
            "start": start,
            "end": end,
            "status": event.get("status"),
            "html_link": event.get("htmlLink"),
        }

    def create_event(
        self,
        *,
        title: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        description: str = "",
        time_zone: str = "UTC",
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a calendar event after validating every field."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValidationError("Event title is required")
        if len(clean_title) > MAX_TITLE_LENGTH:
            raise ValidationError(
                f"Event title exceeds {MAX_TITLE_LENGTH} characters"
            )
        target = validate_calendar_id(calendar_id)
        zone = validate_timezone(time_zone)
        start_at = _parse_moment(start, "start")
        end_at = _parse_moment(end, "end")
        if end_at <= start_at:
            raise ValidationError("Event end must be after its start")
        if end_at - start_at > timedelta(hours=MAX_DURATION_HOURS):
            raise ValidationError(
                f"Event duration exceeds {MAX_DURATION_HOURS} hours"
            )
        if attendees is not None and len(attendees) > 50:
            raise ValidationError("Too many attendees (max 50)")

        body: Dict[str, Any] = {
            "summary": clean_title,
            "description": description or "",
            "start": {"dateTime": start_at.isoformat(), "timeZone": str(zone)},
            "end": {"dateTime": end_at.isoformat(), "timeZone": str(zone)},
        }
        if attendees:
            body["attendees"] = [{"email": item} for item in attendees]
        created = self._google_request(
            "POST", f"{CALENDAR_API}/calendars/{target}/events", json=body
        )
        return {"ok": True, **self._summarize(created)}

    def update_event(
        self,
        *,
        event_id: str,
        title: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        calendar_id: str = "primary",
        time_zone: str = "UTC",
    ) -> Dict[str, Any]:
        """Update an existing event, validating the new values first."""
        if not event_id:
            raise ValidationError("event_id is required")
        target = validate_calendar_id(calendar_id)
        zone = validate_timezone(time_zone)
        body: Dict[str, Any] = {}
        if title is not None:
            clean = title.strip()
            if not clean:
                raise ValidationError("Event title cannot be empty")
            if len(clean) > MAX_TITLE_LENGTH:
                raise ValidationError(
                    f"Event title exceeds {MAX_TITLE_LENGTH} characters"
                )
            body["summary"] = clean
        if description is not None:
            body["description"] = description
        if start is not None and end is not None:
            start_at = _parse_moment(start, "start")
            end_at = _parse_moment(end, "end")
            if end_at <= start_at:
                raise ValidationError("Event end must be after its start")
            body["start"] = {"dateTime": start_at.isoformat(), "timeZone": str(zone)}
            body["end"] = {"dateTime": end_at.isoformat(), "timeZone": str(zone)}
        if not body:
            raise ValidationError("No updatable fields supplied")
        updated = self._google_request(
            "PATCH",
            f"{CALENDAR_API}/calendars/{target}/events/{event_id}",
            json=body,
        )
        return {"ok": True, **self._summarize(updated)}

    # ------------------------------------------------------------- actions

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action == "calendar_list_events":
            return {
                "ok": True,
                "events": self.list_events(
                    calendar_id=str(payload.get("calendar_id") or "primary"),
                    time_min=payload.get("time_min"),
                    max_results=int(payload.get("max_results", 10) or 10),
                    time_zone=str(payload.get("timezone") or "UTC"),
                ),
            }
        if action == "calendar_create_event":
            return self.create_event(
                title=str(payload.get("title") or ""),
                start=str(payload.get("start") or ""),
                end=str(payload.get("end") or ""),
                calendar_id=str(payload.get("calendar_id") or "primary"),
                description=str(payload.get("description") or ""),
                time_zone=str(payload.get("timezone") or "UTC"),
                attendees=payload.get("attendees"),
            )
        if action == "calendar_update_event":
            return self.update_event(
                event_id=str(payload.get("event_id") or ""),
                title=payload.get("title"),
                start=payload.get("start"),
                end=payload.get("end"),
                description=payload.get("description"),
                calendar_id=str(payload.get("calendar_id") or "primary"),
                time_zone=str(payload.get("timezone") or "UTC"),
            )
        raise ValidationError(f"Calendar does not support action '{action}'")

    def poll_events(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Surface newly created upcoming events as trigger candidates."""
        if not self.is_connected():
            return []
        try:
            events = self.list_events(
                calendar_id=str(config.get("calendar_id") or "primary"),
                max_results=int(config.get("max_results", 5) or 5),
                time_zone=str(config.get("timezone") or "UTC"),
            )
        except (IntegrationError, ValidationError) as exc:
            logger.warning("Calendar poll failed: %s", exc)
            return []
        criteria = (config.get("criteria") or "").strip().lower()
        results = []
        for event in events:
            if not event.get("id"):
                continue
            if criteria and criteria not in str(event.get("summary", "")).lower():
                continue
            results.append(
                {
                    "external_event_id": f"calendar:{event['id']}",
                    "provider": self.name,
                    "event_id": event["id"],
                    "summary": event.get("summary"),
                    "start": event.get("start"),
                    "end": event.get("end"),
                }
            )
        return results


__all__ = [
    "GoogleCalendarIntegration",
    "MAX_DURATION_HOURS",
    "MAX_TITLE_LENGTH",
    "SCOPES",
    "validate_calendar_id",
    "validate_timezone",
]
