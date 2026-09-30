"""The real execution path: local AI inference and honest failure.

These tests pin the guarantees the hackathon demo depends on:

* a step that needs the model performs **real** inference through the
  configured provider and stores a validated result;
* an unreachable model, a malformed reply, or missing content makes the step
  fail — never a fabricated answer;
* an action aimed at a real service WorkFlowOS can actually call is never
  quietly downgraded to the local simulator.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.ai.errors import AIProviderError
from app.automation.actions import (
    ActionContext,
    NonRetryableActionError,
    RetryableActionError,
    resolve_action,
    run_action,
)
from app.schemas.ai import ContentAnalysis, ContentAnalysisRequest


# --------------------------------------------------------------- resolution


def test_email_actions_never_resolve_to_the_simulator():
    for action in ("read_email", "open_email", "send_email", "search_emails"):
        resolved = resolve_action(action, application="Gmail")
        assert resolved is not None, action
        assert resolved != "simulate", action
        assert resolved.startswith("gmail_"), action


def test_external_application_step_cannot_fall_back_to_simulate():
    """An unknown Gmail/Slack/Calendar action must fail, not pretend."""
    for application in ("Gmail", "Slack", "Calendar"):
        assert (
            resolve_action("frobnicate_widget", application=application) is None
        ), application
    # A non-integrated application keeps its documented local behaviour.
    assert resolve_action("fetch_report", application="CRM") == "simulate"


def test_ai_shaped_actions_resolve_to_the_local_ai_action():
    for action in (
        "analyze_email",
        "classify_priority",
        "understand_email",
        "triage_request",
    ):
        assert resolve_action(action, application="WorkFlowOS AI") == (
            "ai_analyze_content"
        ), action


def test_ai_action_is_registered_and_described():
    from app.automation.actions import ACTION_DESCRIPTIONS, registry_names

    assert "ai_analyze_content" in registry_names()
    assert ACTION_DESCRIPTIONS["ai_analyze_content"]


# ------------------------------------------------------------------ content


def _context(**overrides) -> ActionContext:
    payload = {
        "execution_id": "execution-0001",
        "step_number": 3,
        "application": "WorkFlowOS AI",
        "action": "analyze_email",
        "purpose": "Classify the email priority",
    }
    payload.update(overrides)
    return ActionContext(**payload)


def test_ai_step_analyses_the_previous_steps_real_output():
    """The content analysed must be the real previous step's output."""
    previous = json.dumps(
        {
            "provider": "gmail",
            "is_mock": False,
            "result": {
                "from": "customer@example.com",
                "subject": "URGENT: charged twice",
                "snippet": "I was charged twice for invoice 88213",
            },
        }
    )
    context = _context(previous_output=previous)
    output = json.loads(run_action("ai_analyze_content", context))
    assert output["provider"] == "mock"  # AI_PROVIDER=mock under pytest
    assert output["action"] == "analyze_content"
    assert "URGENT: charged twice" not in output["analysed_characters"].__str__()
    assert output["analysed_characters"] > 0
    ContentAnalysis.model_validate(output["result"])


def test_ai_step_records_the_task_it_was_given():
    context = _context(
        previous_output=json.dumps({"result": {"subject": "hello"}}),
        parameters={"task": "Decide whether this needs a refund."},
    )
    output = json.loads(run_action("ai_analyze_content", context))
    assert output["task"] == "Decide whether this needs a refund."


def test_ai_step_uses_an_explicit_content_parameter_when_given():
    context = _context(parameters={"content": "explicit content wins"})
    output = json.loads(run_action("ai_analyze_content", context))
    assert output["analysed_characters"] == len("explicit content wins")


# ------------------------------------------------------------ honest failure


def test_ai_step_fails_when_there_is_no_content_to_analyse():
    context = _context()
    with pytest.raises(NonRetryableActionError) as excinfo:
        run_action("ai_analyze_content", context)
    assert "No content to analyse" in str(excinfo.value)


def test_ai_step_fails_loudly_when_the_model_is_unavailable(monkeypatch):
    """An unreachable local model must never yield a canned answer."""
    import app.ai.provider as provider_module

    class BrokenProvider:
        name = "ollama"
        model = "qwen2.5-coder:7b"

        async def analyze_content(self, payload):
            raise AIProviderError("Local Ollama server unavailable at http://x")

    monkeypatch.setattr(
        provider_module, "get_provider", lambda *a, **k: BrokenProvider()
    )
    with pytest.raises(RetryableActionError) as excinfo:
        run_action("ai_analyze_content", _context(previous_output="some text"))
    message = str(excinfo.value)
    assert "Local AI provider 'ollama' failed" in message
    assert "unavailable" in message


def test_ai_step_fails_when_the_model_returns_the_wrong_shape(monkeypatch):
    import app.ai.provider as provider_module

    class SloppyProvider:
        name = "ollama"
        model = "qwen2.5-coder:7b"

        async def analyze_content(self, payload):
            return json.dumps({"priority": "urgent"})  # missing every other field

    monkeypatch.setattr(
        provider_module, "get_provider", lambda *a, **k: SloppyProvider()
    )
    with pytest.raises(RetryableActionError) as excinfo:
        run_action("ai_analyze_content", _context(previous_output="some text"))
    assert "does not match the expected schema" in str(excinfo.value)


# ------------------------------------------------------- the real provider


def test_ollama_provider_sends_a_structured_request(monkeypatch):
    """The wire body must pin the model, JSON mode and the schema."""
    from app.ai.ollama_provider import OllamaProvider

    captured = {}

    async def fake_chat(self, body):
        captured.update(body)
        return json.dumps(
            {
                "summary": "s",
                "category": "billing",
                "priority": "high",
                "reasoning": "r",
                "recommended_action": "a",
                "confidence": 0.8,
            }
        )

    monkeypatch.setattr(OllamaProvider, "_chat", fake_chat)
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="test-model")
    raw = asyncio.run(
        provider.analyze_content(
            ContentAnalysisRequest(content="c", task="t")
        )
    )

    assert captured["model"] == "test-model"
    assert captured["stream"] is False
    priority_ref = captured["format"]["properties"]["priority"]["$ref"]
    assert captured["format"]["$defs"][priority_ref.split("/")[-1]]["enum"] == [
        "low",
        "normal",
        "high",
        "urgent",
    ]
    assert "c" in captured["messages"][1]["content"]
    assert "t" in captured["messages"][1]["content"]
    ContentAnalysis.model_validate_json(raw)


def test_ollama_unreachable_host_raises_a_clear_error():
    from app.ai.ollama_provider import OllamaProvider

    provider = OllamaProvider(
        base_url="http://127.0.0.1:1", model="m", timeout=1.0
    )
    with pytest.raises(AIProviderError) as excinfo:
        asyncio.run(
            provider.analyze_content(ContentAnalysisRequest(content="c", task="t"))
        )
    assert "Local Ollama server unavailable" in str(excinfo.value)


# ------------------------------------------------------------- through HTTP


def test_ai_step_survives_a_real_execution_and_is_audited(client):
    """End to end through the engine: execute, then read it back."""
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection
    from app.automation.engine import execute_draft

    draft = WorkflowDraft(
        name="AI triage",
        description="Classifies a real message locally",
        trigger=WorkflowTrigger(type="manual", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1,
                application="WorkFlowOS",
                action="log",
                purpose="Provide the content",
                parameters={"content": "URGENT: I was charged twice for invoice 88213"},
            ),
            GeneratedWorkflowStep(
                step_number=2,
                application="WorkFlowOS AI",
                action="analyze_email",
                purpose="Classify the priority",
            ),
        ],
        confidence=0.9,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-ai-triage",
        )
        draft_model.set_status(connection, record, "approved")

    detail = execute_draft(record.id)
    assert detail.status == "completed", detail.steps
    ai_step = detail.steps[1]
    assert ai_step.action_type == "ai_analyze_content"
    payload = json.loads(ai_step.output or "{}")
    assert payload["result"]["priority"] in {"low", "normal", "high", "urgent"}
    # The provider is named in the stored output so the UI can show it.
    assert payload["provider"]
    assert payload["model"]


def test_ai_step_failure_is_recorded_and_blocks_later_steps(client):
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection
    from app.automation.engine import execute_draft

    draft = WorkflowDraft(
        name="AI triage failure",
        description="No content to classify",
        trigger=WorkflowTrigger(type="manual", application="Gmail", action="new_email"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1,
                application="WorkFlowOS AI",
                action="analyze_email",
                purpose="Classify",
            ),
            GeneratedWorkflowStep(
                step_number=2,
                application="WorkFlowOS",
                action="log",
                purpose="Must not run",
            ),
        ],
        confidence=0.9,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-ai-fail",
        )
        draft_model.set_status(connection, record, "approved")

    detail = execute_draft(record.id)
    assert detail.status == "failed"
    assert detail.steps[0].status == "failed"
    assert "No content to analyse" in (detail.steps[0].error or "")
    assert detail.steps[1].status == "pending"


# --------------------------------------------------- demo adapters are hidden


def test_mock_providers_are_absent_from_the_integration_list(client, monkeypatch):
    """With DEMO_MODE off the product must not list simulated accounts."""
    from app.config import settings

    # Settings is a frozen dataclass, so patch the instance dict directly and
    # restore it afterwards.
    original = settings.__dict__["demo_mode"]
    object.__setattr__(settings, "demo_mode", False)
    try:
        body = client.get("/api/integrations").json()
    finally:
        object.__setattr__(settings, "demo_mode", original)
    providers = {item["provider"] for item in body["integrations"]}
    assert "gmail_demo" not in providers
    assert "slack_demo" not in providers
    assert "gmail" in providers
    assert not any(item["is_mock"] for item in body["integrations"])


def test_mock_actions_are_not_advertised_as_runnable(client, monkeypatch):
    from app.config import settings

    original = settings.__dict__["demo_mode"]
    object.__setattr__(settings, "demo_mode", False)
    try:
        body = client.get("/api/integrations/actions").json()
    finally:
        object.__setattr__(settings, "demo_mode", original)
    assert not any(item["is_mock"] for item in body["actions"])
    assert not any(item["action"].endswith("_demo") for item in body["actions"])


def test_analytics_reports_zero_rather_than_inventing_numbers(client):
    """No executions must mean zeros, never a fabricated success rate."""
    summary = client.get("/api/analytics").json()["summary"]
    assert summary["total_executions"] == 0
    assert summary["successful_executions"] == 0
    assert summary["failed_executions"] == 0
    assert summary["success_rate"] == 0.0
    assert client.get("/api/activity").json()["events"] == []


def test_approval_gate_refuses_an_unapproved_draft(client):
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection

    draft = WorkflowDraft(
        name="Unapproved",
        description="Must not run",
        trigger=WorkflowTrigger(type="manual", application="WorkFlowOS", action="x"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="WorkFlowOS", action="log", purpose="p"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="mock-v1",
            workflow_candidate_id="workflow-unapproved",
        )
    assert record.status == "pending_approval"

    response = client.post(f"/api/workflows/drafts/{record.id}/execute")
    assert response.status_code == 409
    assert "approved" in response.json()["error"]["detail"].lower()
    assert client.get("/api/workflows/executions").json()["executions"] == []


def _approved_draft(candidate_id: str = "workflow-guard") -> str:
    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection

    draft = WorkflowDraft(
        name="Guard fixture",
        description="Guard fixture draft",
        trigger=WorkflowTrigger(type="manual", application="W", action="x"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="W", action="log", purpose="p"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="m",
            workflow_candidate_id=candidate_id,
        )
        draft_model.set_status(connection, record, "approved")
    return record.id


@pytest.mark.parametrize("trigger", ["demo_gmail", "demo_slack", "demo_calendar"])
def test_demo_triggers_are_refused_when_demo_mode_is_off(client, trigger):
    """A simulated integration must not be schedulable in a real deployment."""
    from app.config import settings

    draft_id = _approved_draft(f"workflow-guard-{trigger}")
    original = settings.__dict__["demo_mode"]
    object.__setattr__(settings, "demo_mode", False)
    try:
        response = client.post(
            "/api/automations",
            json={
                "name": f"attempt {trigger}",
                "draft_id": draft_id,
                "trigger_type": trigger,
                "trigger_config": "{}",
            },
        )
    finally:
        object.__setattr__(settings, "demo_mode", original)

    assert response.status_code == 409
    assert "demo integrations" in response.json()["error"]["detail"]


def test_real_gmail_trigger_is_accepted_with_demo_mode_off(client):
    from app.config import settings

    draft_id = _approved_draft("workflow-guard-real")
    original = settings.__dict__["demo_mode"]
    object.__setattr__(settings, "demo_mode", False)
    try:
        response = client.post(
            "/api/automations",
            json={
                "name": "Real monitor",
                "draft_id": draft_id,
                "trigger_type": "gmail",
                "trigger_config": '{"subject": "WorkFlowOS Demo"}',
            },
        )
    finally:
        object.__setattr__(settings, "demo_mode", original)

    assert response.status_code == 201
    assert response.json()["trigger_type"] == "gmail"


def test_simulated_activity_is_refused_when_demo_mode_is_off(client):
    from app.config import settings

    original = settings.__dict__["demo_mode"]
    object.__setattr__(settings, "demo_mode", False)
    try:
        response = client.post(
            "/api/activity/simulate",
            json={"workflow": "customer_request", "repetitions": 1},
        )
    finally:
        object.__setattr__(settings, "demo_mode", original)

    assert response.status_code == 409
    assert "synthetic events" in response.json()["error"]["detail"]
    assert client.get("/api/activity").json()["events"] == []


def test_execution_does_not_hold_the_write_lock_across_an_action(client):
    """Regression: an expiring token must not deadlock the step.

    The engine keeps one connection open for the whole step loop. If that
    transaction is still open when an action runs, any write the action makes
    on a *second* connection (refreshing and re-storing an OAuth token is the
    real case) blocks and the step fails with "database is locked".
    """
    import sqlite3

    from app.schemas.generator import (
        GeneratedWorkflowStep,
        WorkflowDraft,
        WorkflowTrigger,
    )
    from app.models import generator as draft_model
    from app.database import get_connection
    from app.automation import engine as engine_module

    draft = WorkflowDraft(
        name="Write-during-action",
        description="The action writes to the DB while the engine is mid-step",
        trigger=WorkflowTrigger(type="manual", application="WorkFlowOS", action="x"),
        steps=[
            GeneratedWorkflowStep(
                step_number=1, application="WorkFlowOS", action="record_probe", purpose="p"
            )
        ],
        confidence=0.5,
    )
    with get_connection() as connection:
        draft_model.ensure_table(connection)
        record = draft_model.upsert_draft(
            connection, draft, provider="mock", model="m",
            workflow_candidate_id="workflow-lock-probe",
        )
        draft_model.set_status(connection, record, "approved")

    original = engine_module.run_action

    def probe(action_type, context):
        # Simulate a token refresh: a *separate* connection writing mid-action.
        try:
            with get_connection() as other:
                other.execute(
                    "INSERT INTO schema_version (version, applied_at) "
                    "VALUES (99, 'probe')"
                )
        except sqlite3.OperationalError as exc:  # pragma: no cover
            raise AssertionError(f"write during action was blocked: {exc}") from exc
        return original(action_type, context)

    engine_module.run_action = probe
    try:
        detail = engine_module.execute_draft(record.id)
    finally:
        engine_module.run_action = original

    assert detail.status == "completed", detail.steps
    assert detail.steps[0].status == "completed"


def test_system_status_reports_the_real_connection_state(client):
    """Settings must not claim "nothing connected" while Gmail is connected."""
    status = client.get("/api/system/status").json()
    execution = status["execution"]

    # No demo adapters and no credential -> nothing is connected.
    assert execution["external_integrations"] == []
    assert "No external service is connected" in execution["note"]

    # Mock actions are not advertised while demo mode is off.
    assert not any(a.endswith("_demo") for a in execution["allowed_actions"])


def test_system_status_lists_a_connected_real_provider(client):
    from app.integrations.credentials import encrypt_token
    from app.models import integration as integration_model
    from app.database import get_connection

    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        integration_model.upsert_account(
            connection, "gmail",
            encrypted_token=encrypt_token({"access_token": "a", "refresh_token": "r"}),
            account_email="judge@gmail.com", is_mock=False, verified=True,
        )

    execution = client.get("/api/system/status").json()["execution"]
    assert "gmail" in execution["external_integrations"]
    assert "gmail" in execution["note"]
    assert "No external service is connected" not in execution["note"]


def test_completion_summary_never_claims_a_real_run_was_mocked():
    """A real Gmail run must not be described as a mock one.

    The old wording asserted "using mock actions ... No external service was
    contacted" for every successful execution, including ones that really read
    a message and really sent a reply.
    """
    from app.automation.engine import _completion_summary

    real = _completion_summary(4, 4, {"gmail", "ollama"}, ["gmail_read_email"])
    assert "mock" not in real.lower()
    assert "No external service was contacted" not in real
    assert "gmail" in real

    demo = _completion_summary(4, 4, {"gmail_demo"}, ["gmail_demo_read_email"])
    assert "demo" in demo.lower()
    assert "No real account was contacted" in demo

    empty = _completion_summary(1, 1, set(), ["log"])
    assert "No external service was contacted" in empty
