"""Phase 6 tests — Workflow Execution.

Fully offline: execution never calls an AI provider and never touches the
network. The suite runs on MockProvider for draft creation and executes the
persisted approved draft.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from app.automation import engine
from app.automation.actions import (
    ACTION_REGISTRY,
    ActionContext,
    UnsupportedActionError,
    registry_names,
    resolve_action,
    run_action,
)
from app.automation.engine import (
    DraftNotApprovedError,
    DraftNotFoundError,
    ExecutionNotFoundError,
)
from app.database import get_connection
from app.models import activity as activity_model
from app.models import execution as execution_model
from app.models import generator as draft_model
from app.schemas.discovery import WorkflowCandidate, WorkflowStep
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)

BASE = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


def seed_candidate() -> WorkflowCandidate:
    from app.models import workflow_candidate as candidate_model

    candidate = WorkflowCandidate(
        id="workflow-001",
        name="Customer Request Processing",
        sequence=[
            WorkflowStep(
                application="Gmail",
                category="communication",
                action="open_email",
                description="Opened email",
            ),
            WorkflowStep(
                application="CRM", category="crm", action="update", description="Upd"
            ),
        ],
        occurrence_count=3,
        session_ids=["session-001"],
        similarity_score=0.94,
        confidence=0.86,
        confidence_label="high",
        applications=["Gmail", "CRM"],
        first_seen=BASE,
        last_seen=BASE,
        status="detected",
    )
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidate_model.insert_candidate(connection, candidate)
    return candidate


def make_draft(
    steps: Optional[List[dict]] = None,
    status: Optional[str] = None,
    candidate_id: str = "workflow-001",
) -> str:
    """Persist a draft for ``candidate_id`` and return its real id.

    ``status`` forces a lifecycle state: ``"approved"`` approves the draft,
    ``"rejected"`` rejects it, ``None`` leaves it ``pending_approval``.
    """
    plan = steps if steps is not None else [
        {"step_number": 1, "application": "Gmail", "action": "record_request", "purpose": "Read"},
        {"step_number": 2, "application": "CRM", "action": "find_customer", "purpose": "Look up"},
        {"step_number": 3, "application": "Slack", "action": "send_notification", "purpose": "Tell team"},
    ]
    draft = WorkflowDraft(
        name="Customer Request Workflow",
        description="Handles a customer request end to end.",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="open_email"),
        steps=[GeneratedWorkflowStep(**step) for step in plan],
        inputs=["Customer email"],
        outputs=["CRM record updated"],
        applications=["Gmail", "CRM", "Slack"],
        confidence=0.87,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        execution_model.ensure_tables(connection)
        record = draft_model.upsert_draft(
            connection,
            draft,
            provider="mock",
            model="mock-v1",
            workflow_candidate_id=candidate_id,
            understanding_id="understanding-001",
        )
        if status == "approved":
            draft_model.set_status(connection, record, "approved")
        elif status == "rejected":
            draft_model.set_status(connection, record, "rejected", "no")
    return record.id


def approved_draft(steps: Optional[List[dict]] = None) -> str:
    """Create an approved draft and return its id."""
    return make_draft(steps=steps, status="approved")


# ------------------------------------------------------- A. action registry

def test_registry_contains_only_safe_actions():
    """The allowlist grew in Phase 8 with integration actions.

    It must stay a closed, code-controlled set: the local mocks plus the
    allowlisted Gmail/Slack/Calendar actions. Nothing else may appear.
    """
    names = set(registry_names())
    assert set(ACTION_REGISTRY) == names
    # Local mock actions are always present.
    assert {
        "delay_mock",
        "log",
        "notify_mock",
        "simulate",
        "transform",
    } <= names
    # Integration actions are allowlisted explicitly, not derived.
    assert {
        "gmail_read_email",
        "gmail_send_email",
        "slack_send_message",
        "calendar_list_events",
        "calendar_create_event",
        "calendar_update_event",
    } <= names
    # Nothing shell- or eval-shaped may ever be registered.
    for banned in ("exec", "eval", "system", "shell", "subprocess", "popen"):
        assert not any(banned in name for name in names), banned


def test_registry_has_no_arbitrary_execution_entry():
    for name in registry_names():
        assert not name.startswith(("os_", "subprocess_", "sh_", "run_"))


def test_resolve_action_maps_known_actions():
    assert resolve_action("simulate") == "simulate"
    assert resolve_action("notify_mock") == "notify_mock"
    assert resolve_action("send_notification") == "notify_mock"
    assert resolve_action("Transform data") == "transform"
    assert resolve_action("find_customer") == "simulate"
    # Email-shaped actions always resolve to the REAL Gmail adapter, whether or
    # not Gmail happens to be connected right now. The provider then fails
    # loudly, so a run can never report a success that did not happen.
    assert resolve_action("open_email") == "gmail_read_email"
    assert resolve_action("read_email") == "gmail_read_email"
    assert resolve_action("send_email") == "gmail_send_email"
    assert resolve_action("search_emails") == "gmail_search_emails"


def test_resolve_action_refuses_unknown_action():
    assert resolve_action("run_shell_command") is None
    assert resolve_action("curl http://example.com") is None
    assert resolve_action("") is None
    assert resolve_action("   ") is None


def test_run_action_refuses_unregistered_name():
    with pytest.raises(UnsupportedActionError):
        run_action("os.system", ActionContext(
            execution_id="execution-0001", step_number=1,
            application="x", action="y",
        ))


def test_action_handlers_are_deterministic():
    context = ActionContext(
        execution_id="execution-0001",
        step_number=1,
        application="Gmail",
        action="record_request",
    )
    assert run_action("simulate", context) == run_action("simulate", context)


def test_transform_passes_previous_output_forward():
    context = ActionContext(
        execution_id="execution-0001",
        step_number=2,
        application="CRM",
        action="transform",
        previous_output="seed",
    )
    assert "seed" in run_action("transform", context)


# -------------------------------------------- B. approval gate (safety rule)

def test_approved_draft_executes(client):
    draft_id = approved_draft()
    response = client.post(f"/api/workflows/drafts/{draft_id}/execute")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["total_steps"] == 3
    assert body["completed_steps"] == 3
    assert len(body["steps"]) == 3


def test_pending_approval_draft_cannot_execute(client):
    draft_id = make_draft()
    response = client.post(f"/api/workflows/drafts/{draft_id}/execute")
    assert response.status_code == 409
    assert "not 'approved'" in response.json()["error"]["detail"]


def test_rejected_draft_cannot_execute(client):
    draft_id = make_draft(status="rejected")
    response = client.post(f"/api/workflows/drafts/{draft_id}/execute")
    assert response.status_code == 409
    assert client.get("/api/workflows/executions").json()["count"] == 0


def test_unknown_draft_returns_404(client):
    response = client.post("/api/workflows/drafts/draft-999/execute")
    assert response.status_code == 404
    assert "draft-999" in response.json()["error"]["detail"]


def test_refused_execution_creates_no_steps(client):
    draft_id = make_draft(status="rejected")
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        rows = connection.execute(
            f"SELECT COUNT(*) AS total FROM {execution_model.STEPS_TABLE}"
        ).fetchone()
    assert rows["total"] == 0


def test_engine_raises_typed_errors():
    # Uses its own draft id: this test has no `client` fixture, so the shared
    # database is not reset between tests.
    draft_id = make_draft(candidate_id="workflow-engine", status="rejected")
    with pytest.raises(DraftNotFoundError):
        engine.execute_draft("draft-999")
    with pytest.raises(DraftNotApprovedError):
        engine.execute_draft(draft_id)


# ----------------------------------------------- C. persistence + ordering

def test_execution_record_is_persisted(client):
    draft_id = approved_draft()
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        stored = execution_model.select_execution(connection, body["id"])
    assert stored is not None
    assert stored.status == "completed"
    assert stored.draft_id == draft_id
    assert stored.started_at is not None
    assert stored.completed_at is not None


def test_execution_steps_are_persisted(client):
    draft_id = approved_draft()
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    with get_connection() as connection:
        execution_model.ensure_tables(connection)
        steps = execution_model.select_steps(connection, body["id"])
    assert [step.step_number for step in steps] == [1, 2, 3]
    assert all(step.status == "completed" for step in steps)


def test_steps_execute_in_generated_order(client):
    draft_id = draft_id = approved_draft(
        steps=[
            {"step_number": 1, "application": "A", "action": "first", "purpose": ""},
            {"step_number": 2, "application": "B", "action": "second", "purpose": ""},
            {"step_number": 3, "application": "C", "action": "third", "purpose": ""},
            {"step_number": 4, "application": "D", "action": "fourth", "purpose": ""},
        ]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert [step["application"] for step in body["steps"]] == ["A", "B", "C", "D"]
    assert [step["step_number"] for step in body["steps"]] == [1, 2, 3, 4]


def test_steps_api_returns_generated_order(client):
    draft_id = draft_id = approved_draft(
        steps=[
            {"step_number": 1, "application": "Z", "action": "alpha", "purpose": ""},
            {"step_number": 2, "application": "Y", "action": "beta", "purpose": ""},
        ]
    )
    execution_id = client.post(
        f"/api/workflows/drafts/{draft_id}/execute"
    ).json()["id"]
    body = client.get(f"/api/workflows/executions/{execution_id}/steps").json()
    assert [step["action"] for step in body["steps"]] == ["alpha", "beta"]
    assert body["execution_id"] == execution_id


def test_duplicate_step_number_is_rejected_by_schema(client):
    """The storage layer must refuse a repeated step for one execution."""
    draft_id = approved_draft()
    execution_id = client.post(
        f"/api/workflows/drafts/{draft_id}/execute"
    ).json()["id"]
    import sqlite3

    with get_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {execution_model.STEPS_TABLE}
                    (id, execution_id, step_number, application, action, status)
                VALUES ('execstep-99999', ?, 1, 'X', 'dup', 'pending')
                """,
                (execution_id,),
            )


# ------------------------------------------------------- D. failure paths

def test_unsupported_action_fails_step_and_execution(client):
    draft_id = approved_draft(
        steps=[
            {"step_number": 1, "application": "Gmail", "action": "record_request", "purpose": ""},
            {"step_number": 2, "application": "Shell", "action": "run_shell_command", "purpose": ""},
            {"step_number": 3, "application": "CRM", "action": "update", "purpose": ""},
        ]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert body["status"] == "failed"
    assert body["steps"][1]["status"] == "failed"
    assert "Unsupported action" in body["steps"][1]["error"]
    # Later steps must not have run.
    assert body["steps"][2]["status"] == "pending"
    assert body["completed_steps"] == 1


def test_failed_step_records_error_and_summary(client):
    draft_id = approved_draft(
        steps=[{"step_number": 1, "application": "X", "action": "exec", "purpose": ""}]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert body["error"]
    assert body["failed_step"] == "1. exec"
    assert "Failed at step 1" in body["result_summary"]


def test_failed_execution_is_listed(client):
    draft_id = approved_draft(
        steps=[{"step_number": 1, "application": "X", "action": "nope", "purpose": ""}]
    )
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    listed = client.get("/api/workflows/executions?status=failed").json()
    assert listed["count"] == 1
    assert listed["executions"][0]["status"] == "failed"


# --------------------------------------------- E. reruns + history safety

def test_second_execution_creates_separate_record(client):
    draft_id = approved_draft()
    first = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    second = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert first["id"] != second["id"]
    assert client.get("/api/workflows/executions").json()["count"] == 2


def test_previous_execution_history_is_preserved(client):
    draft_id = approved_draft()
    first = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    fetched = client.get(f"/api/workflows/executions/{first['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == first["id"]
    assert fetched.json()["status"] == "completed"


def test_executions_can_be_filtered_by_draft(client):
    draft_id = approved_draft()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    filtered = client.get(f"/api/workflows/executions?draft_id={draft_id}").json()
    assert filtered["count"] == 1
    assert client.get("/api/workflows/executions?draft_id=draft-777").json()["count"] == 0


# ------------------------------------------------- F. execution + listing

def test_list_executions_empty(client):
    assert client.get("/api/workflows/executions").json() == {
        "executions": [],
        "count": 0,
    }


def test_get_unknown_execution_returns_404(client):
    response = client.get("/api/workflows/executions/execution-9999")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "http_error"


def test_get_unknown_execution_steps_returns_404(client):
    assert (
        client.get("/api/workflows/executions/execution-9999/steps").status_code == 404
    )


def test_execution_survives_restart(client):
    """Records come from SQLite, not memory — a fresh app instance sees them."""
    from fastapi.testclient import TestClient

    from app.main import app

    draft_id = approved_draft()
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    with TestClient(app) as fresh:
        again = fresh.get(f"/api/workflows/executions/{body['id']}")
        assert again.status_code == 200
        assert again.json()["status"] == "completed"
        assert len(again.json()["steps"]) == 3


# ----------------------------------------------------- G. activity audit

def test_execution_creates_activity_events(client):
    draft_id = approved_draft()
    execution_id = client.post(
        f"/api/workflows/drafts/{draft_id}/execute"
    ).json()["id"]
    events = client.get("/api/activity?limit=200").json()["events"]
    actions = [event["action"] for event in events]
    for expected in (
        "execution_created",
        "execution_started",
        "step_started",
        "step_completed",
        "execution_completed",
    ):
        assert expected in actions
    assert all(
        event["metadata"].get("execution_id") == execution_id
        for event in events
        if event["action"].startswith(("execution_", "step_"))
    )


def test_failed_execution_audits_failure_events(client):
    draft_id = approved_draft(
        steps=[{"step_number": 1, "application": "X", "action": "nope", "purpose": ""}]
    )
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    actions = [
        event["action"]
        for event in client.get("/api/activity?limit=200").json()["events"]
    ]
    assert "step_failed" in actions
    assert "execution_failed" in actions


def test_audit_events_share_execution_session(client):
    draft_id = approved_draft()
    execution_id = client.post(
        f"/api/workflows/drafts/{draft_id}/execute"
    ).json()["id"]
    events = client.get("/api/activity?limit=200").json()["events"]
    execution_events = [
        event for event in events if event["action"].startswith("execution_")
    ]
    assert execution_events
    assert {event["session_id"] for event in execution_events} == {
        f"execution-{execution_id}"
    }


# --------------------------------------------------------- H. safety

def _code_without_docstrings(path):
    """Return executable source with docstrings and comments removed.

    Prose that merely *mentions* subprocess or sockets must not trip a static
    safety check — only real code should be scanned.
    """
    import ast
    import io
    import tokenize

    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_execution_module_has_no_shell_or_dynamic_execution():
    """Static check: no subprocess/eval/exec/dynamic import in Phase 6 code."""
    import pathlib

    banned = (
        "subprocess",
        "eval(",
        "exec(",
        "os.system",
        "os.popen",
        "popen",
        "importlib",
        "__import__",
        "runpy",
    )
    paths = list(pathlib.Path("app/automation").rglob("*.py"))
    paths.append(pathlib.Path("app/models/execution.py"))
    paths.append(pathlib.Path("app/api/routes_executions.py"))
    for path in paths:
        code = _code_without_docstrings(path)
        for token in banned:
            assert token not in code, f"{path} contains {token}"


def test_execution_does_not_call_ai_provider(client, monkeypatch):
    """Execution consumes the persisted draft; it never regenerates it."""
    from app.generator import service as generator_service

    draft_id = approved_draft()
    monkeypatch.setattr(
        generator_service,
        "get_provider",
        lambda *a, **k: pytest.fail("execution must not call the AI provider"),
    )
    response = client.post(f"/api/workflows/drafts/{draft_id}/execute")
    assert response.status_code == 200


def test_execution_uses_exact_approved_draft(client):
    draft_id = approved_draft(
        steps=[
            {"step_number": 1, "application": "Alpha", "action": "first", "purpose": "p1"},
            {"step_number": 2, "application": "Beta", "action": "second", "purpose": "p2"},
        ]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert [step["application"] for step in body["steps"]] == ["Alpha", "Beta"]
    assert [step["purpose"] for step in body["steps"]] == ["p1", "p2"]
    assert body["workflow_name"] == "Customer Request Workflow"


def test_no_network_client_in_execution_paths():
    import pathlib

    network = ("httpx", "requests", "urllib", "socket", "http.client", "aiohttp")
    for relative in (
        "app/automation",
        "app/models/execution.py",
        "app/api/routes_executions.py",
    ):
        target = pathlib.Path(relative)
        files = list(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            code = _code_without_docstrings(path)
            for token in network:
                assert token not in code, f"{path} references {token}"


def test_malicious_action_name_is_refused_not_executed(client):
    draft_id = approved_draft(
        steps=[
            {"step_number": 1, "application": "Gmail", "action": "record_request", "purpose": ""},
            {
                "step_number": 2,
                "application": "System",
                "action": "os.system('touch /tmp/pwned')",
                "purpose": "",
            },
        ]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    assert body["status"] == "failed"
    assert body["steps"][1]["status"] == "failed"
    import os

    assert not os.path.exists("/tmp/pwned")


# ------------------------------------------------- I. schema validation

def test_execution_schema_states():
    from app.schemas.execution import EXECUTION_STATUSES, STEP_STATUSES

    assert EXECUTION_STATUSES == (
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    )
    assert STEP_STATUSES == (
        "pending",
        "running",
        "completed",
        "failed",
        "skipped",
    )


def test_execution_step_rejects_zero_step_number():
    from pydantic import ValidationError

    from app.schemas.execution import ExecutionStepResult

    with pytest.raises(ValidationError):
        ExecutionStepResult(
            id="execstep-00001",
            execution_id="execution-0001",
            step_number=0,
            application="X",
            action="y",
            status="pending",
        )


def test_completed_execution_has_no_stack_traces(client):
    draft_id = approved_draft(
        steps=[{"step_number": 1, "application": "X", "action": "nope", "purpose": ""}]
    )
    body = client.post(f"/api/workflows/drafts/{draft_id}/execute").json()
    serialized = json.dumps(body)
    assert "Traceback" not in serialized
    assert "File \"" not in serialized
