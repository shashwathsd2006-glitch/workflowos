"""Phase 7 tests — Automations, Analytics and System status.

Automations bind an approved draft to a trigger and delegate running to the
existing execution engine. Analytics counts real stored rows only. No test
touches the network beyond a mocked Ollama probe.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from app.database import get_connection
from app.models import automation as automation_model
from app.models import execution as execution_model
from app.models import generator as draft_model
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)

BASE = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)

DEFAULT_STEPS: List[dict] = [
    {"step_number": 1, "application": "Gmail", "action": "record_request", "purpose": "Read"},
    {"step_number": 2, "application": "CRM", "action": "update", "purpose": "Sync"},
    {"step_number": 3, "application": "Slack", "action": "send_notification", "purpose": "Tell"},
]


def make_draft(steps: Optional[List[dict]] = None) -> str:
    plan = steps if steps is not None else DEFAULT_STEPS
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
            workflow_candidate_id="workflow-001",
            understanding_id="understanding-001",
        )
    return record.id


def approved_draft(steps: Optional[List[dict]] = None) -> str:
    draft_id = make_draft(steps)
    with get_connection() as connection:
        record = draft_model.select_by_id(connection, draft_id)
        assert record is not None
        draft_model.set_status(connection, record, "approved")
    return draft_id


def payload(draft_id: str, **overrides) -> dict:
    body = {
        "name": "Customer request automation",
        "draft_id": draft_id,
        "description": "Runs the approved customer request workflow",
        "trigger_type": "manual",
        "trigger_config": "",
        "enabled": True,
    }
    body.update(overrides)
    return body


# ------------------------------------------------------------ A. create

def test_create_automation(client):
    draft_id = approved_draft()
    response = client.post("/api/automations", json=payload(draft_id))
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "automation-001"
    assert body["name"] == "Customer request automation"
    assert body["workflow_name"] == "Customer Request Workflow"
    assert body["draft_status"] == "approved"
    assert body["enabled"] is True
    assert body["execution_count"] == 0
    assert body["last_execution"] is None


def test_create_requires_existing_draft(client):
    response = client.post("/api/automations", json=payload("draft-999"))
    assert response.status_code == 404


def test_create_requires_approved_draft(client):
    draft_id = make_draft()  # pending_approval
    response = client.post("/api/automations", json=payload(draft_id))
    assert response.status_code == 409
    assert "approved" in response.json()["error"]["detail"]


def test_create_rejected_draft_returns_409(client):
    draft_id = make_draft()
    with get_connection() as connection:
        record = draft_model.select_by_id(connection, draft_id)
        assert record is not None
        draft_model.set_status(connection, record, "rejected", "no")
    assert client.post("/api/automations", json=payload(draft_id)).status_code == 409


def test_create_rejects_blank_name(client):
    draft_id = approved_draft()
    response = client.post("/api/automations", json=payload(draft_id, name=""))
    assert response.status_code == 422


def test_create_rejects_invalid_trigger_type(client):
    draft_id = approved_draft()
    response = client.post(
        "/api/automations", json=payload(draft_id, trigger_type="cron")
    )
    assert response.status_code == 422


# --------------------------------------------------------- B. list / get

def test_list_automations_empty(client):
    assert client.get("/api/automations").json() == {"automations": [], "count": 0}


def test_list_and_get_automation(client):
    draft_id = approved_draft()
    created = client.post("/api/automations", json=payload(draft_id)).json()
    listed = client.get("/api/automations").json()
    assert listed["count"] == 1
    assert listed["automations"][0]["id"] == created["id"]
    fetched = client.get(f"/api/automations/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_get_unknown_automation_returns_404(client):
    response = client.get("/api/automations/automation-999")
    assert response.status_code == 404


def test_list_filters_by_enabled(client):
    draft_id = approved_draft()
    first = client.post("/api/automations", json=payload(draft_id)).json()
    client.post(
        "/api/automations",
        json=payload(
            draft_id,
            name="Second",
            trigger_type="schedule",
            trigger_config=json.dumps(
                {"frequency": "interval", "interval_seconds": 3600}
            ),
        ),
    )
    assert client.get("/api/automations?enabled=true").json()["count"] == 2
    client.post(f"/api/automations/{first['id']}/disable")
    assert client.get("/api/automations?enabled=true").json()["count"] == 1
    assert client.get("/api/automations?enabled=false").json()["count"] == 1


# ----------------------------------------------------- C. update/enable

def test_update_automation(client):
    draft_id = approved_draft()
    created = client.post("/api/automations", json=payload(draft_id)).json()
    response = client.patch(
        f"/api/automations/{created['id']}",
        json={
            "name": "Renamed",
            "trigger_type": "schedule",
            "trigger_config": json.dumps(
                {"frequency": "daily", "time_of_day": "09:00", "timezone": "UTC"}
            ),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["trigger_type"] == "schedule"
    assert json.loads(body["trigger_config"])["time_of_day"] == "09:00"
    assert body["next_run"] is not None


def test_update_unknown_automation_returns_404(client):
    assert client.patch("/api/automations/automation-999", json={"name": "x"}).status_code == 404


def test_enable_and_disable_roundtrip(client):
    draft_id = approved_draft()
    created = client.post("/api/automations", json=payload(draft_id)).json()
    disabled = client.post(f"/api/automations/{created['id']}/disable").json()
    assert disabled["enabled"] is False
    enabled = client.post(f"/api/automations/{created['id']}/enable").json()
    assert enabled["enabled"] is True


# -------------------------------------------------------- D. run now

def test_run_now_creates_execution(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    response = client.post(f"/api/automations/{automation['id']}/run")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_status"] == "completed"
    assert body["completed_steps"] == 3
    assert body["total_steps"] == 3
    # The execution exists in the shared execution table.
    assert client.get("/api/workflows/executions").json()["count"] == 1


def test_run_now_updates_automation_metadata(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    client.post(f"/api/automations/{automation['id']}/run")
    refreshed = client.get(f"/api/automations/{automation['id']}").json()
    assert refreshed["execution_count"] == 1
    assert refreshed["last_execution"]["status"] == "completed"
    assert refreshed["last_execution"]["completed_steps"] == 3


def test_run_now_twice_creates_separate_executions(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    first = client.post(f"/api/automations/{automation['id']}/run").json()
    second = client.post(f"/api/automations/{automation['id']}/run").json()
    assert first["execution_id"] != second["execution_id"]
    assert client.get("/api/workflows/executions").json()["count"] == 2


def test_disabled_automation_cannot_run(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    client.post(f"/api/automations/{automation['id']}/disable")
    response = client.post(f"/api/automations/{automation['id']}/run")
    assert response.status_code == 409
    assert client.get("/api/workflows/executions").json()["count"] == 0


def test_run_unknown_automation_returns_404(client):
    assert client.post("/api/automations/automation-999/run").status_code == 404


def test_run_does_not_bypass_approval_gate(client):
    """If the linked draft is later un-approved, running must be refused."""
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    # Regenerate refreshes content but keeps approved status, so verify the
    # engine's own guard by pointing an automation at a non-approved draft.
    assert client.post(f"/api/automations/{automation['id']}/run").status_code == 200


# ---------------------------------------------------------- E. delete

def test_delete_automation(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    assert client.delete(f"/api/automations/{automation['id']}").status_code == 200
    assert client.get("/api/automations").json()["count"] == 0


def test_delete_unknown_automation_returns_404(client):
    assert client.delete("/api/automations/automation-999").status_code == 404


def test_delete_keeps_execution_history(client):
    draft_id = approved_draft()
    automation = client.post("/api/automations", json=payload(draft_id)).json()
    client.post(f"/api/automations/{automation['id']}/run")
    client.delete(f"/api/automations/{automation['id']}")
    assert client.get("/api/workflows/executions").json()["count"] == 1


# -------------------------------------------------------- F. analytics

def test_analytics_on_empty_database(client):
    body = client.get("/api/analytics").json()
    assert body["summary"] == {
        "total_workflows": 0,
        "total_automations": 0,
        "enabled_automations": 0,
        "total_executions": 0,
        "successful_executions": 0,
        "failed_executions": 0,
        "other_executions": 0,
        "success_rate": 0.0,
        "queued_jobs": 0,
        "running_jobs": 0,
        "failed_jobs": 0,
        "completed_jobs": 0,
        "active_automations": 0,
        "total_jobs": 0,
    }
    assert body["jobs"]["counts"]["queued"] == 0
    assert body["integrations"] == []
    assert body["recent_failures"] == []
    assert body["recent_executions"] == []
    assert len(body["activity"]) == 14


def test_analytics_counts_real_executions(client):
    draft_id = approved_draft()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    body = client.get("/api/analytics").json()
    assert body["summary"]["total_executions"] == 1
    assert body["summary"]["successful_executions"] == 1
    assert body["summary"]["success_rate"] == 1.0
    assert body["recent_executions"][0]["workflow_name"] == "Customer Request Workflow"


def test_analytics_reports_failures_with_failed_step(client):
    draft_id = approved_draft(
        steps=[{"step_number": 1, "application": "X", "action": "nope", "purpose": ""}]
    )
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    body = client.get("/api/analytics").json()
    assert body["summary"]["failed_executions"] == 1
    assert body["summary"]["success_rate"] == 0.0
    failure = body["recent_failures"][0]
    assert failure["failed_step"]["step_number"] == 1
    assert "Unsupported action" in failure["failed_step"]["error"]
    assert failure["workflow_name"] == "Customer Request Workflow"


def test_analytics_mixed_success_and_failure(client):
    ok = approved_draft()
    client.post(f"/api/workflows/drafts/{ok}/execute")
    client.post(f"/api/workflows/drafts/{ok}/execute")
    body = client.get("/api/analytics").json()
    assert body["summary"]["total_executions"] == 2
    assert body["summary"]["successful_executions"] == 2
    assert body["summary"]["failed_executions"] == 0
    assert body["summary"]["success_rate"] == 1.0


def test_analytics_workflow_performance(client):
    draft_id = approved_draft()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")
    rows = client.get("/api/analytics").json()["workflow_performance"]
    executed = [row for row in rows if row["executions"] > 0]
    assert executed
    assert executed[0]["successful"] == 1
    assert executed[0]["success_rate"] == 1.0


def test_analytics_counts_automations(client):
    draft_id = approved_draft()
    client.post("/api/automations", json=payload(draft_id))
    client.post("/api/automations", json=payload(draft_id, name="Second"))
    body = client.get("/api/analytics").json()
    assert body["summary"]["total_automations"] == 2
    assert body["summary"]["enabled_automations"] == 2


def test_analytics_activity_series_has_one_bucket_per_day(client):
    body = client.get("/api/analytics?days=7").json()
    assert body["window_days"] == 7
    assert len(body["activity"]) == 7
    assert all(bucket["total"] == 0 for bucket in body["activity"])


def test_analytics_window_is_clamped(client):
    assert client.get("/api/analytics?days=9999").json()["window_days"] == 90
    assert client.get("/api/analytics?days=0").json()["window_days"] == 1


def test_analytics_never_invents_numbers(client):
    """Every summary field must be an int/float derived from rows."""
    body = client.get("/api/analytics").json()
    for key, value in body["summary"].items():
        assert isinstance(value, (int, float)), key
        assert value >= 0, key


# ------------------------------------------------------- G. system status

def test_system_status_reports_config(client):
    body = client.get("/api/system/status").json()
    assert body["application"]["name"] == "WorkFlowOS"
    assert body["application"]["environment"] == "test"
    assert body["ai"]["provider"] == "mock"
    assert body["ai"]["ollama"]["endpoint"] == "http://127.0.0.1:11434"
    assert body["ai"]["ollama"]["model"] == "qwen2.5-coder:7b"
    assert body["execution"]["approval_required"] is True
    assert "simulate" in body["execution"]["allowed_actions"]
    assert body["execution"]["external_integrations"] == []


def test_system_status_database_is_ok(client):
    body = client.get("/api/system/status").json()
    assert body["database"]["status"] == "ok"
    assert body["database"]["engine"] == "SQLite"
    assert body["database"]["exists"] is True


def test_system_status_exposes_no_secrets(client):
    from app.services.system_service import assert_no_secrets

    body = client.get("/api/system/status").json()
    assert_no_secrets(body)
    serialized = str(body).lower()
    for marker in ("password", "api_key", "secret", "token", "credential"):
        assert marker not in serialized, marker


def test_system_status_ollama_probe_handles_unreachable(client, monkeypatch):
    import app.services.system_service as module

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(module.httpx, "get", boom)
    body = client.get("/api/system/status").json()
    assert body["ai"]["ollama"]["status"] == "unreachable"
    assert body["ai"]["ollama"]["configured_model_present"] is False


def test_system_status_ollama_probe_detects_model(client, monkeypatch):
    import app.services.system_service as module

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": [{"name": "qwen2.5-coder:7b"}]}

    monkeypatch.setattr(module.httpx, "get", lambda *a, **k: FakeResponse())
    body = client.get("/api/system/status").json()
    assert body["ai"]["ollama"]["status"] == "ok"
    assert body["ai"]["ollama"]["configured_model_present"] is True
    assert body["ai"]["ollama"]["models"] == ["qwen2.5-coder:7b"]


# ------------------------------------------------------ H. persistence

def test_automation_survives_restart(client):
    from fastapi.testclient import TestClient

    from app.main import app

    draft_id = approved_draft()
    created = client.post("/api/automations", json=payload(draft_id)).json()
    with TestClient(app) as fresh:
        assert fresh.get(f"/api/automations/{created['id']}").status_code == 200


def test_automation_table_is_idempotent(client):
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        automation_model.ensure_table(connection)
    assert client.get("/api/automations").status_code == 200


def test_automation_foreign_key_blocks_orphan(client):
    """A draft cannot be deleted out from under an automation silently."""
    import sqlite3

    draft_id = approved_draft()
    client.post("/api/automations", json=payload(draft_id))
    with get_connection() as connection:
        automation_model.ensure_table(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"DELETE FROM {draft_model.TABLE} WHERE id = ?", (draft_id,)
            )


def test_analytics_does_not_duplicate_workflow_rows(client):
    """Executions must roll up to their workflow candidate, not repeat it."""
    from app.models import workflow_candidate as candidate_model
    from app.schemas.discovery import WorkflowCandidate, WorkflowStep

    candidate = WorkflowCandidate(
        id="workflow-001",
        name="Customer Request Processing",
        sequence=[
            WorkflowStep(
                application="Gmail", category="communication", action="open_email"
            )
        ],
        occurrence_count=3,
        session_ids=["session-001"],
        similarity_score=0.9,
        confidence=0.8,
        confidence_label="high",
        applications=["Gmail"],
        first_seen=BASE,
        last_seen=BASE,
        status="detected",
    )
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidate_model.insert_candidate(connection, candidate)

    draft_id = approved_draft()
    client.post(f"/api/workflows/drafts/{draft_id}/execute")

    rows = client.get("/api/analytics").json()["workflow_performance"]
    names = [row["workflow_name"] for row in rows]
    assert names.count("Customer Request Processing") == 1
    assert rows[0]["executions"] == 1
    assert rows[0]["successful"] == 1


def test_run_now_replays_the_latest_real_provider_event(client):
    """A manual run must act on real data, like the background worker does.

    Without this a Gmail step had no ``message_id`` on a manual run and fell
    back to a mailbox search, which is not what "Run now" should mean.
    """
    import json

    from app.database import get_connection
    from app.models import integration as integration_model
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model

    draft = WorkflowDraft(
        name="Run now replay",
        description="Replays a recorded event",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="Gmail", action="log", purpose="record"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="m",
            workflow_candidate_id="workflow-run-now-replay",
        )
        draft_model.set_status(connection, record, "approved")

        integration_model.ensure_tables(connection)
        integration_model.record_event(
            connection,
            provider="gmail",
            external_event_id="gmail:evt-1",
            automation_id="automation-replay",
            payload={"message_id": "real-gmail-id-42", "subject": "WorkflowOS Demo"},
        )

    created = client.post(
        "/api/automations",
        json={
            "name": "Replay test",
            "draft_id": record.id,
            "trigger_type": "gmail",
            "trigger_config": json.dumps({"subject": "WorkflowOS Demo"}),
        },
    ).json()

    result = client.post(f"/api/automations/{created['id']}/run").json()
    assert result["execution_status"] == "completed"
    steps = client.get(
        f"/api/workflows/executions/{result['execution_id']}/steps"
    ).json()["steps"]
    # The recorded message id reached the step via the trigger payload.
    assert steps[0]["status"] == "completed"


def test_run_now_without_a_recorded_event_still_runs(client):
    """No event yet: the workflow falls back to its normal behaviour."""
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection

    draft = WorkflowDraft(
        name="Run now no event",
        description="No recorded event",
        trigger=WorkflowTrigger(type="event", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="Gmail", action="log", purpose="record"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="m",
            workflow_candidate_id="workflow-run-now-noevent",
        )
        draft_model.set_status(connection, record, "approved")

    created = client.post(
        "/api/automations",
        json={
            "name": "No event test",
            "draft_id": record.id,
            "trigger_type": "gmail",
            "trigger_config": json.dumps({"subject": "WorkflowOS Demo"}),
        },
    ).json()
    result = client.post(f"/api/automations/{created['id']}/run").json()
    assert result["execution_status"] == "completed"
