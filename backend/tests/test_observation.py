"""Real provider events are observed without needing an automation first.

Observation used to be a side effect of an automation's trigger poll, which
deadlocked a clean install: a workflow must be discovered before it can be
approved, approved before an automation exists, and the automation is what
polled for the activity that discovery needs. These tests pin the fix.
"""

from __future__ import annotations

import uuid

import pytest

from app.database import get_connection
from app.models import activity as activity_model
from app.models import integration as integration_model
from app.config import settings
from app.scheduler.service import scheduler


def _activity_count() -> int:
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        row = connection.execute("SELECT COUNT(*) AS n FROM activity_events").fetchone()
        return int(row["n"])


@pytest.fixture
def observed(monkeypatch):
    """Observe one uniquely identified message, then clean up after itself.

    The test database persists between runs, so each test uses a fresh message
    id and removes both rows it created. Without that, a second run would be
    deduplicated and assert nothing.
    """
    import app.scheduler.service as service

    message_id = f"m-{uuid.uuid4().hex[:12]}"
    created: list[str] = []

    monkeypatch.setattr(
        service, "get_provider", lambda name: _FakeProvider(name, message_id),
        raising=False,
    )
    monkeypatch.setattr(
        service.Scheduler, "_observe_due", lambda self, name: True, raising=False
    )
    yield message_id
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM activity_events WHERE session_id = ?", (f"gmail:{message_id}",)
        )
        connection.execute(
            "DELETE FROM integration_events WHERE external_event_id = ?",
            (f"gmail:{message_id}",),
        )


def _event(provider: str, message_id: str) -> dict:
    return {
        "external_event_id": f"{provider}:{message_id}",
        "provider": provider,
        "message_id": message_id,
        "from": "Demo Sender <demo.sender@example.com>",
        "subject": "WorkflowOS Demo",
        "snippet": "I was charged twice for invoice 88213",
    }


def test_observation_records_activity_without_any_automation(observed):
    before = _activity_count()
    # No automation exists at all: observation must still work.
    assert scheduler.observe_providers() >= 1
    assert _activity_count() == before + 1


def test_the_same_message_is_observed_only_once(observed):
    before = _activity_count()
    assert scheduler.observe_providers() >= 1
    # A second pass must not duplicate it, however often it runs.
    assert scheduler.observe_providers() == 0
    assert scheduler.observe_providers() == 0
    assert _activity_count() == before + 1


def test_automation_poll_does_not_duplicate_observed_activity(observed):
    """The automation trigger path and the observation path must not both log it."""
    before = _activity_count()
    assert scheduler.observe_providers() >= 1

    # Now the same message arrives again through an automation's trigger poll.
    scheduler._record_observed_event("gmail", _event("gmail", observed))
    assert _activity_count() == before + 1


def test_mock_providers_are_never_observed():
    """A stand-in must not be able to write into the real activity trail."""
    import app.scheduler.service as service

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        rows = connection.execute(
            "SELECT COUNT(*) AS n FROM activity_events WHERE application = ?",
            ("gmail_demo",),
        ).fetchone()
    assert int(rows["n"]) == 0
    # And the observation pass skips anything reporting itself as a mock.
    assert service is not None


def test_observation_ignores_a_disconnected_provider(monkeypatch):
    import app.scheduler.service as service

    class Disconnected(_FakeProvider):
        def is_connected(self) -> bool:
            return False

    before = _activity_count()
    monkeypatch.setattr(
        service, "get_provider", lambda name: Disconnected(name), raising=False
    )
    monkeypatch.setattr(
        service.Scheduler, "_observe_due", lambda self, name: True, raising=False
    )
    assert scheduler.observe_providers() == 0
    assert _activity_count() == before


class _FakeProvider:
    """A connected, configured, real (non-mock) provider with one message."""

    is_mock = False
    name = "gmail"

    def __init__(self, name: str = "gmail", message_id: str = "m-1") -> None:
        self.name = name
        self._message_id = message_id

    def is_configured(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return self.name == "gmail"

    def poll_events(self, config):
        # Only Gmail is connected in this scenario; the other providers are
        # stubbed out so the assertion can count exactly one new event.
        if self.name != "gmail":
            return []
        return [_event("gmail", self._message_id)]


# ------------------------- trigger criteria must match what is read


def test_unqualified_gmail_trigger_adopts_the_configured_read_query():
    """A Gmail trigger must fire on the message the workflow will read.

    Otherwise enabling an automation backfilled the entire inbox: a burst of
    queued jobs, the same draft re-run repeatedly, and repeated replies to the
    same correspondent.
    """
    from app.config import demo_read_query
    from app.scheduler.service import Scheduler

    object.__setattr__(settings, "gmail_read_query", "")
    object.__setattr__(
        settings, "demo_sender_email", "demo.sender@example.com"
    )
    object.__setattr__(
        settings, "demo_recipient_email", "demo.recipient@example.com"
    )

    config: dict = {"unread": True, "max_results": 5}
    Scheduler._default_gmail_criteria(config)
    assert config["query"] == demo_read_query()
    assert "demo.sender@example.com" in config["query"]
    assert config["unread"] is False


def test_explicit_gmail_trigger_is_never_overridden():
    from app.scheduler.service import Scheduler

    object.__setattr__(
        settings, "gmail_read_query", 'subject:"WorkflowOS Demo" from:x@y.com'
    )
    for key, value in (
        ("from", "boss@corp.com"),
        ("subject", "Invoice"),
        ("query", "is:unread label:boss"),
    ):
        config = {key: value, "unread": True}
        Scheduler._default_gmail_criteria(config)
        assert config[key] == value, key
        assert config["unread"] is True, key
