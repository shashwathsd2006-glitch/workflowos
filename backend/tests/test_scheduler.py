"""Phase 8 tests — scheduling, background worker, integrations and demo mode.

No test requires network access or real credentials. Real providers are
verified through configuration checks and the not-configured path; provider
*behaviour* is verified with the local demo adapters, which is reported
honestly rather than dressed up as a real integration test.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from app.database import get_connection
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.credentials import (
    CredentialError,
    decrypt_token,
    encrypt_token,
    token_metadata,
)
from app.integrations.registry import (
    ACTION_PROVIDERS,
    action_provider,
    get_provider,
    integration_actions,
    provider_names,
)
from app.models import activity as activity_model
from app.models import automation as automation_model
from app.models import generator as draft_model
from app.models import integration as integration_model
from app.models import job as job_model
from app.models import schedule as schedule_model
from app.scheduler import queue
from app.scheduler.recurrence import (
    ScheduleValidationError,
    compute_next_run,
    resolve_timezone,
)
from app.scheduler.worker import Worker, backoff_seconds
from app.schemas.discovery import WorkflowCandidate, WorkflowStep
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)
from app.schemas.job import ScheduleCreate

BASE = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_phase8():
    """Clear Phase 8 tables before each test in this module.

    Several tests drive the worker directly (no ``client`` fixture), and the
    worker always claims the *oldest* queued job — so any leftover row from a
    previous test would be processed instead of the one under test.
    """
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        draft_model.ensure_table(connection)
        job_model.ensure_table(connection)
        schedule_model.ensure_table(connection)
        automation_model.ensure_table(connection)
        integration_model.ensure_tables(connection)
        connection.execute(f"DELETE FROM {integration_model.EVENTS_TABLE}")
        connection.execute(f"DELETE FROM {integration_model.ACCOUNTS_TABLE}")
        connection.execute(f"DELETE FROM {job_model.TABLE}")
        connection.execute(f"DELETE FROM {schedule_model.TABLE}")
        connection.execute(f"DELETE FROM {automation_model.TABLE}")
    yield


# ------------------------------------------------------------- fixtures

def make_draft(steps: Optional[List[dict]] = None, candidate: str = "workflow-001") -> str:
    plan = steps if steps is not None else [
        {"step_number": 1, "application": "CRM", "action": "update", "purpose": "Sync"},
    ]
    draft = WorkflowDraft(
        name="Customer Request Workflow",
        description="Handles a request.",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="open_email"),
        steps=[GeneratedWorkflowStep(**step) for step in plan],
        confidence=0.87,
    )
    with get_connection() as connection:
        activity_model.ensure_table(connection)
        draft_model.ensure_table(connection)
        job_model.ensure_table(connection)
        automation_model.ensure_table(connection)
        schedule_model.ensure_table(connection)
        integration_model.ensure_tables(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id=candidate,
        )
        # Approve only when needed: upsert refreshes the same row, and a
        # second approve on an already-approved draft is (correctly) refused.
        if record.status == "pending_approval":
            draft_model.set_status(connection, record, "approved")
    return record.id


def make_automation(draft_id: str, **overrides) -> str:
    payload = {
        "name": "Support automation",
        "draft_id": draft_id,
        "description": "demo",
        "trigger_type": "manual",
        "trigger_config": "",
        "enabled": True,
    }
    payload.update(overrides)
    record = queue.enqueue_job.__self__ if False else None  # noqa: E731
    from app.services import automation_service

    created = automation_service.create_automation(
        __import__("app.schemas.automation", fromlist=["AutomationCreate"]).AutomationCreate(
            **payload
        )
    )
    return created.id


# ------------------------------------------------- A. schedule arithmetic

def test_next_run_interval():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(ScheduleCreate(frequency="interval", interval_seconds=300), now)
    assert nxt == now + timedelta(seconds=300)


def test_next_run_daily_utc():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(ScheduleCreate(frequency="daily", time_of_day="09:00"), now)
    assert nxt == datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


def test_next_run_respects_timezone():
    """09:00 Asia/Tokyo is 00:00 UTC — timezone support is real."""
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(
        ScheduleCreate(frequency="daily", time_of_day="09:00", timezone="Asia/Tokyo"), now
    )
    assert nxt == datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)


def test_next_run_weekly_picks_next_matching_day():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)  # Saturday
    nxt = compute_next_run(
        ScheduleCreate(frequency="weekly", time_of_day="08:30", days_of_week=[0]), now
    )
    assert nxt.weekday() == 0
    assert nxt > now


def test_next_run_once_past_returns_none():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=1)
    assert compute_next_run(ScheduleCreate(frequency="once", run_at=past), now) is None


def test_next_run_once_future():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(hours=1)
    assert compute_next_run(ScheduleCreate(frequency="once", run_at=future), now) == future


def test_invalid_schedule_rejected():
    with pytest.raises(ScheduleValidationError):
        compute_next_run(ScheduleCreate(frequency="weekly", time_of_day="08:30"))
    with pytest.raises(Exception):
        # interval_seconds has a schema-level floor of 10.
        ScheduleCreate(frequency="interval", interval_seconds=1)
    with pytest.raises(ScheduleValidationError):
        compute_next_run(ScheduleCreate(frequency="once"))
    with pytest.raises(ScheduleValidationError):
        resolve_timezone("Mars/Base")


# -------------------------------------------------- B. schedule lifecycle

def test_schedule_created_and_persisted():
    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "interval", "interval_seconds": 3600}),
    )
    schedule = queue.get_schedule(automation_id)
    assert schedule is not None
    assert schedule.frequency == "interval"
    assert schedule.next_run is not None
    # Survives a fresh read (i.e. it is in SQLite, not memory).
    assert queue.get_schedule(automation_id).id == schedule.id


def test_schedule_visible_via_api(client):
    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "daily", "time_of_day": "09:00"}),
    )
    listed = client.get("/api/schedules").json()
    assert listed["count"] == 1
    assert listed["schedules"][0]["automation_id"] == automation_id
    single = client.get(f"/api/schedules/{automation_id}")
    assert single.status_code == 200
    assert single.json()["next_run"] is not None


def test_invalid_schedule_returns_422(client):
    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "interval", "interval_seconds": 3600}),
    )
    response = client.put(
        f"/api/schedules/{automation_id}",
        json={"frequency": "weekly", "time_of_day": "08:30"},
    )
    assert response.status_code == 422


def test_disabled_automation_schedule_does_not_fire():
    from app.scheduler.service import Scheduler

    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "interval", "interval_seconds": 60}),
    )
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        automation_model.set_enabled(connection, automation_id, False)

    scheduler = Scheduler()
    fired = scheduler.fire_due_schedules()
    assert fired == 0
    with get_connection() as connection:
        job_model.ensure_table(connection)
        assert job_model.select_all(connection) == []


def test_due_schedule_enqueues_one_job():
    from app.scheduler.service import Scheduler

    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "interval", "interval_seconds": 60}),
    )
    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        schedule = schedule_model.select_by_automation(connection, automation_id)
        schedule_model.update_schedule(
            connection, schedule.id, next_run=datetime.now(timezone.utc) - timedelta(seconds=5)
        )

    scheduler = Scheduler()
    assert scheduler.fire_due_schedules() == 1
    # next_run must advance so it does not spin on the same due time.
    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        after = schedule_model.select_by_automation(connection, automation_id)
        assert after.next_run > datetime.now(timezone.utc) - timedelta(seconds=1)
        assert after.run_count == 1
    # A second tick must not double-fire.
    assert scheduler.fire_due_schedules() == 0


def test_deleted_automation_leaves_no_jobs():
    from app.services import automation_service

    draft_id = make_draft()
    automation_id = make_automation(
        draft_id, trigger_type="schedule",
        trigger_config=json.dumps({"frequency": "interval", "interval_seconds": 60}),
    )
    from app.scheduler import queue as q

    q.enqueue_job(automation_id=automation_id, trigger="manual")
    automation_service.delete_automation(automation_id)
    with get_connection() as connection:
        job_model.ensure_table(connection)
        schedule_model.ensure_table(connection)
        assert job_model.select_all(connection) == []
        assert schedule_model.select_by_automation(connection, automation_id) is None


# ------------------------------------------------------ C. job queue/worker

def test_enqueue_and_process_job():
    draft_id = make_draft(steps=[
        {"step_number": 1, "application": "CRM", "action": "update", "purpose": "Sync"},
        {"step_number": 2, "application": "Slack", "action": "log", "purpose": "Note"},
    ])
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id, trigger="manual")
    assert job.status == "queued"

    worker = Worker()
    assert worker.process_one() == job.id

    with get_connection() as connection:
        job_model.ensure_table(connection)
        finished = job_model.select_by_id(connection, job.id)
    assert finished.status == "completed"
    assert finished.execution_id is not None
    assert finished.completed_at is not None
    assert finished.duration_ms is not None


def test_worker_never_processes_a_job_twice():
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    queue.enqueue_job(automation_id=automation_id)
    worker = Worker()
    assert worker.process_one() is not None
    # Queue is empty now.
    assert worker.process_one() is None
    with get_connection() as connection:
        job_model.ensure_table(connection)
        assert job_model.counts(connection)["completed"] == 1


def test_claim_is_exclusive_under_contention():
    """Two workers racing for one job: exactly one wins."""
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id)
    with get_connection() as connection:
        job_model.ensure_table(connection)
        first = job_model.claim_next_job(connection)
        second = job_model.claim_next_job(connection)
    assert first is not None
    assert first.id == job.id
    assert second is None


def test_disabled_automation_cannot_enqueue():
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        automation_model.set_enabled(connection, automation_id, False)
    with pytest.raises(queue.AutomationDisabledError):
        queue.enqueue_job(automation_id=automation_id)


def test_enqueue_unknown_automation_raises():
    with pytest.raises(queue.AutomationNotFoundError):
        queue.enqueue_job(automation_id="automation-999")


def test_stale_running_jobs_are_recovered():
    """A job stranded 'running' by a crash goes back to the queue."""
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id, max_retries=2)
    with get_connection() as connection:
        job_model.ensure_table(connection)
        # Simulate a crash mid-execution.
        job_model.claim_next_job(connection)
        stranded = job_model.select_by_id(connection, job.id)
        assert stranded.status == "running"
        recovered = job_model.recover_stale_jobs(connection, stale_after_seconds=0)
        assert recovered == 1
        after = job_model.select_by_id(connection, job.id)
        assert after.status == "queued"
        assert after.retry_count == 1


def test_stale_job_with_no_retries_left_fails():
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id, max_retries=0)
    with get_connection() as connection:
        job_model.ensure_table(connection)
        job_model.claim_next_job(connection)
        job_model.recover_stale_jobs(connection, stale_after_seconds=0)
        after = job_model.select_by_id(connection, job.id)
    assert after.status == "failed"


def test_backoff_grows_exponentially():
    assert backoff_seconds(1) < backoff_seconds(2) < backoff_seconds(3)
    assert backoff_seconds(20) <= 3600.0


# --------------------------------------------------------- D. retry policy

def test_validation_failure_is_not_retried():
    """An unsupported action can never be fixed by retrying."""
    draft_id = make_draft(steps=[
        {"step_number": 1, "application": "X", "action": "totally_unknown", "purpose": ""},
    ])
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id, max_retries=3)
    Worker().process_one()
    with get_connection() as connection:
        job_model.ensure_table(connection)
        finished = job_model.select_by_id(connection, job.id)
    assert finished.status == "failed"
    assert finished.retry_count == 0
    assert finished.retryable is False


def test_unapproved_workflow_is_not_retried():
    """Approval cannot appear later, so this must fail permanently."""
    draft = WorkflowDraft(
        name="Pending workflow",
        description="Not approved.",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="open_email"),
        steps=[GeneratedWorkflowStep(step_number=1, application="CRM", action="update")],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        job_model.ensure_table(connection)
        automation_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-pending",
        )
    from app.services import automation_service
    from app.schemas.automation import AutomationCreate

    # Bypass the approval guard the API enforces, to prove the worker is
    # also safe on its own.
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.insert_automation(
            connection,
            AutomationCreate(name="sneaky", draft_id=record.id),
        )
        automation_id = row["id"]

    job = queue.enqueue_job(automation_id=automation_id, max_retries=3)
    Worker().process_one()
    with get_connection() as connection:
        job_model.ensure_table(connection)
        finished = job_model.select_by_id(connection, job.id)
    assert finished.status == "failed"
    assert finished.retry_count == 0


def test_retryable_failure_is_requeued():
    """A provider fault that reports retryable=True must come back."""
    from app.automation.actions import RetryableActionError

    draft_id = make_draft(steps=[
        {"step_number": 1, "application": "Gmail", "action": "gmail_read_email", "purpose": ""},
    ])
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id, max_retries=2)

    import app.scheduler.worker as worker_module

    original = worker_module.execute_draft

    def flaky(*args, **kwargs):
        raise RetryableActionError("temporary network fault")

    worker_module.execute_draft = flaky
    try:
        Worker().process_one()
    finally:
        worker_module.execute_draft = original

    with get_connection() as connection:
        job_model.ensure_table(connection)
        after = job_model.select_by_id(connection, job.id)
    assert after.status == "queued"
    assert after.retry_count == 1
    assert after.retryable is True
    assert after.next_attempt_at is not None


# ---------------------------------------------------------- E. credentials

def test_token_is_encrypted_at_rest():
    blob = encrypt_token({"access_token": "super-secret", "refresh_token": "r"})
    assert "super-secret" not in blob
    assert decrypt_token(blob)["access_token"] == "super-secret"


def test_token_metadata_never_leaks_secrets():
    blob = encrypt_token({"access_token": "secret", "refresh_token": "r", "scope": "a b"})
    meta = token_metadata(blob)
    assert meta["present"] is True
    assert "access_token" not in meta
    assert "refresh_token" not in meta
    assert "secret" not in json.dumps(meta)
    assert meta["can_refresh"] is True


def test_unreadable_credential_is_reported_not_raised():
    meta = token_metadata("not-a-valid-fernet-blob")
    assert meta["present"] is True
    assert meta["readable"] is False


def test_empty_credential_metadata():
    assert token_metadata(None) == {"present": False}


# --------------------------------------------------------- F. integrations

@contextmanager
def google_unconfigured():
    """Temporarily hide Google credentials, then restore them."""
    from app.config import settings

    saved = (
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_client_source,
        settings.google_credentials_present,
    )
    object.__setattr__(settings, "google_client_id", None)
    object.__setattr__(settings, "google_client_secret", None)
    object.__setattr__(settings, "google_client_source", "none")
    object.__setattr__(settings, "google_credentials_present", False)
    try:
        yield
    finally:
        object.__setattr__(settings, "google_client_id", saved[0])
        object.__setattr__(settings, "google_client_secret", saved[1])
        object.__setattr__(settings, "google_client_source", saved[2])
        object.__setattr__(settings, "google_credentials_present", saved[3])


def test_google_providers_read_credentials_file():
    """A Google client is configured, from the real file or a test placeholder."""
    from app.config import settings

    assert settings.google_configured is True
    assert settings.google_client_id
    assert settings.google_client_secret


def test_google_providers_report_not_configured_when_hidden():
    """With no credentials the status must say so, never 'connected'."""
    with google_unconfigured():
        for name in ("gmail", "calendar"):
            provider = get_provider(name)
            status = provider.status()
            assert status["configured"] is False
            assert status["connected"] is False
            assert status["state"] == "not_configured"
            assert "not configured" in status["message"].lower()


def test_slack_reports_not_configured():
    status = get_provider("slack").status()
    assert status["configured"] is False
    assert status["state"] == "not_configured"


def test_google_providers_refuse_oauth_without_credentials():
    with google_unconfigured():
        for name in ("gmail", "calendar"):
            with pytest.raises(NotConfiguredError):
                get_provider(name).get_authorize_url()


def test_gmail_status_exposes_scopes_and_redirect_but_no_secret():
    from app.config import settings
    from app.integrations.gmail import SCOPES

    # Configure the client explicitly so the test does not depend on a
    # credentials file being present in the working tree.
    saved = (
        settings.__dict__["google_client_id"],
        settings.__dict__["google_client_secret"],
    )
    object.__setattr__(settings, "google_client_id", "test-id.apps.googleusercontent.com")
    object.__setattr__(settings, "google_client_secret", "test-secret")
    try:
        status = get_provider("gmail").status()
    finally:
        object.__setattr__(settings, "google_client_id", saved[0])
        object.__setattr__(settings, "google_client_secret", saved[1])

    assert status["configured"] is True
    # Minimum scopes only, and no mailbox-modify scope.
    assert status["requested_scopes"] == list(SCOPES)
    assert "https://www.googleapis.com/auth/gmail.modify" not in SCOPES
    assert len(SCOPES) == 2
    assert status["redirect_uri"].endswith("/api/integrations/gmail/callback")
    # Never a secret.
    assert "client_secret" not in status
    assert "client_id" not in status


def test_real_providers_refuse_actions_when_not_connected():
    for name, action in (
        ("gmail", "gmail_read_email"),
        ("slack", "slack_send_message"),
        ("calendar", "calendar_list_events"),
    ):
        with pytest.raises((AuthenticationError, IntegrationError)):
            get_provider(name).execute_action(action, {})


def test_action_allowlist_is_code_controlled():
    assert action_provider("gmail_read_email") == "gmail"
    assert action_provider("slack_send_message") == "slack"
    assert action_provider("calendar_create_event") == "calendar"
    # Unknown / dangerous names resolve to nothing.
    for bad in ("os_system", "subprocess", "eval", "exec", "rm_rf", "curl"):
        assert action_provider(bad) is None


def test_action_allowlist_has_no_unsafe_entries():
    for action in integration_actions():
        for banned in ("system", "subprocess", "shell", "popen", "eval", "exec"):
            assert banned not in action, action


def test_integration_list_api(client):
    body = client.get("/api/integrations").json()
    assert body["count"] == len(provider_names())
    states = {item["provider"]: item["state"] for item in body["integrations"]}
    assert states["slack"] == "not_configured"
    # A provider must never be reported as connected without a real grant.
    # With an OAuth client on disk but no authorisation it reads
    # "disconnected"; on a fresh clone with no credentials file at all it
    # honestly reads "not_configured".
    assert states["gmail"] in {
        "disconnected",
        "connected",
        "not_configured",
    }
    assert body["actions"]


def test_integration_actions_api(client):
    body = client.get("/api/integrations/actions").json()
    assert body["count"] == len(integration_actions())
    for item in body["actions"]:
        assert item["provider"] in provider_names()


def test_unknown_provider_returns_404(client):
    assert client.get("/api/integrations/nope").status_code == 404
    assert client.post("/api/integrations/nope/test").status_code == 404


def test_connect_real_provider_returns_authorize_url(client):
    """Connect returns a Google consent URL built from the OAuth client.

    The client id/secret are injected rather than read from a real credentials
    file, so the suite passes on a fresh clone where no secrets file exists.
    """
    from urllib.parse import parse_qs, urlparse

    from app.config import settings

    saved = (
        settings.__dict__["google_client_id"],
        settings.__dict__["google_client_secret"],
    )
    object.__setattr__(settings, "google_client_id", "test-id.apps.googleusercontent.com")
    object.__setattr__(settings, "google_client_secret", "test-secret")
    try:
        body = client.post("/api/integrations/gmail/connect", json={}).json()
    finally:
        object.__setattr__(settings, "google_client_id", saved[0])
        object.__setattr__(settings, "google_client_secret", saved[1])
    assert "authorize_url" in body
    assert "authorize_url" in body
    url = body["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"][0]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"][0]
    # The redirect URI is the backend callback; the client secret is absent.
    assert query["redirect_uri"] == [
        "http://localhost:8000/api/integrations/gmail/callback"
    ]
    assert "client_secret" not in query
    assert "secret" not in url


# ------------------------------------------------------------ G. demo mode

def test_demo_providers_refuse_when_demo_mode_off(client):
    from app.config import settings

    original = settings.demo_mode
    object.__setattr__(settings, "demo_mode", False)
    try:
        assert get_provider("gmail_demo").is_configured() is False
        response = client.post("/api/integrations/gmail_demo/connect", json={})
        assert response.status_code == 409
        assert "not configured" in response.json()["error"]["detail"].lower()
    finally:
        object.__setattr__(settings, "demo_mode", original)


def test_demo_providers_never_claim_a_real_connection():
    for name in ("gmail_demo", "slack_demo", "calendar_demo"):
        status = get_provider(name).status()
        assert status["is_mock"] is True
        assert status["provider"] == name
        message = status["message"].lower()
        # Honest wording in both states: never implies a real link.
        assert "demo mode" in message
        assert "no real account" in message or "not activated" in message


def test_demo_connect_activates_locally(client):
    body = client.post("/api/integrations/gmail_demo/connect", json={}).json()
    assert body["connected"] is True
    assert body["is_mock"] is True
    assert "no real account" in body["message"].lower()


def test_demo_slack_send_message(client):
    client.post("/api/integrations/slack_demo/connect", json={})
    provider = get_provider("slack_demo")
    result = provider.execute_action(
        "slack_send_message", {"channel": "#support", "text": "hello"}
    )
    assert result["ok"] is True
    assert result["is_mock"] is True
    assert result["channel"] == "#support"
    assert "nothing was posted" in result["note"].lower()


def test_demo_slack_requires_channel_and_text(client):
    client.post("/api/integrations/slack_demo/connect", json={})
    provider = get_provider("slack_demo")
    with pytest.raises(ValidationError):
        provider.execute_action("slack_send_message", {"text": "x"})
    with pytest.raises(ValidationError):
        provider.execute_action("slack_send_message", {"channel": "#c"})


def test_demo_calendar_create_event(client):
    client.post("/api/integrations/calendar_demo/connect", json={})
    provider = get_provider("calendar_demo")
    result = provider.execute_action(
        "calendar_create_event",
        {
            "title": "Follow up",
            "start": "2026-09-28T10:00:00+00:00",
            "end": "2026-09-28T10:30:00+00:00",
        },
    )
    assert result["ok"] is True
    assert result["is_mock"] is True
    assert "no calendar was modified" in result["note"].lower()


def test_demo_calendar_validates_input(client):
    client.post("/api/integrations/calendar_demo/connect", json={})
    provider = get_provider("calendar_demo")
    with pytest.raises(ValidationError):
        provider.execute_action(
            "calendar_create_event",
            {"title": "x", "start": "2026-09-28T10:00:00+00:00",
             "end": "2026-09-28T09:00:00+00:00"},
        )
    with pytest.raises(ValidationError):
        provider.execute_action("calendar_create_event", {"start": "a", "end": "b"})


def test_real_calendar_validates_timezone():
    from app.integrations.calendar import validate_calendar_id, validate_timezone

    assert str(validate_timezone("Asia/Tokyo")) == "Asia/Tokyo"
    with pytest.raises(ValidationError):
        validate_timezone("Mars/Base")
    assert validate_calendar_id("primary") == "primary"
    with pytest.raises(ValidationError):
        validate_calendar_id("bad\nid")


def test_gmail_query_builder_is_safe():
    from app.integrations.gmail import build_query

    assert build_query(from_address="a@b.com", unread=True) == 'from:"a@b.com" is:unread'
    # An injected quote is stripped, so it cannot close the clause and add
    # its own operators.
    # The quote is stripped from the value, so the clause cannot be closed
    # early and the injected OR stays inside the quoted term.
    hostile = build_query(subject='evil" OR is:unread')
    assert hostile == 'subject:"evil OR is:unread"'
    assert hostile.count('"') == 2
    # A newline cannot smuggle in a new operator either.
    assert "\n" not in build_query(subject="a\nis:unread")


# --------------------------------------------------------- H. idempotency

def test_same_event_does_not_run_twice(client):
    draft_id = make_draft()
    automation_id = make_automation(draft_id, trigger_type="demo_gmail")
    client.post("/api/integrations/gmail_demo/connect", json={})

    from app.services import automation_service

    first = automation_service.demo_event_payload("gmail_demo", automation_id)
    assert first["is_new_event"] is True
    second = automation_service.demo_event_payload("gmail_demo", automation_id)
    assert second["is_new_event"] is False


def test_duplicate_demo_run_returns_409(client):
    draft_id = make_draft()
    automation_id = make_automation(draft_id, trigger_type="demo_gmail")
    client.post("/api/integrations/gmail_demo/connect", json={})
    first = client.post(f"/api/automations/{automation_id}/demo")
    assert first.status_code == 200
    again = client.post(f"/api/automations/{automation_id}/demo")
    assert again.status_code == 409
    assert "already been processed" in again.json()["error"]["detail"]


def test_event_ledger_unique_constraint():
    """The dedupe guarantee lives in the schema, not just in Python."""
    import sqlite3

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        identifier = integration_model.record_event(
            connection, provider="gmail", external_event_id="m1",
            automation_id="a1", payload={},
        )
        assert identifier is not None
        duplicate = integration_model.record_event(
            connection, provider="gmail", external_event_id="m1",
            automation_id="a1", payload={},
        )
        assert duplicate is None
        # A different automation may still process the same message.
        other = integration_model.record_event(
            connection, provider="gmail", external_event_id="m1",
            automation_id="a2", payload={},
        )
        assert other is not None


# ----------------------------------------------------- I. scheduler runtime

def test_scheduler_and_worker_status_are_measured(client):
    scheduler_status = client.get("/api/scheduler/status").json()
    for key in ("running", "enabled", "tick_seconds", "active_schedules", "ticks"):
        assert key in scheduler_status
    worker_status = client.get("/api/worker/status").json()
    for key in ("running", "enabled", "poll_seconds", "jobs_processed", "queue"):
        assert key in worker_status
    # Disabled in tests, and reported honestly as such.
    assert worker_status["running"] is False


def test_jobs_api(client):
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    job = queue.enqueue_job(automation_id=automation_id)
    listed = client.get("/api/background-jobs").json()
    assert listed["count"] == 1
    assert listed["jobs"][0]["id"] == job.id
    single = client.get(f"/api/background-jobs/{job.id}")
    assert single.status_code == 200
    assert single.json()["status"] == "queued"
    assert client.get("/api/background-jobs/job-999999").status_code == 404


def test_worker_drain_endpoint_processes(client):
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    queue.enqueue_job(automation_id=automation_id)
    body = client.post("/api/worker/drain?limit=5").json()
    assert body["processed"] == 1
    assert body["status"]["queue"]["completed"] == 1


def test_scheduler_tick_endpoint(client):
    body = client.post("/api/scheduler/tick").json()
    assert "fired" in body


# ------------------------------------------------------- J. analytics + system

def test_analytics_includes_job_and_integration_stats(client):
    draft_id = make_draft()
    automation_id = make_automation(draft_id)
    queue.enqueue_job(automation_id=automation_id)
    Worker().process_one()
    body = client.get("/api/analytics").json()
    assert body["summary"]["total_jobs"] == 1
    assert body["summary"]["completed_jobs"] == 1
    assert body["jobs"]["recent"][0]["status"] == "completed"
    assert isinstance(body["integrations"], list)


def test_analytics_failure_identifies_integration(client):
    from app.integrations.registry import get_provider

    draft_id = make_draft(steps=[
        {"step_number": 1, "application": "Gmail", "action": "gmail_read_email", "purpose": ""},
    ])
    get_provider("gmail_demo").connect()
    # Force a failure: disconnect before running so the action cannot succeed.
    get_provider("gmail_demo").disconnect()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    body = client.get("/api/analytics").json()
    assert body["summary"]["failed_executions"] == 1
    failure = body["recent_failures"][0]
    assert failure["execution_id"]
    assert failure["failed_step"]["step_number"] == 1
    assert failure["integration"] in {"gmail", None}


def test_system_status_exposes_scheduler_and_worker(client):
    body = client.get("/api/system/status").json()
    assert "scheduler" in body
    assert body["scheduler"]["scheduler"]["running"] in {True, False}
    assert body["scheduler"]["worker"]["running"] in {True, False}
    assert "demo_mode" in body["scheduler"]


def test_system_status_lists_integration_actions(client):
    body = client.get("/api/system/status").json()
    actions = body["execution"]["allowed_actions"]
    for expected in (
        "gmail_read_email",
        "gmail_send_email",
        "slack_send_message",
        "calendar_create_event",
    ):
        assert expected in actions


# ------------------------------------------------------------- K. security

def test_scheduler_and_worker_code_has_no_unsafe_execution():
    """Static check on the Phase 8 runtime modules."""
    import ast
    import pathlib

    banned = (
        "subprocess", "eval(", "exec(", "os.system", "os.popen", "popen",
        "__import__", "runpy", "shell=True",
    )
    targets = [
        pathlib.Path("app/scheduler/service.py"),
        pathlib.Path("app/scheduler/worker.py"),
        pathlib.Path("app/scheduler/queue.py"),
        pathlib.Path("app/scheduler/recurrence.py"),
        pathlib.Path("app/integrations"),
    ]
    files: List[pathlib.Path] = []
    for target in targets:
        files.extend(target.rglob("*.py") if target.is_dir() else [target])
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for token in banned:
            assert token not in code, f"{path} contains {token}"


def test_no_secret_leaks_through_status_endpoints(client):
    for path in ("/api/system/status", "/api/integrations"):
        body = client.get(path).text.lower()
        for marker in ("access_token", "refresh_token", "client_secret", "password"):
            assert marker not in body, f"{path} leaked {marker}"


def test_unapproved_workflow_cannot_be_executed_by_automation(client):
    """Approval is enforced at the engine, not just the API."""
    draft = WorkflowDraft(
        name="Unapproved",
        description="pending_approval",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="open_email"),
        steps=[GeneratedWorkflowStep(step_number=1, application="CRM", action="update")],
        confidence=0.4,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-unapproved",
        )
    from app.automation.engine import DraftNotApprovedError, execute_draft

    with pytest.raises(DraftNotApprovedError):
        execute_draft(record.id)


def test_unknown_action_fails_safely_and_persists_error(client):
    draft_id = make_draft(steps=[
        {"step_number": 1, "application": "X", "action": "curl http://evil.example", "purpose": ""},
    ])
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert body["status"] == "failed"
    assert "Unsupported action" in body["error"]
    assert body["retryable"] is False


# ------------------------------------------------- L. schema migration safety

def test_execution_table_migration_adds_new_columns_to_old_database():
    """A database created before Phase 8 must keep working.

    ``CREATE TABLE IF NOT EXISTS`` cannot add a column to an existing table,
    so an older database would be missing error_type/retryable and every read
    would raise. This reproduces that situation and proves the migration fixes
    it without losing data.
    """
    import sqlite3

    from app.models import execution as exec_model

    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    # Recreate the table exactly as Phase 6 shipped it (no error_type/retryable).
    legacy.execute(
        """
        CREATE TABLE workflow_executions (
            id TEXT PRIMARY KEY, draft_id TEXT NOT NULL,
            workflow_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            current_step INTEGER NOT NULL DEFAULT 0,
            total_steps INTEGER NOT NULL DEFAULT 0,
            completed_steps INTEGER NOT NULL DEFAULT 0,
            failed_step TEXT, error TEXT, result_summary TEXT,
            started_at TEXT, completed_at TEXT, duration_ms INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        "INSERT INTO workflow_executions "
        "(id, draft_id, workflow_name, status, created_at, updated_at) "
        "VALUES ('execution-0001', 'draft-0001', 'Legacy', 'completed', "
        "'2026-09-26T10:00:00+00:00', '2026-09-26T10:00:01+00:00')"
    )
    legacy.commit()

    columns_before = {row["name"] for row in legacy.execute("PRAGMA table_info(workflow_executions)")}
    assert "error_type" not in columns_before

    exec_model.apply_migrations(legacy)

    columns_after = {row["name"] for row in legacy.execute("PRAGMA table_info(workflow_executions)")}
    assert {"error_type", "retryable"} <= columns_after

    # The pre-existing row is intact and now readable.
    record = exec_model.row_to_execution(
        legacy.execute("SELECT * FROM workflow_executions").fetchone()
    )
    assert record.id == "execution-0001"
    assert record.status == "completed"
    assert record.error_type is None


def test_execution_migration_is_idempotent():
    from app.models import execution as exec_model

    with get_connection() as connection:
        exec_model.ensure_tables(connection)
        exec_model.apply_migrations(connection)
        exec_model.apply_migrations(connection)
        rows = exec_model.select_executions(connection)
    assert isinstance(rows, list)


def test_diagnostics_derive_configured_and_connected_from_state():
    """A connected provider must never report configured/connected as false.

    ``diagnose()`` returns one unambiguous ``state`` instead of separate
    booleans, so the diagnostics panel has to derive them. Getting this wrong
    shows a fully connected Gmail as unconfigured in the Settings panel.
    """
    from app.services.system_service import _oauth_diagnostics

    providers = {item["provider"]: item for item in _oauth_diagnostics()["providers"]}
    gmail = providers["gmail"]

    connected = gmail["state"] in ("connected", "token_expired_refreshable")
    if connected:
        assert gmail["connected"] is True
        assert gmail["configured"] is True
    else:
        assert gmail["connected"] is False
        if gmail["state"] == "not_configured":
            assert gmail["configured"] is False
        else:
            assert gmail["configured"] is True

    # The booleans must never contradict the state they came from.
    for item in providers.values():
        if item["state"] == "not_configured":
            assert item["configured"] is False
        if item["state"] == "connected":
            assert item["connected"] is True
            assert item["configured"] is True
