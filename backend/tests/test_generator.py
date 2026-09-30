"""Phase 5 tests — Workflow Generation + Human Approval.

Fully offline: the suite runs on the MockProvider and never contacts
Ollama, the internet or any API key. Ollama-specific tests only exercise
configuration and connection-refused paths.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from app.ai.errors import AIProviderError, AIResponseError
from app.ai.mock_provider import MockProvider
from app.ai.ollama_provider import OllamaProvider
from app.ai.provider import AIProvider
from app.generator import service
from app.generator.parser import normalize_draft, parse_draft
from app.generator.prompts import build_user_prompt
from app.models import generator as draft_model
from app.models.generator import InvalidTransitionError
from app.schemas.discovery import WorkflowCandidate, WorkflowStep
from app.schemas.generator import (
    GeneratedWorkflowStep,
    WorkflowDraft,
    WorkflowTrigger,
)

BASE = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)

DEFAULT_STEPS: List[dict] = [
    {"application": "Gmail", "category": "communication", "action": "open_email"},
    {"application": "CRM", "category": "crm", "action": "find_customer"},
    {"application": "Slack", "category": "communication", "action": "send_notification"},
]


def make_candidate(
    identifier: str = "workflow-001",
    name: str = "Customer Request Processing",
    steps: Optional[List[dict]] = None,
) -> WorkflowCandidate:
    raw = steps if steps is not None else DEFAULT_STEPS
    sequence = [
        WorkflowStep(**step, description=f"Did {step['action']}") for step in raw
    ]
    return WorkflowCandidate(
        id=identifier,
        name=name,
        sequence=sequence,
        occurrence_count=3,
        session_ids=["session-001", "session-002", "session-003"],
        similarity_score=0.94,
        confidence=0.86,
        confidence_label="high",
        applications=list(dict.fromkeys(s.application for s in sequence)),
        first_seen=BASE,
        last_seen=BASE,
        status="detected",
    )


def seed_candidate(candidate: Optional[WorkflowCandidate] = None) -> WorkflowCandidate:
    """Persist a candidate and its AI understanding so generation can run."""
    from app.database import get_connection
    from app.models import understanding as understanding_model
    from app.models import workflow_candidate as candidate_model
    from app.schemas.ai import (
        WorkflowUnderstanding,
        WorkflowUnderstandingStep,
    )

    resolved = candidate or make_candidate()
    understanding = WorkflowUnderstanding(
        workflow_id=resolved.id,
        workflow_name=resolved.name,
        intent="Process incoming customer requests",
        description="Reads an email, updates the CRM, notifies the team.",
        trigger="Incoming customer email",
        steps=[
            WorkflowUnderstandingStep(
                order=index,
                application=step.application,
                category=step.category,
                action=step.action,
                purpose=f"Handle {step.action}",
            )
            for index, step in enumerate(resolved.sequence, start=1)
        ],
        applications=resolved.applications,
        categories=["communication", "crm"],
        inputs=["Customer email"],
        outputs=["Updated CRM record"],
        dependencies=["CRM access"],
        assumptions=["The email contains a customer request"],
        confidence=0.88,
        suggested_automation="Draft the CRM update for approval.",
    )

    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidate_model.insert_candidate(connection, resolved)
        understanding_model.ensure_table(connection)
        understanding_model.upsert_understanding(
            connection, understanding, "mock", "mock-v1"
        )
    return resolved


def generation_input(candidate: Optional[WorkflowCandidate] = None):
    resolved = candidate or make_candidate()
    from app.schemas.ai import WorkflowUnderstandingRecord, WorkflowUnderstandingStep

    steps = [
        WorkflowUnderstandingStep(
            order=index,
            application=step.application,
            category=step.category,
            action=step.action,
            purpose=f"Handle {step.action}",
        )
        for index, step in enumerate(resolved.sequence, start=1)
    ]
    record = WorkflowUnderstandingRecord(
        id="understanding-001",
        workflow_candidate_id=resolved.id,
        workflow_name=resolved.name,
        intent="Process incoming customer requests",
        description="Reads an email, updates the CRM, notifies the team.",
        trigger="Incoming customer email",
        steps=steps,
        applications=resolved.applications,
        categories=["communication", "crm"],
        inputs=["Customer email"],
        outputs=["Updated CRM record"],
        dependencies=["CRM access"],
        assumptions=["The email contains a customer request"],
        confidence=0.88,
        suggested_automation="Draft the CRM update for approval.",
        provider="mock",
        model="mock-v1",
        created_at=BASE,
        updated_at=BASE,
    )
    return service.build_generation_input(resolved, record)


def valid_draft_json() -> str:
    return json.dumps(
        {
            "name": "Customer Request Workflow",
            "description": "Handles an incoming customer request end to end.",
            "trigger": {
                "type": "event",
                "application": "Gmail",
                "action": "open_email",
            },
            "steps": [
                {
                    "step_number": 1,
                    "application": "Gmail",
                    "action": "Open customer email",
                    "purpose": "Understand the request",
                },
                {
                    "step_number": 2,
                    "application": "CRM",
                    "action": "Find customer record",
                    "purpose": "Identify the account",
                },
                {
                    "step_number": 3,
                    "application": "Slack",
                    "action": "Notify support channel",
                    "purpose": "Keep the team informed",
                },
            ],
            "inputs": ["Customer email"],
            "outputs": ["Updated CRM record"],
            "applications": ["Gmail", "CRM", "Slack"],
            "dependencies": ["CRM access"],
            "assumptions": ["The email contains a customer request"],
            "confidence": 0.87,
        }
    )


class StubProvider(AIProvider):
    name = "stub"
    model = "stub-1"

    def __init__(self, response: str) -> None:
        self.response = response

    async def generate_workflow_understanding(self, payload) -> str:
        return ""

    async def generate_workflow_draft(self, payload) -> str:
        return self.response


# ------------------------------------------------------------- A. schemas

def test_valid_draft_parses():
    draft = parse_draft(valid_draft_json())
    assert draft.name == "Customer Request Workflow"
    assert draft.trigger.type == "event"
    assert len(draft.steps) == 3
    assert draft.confidence == 0.87


def test_draft_requires_at_least_one_step():
    data = json.loads(valid_draft_json())
    data["steps"] = []
    with pytest.raises(AIResponseError):
        parse_draft(json.dumps(data))


def test_draft_requires_name_and_description():
    data = json.loads(valid_draft_json())
    del data["name"]
    with pytest.raises(AIResponseError):
        parse_draft(json.dumps(data))

    data = json.loads(valid_draft_json())
    del data["description"]
    with pytest.raises(AIResponseError):
        parse_draft(json.dumps(data))


def test_draft_rejects_confidence_out_of_range():
    data = json.loads(valid_draft_json())
    data["confidence"] = 4.2
    with pytest.raises(AIResponseError):
        parse_draft(json.dumps(data))


def test_draft_rejects_wrong_field_types():
    data = json.loads(valid_draft_json())
    data["steps"] = "open email then update crm"
    with pytest.raises(AIResponseError):
        parse_draft(json.dumps(data))


# ------------------------------------------------- B/C. parsing + normalize

def test_malformed_json_raises():
    with pytest.raises(AIResponseError):
        parse_draft("I cannot generate a workflow for this.")


def test_empty_response_raises():
    with pytest.raises(AIResponseError):
        parse_draft("   ")


def test_draft_in_markdown_fence_extracts():
    draft = parse_draft(f"```json\n{valid_draft_json()}\n```")
    assert draft.name == "Customer Request Workflow"


def test_draft_surrounded_by_prose_extracts():
    draft = parse_draft(f"Here you go:\n{valid_draft_json()}\nLet me know!")
    assert draft.confidence == 0.87


def test_non_object_json_raises():
    with pytest.raises(AIResponseError):
        parse_draft("[1, 2, 3]")


def test_normalize_repairs_trigger_sentence():
    data = json.loads(valid_draft_json())
    data["trigger"] = "An incoming customer email"
    draft = parse_draft(json.dumps(data))
    assert draft.trigger.action == "An incoming customer email"
    assert draft.trigger.type == "event"


def test_normalize_renumbers_steps_from_one():
    data = json.loads(valid_draft_json())
    data["steps"] = [
        {"step_number": 0, "application": "Gmail", "action": "Open email"},
        {"step_number": 99, "application": "CRM", "action": "Update record"},
    ]
    draft = parse_draft(json.dumps(data))
    assert [step.step_number for step in draft.steps] == [1, 2]


def test_normalize_drops_extra_fields():
    data = json.loads(valid_draft_json())
    data["unexpected"] = "ignored"
    assert parse_draft(json.dumps(data)).name == "Customer Request Workflow"


# ------------------------------------------------------- D. prompt building

def test_generation_prompt_requests_json_and_lists_schema():
    from app.generator.prompts import SYSTEM_PROMPT

    prompt = build_user_prompt(generation_input())
    assert "single JSON object only" in prompt
    assert '"trigger"' in prompt
    assert "step_number" in prompt
    assert "Do not execute actions" in SYSTEM_PROMPT


# ------------------------------------------------------------- E. providers

def test_mock_generation_is_deterministic():
    payload = generation_input()
    provider = MockProvider()
    first = asyncio.run(provider.generate_workflow_draft(payload))
    second = asyncio.run(provider.generate_workflow_draft(payload))
    assert first == second


def test_mock_generation_validates():
    payload = generation_input()
    raw = asyncio.run(MockProvider().generate_workflow_draft(payload))
    draft = parse_draft(raw)
    assert draft.name == "Customer Request Processing"
    assert len(draft.steps) == len(payload.steps)
    assert draft.applications == payload.applications


def test_mock_generation_adapts_to_any_workflow():
    candidate = make_candidate(
        identifier="workflow-777",
        name="Ticket Triage",
        steps=[
            {"application": "Notion", "category": "file", "action": "read_ticket"},
            {"application": "Jira", "category": "system", "action": "create_issue"},
        ],
    )
    raw = asyncio.run(MockProvider().generate_workflow_draft(generation_input(candidate)))
    draft = parse_draft(raw)
    assert draft.name == "Ticket Triage"
    assert [step.application for step in draft.steps] == ["Notion", "Jira"]


def test_ollama_generation_request_body_contract():
    from app.config import settings

    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
    )
    body = provider._generation_request_body(generation_input())
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["messages"][0]["role"] == "system"
    assert "single JSON object only" in body["messages"][1]["content"]


def test_ollama_unavailable_raises_provider_error():
    provider = OllamaProvider("http://127.0.0.1:9", "qwen2.5-coder:7b", timeout=1.0)
    with pytest.raises(AIProviderError):
        asyncio.run(provider.generate_workflow_draft(generation_input()))


def test_ollama_empty_response_raises_provider_error(monkeypatch):
    import app.ai.ollama_provider as module

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "   "}}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    provider = module.OllamaProvider("http://127.0.0.1:11434", "qwen2.5-coder:7b")
    with pytest.raises(AIProviderError):
        asyncio.run(provider.generate_workflow_draft(generation_input()))


# ------------------------------------------------------- F. generation + API

def test_generate_unknown_candidate_returns_404(client):
    response = client.post("/api/workflows/drafts/workflow-404/generate")
    assert response.status_code == 404
    assert "workflow-404" in response.json()["error"]["detail"]


def test_generate_without_understanding_returns_409(client):
    from app.database import get_connection
    from app.models import workflow_candidate as candidate_model

    candidate = make_candidate()
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidate_model.insert_candidate(connection, candidate)

    response = client.post(f"/api/workflows/drafts/{candidate.id}/generate")
    assert response.status_code == 409
    assert "understanding" in response.json()["error"]["detail"].lower()


def test_successful_generation_persists_draft(client):
    seed_candidate()
    response = client.post("/api/workflows/drafts/workflow-001/generate")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_candidate_id"] == "workflow-001"
    assert body["status"] == "pending_approval"
    assert body["generated_by"] == "mock"
    assert body["model"] == "mock-v1"
    assert body["id"].startswith("draft-")
    assert len(body["steps"]) == 3
    assert body["trigger"]["type"] == "event"


def test_generated_draft_is_stored_in_sqlite(client):
    seed_candidate()
    created = client.post("/api/workflows/drafts/workflow-001/generate").json()

    from app.database import get_connection

    with get_connection() as connection:
        draft_model.ensure_table(connection)
        stored = draft_model.select_by_id(connection, created["id"])
    assert stored is not None
    assert stored.name == created["name"]
    assert len(stored.steps) == 3
    assert stored.status == "pending_approval"


def test_failed_generation_is_not_persisted(client, monkeypatch):
    seed_candidate()
    monkeypatch.setattr(
        service, "get_provider", lambda *a, **k: StubProvider("not json at all")
    )
    assert (
        client.post("/api/workflows/drafts/workflow-001/generate").status_code == 502
    )
    assert client.get("/api/workflows/drafts").json()["count"] == 0


def test_malformed_ai_response_returns_502(client, monkeypatch):
    seed_candidate()
    monkeypatch.setattr(
        service, "get_provider", lambda *a, **k: StubProvider("garbage")
    )
    response = client.post("/api/workflows/drafts/workflow-001/generate")
    assert response.status_code == 502
    assert "invalid draft" in response.json()["error"]["detail"]


def test_provider_failure_returns_502(client, monkeypatch):
    seed_candidate()
    monkeypatch.setattr(
        service,
        "get_provider",
        lambda *a, **k: OllamaProvider("http://127.0.0.1:9", "qwen2.5-coder:7b", 1.0),
    )
    response = client.post("/api/workflows/drafts/workflow-001/generate")
    assert response.status_code == 502
    assert "unavailable" in response.json()["error"]["detail"]


# --------------------------------------------------------- G. list / get

def test_list_drafts_empty(client):
    assert client.get("/api/workflows/drafts").json() == {"drafts": [], "count": 0}


def test_list_and_get_draft(client):
    seed_candidate()
    created = client.post("/api/workflows/drafts/workflow-001/generate").json()

    listed = client.get("/api/workflows/drafts").json()
    assert listed["count"] == 1
    assert listed["drafts"][0]["id"] == created["id"]

    fetched = client.get(f"/api/workflows/drafts/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_get_unknown_draft_returns_404(client):
    response = client.get("/api/workflows/drafts/draft-999")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "http_error"


def test_regeneration_updates_in_place(client):
    seed_candidate()
    first = client.post("/api/workflows/drafts/workflow-001/generate").json()
    second = client.post("/api/workflows/drafts/workflow-001/generate").json()
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert client.get("/api/workflows/drafts").json()["count"] == 1


# ------------------------------------------------- H. approval state machine

def test_approve_pending_draft(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    response = client.post(f"/api/workflows/drafts/{draft['id']}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_at"] is not None


def test_reject_pending_draft_with_reason(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    response = client.post(
        f"/api/workflows/drafts/{draft['id']}/reject",
        json={"reason": "Not the right order"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "Not the right order"


def test_double_approval_returns_409(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    client.post(f"/api/workflows/drafts/{draft['id']}/approve")
    again = client.post(f"/api/workflows/drafts/{draft['id']}/approve")
    assert again.status_code == 409
    assert "approved" in again.json()["error"]["detail"]


def test_approved_draft_cannot_be_rejected(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    client.post(f"/api/workflows/drafts/{draft['id']}/approve")
    response = client.post(f"/api/workflows/drafts/{draft['id']}/reject")
    assert response.status_code == 409


def test_rejected_draft_cannot_be_approved(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    client.post(f"/api/workflows/drafts/{draft['id']}/reject")
    response = client.post(f"/api/workflows/drafts/{draft['id']}/approve")
    assert response.status_code == 409


def test_approve_unknown_draft_returns_404(client):
    assert client.post("/api/workflows/drafts/draft-999/approve").status_code == 404


def test_state_machine_transitions_are_explicit():
    assert draft_model.ALLOWED_TRANSITIONS["pending_approval"] == (
        "approved",
        "rejected",
    )
    assert draft_model.ALLOWED_TRANSITIONS["approved"] == ()
    assert draft_model.ALLOWED_TRANSITIONS["rejected"] == ()


def test_invalid_transition_raises_model_error(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    client.post(f"/api/workflows/drafts/{draft['id']}/approve")

    from app.database import get_connection

    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.select_by_id(connection, draft["id"])
        with pytest.raises(InvalidTransitionError):
            draft_model.set_status(connection, record, "rejected")


# ------------------------------------------------------------- I. delete

def test_delete_draft(client):
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    assert client.delete(f"/api/workflows/drafts/{draft['id']}").status_code == 200
    assert client.get("/api/workflows/drafts").json()["count"] == 0
    assert client.get(f"/api/workflows/drafts/{draft['id']}").status_code == 404


def test_delete_unknown_draft_returns_404(client):
    assert client.delete("/api/workflows/drafts/draft-999").status_code == 404


def test_regenerate_after_delete_creates_new_draft(client):
    seed_candidate()
    first = client.post("/api/workflows/drafts/workflow-001/generate").json()
    client.delete(f"/api/workflows/drafts/{first['id']}")
    second = client.post("/api/workflows/drafts/workflow-001/generate").json()
    assert second["status"] == "pending_approval"
    assert client.get("/api/workflows/drafts").json()["count"] == 1


# --------------------------------------------------------- J. no execution

def test_approval_does_not_execute_anything(client, monkeypatch):
    """Approval only flips status — no provider call, no side effect."""
    from app.generator import service as generator_service

    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()

    def explode(*args, **kwargs):
        raise AssertionError("approval must not call the AI provider")

    monkeypatch.setattr(generator_service, "get_provider", explode)
    assert client.post(f"/api/workflows/drafts/{draft['id']}/approve").status_code == 200


def test_draft_step_is_data_not_a_command(client):
    """Steps describe intent only — no executor, command or payload hooks."""
    seed_candidate()
    draft = client.post("/api/workflows/drafts/workflow-001/generate").json()
    forbidden = {"executor", "command", "run", "execute", "webhook", "shell", "payload"}
    for step in draft["steps"]:
        assert not (set(step) & forbidden)
        assert isinstance(step["step_number"], int)
        assert step["application"] and step["action"]
    # Approval state is recorded, not acted upon.
    assert draft["status"] == "pending_approval"


def test_manual_draft_construction_round_trips():
    draft = WorkflowDraft(
        name="Manual plan",
        description="A plan built by hand",
        trigger=WorkflowTrigger(type="manual", application="Operator", action="start"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="CRM", action="update_record", purpose="Sync"
            )
        ],
        confidence=0.5,
    )
    assert draft.trigger.type == "manual"
    assert draft.applications == []
