"""Deterministic MockProvider — offline stand-in for Ollama.

Used only when ``AI_PROVIDER=mock`` (the pytest default). It derives its
answer *from the evidence payload* with simple rules — no workflow is
hardcoded — and returns the same strict JSON schema the real provider must
produce, so parsing/validation/persistence are exercised identically.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app.ai.provider import AIProvider
from app.schemas.ai import ContentAnalysisRequest, WorkflowUnderstandingInput
from app.schemas.generator import WorkflowGenerationInput

_ACTION_STOPWORDS = {"a", "an", "the", "to", "of", "in", "and"}


def _humanize(action: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", action.lower()).split()
    return " ".join(word for word in words if word not in _ACTION_STOPWORDS)


class MockProvider(AIProvider):
    name = "mock"
    model = "mock-v1"

    async def generate_workflow_understanding(
        self, payload: WorkflowUnderstandingInput
    ) -> str:
        return json.dumps(self._build(payload), ensure_ascii=False)

    async def generate_workflow_draft(
        self, payload: WorkflowGenerationInput
    ) -> str:
        return json.dumps(self._build_draft(payload), ensure_ascii=False)

    async def analyze_content(self, payload: ContentAnalysisRequest) -> str:
        """Derive an analysis from the supplied content, with simple rules.

        Test-only: the shape must match what the real model returns so
        parsing and validation are exercised identically. It never pretends to
        be a model — ``AI_PROVIDER=mock`` is a test-only configuration.
        """
        text = (payload.content or "").strip()
        lowered = text.lower()
        urgent_words = ("urgent", "asap", "immediately", "charged twice", "outage")
        high_words = ("refund", "complaint", "cancel", "failed", "error")
        if any(word in lowered for word in urgent_words):
            priority = "urgent"
        elif any(word in lowered for word in high_words):
            priority = "high"
        else:
            priority = "normal"
        first_line = text.splitlines()[0] if text else ""
        return json.dumps(
            {
                "summary": f"[mock] {first_line[:160]}" if first_line else "[mock] empty",
                "category": "general",
                "priority": priority,
                "reasoning": "[mock] Derived from keywords in the supplied content.",
                "recommended_action": f"[mock] Triage as {priority} priority",
                "confidence": 0.5,
            },
            ensure_ascii=False,
        )

    def _build_draft(self, payload: WorkflowGenerationInput) -> Dict[str, Any]:
        """Derive a draft plan from the evidence — plain JSON, no Pydantic.

        The mock must look exactly like a real model on the wire, so no
        schema objects are emitted: everything is a JSON primitive.
        """
        steps: List[Dict[str, Any]] = []
        for order, step in enumerate(payload.steps, start=1):
            label = _humanize(step["action"]) or step["action"]
            steps.append(
                {
                    "step_number": order,
                    "application": step["application"],
                    "action": label[:1].upper() + label[1:],
                    "purpose": f"Perform '{label}' in {step['application']}",
                    "input": None,
                    "output": None,
                    "parameters": {},
                }
            )

        applications = list(payload.applications)
        return {
            "name": payload.workflow_name,
            "description": (
                f"Draft plan for '{payload.workflow_name}' derived from "
                f"{payload.occurrence_count} observed occurrence(s). "
                "No actions are executed by this draft."
            ),
            "trigger": {
                "type": "event",
                "application": applications[0] if applications else "Manual",
                "action": "observed_repetition",
            },
            "steps": steps,
            "inputs": list(payload.inputs),
            "outputs": list(payload.outputs),
            "applications": applications,
            "conditions": [],
            "dependencies": list(payload.dependencies),
            "assumptions": list(payload.assumptions)
            + ["Draft structure inferred by the mock provider"],
            "confidence": round(min(1.0, payload.understanding_confidence), 4),
        }

    def _build(self, payload: WorkflowUnderstandingInput) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        for step in payload.steps:
            label = _humanize(step.action) or step.action
            steps.append(
                {
                    "order": step.order,
                    "application": step.application,
                    "category": step.category,
                    "action": label[:1].upper() + label[1:],
                    "purpose": f"Perform '{label}' in {step.application}",
                    "input": None,
                    "output": None,
                }
            )

        chain = " → ".join(payload.applications)
        intent = f"Handle {payload.workflow_name}"
        description = (
            f"Observed repeating sequence: {chain}. "
            f"The activity repeated {payload.occurrence_count} time(s) across "
            f"{payload.session_count} session(s) with similarity "
            f"{payload.similarity_score:.2f}."
        )
        confidence = round(min(1.0, payload.confidence + 0.01), 4)

        return {
            "workflow_id": payload.workflow_id,
            "workflow_name": payload.workflow_name,
            "intent": intent,
            "description": description,
            "trigger": "Repeated observed activity matching this sequence",
            "steps": steps,
            "applications": list(payload.applications),
            "categories": list(payload.categories),
            "inputs": [],
            "outputs": [],
            "dependencies": list(payload.applications),
            "assumptions": [
                "Intent inferred from observed action names (mock provider)"
            ],
            "confidence": confidence,
            "suggested_automation": (
                f"Automate the observed '{payload.workflow_name}' sequence "
                f"({chain}) after explicit user approval."
            ),
        }
