"""Schedule computation — pure, testable next-run arithmetic.

Kept separate from the ticker so the arithmetic can be unit tested without a
running scheduler. All calculations are timezone-aware; a schedule carries its
own IANA timezone so "09:00 daily" means 09:00 in that zone regardless of the
server's local time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.job import ScheduleCreate


class ScheduleValidationError(Exception):
    """Raised when a schedule definition is not usable."""


def resolve_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone name, defaulting to UTC."""
    candidate = (name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ScheduleValidationError(f"Unknown timezone '{candidate}'") from exc


def _parse_time_of_day(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError) as exc:
        raise ScheduleValidationError(
            "time_of_day must look like HH:MM (24-hour)"
        ) from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleValidationError("time_of_day out of range")
    return hour, minute


def _at_time(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def validate(payload: ScheduleCreate) -> None:
    """Check that the definition is complete for its frequency."""
    zone = resolve_timezone(payload.timezone)
    if payload.frequency == "once":
        if payload.run_at is None:
            raise ScheduleValidationError("A one-time schedule needs 'run_at'")
        return
    if payload.frequency == "interval":
        if not payload.interval_seconds or payload.interval_seconds < 10:
            raise ScheduleValidationError(
                "An interval schedule needs interval_seconds >= 10"
            )
        return
    if payload.frequency == "daily":
        _parse_time_of_day(payload.time_of_day or "")
        return
    if payload.frequency == "weekly":
        _parse_time_of_day(payload.time_of_day or "")
        days = payload.days_of_week or []
        if not days:
            raise ScheduleValidationError(
                "A weekly schedule needs at least one day_of_week (0=Mon)"
            )
        if any(day < 0 or day > 6 for day in days):
            raise ScheduleValidationError("days_of_week must be 0-6 (0=Mon)")
    # Touch the zone so an unknown timezone fails here, not later.
    _ = zone


def compute_next_run(
    payload: ScheduleCreate,
    after: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return the next fire time strictly after ``after`` (default: now)."""
    validate(payload)
    zone = resolve_timezone(payload.timezone)
    reference = (after or datetime.now(timezone.utc)).astimezone(zone)

    if payload.frequency == "once":
        if payload.run_at is None:
            return None
        target = payload.run_at
        if target.tzinfo is None:
            target = target.replace(tzinfo=zone)
        target = target.astimezone(zone)
        return None if target <= reference else target

    if payload.frequency == "interval":
        seconds = int(payload.interval_seconds or 60)
        return (reference + timedelta(seconds=seconds)).astimezone(timezone.utc)

    hour, minute = _parse_time_of_day(payload.time_of_day or "")

    if payload.frequency == "daily":
        candidate = _at_time(reference, hour, minute)
        if candidate <= reference:
            candidate = _at_time(reference + timedelta(days=1), hour, minute)
        return candidate.astimezone(timezone.utc)

    # weekly
    wanted = set(payload.days_of_week or [])
    for offset in range(0, 8):
        day = reference + timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        candidate = _at_time(day, hour, minute)
        if candidate > reference:
            return candidate.astimezone(timezone.utc)
    return None


__all__ = [
    "ScheduleValidationError",
    "compute_next_run",
    "resolve_timezone",
    "validate",
]
