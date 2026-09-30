"""Controlled action registry — the security boundary of Phase 6.

Only handlers registered here can ever run. The engine resolves a generated
workflow step to a registry key through a fixed, inspectable rule set; model
output never selects a Python callable, never names a module, and is never
passed to ``eval``/``exec``/``subprocess``/an HTTP client.

Handlers are pure, deterministic and local: they format a value and return a
string. Nothing here opens a socket, spawns a process, touches the filesystem
or reads credentials.

An unrecognised action resolves to ``None`` and the step fails with
``UnsupportedActionError`` — the safe outcome is a visible failure, never a
best-effort guess at what the model "meant".
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

Handler = Callable[["ActionContext"], str]


class UnsupportedActionError(Exception):
    """Raised when a step action has no safe registered implementation."""


class ActionContext:
    """Everything a handler is allowed to see.

    Deliberately narrow: identifiers, the step's own text, and the previous
    step's output. No connection, no filesystem, no environment access.
    """

    def __init__(
        self,
        *,
        execution_id: str,
        step_number: int,
        application: str,
        action: str,
        purpose: str = "",
        step_input: Optional[str] = None,
        previous_output: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        trigger_payload: Optional[Dict[str, Any]] = None,
        action_type: Optional[str] = None,
    ) -> None:
        self.execution_id = execution_id
        self.step_number = step_number
        self.application = application
        self.action = action
        self.purpose = purpose
        self.step_input = step_input
        self.previous_output = previous_output
        #: The registry key the step resolved to (set by the engine).
        self.action_type = action_type
        #: Parameters the workflow author attached to this step.
        self.parameters = parameters or {}
        #: Context supplied by the trigger (e.g. the Gmail message that fired).
        self.trigger_payload = trigger_payload or {}

    def describe(self) -> str:
        return f"{self.application}:{self.action}"


def _simulate(context: ActionContext) -> str:
    """Pretend to perform an external app action. Changes nothing."""
    return (
        f"simulated {context.application} action '{context.action}' "
        f"for execution {context.execution_id} step {context.step_number}"
    )


def _log(context: ActionContext) -> str:
    """Record a human-readable step result."""
    detail = context.purpose or context.action
    return f"logged {context.application} step {context.step_number}: {detail}"


def _transform(context: ActionContext) -> str:
    """Pass the previous step's output forward, or seed a default payload."""
    if context.previous_output:
        payload: object = context.previous_output
    else:
        payload = {
            "execution_id": context.execution_id,
            "step": context.step_number,
            "application": context.application,
        }
    return json.dumps(
        {"transformed": payload, "step": context.step_number}, ensure_ascii=False
    )


def _notify_mock(context: ActionContext) -> str:
    """Pretend to send a notification. No message is ever delivered."""
    return (
        f"mock notification queued for {context.application} "
        f"(execution {context.execution_id}, step {context.step_number}); "
        "no external service contacted"
    )


def _delay_mock(context: ActionContext) -> str:
    """Model a wait without actually sleeping, keeping runs fast."""
    return (
        f"mock delay of 0ms recorded for step {context.step_number} "
        f"(execution {context.execution_id})"
    )


# ---------------------------------------------------------------- Phase 8
# Integration-backed actions. These delegate to an IntegrationProvider chosen
# by app.integrations.registry, so the engine still never touches a vendor API
# directly and no model output can introduce a new action.


def _run_async(coro):
    """Run a coroutine from this synchronous action handler.

    The engine is synchronous and FastAPI runs sync endpoints in a worker
    thread, so there is normally no running loop. If one is present we hand the
    coroutine to a dedicated thread rather than nesting loops.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _content_from_context(context: ActionContext) -> str:
    """Collect the real text this step should reason about.

    Sources, in order: the step's own ``content``/``text`` parameter, the
    previous step's JSON result, and the trigger payload that started the run.
    Nothing is invented — if none of those carry text the step fails rather
    than analysing an empty string.
    """
    parameters = context.parameters or {}
    for key in ("content", "text", "body", "message"):
        value = parameters.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    def harvest(value: Any, found: List[str]) -> None:
        if isinstance(value, str):
            if value.strip():
                found.append(value.strip())
        elif isinstance(value, dict):
            for item in value.values():
                harvest(item, found)
        elif isinstance(value, list):
            for item in value:
                harvest(item, found)

    found: List[str] = []
    if context.previous_output:
        try:
            decoded = json.loads(context.previous_output)
        except (TypeError, ValueError):
            decoded = None
        harvest(decoded if decoded is not None else context.previous_output, found)
    harvest(context.trigger_payload or {}, found)
    return "\n".join(found)


def _analyze_content(context: ActionContext) -> str:
    """Send real content to the configured local model and store its verdict.

    This is the AI step of the demo workflow: the previous step's real output
    (for example a real Gmail message) is sent to Ollama, and the reply is
    validated against ``ContentAnalysis`` before it is recorded.

    If the provider is unreachable, times out or returns something that does
    not match the schema, the step raises so the execution fails visibly. It
    never substitutes a canned answer.
    """
    from app.ai.errors import AIError
    from app.ai.provider import get_provider
    from app.schemas.ai import ContentAnalysis, ContentAnalysisRequest

    content = _content_from_context(context)
    if not content:
        raise NonRetryableActionError(
            "No content to analyse: the previous step produced no text. "
            "Run a Gmail read/search step before the AI step."
        )

    task = str((context.parameters or {}).get("task") or "").strip()
    if not task:
        task = context.purpose.strip() or "Summarise and classify this content."

    provider = get_provider()
    try:
        raw = _run_async(
            provider.analyze_content(
                ContentAnalysisRequest(content=content, task=task)
            )
        )
    except AIError as exc:
        # Transport/model failure: a retry may help, and a human must see it.
        raise RetryableActionError(
            f"Local AI provider '{provider.name}' failed: {exc}"
        ) from exc

    try:
        analysis = ContentAnalysis.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - any parse problem is a real fault
        raise RetryableActionError(
            f"Local AI provider '{provider.name}' returned a response that does "
            f"not match the expected schema: {exc}"
        ) from exc

    return json.dumps(
        {
            "action": "analyze_content",
            "provider": provider.name,
            "model": getattr(provider, "model", ""),
            "task": task,
            "analysed_characters": len(content),
            # Where the analysed content came from, so a reply step can address
            # the real sender instead of a configured fallback.
            "source": _source_from_output(context.previous_output),
            "result": analysis.model_dump(),
        },
        ensure_ascii=False,
    )


def _source_from_output(previous: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pull the originating message's sender/subject out of a step's output.

    Returns ``None`` unless the previous step really was a message read, so a
    reply is never addressed to something we did not receive.
    """
    if not previous:
        return None
    try:
        decoded = json.loads(previous)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict) or decoded.get("action") != "gmail_read_email":
        return None
    result = decoded.get("result")
    if not isinstance(result, dict):
        return None
    sender = str(result.get("from") or "").strip()
    if not sender:
        return None
    source: Dict[str, Any] = {"sender": sender}
    reply_to = str(result.get("reply_to") or "").strip()
    if reply_to:
        source["reply_to"] = reply_to
    for key in ("to", "subject", "id", "thread_id"):
        value = str(result.get(key) or "").strip()
        if value:
            source[key] = value
    return source


def _integration_handler(provider_name: str) -> Handler:
    """Build a registry handler that calls one provider's action endpoint.

    The provider is chosen from the action's own registry key, so a step that
    says ``gmail_read_email`` always means *real* Gmail and a step that says
    ``gmail_demo_read_email`` always means the local stand-in. There is no
    silent substitution between the two: an unconnected real provider fails
    with a clear, non-retryable error rather than quietly running against a
    mock.
    """

    def handler(context: ActionContext) -> str:
        from app.integrations.base import (
            AuthenticationError,
            IntegrationError,
            NotConfiguredError,
            ValidationError,
        )
        from app.integrations.registry import get_provider

        provider = get_provider(provider_name)
        payload = _integration_payload(context)
        action = _integration_action(context)
        try:
            result = provider.execute_action(action, payload)
        except NotConfiguredError as exc:
            raise NonRetryableActionError(str(exc)) from exc
        except AuthenticationError as exc:
            # Expired/revoked grant: retrying cannot help, reconnection can.
            raise NonRetryableActionError(str(exc)) from exc
        except ValidationError as exc:
            raise NonRetryableActionError(str(exc)) from exc
        except IntegrationError as exc:
            # Network or 5xx: worth another attempt.
            raise RetryableActionError(str(exc)) from exc
        return json.dumps(
            {
                "action": action,
                "provider": provider_name,
                "is_mock": provider.is_mock,
                "result": result,
            },
            ensure_ascii=False,
            default=str,
        )

    return handler


class RetryableActionError(Exception):
    """An action failed in a way a retry may fix (network, 5xx)."""


class NonRetryableActionError(Exception):
    """An action failed permanently (validation, auth, not configured)."""


def _integration_action(context: ActionContext) -> str:
    """Pick the provider action for this step.

    The engine already resolved ``context.action_type`` to a registry key
    (e.g. ``gmail_read_email``), and that key is what the provider's
    ``execute_action`` expects. A step may narrow it via
    ``parameters.action`` — which is still checked against the allowlist by
    the provider itself, never dispatched dynamically.
    """
    explicit = (context.parameters or {}).get("action")
    if isinstance(explicit, str) and explicit:
        return explicit
    return context.action_type or ""


def _integration_payload(context: ActionContext) -> Dict[str, Any]:
    """Merge the step's declared parameters with trigger and prior context.

    Precedence (lowest to highest): the previous step's output, the trigger
    event that fired this run, then the step's own declared parameters. A
    step that declared a value always wins.

    Notification-style actions (``*_send_message``) need a body; when the step
    did not declare one, the previous step's output is passed through as
    ``text`` so the message is never silently empty.
    """
    payload: Dict[str, Any] = {}

    previous = context.previous_output
    if previous:
        payload["previous_output"] = previous
        try:
            decoded = json.loads(previous)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            result = decoded.get("result")
            if isinstance(result, dict):
                payload.update(
                    {k: v for k, v in result.items() if k not in {"note"}}
                )

    payload.update(context.trigger_payload or {})
    payload.update(context.parameters or {})

    action = _integration_action(context)
    # Notification-style actions need a body. When the step declared none, the
    # previous step's output is passed through so the message is never empty.
    # This covers Slack-style ``*_send_message`` and Gmail ``*_send_email``.
    if action.endswith("_send_email"):
        # Addressing is independent of the body: a read result already carries
        # a "body" field, so it must not suppress the recipient/subject logic.
        _apply_reply_addressing(payload, previous, context.parameters)
    if action.endswith(("_send_message", "_send_email")) and not str(
        payload.get("text") or payload.get("body") or ""
    ).strip():
        summary = _summarise(previous)
        payload["text"] = summary
        # Gmail's send action reads "body"; keep both keys in step.
        if action.endswith("_send_email"):
            payload["body"] = _reply_body(previous) or summary
    return payload


def _apply_reply_addressing(
    payload: Dict[str, Any],
    previous: Optional[str],
    parameters: Optional[Dict[str, Any]] = None,
) -> None:
    """Address a reply to the sender of the message that was actually read.

    Ordering: an explicit step parameter wins, then the address on the
    received message, then whatever the operator configured. The recipient is
    only taken from a real Gmail read, never guessed.

    Note the previous step's own fields are flattened into ``payload`` first,
    and a Gmail read carries ``to`` — which is *us*, not the correspondent.
    So only a declared step parameter may pre-empt the real sender here.
    """
    source = None
    if previous:
        try:
            decoded = json.loads(previous)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            if decoded.get("action") == "analyze_content":
                candidate = decoded.get("source")
                if isinstance(candidate, dict):
                    source = candidate
            elif decoded.get("action") == "gmail_read_email":
                # No AI step in between: the read output is right here.
                source = _source_from_output(previous)

    declared = parameters or {}
    explicit_to = str(declared.get("to") or "").strip()
    explicit_subject = str(declared.get("subject") or "").strip()

    # A template placeholder is not a value. Generated drafts often carry
    # subjects like "Re: [Original Subject]", and honouring one sends a real
    # reply with a meaningless subject line. Treat it as absent so the real
    # subject of the message that was read is used instead.
    if _PLACEHOLDER_RE.search(explicit_subject):
        explicit_subject = ""
        declared = {k: v for k, v in declared.items() if k != "subject"}
        payload.pop("subject", None)

    # A declared recipient only counts when it is a real, sendable address.
    # Workflow drafts are model-generated, and a placeholder such as
    # "the sender's email address" would otherwise pre-empt the real
    # correspondent and make the send fail. Anything that is not an address
    # is discarded so the genuine sender of the message that was read is used.
    if explicit_to and not _EMAIL_ADDRESS_RE.fullmatch(explicit_to):
        explicit_to = ""
        declared = {k: v for k, v in declared.items() if k != "to"}
        payload.pop("to", None)

    if not explicit_to and source:
        reply_to = str(source.get("reply_to") or source.get("sender") or "").strip()
        address = _address_of(reply_to)
        if address:
            payload["to"] = address
    if not explicit_subject and source:
        original = str(source.get("subject") or "").strip()
        if original:
            payload["subject"] = (
                original if original.lower().startswith("re:") else f"Re: {original}"
            )


def _address_of(value: str) -> str:
    """Extract a bare email address from a ``Name <addr@host>`` header."""
    text = (value or "").strip()
    if "<" in text and ">" in text:
        text = text[text.rfind("<") + 1 : text.rfind(">")]
    text = text.strip().strip(",").strip()
    return text if "@" in text and " " not in text else ""


def _reply_body(previous: Optional[str]) -> str:
    """Compose a reply body from a preceding AI analysis, when there is one.

    Keeps the reply grounded in what the local model actually concluded about
    the real email that was read, instead of a bare one-liner. Returns "" when
    the previous step was not an AI analysis, so callers fall back.
    """
    if not previous:
        return ""
    try:
        decoded = json.loads(previous)
    except (TypeError, ValueError):
        return ""
    if not isinstance(decoded, dict) or decoded.get("action") != "analyze_content":
        return ""
    result = decoded.get("result")
    if not isinstance(result, dict):
        return ""
    lines: List[str] = []
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    for label, key in (
        ("Category", "category"),
        ("Priority", "priority"),
        ("Recommended action", "recommended_action"),
    ):
        value = str(result.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    model = str(decoded.get("model") or "").strip()
    if model:
        lines.append(f"-- Classified on-premise by WorkFlowOS ({model})")
    return "\n\n".join(lines) if lines else ""


def _summarise(previous: Optional[str]) -> str:
    """Build a short message body from the previous step's output."""
    if not previous:
        return "Automated update from WorkFlowOS."
    try:
        decoded = json.loads(previous)
    except (TypeError, ValueError):
        return previous[:480]
    if isinstance(decoded, dict):
        result = decoded.get("result")
        if isinstance(result, dict):
            for key in ("summary", "subject", "snippet", "message", "body"):
                value = result.get(key)
                if value:
                    return str(value)[:480]
    return str(previous)[:480]


#: action name -> provider that serves it (mirrors the registry allowlist).
_INTEGRATION_ACTIONS: Dict[str, str] = {
    "gmail_search_emails": "gmail",
    "gmail_read_email": "gmail",
    "gmail_send_email": "gmail",
    "gmail_fetch_messages": "gmail",
    "slack_send_message": "slack",
    "slack_list_channels": "slack",
    "calendar_list_events": "calendar",
    "calendar_create_event": "calendar",
    "calendar_update_event": "calendar",
    "gmail_demo_read_email": "gmail_demo",
    "gmail_demo_send_email": "gmail_demo",
    "gmail_demo_fetch_messages": "gmail_demo",
    "slack_demo_send_message": "slack_demo",
    "slack_demo_list_channels": "slack_demo",
    "calendar_demo_list_events": "calendar_demo",
    "calendar_demo_create_event": "calendar_demo",
    "calendar_demo_update_event": "calendar_demo",
}


#: The only callables the engine may invoke, keyed by registry name.
ACTION_REGISTRY: Dict[str, Handler] = {
    "ai_analyze_content": _analyze_content,
    "simulate": _simulate,
    "log": _log,
    "transform": _transform,
    "notify_mock": _notify_mock,
    "delay_mock": _delay_mock,
}

# Integration actions are added to the same registry so the engine, the
# /system/status payload and the UI all read one allowlist.
for _name, _provider in _INTEGRATION_ACTIONS.items():
    ACTION_REGISTRY[_name] = _integration_handler(_provider)

ACTION_DESCRIPTIONS: Dict[str, str] = {
    "ai_analyze_content": "Send real content to the configured local AI provider.",
    "gmail_search_emails": "Search the connected Gmail mailbox.",
    "simulate": "Pretend to perform an external application action.",
    "log": "Record a step result with no side effects.",
    "transform": "Pass structured data to the next step.",
    "notify_mock": "Queue a mock notification; nothing is delivered.",
    "delay_mock": "Record a mock wait without blocking.",
}

for _name, _provider in _INTEGRATION_ACTIONS.items():
    _mock = _provider.endswith("_demo")
    ACTION_DESCRIPTIONS[_name] = (
        f"{'Demo (local, not sent)' if _mock else 'Real'} action via {_provider}."
    )

#: Explicit keyword -> registry mapping. Ordered: the first match wins.
#: Kept short on purpose so unknown phrasing fails loudly instead of being
#: coerced into some action the user never approved.
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "analyze",
            "analyse",
            "classify",
            "understand",
            "triage",
            "summarize",
            "summarise",
            "categorize",
            "categorise",
        ),
        "ai_analyze_content",
    ),
    (("transform", "parse", "format", "convert", "enrich"), "transform"),
    (
        ("notify", "notification", "notifications", "notified", "alert", "message"),
        "notify_mock",
    ),
    (("delay", "wait", "pause", "delay_mock"), "delay_mock"),
    (("log", "record", "note"), "log"),
    (
        (
            "simulate",
            "read",
            "open",
            "fetch",
            "download",
            "create",
            "update",
            "send",
            "find",
            "search",
            "lookup",
            "insert",
        ),
        "simulate",
    ),
)

_ALIASES: Dict[str, str] = {
    "simulated": "simulate",
    "sim": "simulate",
    "log_message": "log",
    "notify": "notify_mock",
    "notification": "notify_mock",
    "delay": "delay_mock",
}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


#: When a real Gmail account is connected, these ordinary workflow action
#: names are routed to the real Gmail adapter instead of the local simulator.
#: The mapping is code-controlled and explicit — a model still cannot invent an
#: action, it can only benefit from one of these already-allowlisted names.
_GMAIL_LIVE_MAP: Dict[str, str] = {
    "read_email": "gmail_read_email",
    "open_email": "gmail_read_email",
    "get_email": "gmail_read_email",
    "fetch_email": "gmail_read_email",
    "download_email": "gmail_read_email",
    "download_attachment": "gmail_read_email",
    "search_email": "gmail_search_emails",
    "search_emails": "gmail_search_emails",
    "find_email": "gmail_search_emails",
    "send_email": "gmail_send_email",
    "send_gmail": "gmail_send_email",
    "email_send": "gmail_send_email",
}


def gmail_is_connected() -> bool:
    """True when a real Gmail account is authorised (not the demo stand-in)."""
    try:
        from app.integrations.registry import get_provider

        provider = get_provider("gmail")
        return (not provider.is_mock) and provider.is_connected()
    except Exception:  # noqa: BLE001 - resolution must never raise
        return False


#: Applications for which a real adapter exists. A step aimed at one of these
#: must never be quietly downgraded to the local simulator: either a real
#: action resolves, or the step fails as unsupported.
_REAL_APPLICATIONS = frozenset({"gmail", "slack", "calendar", "google"})

# A single, complete, sendable mailbox address.
_EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A template placeholder such as "[Original Subject]" or "{subject}".
_PLACEHOLDER_RE = re.compile(r"\[[^\[\]]*\]|\{[^{}]*\}|<[^<>]*>")


def resolve_action(
    action: str,
    *,
    application: str = "",
    use_live_integrations: bool = True,
) -> Optional[str]:
    """Map a generated step action to a registry key, or ``None``.

    Resolution order:

    1. an exact allowlisted registry name (``gmail_read_email``, ``log``, …);
    2. an alias;
    3. a known email action name, which always resolves to the **real** Gmail
       adapter (when Gmail is not connected the provider raises, so the step
       fails visibly rather than reporting a success that never happened);
    4. otherwise the first matching keyword rule (AI analysis, transform,
       notify, delay, log, simulate).

    For a step whose ``application`` is one WorkFlowOS can really talk to
    (Gmail, Slack, Calendar), falling through to ``simulate`` is refused: the
    step becomes an unsupported action and fails. WorkFlowOS must never show a
    simulated outcome for work it claims to have done on a real service.

    Nothing here is invented: every returned value is a key that already exists
    in ``ACTION_REGISTRY``.
    """
    normalized = _normalize(action)
    if not normalized:
        return None
    if normalized in ACTION_REGISTRY:
        return normalized
    alias = _ALIASES.get(normalized)
    if alias:
        return alias
    if use_live_integrations and normalized in _GMAIL_LIVE_MAP:
        return _GMAIL_LIVE_MAP[normalized]
    tokens = set(normalized.split("_"))
    for keywords, target in _KEYWORD_RULES:
        if tokens & set(keywords):
            if target == "simulate" and _normalize(application) in _REAL_APPLICATIONS:
                return None
            return target
    return None


def run_action(action_type: str, context: ActionContext) -> str:
    """Invoke a registered handler. Unregistered names are refused."""
    handler = ACTION_REGISTRY.get(action_type)
    if handler is None:
        raise UnsupportedActionError(
            f"Action '{action_type}' is not in the registry; only "
            f"{', '.join(sorted(ACTION_REGISTRY))} may run"
        )
    return handler(context)


def registry_names() -> List[str]:
    return sorted(ACTION_REGISTRY)


def gmail_live_actions() -> List[str]:
    """Action names that upgrade to real Gmail when it is connected."""
    return sorted(_GMAIL_LIVE_MAP)
