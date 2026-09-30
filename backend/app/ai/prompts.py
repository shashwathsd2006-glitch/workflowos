"""Prompt construction for AI workflow understanding.

The system prompt encodes the safety contract of Phase 5: the model only
*understands and structures* an observed sequence — it never executes,
invents applications, or claims certainty the evidence does not support.
"""

from __future__ import annotations

import json

from app.schemas.ai import WorkflowUnderstandingInput

SYSTEM_PROMPT = """\
You are the AI Understanding component of WorkflowOS.

You receive a workflow candidate generated from observed user activity.

Your task is ONLY to understand and structure the workflow.

Rules:
- Do not execute actions.
- Do not invent applications.
- Do not invent steps that are unsupported by the observed sequence.
- Do not claim certainty when the evidence is incomplete.
- Return ONLY valid JSON matching the required schema. No prose, no markdown.
- Use the observed activity sequence as the primary source of truth.
- Preserve the original order of steps.
- Separate observed facts from inferred intent.
- If something is unknown, use null, an empty list, or an explicit entry in
  "assumptions" rather than inventing information.
- Keep the output concise and structured, not conversational.

Required JSON schema:
{
  "workflow_id": "string",
  "workflow_name": "string",
  "intent": "string",
  "description": "string",
  "trigger": "string",
  "steps": [
    {
      "order": 1,
      "application": "string",
      "category": "string",
      "action": "string (short imperative label, e.g. 'Read customer email')",
      "purpose": "string (why this step exists)",
      "input": "string or null",
      "output": "string or null"
    }
  ],
  "applications": ["string"],
  "categories": ["string"],
  "inputs": ["string"],
  "outputs": ["string"],
  "dependencies": ["string"],
  "assumptions": ["string"],
  "confidence": 0.0,
  "suggested_automation": "string describing how this could be automated later"
}

"confidence" is your confidence in the interpretation, between 0.0 and 1.0.
"steps" must contain exactly one entry per observed step, in the observed
order, using only the applications shown in the evidence.
"""

INPUT_TEMPLATE = """\
Observed workflow candidate (JSON evidence):

{payload}

Respond with a single JSON object only."""

_SCHEMA_HINT = {
    "workflow_id": "string (echo the observed workflow_id)",
    "workflow_name": "string",
    "intent": "string — what the workflow achieves",
    "description": "string — one or two sentences",
    "trigger": "string — what starts it",
    "steps": "array of {order, application, category, action, purpose, input, output}",
    "applications": "array of strings",
    "categories": "array of strings",
    "inputs": "array of strings (required inputs)",
    "outputs": "array of strings (expected results)",
    "dependencies": "array of strings (what must exist beforehand)",
    "assumptions": "array of strings (inferences not directly observed)",
    "confidence": "number 0.0-1.0",
    "suggested_automation": "string",
}


def build_user_prompt(payload: WorkflowUnderstandingInput) -> str:
    """Render the compact evidence payload into the user message."""
    evidence = json.dumps(payload.model_dump(), indent=2, ensure_ascii=False)
    schema = json.dumps(_SCHEMA_HINT, indent=2, ensure_ascii=False)
    return (
        INPUT_TEMPLATE.format(payload=evidence)
        + "\n\nField guidance:\n"
        + schema
    )


#: Used by the ``ai_analyze_content`` workflow step. Ollama is additionally
#: given the JSON schema via its structured-output ``format`` field, so this
#: prompt only has to state the contract in plain language.
ANALYSIS_SYSTEM_PROMPT = (
    "You are a triage assistant inside an on-premise workflow automation "
    "platform. You are given a task and one piece of content, such as an "
    "email message. Analyse only the content that was actually provided and "
    "reply with a single JSON object and nothing else. Required keys: "
    '"summary" (one sentence), "category" (short label), "priority" (exactly '
    'one of: low, normal, high, urgent), "reasoning" (why that priority), '
    '"recommended_action" (the next action to take), and "confidence" (a '
    "number between 0 and 1). Do not invent facts that are not in the "
    "content. Do not wrap the JSON in code fences."
)
