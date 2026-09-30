"""Sequence Normalizer — reduces events to comparable steps.

Only ``application``, ``category`` and ``action`` take part in comparison.
Event ids, timestamps, descriptions and metadata (email subject, customer
name, attachment filename, …) are deliberately dropped so that superficial
differences never make two runs of the same workflow look different.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from app.schemas.activity import ActivityEvent


@dataclass(frozen=True)
class NormalizedStep:
    application: str
    category: str
    action: str

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.application, self.category, self.action)

    def __str__(self) -> str:
        return f"{self.application}|{self.category}|{self.action}"


def normalize_event(event: ActivityEvent) -> NormalizedStep:
    return NormalizedStep(
        application=event.application.strip().lower(),
        category=event.category.strip().lower(),
        action=event.action.strip().lower(),
    )


def normalize_sequence(events: Sequence[ActivityEvent]) -> List[NormalizedStep]:
    return [normalize_event(event) for event in events]
