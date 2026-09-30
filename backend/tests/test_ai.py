"""Phase 5 tests — AI Understanding.

Fully offline: the suite runs on the MockProvider and never contacts
Ollama, the internet or any API key. Ollama-specific tests only exercise
configuration and a connection-refused path.
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
from app.ai.parsing import extract_json_object, parse_understanding
from app.ai.provider import AIProvider, get_provider
from app.ai import service
from app.config import settings
from app.database import get_connection
from app.models import understanding as understanding_model
from app.models import workflow_candidate as candidate_model
from app.schemas.ai import (
    WorkflowUnderstandingInput,
)
from app.schemas.discovery import WorkflowCandidate, WorkflowStep

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
    occurrence_count: int = 3,
) -> WorkflowCandidate:
    raw_steps = steps if steps is not None else DEFAULT_STEPS
    sequence = [
        WorkflowStep(**step, description=f"Did {step['action']}") for step in raw_steps
    ]
    applications = list(dict.fromkeys(step.application for step in sequence))
    return WorkflowCandidate(
        id=identifier,
        name=name,
        sequence=sequence,
        occurrence_count=occurrence_count,
        session_ids=[f"session-{index:03d}" for index in range(1, occurrence_count + 1)],
        similarity_score=1.0,
        confidence=0.85,
        confidence_label="high",
        applications=applications,
        first_seen=BASE,
        last_seen=BASE,
        status="detected",
    )


def seed(candidate: WorkflowCandidate) -> WorkflowCandidate:
    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        candidate_model.insert_candidate(connection, candidate)
    return candidate


def make_input(candidate: Optional[WorkflowCandidate] = None) -> WorkflowUnderstandingInput:
    return service.build_input(candidate or make_candidate())


def valid_ai_json() -> str:
    return json.dumps(
        {
            "workflow_id": "workflow-001",
            "workflow_name": "Customer Request Processing",
            "intent": "Process incoming customer requests",
            "description": "Reads email, updates CRM, notifies team.",
            "trigger": "Incoming customer email",
            "steps": [
                {
                    "order": 1,
                    "application": "Gmail",
                    "category": "communication",
                    "action": "Read customer email",
                    "purpose": "Understand the request",
                },
                {
                    "order": 2,
                    "application": "CRM",
                    "category": "crm",
                    "action": "Find customer",
                    "purpose": "Identify the record",
                },
                {
                    "order": 3,
                    "application": "Slack",
                    "category": "communication",
                    "action": "Notify team",
                    "purpose": "Keep the team informed",
                },
            ],
            "applications": ["Gmail", "CRM", "Slack"],
            "categories": ["communication", "crm"],
            "inputs": ["Customer email"],
            "outputs": ["Updated CRM record"],
            "dependencies": ["CRM access"],
            "assumptions": ["The email contains a customer request"],
            "confidence": 0.91,
            "suggested_automation": "Draft the CRM update for approval.",
        }
    )


class StubProvider(AIProvider):
    name = "stub"
    model = "stub-1"

    def __init__(self, response: str) -> None:
        self.response = response

    async def generate_workflow_understanding(
        self, payload: WorkflowUnderstandingInput
    ) -> str:
        return self.response


# ---------------------------------------------------------------- A. interface

def test_ai_provider_is_abstract():
    with pytest.raises(TypeError):
        AIProvider()  # type: ignore[abstract]


def test_get_provider_returns_mock_in_tests():
    provider = get_provider()
    assert isinstance(provider, AIProvider)
    assert provider.name == "mock"
    assert provider.model == MockProvider.model


def test_get_provider_name_override_builds_ollama():
    provider = get_provider("ollama")
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == settings.ollama_base_url
    assert provider.model == settings.ollama_model


# ---------------------------------------------------------------- B. mock

def test_mock_provider_is_deterministic():
    provider = MockProvider()
    payload = make_input()
    first = asyncio.run(provider.generate_workflow_understanding(payload))
    second = asyncio.run(provider.generate_workflow_understanding(payload))
    assert first == second


def test_mock_provider_output_validates():
    payload = make_input()
    raw = asyncio.run(MockProvider().generate_workflow_understanding(payload))
    understanding = parse_understanding(raw, payload.workflow_id)
    assert understanding.workflow_id == "workflow-001"
    assert len(understanding.steps) == len(payload.steps)
    assert understanding.applications == payload.applications
    assert understanding.confidence <= 1.0


def test_mock_provider_is_not_hardcoded_to_known_workflows():
    candidate = make_candidate(
        identifier="workflow-009",
        name="Ticket Triage",
        steps=[
            {"application": "Notion", "category": "file", "action": "read_ticket"},
            {"application": "Jira", "category": "system", "action": "create_issue"},
        ],
        occurrence_count=2,
    )
    payload = service.build_input(candidate)
    raw = asyncio.run(MockProvider().generate_workflow_understanding(payload))
    understanding = parse_understanding(raw, candidate.id)
    assert understanding.workflow_name == "Ticket Triage"
    assert [step.application for step in understanding.steps] == ["Notion", "Jira"]


# ---------------------------------------------------------------- C/D. schema

def test_valid_ai_json_parses():
    understanding = parse_understanding(valid_ai_json(), "workflow-001")
    assert understanding.intent.startswith("Process")
    assert understanding.confidence == 0.91
    assert len(understanding.steps) == 3


def test_schema_rejects_confidence_out_of_range():
    data = json.loads(valid_ai_json())
    data["confidence"] = 1.5
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_schema_requires_steps():
    data = json.loads(valid_ai_json())
    data["steps"] = []
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_schema_ignores_extra_fields():
    data = json.loads(valid_ai_json())
    data["unexpected_field"] = "ignore me"
    understanding = parse_understanding(json.dumps(data), "workflow-001")
    assert understanding.workflow_name == "Customer Request Processing"


# ---------------------------------------------------------------- E/F. parsing

def test_invalid_json_raises():
    with pytest.raises(AIResponseError):
        parse_understanding("this is definitely not json", "workflow-001")


def test_empty_response_raises():
    with pytest.raises(AIResponseError):
        parse_understanding("   ", "workflow-001")


def test_json_in_markdown_fence_extracts():
    raw = f"```json\n{valid_ai_json()}\n```"
    understanding = parse_understanding(raw, "workflow-001")
    assert understanding.trigger == "Incoming customer email"


def test_json_surrounded_by_prose_extracts():
    raw = f"Here is the JSON:\n{valid_ai_json()}\nHope that helps!"
    understanding = parse_understanding(raw, "workflow-001")
    assert understanding.confidence == 0.91


def test_extract_json_object_rejects_prose_without_json():
    with pytest.raises(AIResponseError):
        extract_json_object("Sorry, I cannot help with that.")


# ---------------------------------------------------------- G/H. bad payloads

def test_missing_required_field_raises():
    data = json.loads(valid_ai_json())
    del data["intent"]
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_missing_steps_field_raises():
    data = json.loads(valid_ai_json())
    del data["steps"]
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_invalid_field_type_raises():
    data = json.loads(valid_ai_json())
    data["confidence"] = "high"
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_steps_wrong_type_raises():
    data = json.loads(valid_ai_json())
    data["steps"] = "none"
    with pytest.raises(AIResponseError):
        parse_understanding(json.dumps(data), "workflow-001")


def test_non_object_json_raises():
    with pytest.raises(AIResponseError):
        parse_understanding("[1, 2, 3]", "workflow-001")


def test_workflow_id_is_enforced_from_candidate():
    data = json.loads(valid_ai_json())
    data["workflow_id"] = "workflow-999"
    understanding = parse_understanding(json.dumps(data), "workflow-001")
    assert understanding.workflow_id == "workflow-001"


# ------------------------------------------------------------ input building

def test_build_input_contains_evidence_only():
    payload = service.build_input(make_candidate())
    dumped = payload.model_dump()
    assert payload.workflow_id == "workflow-001"
    assert payload.session_count == 3
    assert payload.occurrence_count == 3
    assert payload.categories == ["communication", "crm"]
    assert [step.order for step in payload.steps] == [1, 2, 3]
    assert "first_seen" not in dumped
    assert "session_ids" not in dumped
    assert "id" not in dumped["steps"][0]
    assert "timestamp" not in dumped["steps"][0]


# ------------------------------------------------------ I/J/K/L. API + storage

def test_understand_candidate_not_found_returns_404(client):
    response = client.post("/api/ai/understand/workflow-404")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "http_error"
    assert "workflow-404" in body["error"]["detail"]
    assert body["error"]["path"] == "/api/ai/understand/workflow-404"


def test_successful_understanding_generation(client):
    seed(make_candidate())
    response = client.post("/api/ai/understand/workflow-001")
    assert response.status_code == 200
    data = response.json()
    assert data["workflow_candidate_id"] == "workflow-001"
    assert data["intent"]
    assert data["description"]
    assert data["trigger"]
    assert len(data["steps"]) == 3
    assert data["applications"] == ["Gmail", "CRM", "Slack"]
    assert data["provider"] == "mock"
    assert data["model"] == MockProvider.model
    assert data["id"].startswith("understanding-")


def test_understanding_is_persisted(client):
    seed(make_candidate())
    created = client.post("/api/ai/understand/workflow-001").json()
    with get_connection() as connection:
        understanding_model.ensure_table(connection)
        stored = understanding_model.select_by_candidate(connection, "workflow-001")
    assert stored is not None
    assert stored.id == created["id"]
    assert stored.intent == created["intent"]
    assert len(stored.steps) == len(created["steps"])


def test_get_understandings_list(client):
    assert client.get("/api/ai/understandings").json() == {
        "understandings": [],
        "count": 0,
    }
    seed(make_candidate())
    client.post("/api/ai/understand/workflow-001")
    listed = client.get("/api/ai/understandings").json()
    assert listed["count"] == 1
    assert listed["understandings"][0]["workflow_candidate_id"] == "workflow-001"


def test_get_understanding_by_id(client):
    seed(make_candidate())
    created = client.post("/api/ai/understand/workflow-001").json()
    fetched = client.get(f"/api/ai/understandings/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    missing = client.get("/api/ai/understandings/understanding-999")
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "http_error"


def test_multiple_candidates_each_understood(client):
    seed(make_candidate())
    seed(
        make_candidate(
            identifier="workflow-002",
            name="Document Processing",
            steps=[
                {"application": "Gmail", "category": "file", "action": "download"},
                {"application": "Excel", "category": "file", "action": "update"},
            ],
            occurrence_count=2,
        )
    )
    first = client.post("/api/ai/understand/workflow-001")
    second = client.post("/api/ai/understand/workflow-002")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["workflow_name"] == "Customer Request Processing"
    assert second.json()["workflow_name"] == "Document Processing"

    listed = client.get("/api/ai/understandings").json()
    assert listed["count"] == 2
    ids = {item["workflow_candidate_id"] for item in listed["understandings"]}
    assert ids == {"workflow-001", "workflow-002"}


def test_understanding_upserts_per_candidate(client):
    seed(make_candidate())
    first = client.post("/api/ai/understand/workflow-001").json()
    second = client.post("/api/ai/understand/workflow-001").json()
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert client.get("/api/ai/understandings").json()["count"] == 1


# ------------------------------------------------------------ M/N. ollama paths

def test_ollama_provider_configuration():
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
    )
    assert provider.name == "ollama"
    assert provider.base_url == "http://127.0.0.1:11434"
    assert provider.model == "qwen2.5-coder:7b"
    assert provider.timeout > 0


def test_ollama_request_body_contract():
    provider = OllamaProvider("http://127.0.0.1:11434", "qwen2.5-coder:7b")
    body = provider._request_body(make_input())
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["stream"] is False
    assert body["format"] == "json"
    assert body["options"]["temperature"] == 0
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"


def test_ollama_unavailable_raises_provider_error():
    provider = OllamaProvider("http://127.0.0.1:9", "qwen2.5-coder:7b", timeout=1.0)
    with pytest.raises(AIProviderError):
        asyncio.run(provider.generate_workflow_understanding(make_input()))


def test_api_returns_502_when_provider_unavailable(client, monkeypatch):
    seed(make_candidate())
    monkeypatch.setattr(
        service,
        "get_provider",
        lambda *args, **kwargs: OllamaProvider(
            "http://127.0.0.1:9", "qwen2.5-coder:7b", timeout=1.0
        ),
    )
    response = client.post("/api/ai/understand/workflow-001")
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["type"] == "http_error"
    assert "AI understanding unavailable" in body["error"]["detail"]


def test_api_returns_502_on_invalid_ai_response(client, monkeypatch):
    seed(make_candidate())
    monkeypatch.setattr(
        service, "get_provider", lambda *args, **kwargs: StubProvider("not json at all")
    )
    response = client.post("/api/ai/understand/workflow-001")
    assert response.status_code == 502
    assert "invalid response" in response.json()["error"]["detail"]


def test_failed_generation_is_not_persisted(client, monkeypatch):
    seed(make_candidate())
    monkeypatch.setattr(
        service, "get_provider", lambda *args, **kwargs: StubProvider("garbage")
    )
    assert client.post("/api/ai/understand/workflow-001").status_code == 502
    assert client.get("/api/ai/understandings").json()["count"] == 0
