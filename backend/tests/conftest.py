"""Shared pytest fixtures.

The database path is redirected to a temporary file *before* the application
is imported, so tests never touch the development database. The AI provider
is forced to the deterministic MockProvider, so the suite never needs
Ollama, a model, the internet or API keys.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = tempfile.mkdtemp(prefix="workflowos-test-")
os.environ["DATABASE_PATH"] = str(Path(_TMP_DIR) / "workflowos-test.db")
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["AI_PROVIDER"] = "mock"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
os.environ["OLLAMA_MODEL"] = "qwen2.5-coder:7b"
# Phase 8 background runtime: disabled so tests are deterministic. Tests drive
# the scheduler/worker explicitly through their own instances.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["WORKER_ENABLED"] = "false"
os.environ["DEMO_MODE"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        test_client.request("DELETE", "/api/activity")
        # Child rows first: steps → jobs → schedules → automations → drafts.
        _clear_executions()
        _clear_jobs()
        _clear_schedules()
        _clear_automations()
        _clear_workflow_drafts()
        _clear_understandings()
        _clear_workflow_candidates()
        _clear_integration_events()
        _clear_integration_accounts()
        yield test_client


def _clear_integration_accounts() -> None:
    """Connected-account rows decide real-vs-demo action resolution.

    A leftover account makes later tests resolve ``read_email`` to the real
    Gmail adapter, so this table must be cleared like every other one.
    """
    from app.database import get_connection
    from app.models import integration as integration_model

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        connection.execute(f"DELETE FROM {integration_model.ACCOUNTS_TABLE}")


def _clear_integration_events() -> None:
    """Recorded provider events carry dedupe keys.

    They must be cleared between tests: a stale ``dedupe_key`` from an earlier
    test makes a later poll look like a duplicate, which silently starves the
    scheduler and hides real regressions.
    """
    from app.database import get_connection
    from app.models import integration as integration_model

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        connection.execute(f"DELETE FROM {integration_model.EVENTS_TABLE}")


@pytest.fixture(scope="session", autouse=True)
def _ensure_oauth_client_configured():
    """Give the suite a dummy OAuth client when no real one is on disk.

    The credentials JSON is deliberately never committed, so on a fresh clone
    the Gmail provider would report "not configured" and unrelated tests would
    fail. A placeholder client keeps the provider *configured* for tests
    without ever contacting Google — no real secret is involved.
    """
    from app.config import settings

    if not settings.google_configured:
        object.__setattr__(
            settings, "google_client_id", "test-id.apps.googleusercontent.com"
        )
        object.__setattr__(settings, "google_client_secret", "test-secret")
    yield


@pytest.fixture(autouse=True)
def _restore_mutable_settings():
    """Undo settings mutations made by a test.

    ``settings`` is a module-level singleton, so a test that flips a setting
    with ``object.__setattr__`` would otherwise leak into every later test and
    make them fail for unrelated reasons.
    """
    from app.config import settings

    watched = (
        "demo_mode",
        "gmail_read_query",
        "gmail_reply_to",
        "gmail_reply_subject",
        "gmail_poll_interval_seconds",
    )
    saved = {name: settings.__dict__[name] for name in watched}
    try:
        yield
    finally:
        for name, value in saved.items():
            object.__setattr__(settings, name, value)


def _clear_workflow_candidates() -> None:
    from app.database import get_connection
    from app.models import workflow_candidate as candidate_model

    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {candidate_model.TABLE}")


def _clear_understandings() -> None:
    from app.database import get_connection
    from app.models import understanding as understanding_model

    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {understanding_model.TABLE}")


def _clear_workflow_drafts() -> None:
    from app.database import get_connection
    from app.models import generator as draft_model

    with get_connection() as connection:
        draft_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {draft_model.TABLE}")


def _clear_executions() -> None:
    """Steps first: workflow_execution_steps has an FK to workflow_executions."""
    from app.database import get_connection
    from app.models import execution as execution_model

    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        connection.execute(f"DELETE FROM {execution_model.STEPS_TABLE}")
        connection.execute(f"DELETE FROM {execution_model.EXECUTIONS_TABLE}")


def _clear_automations() -> None:
    """Automations FK to workflow_drafts, so clear before drafts."""
    from app.database import get_connection
    from app.models import automation as automation_model

    with get_connection() as connection:
        automation_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {automation_model.TABLE}")


def _clear_jobs() -> None:
    from app.database import get_connection
    from app.models import job as job_model

    with get_connection() as connection:
        job_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {job_model.TABLE}")


def _clear_schedules() -> None:
    from app.database import get_connection
    from app.models import schedule as schedule_model

    with get_connection() as connection:
        schedule_model.ensure_table(connection)
        connection.execute(f"DELETE FROM {schedule_model.TABLE}")
