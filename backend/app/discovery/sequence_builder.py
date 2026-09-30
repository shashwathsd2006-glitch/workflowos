"""Sequence Builder — groups activity events into logical sequences.

Grouping key: ``session_id`` (primary mechanism). Events inside a sequence are
ordered by their own timestamps, never by frontend or insertion order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Sequence

from app.schemas.activity import ActivityEvent


@dataclass(frozen=True)
class ActivitySequence:
    """Chronologically ordered events that belong to one session."""

    session_id: str
    events: Sequence[ActivityEvent] = field(default_factory=tuple)

    @property
    def first_seen(self) -> datetime:
        return self.events[0].timestamp

    @property
    def last_seen(self) -> datetime:
        return self.events[-1].timestamp

    def __len__(self) -> int:
        return len(self.events)


def build_sequences(events: Sequence[ActivityEvent]) -> List[ActivitySequence]:
    """Group events by session and sort each group chronologically.

    Sequences are returned ordered by their first event timestamp so that
    detection is deterministic regardless of input order.
    """
    grouped: Dict[str, List[ActivityEvent]] = {}
    for event in events:
        grouped.setdefault(event.session_id, []).append(event)

    sequences: List[ActivitySequence] = []
    for session_id, session_events in grouped.items():
        ordered = sorted(
            session_events, key=lambda item: (item.timestamp, item.id)
        )
        sequences.append(ActivitySequence(session_id=session_id, events=tuple(ordered)))

    sequences.sort(key=lambda item: (item.first_seen, item.session_id))
    return sequences
