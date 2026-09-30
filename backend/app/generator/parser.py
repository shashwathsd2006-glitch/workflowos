"""Extraction and strict validation of raw AI responses for draft generation.

Reuses the Phase 5 JSON extraction helper so fenced blocks and prose-wrapped
JSON behave identically for both features, then validates against
``WorkflowDraft``. Anything that fails raises ``AIResponseError`` so invalid
output is never persisted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import ValidationError

from app.ai.errors import AIResponseError
from app.ai.parsing import extract_json_object
from app.schemas.generator import WorkflowDraft

__all__ = ["extract_json_object", "parse_draft", "normalize_draft"]

# Step parameter names that must hold a single mailbox address.
_ADDRESS_PARAM_KEYS = frozenset(
    {
        "from",
        "from_address",
        "sender",
        "to",
        "to_address",
        "recipient",
    }
)

# Step parameter names that hold a raw Gmail query rather than one address.
_QUERY_PARAM_KEYS = frozenset({"query", "gmail_query", "search"})

# Actions whose parameters decide which mailbox message gets read.
_MAILBOX_READ_ACTIONS = frozenset(
    {
        "gmail_read_email",
        "gmail_search_emails",
        "read_email",
        "search_email",
        "search_emails",
        "open_email",
        "get_email",
        "fetch_email",
    }
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _address_only(value: str) -> str:
    text = (value or "").strip()
    if "<" in text and ">" in text:
        text = text[text.rfind("<") + 1 : text.rfind(">")]
    return text.strip().strip(",").strip().lower()


def _allowed_addresses() -> set:
    """Addresses the operator configured, which the model may legitimately use.

    Anything outside this set is model-invented. An invented address in a
    mailbox-read step silently redirects a real Gmail read at a message that
    does not exist, so it is dropped rather than trusted.
    """
    from app.config import settings

    allowed = set()
    for value in (
        settings.demo_sender_email,
        settings.demo_recipient_email,
        settings.gmail_read_query,
    ):
        for found in _EMAIL_RE.findall(value or ""):
            allowed.add(_address_only(found))
    return {item for item in allowed if item}


def _sanitize_mailbox_addresses(steps: List[Dict[str, Any]]) -> List[str]:
    """Stop a generated draft carrying a mailbox address that cannot be used.

    Two separate problems are handled:

    1. A parameter that should hold an address but holds prose instead — for
       example ``"to": "the sender's email address"``. That is a model
       placeholder, not a recipient, and it would make a real send fail or
       misdirect one. Placeholders are dropped on every step.
    2. A read/search step naming an address the operator never configured.
       That would redirect a real Gmail read at a message that does not exist,
       so it is dropped too.

    A genuine, well-formed address on a non-read step is left alone: that is
    explicit intent worth honouring. Returns notes describing anything removed
    so the draft records the change instead of silently differing.
    """
    allowed = _allowed_addresses()
    notes: List[str] = []
    for step in steps:
        action = str(step.get("action") or "").strip().lower()
        is_mailbox_read = action in _MAILBOX_READ_ACTIONS
        parameters = step.get("parameters")
        if not isinstance(parameters, dict):
            continue
        for key in list(parameters):
            lowered = key.lower()
            is_address_key = lowered in _ADDRESS_PARAM_KEYS
            is_query_key = lowered in _QUERY_PARAM_KEYS
            if not (is_address_key or is_query_key):
                continue
            value = parameters[key]
            if not isinstance(value, str) or not value.strip():
                continue
            stripped = value.strip()
            found = {f.lower() for f in _EMAIL_RE.findall(value)}
            reasons: List[str] = []

            if is_address_key:
                # Exactly one real address, and nothing else in the value.
                if not (len(found) == 1 and stripped.lower() in found):
                    reasons.append("a placeholder rather than an address")
            if not reasons and is_mailbox_read and allowed:
                invented = sorted(found - allowed)
                if invented:
                    reasons.append(
                        "not configured for this mailbox: " + ", ".join(invented)
                    )
            if reasons:
                notes.append(
                    f"Step {step.get('step_number')} parameter '{key}' was not a "
                    f"usable mailbox target ({'; '.join(reasons)}); it was "
                    f"ignored so the real address is used instead."
                )
                del parameters[key]
    return notes


def parse_draft(raw: str) -> WorkflowDraft:
    """Extract, normalize and strictly validate a generation response."""
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        raise AIResponseError("AI response JSON is not an object")
    data = normalize_draft(data)
    try:
        return WorkflowDraft(**data)
    except ValidationError as exc:
        problems = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors()[:4]
        )
        raise AIResponseError(
            f"AI response failed schema validation ({problems})"
        ) from exc


def normalize_draft(data: dict) -> dict:
    """Coerce common model variations into the exact draft schema.

    Models frequently emit ``trigger`` as a plain sentence rather than the
    required object, or use 0-based/duplicate step numbers. These are
    presentation problems, not hallucinations, so they are repaired here
    instead of failing validation. Unknown extra keys are dropped.
    """
    normalized = dict(data)
    normalized["trigger"] = _normalize_trigger(normalized.get("trigger"))
    normalized["steps"] = _normalize_steps(normalized.get("steps"))
    for key in (
        "inputs",
        "outputs",
        "applications",
        "conditions",
        "dependencies",
        "assumptions",
    ):
        value = normalized.get(key)
        normalized[key] = list(value) if isinstance(value, list) else []
    # Never persist a mailbox address the model made up.
    notes = _sanitize_mailbox_addresses(normalized["steps"])
    if notes:
        normalized["assumptions"] = list(normalized["assumptions"]) + notes
    return normalized


def _normalize_trigger(raw: object) -> dict:
    """Accept a trigger object, a sentence, or a missing value."""
    if isinstance(raw, dict):
        return {
            "type": raw.get("type") or "event",
            "application": raw.get("application") or "Manual",
            "action": raw.get("action") or "start_workflow",
        }
    if isinstance(raw, str) and raw.strip():
        return {"type": "event", "application": "Manual", "action": raw.strip()}
    return {"type": "event", "application": "Manual", "action": "start_workflow"}


def _normalize_steps(raw: object) -> list:
    """Coerce the steps array into schema-shaped dicts with 1-based numbering."""
    if not isinstance(raw, list):
        return []
    steps = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        application = item.get("application") or "Unknown"
        action = item.get("action") or "unknown_action"
        steps.append(
            {
                "step_number": index,
                "application": application,
                "action": action,
                "purpose": item.get("purpose") or "",
                "input": item.get("input"),
                "output": item.get("output"),
                "parameters": item.get("parameters")
                if isinstance(item.get("parameters"), dict)
                else {},
            }
        )
    return steps
