"""Extraction and strict validation of raw AI responses.

Never trust model output: locate a JSON object inside whatever the provider
returned (bare JSON, fenced blocks, surrounding prose) and validate it
against the ``WorkflowUnderstanding`` schema. Anything that fails raises
``AIResponseError`` so malformed output can never be persisted.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.ai.errors import AIResponseError
from app.schemas.ai import WorkflowUnderstanding


def extract_json_object(text: str) -> Any:
    """Return the first decodable JSON object found in ``text``.

    Tries the whole string first (handles ```json fences), then scans for the
    first ``{`` and lets the JSON decoder find the matching object, so prose
    like "Here is the JSON: {...}" also works.
    """
    if not isinstance(text, str) or not text.strip():
        raise AIResponseError("Empty AI response")

    candidate = text.strip()
    for fence in ("```json", "```JSON", "```"):
        if candidate.startswith(fence):
            candidate = candidate.strip("`").lstrip("json").lstrip()
        if candidate.endswith("```"):
            candidate = candidate[: -len("```")].rstrip()

    try:
        return json.loads(candidate)
    except ValueError:
        pass

    decoder = json.JSONDecoder()
    index = candidate.find("{")
    while index != -1:
        try:
            decoded, _ = decoder.raw_decode(candidate[index:])
            return decoded
        except ValueError:
            index = candidate.find("{", index + 1)
    raise AIResponseError("No JSON object found in AI response")


def parse_understanding(raw: str, workflow_id: str) -> WorkflowUnderstanding:
    """Extract, default the workflow id and strictly validate a response."""
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        raise AIResponseError("AI response JSON is not an object")
    data.setdefault("workflow_id", workflow_id)
    try:
        understanding = WorkflowUnderstanding(**data)
    except ValidationError as exc:
        problems = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors()[:4]
        )
        raise AIResponseError(f"AI response failed schema validation ({problems})") from exc
    if understanding.workflow_id != workflow_id:
        understanding = understanding.model_copy(update={"workflow_id": workflow_id})
    return understanding
