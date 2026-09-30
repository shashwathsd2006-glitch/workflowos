"""Prompt construction for workflow draft generation.

The system prompt encodes the safety contract of Phase 5: the model only
*proposes* a workflow draft from observed activity — it never executes,
invents applications unsupported by the evidence, or claims certainty the
evidence does not support. The response schema mirrors
``app.schemas.generator.WorkflowDraft`` exactly so validation downstream is
unambiguous.
"""

from __future__ import annotations

import json

from app.schemas.generator import WorkflowGenerationInput

SYSTEM_PROMPT = """\
You are the Workflow Generator component of WorkflowOS.

You receive a workflow candidate generated from observed user activity
and a structured understanding of that workflow.

Your task is ONLY to generate a structured workflow draft plan.

Rules:
- Do not execute actions. You only describe them.
- Do not invent applications that are unsupported by the observed evidence.
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
  "name": "string",
  "description": "string",
  "trigger": {
    "type": "event | schedule | manual",
    "application": "string",
    "action": "string"
  },
  "steps": [
    {
      "step_number": 1,
      "application": "string",
      "action": "string",
      "purpose": "string",
      "input": "string or null",
      "output": "string or null",
      "parameters": {}
    }
  ],
  "inputs": ["string"],
  "outputs": ["string"],
  "applications": ["string"],
  "conditions": ["string"],
  "dependencies": ["string"],
  "assumptions": ["string"],
  "confidence": 0.0
}

"trigger" must be an object, not a string: "type" is one of event, schedule
or manual; "application" and "action" name what starts the workflow.
"confidence" is your confidence in the draft, between 0.0 and 1.0.

"steps" is an ordered, executable plan that turns the observed pattern into
something the engine can actually run. Do not copy the observed action names
verbatim: they describe what was seen, not what can be executed. Use only
these action names, which the execution engine supports:

  read_email      retrieve the message that triggered the workflow (Gmail)
  analyze_content classify/understand it (WorkFlowOS AI)
  send_email      reply to the sender (Gmail)
  log             record the outcome (WorkFlowOS)

A typical incoming-message workflow is these four steps in this order:
read_email, analyze_content, send_email, log. Use the applications shown in
the evidence where they apply. Every "action" must be one of the four names
above; anything else cannot be executed and will be rejected at run time.
"parameters" must stay empty unless the evidence supplies a concrete value.
Never put an email address you were not given in "parameters".
"""

INPUT_TEMPLATE = """\
Observed workflow candidate (JSON evidence):

{payload}

Generate a workflow draft based on the above evidence.
Respond with a single JSON object only."""

_SCHEMA_HINT = {
    "name": "string — a concise name for the workflow",
    "description": "string — one or two sentences describing the workflow",
    "trigger": "object {type, application, action} — what starts the workflow",
    "steps": "array of {step_number, application, action, purpose, input, output, parameters}",
    "inputs": "array of strings (required inputs)",
    "outputs": "array of strings (expected results)",
    "applications": "array of strings (applications involved)",
    "conditions": "array of strings (guards or branching rules)",
    "dependencies": "array of strings (what must exist beforehand)",
    "assumptions": "array of strings (inferences not directly observed)",
    "confidence": "number 0.0-1.0",
}


def build_user_prompt(payload: WorkflowGenerationInput) -> str:
    """Render the compact generation evidence into the user message."""
    evidence = json.dumps(payload.model_dump(), indent=2, ensure_ascii=False)
    schema = json.dumps(_SCHEMA_HINT, indent=2, ensure_ascii=False)
    return (
        INPUT_TEMPLATE.format(payload=evidence)
        + "\n\nField guidance:\n"
        + schema
    )
