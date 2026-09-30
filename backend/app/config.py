"""Application configuration loaded from environment variables and .env files."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BACKEND_DIR / ".env")

DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")

#: OAuth client file downloaded from Google Cloud Console. Never committed
#: (see .gitignore) and never returned by any API.
#:
#: Both filenames are accepted, because Google labels the download "credentials"
#: while most projects save it as "google-client-secret":
#:   credentials/credentials.json
#:   credentials/google-client-secret.json
#: The first one that exists wins, so renaming the file cannot break the flow.
GOOGLE_CREDENTIALS_FILENAMES = ("credentials.json", "google-client-secret.json")
GOOGLE_CREDENTIALS_DIR = BACKEND_DIR / "credentials"


def google_credentials_path() -> Optional[Path]:
    """Return the credentials file that is actually present, if any."""
    override = _parse_optional(os.getenv("GOOGLE_CREDENTIALS_FILE"))
    if override:
        candidate = _resolve_path(override)
        return candidate if candidate.is_file() else None
    for name in GOOGLE_CREDENTIALS_FILENAMES:
        candidate = GOOGLE_CREDENTIALS_DIR / name
        if candidate.is_file():
            return candidate
    return None


#: Retained for callers that only need to know whether a file exists.
GOOGLE_CREDENTIALS_PATH = GOOGLE_CREDENTIALS_DIR / GOOGLE_CREDENTIALS_FILENAMES[0]


def load_google_credentials() -> Dict[str, Any]:
    """Read the Google OAuth client from the credentials directory.

    Supports both shapes Google produces: ``installed`` (desktop/web clients)
    and ``web``. Returns an empty dict when the file is absent, so a project
    with no Google app still starts and simply reports "not configured".
    """
    path = google_credentials_path()
    if path is None:
        return {}
    try:
        raw = path.read_text()
    except (OSError, ValueError):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    section = parsed.get("installed") or parsed.get("web") or {}
    if not isinstance(section, dict):
        return {}
    return {
        "client_id": section.get("client_id"),
        "client_secret": section.get("client_secret"),
        "auth_uri": section.get("auth_uri"),
        "token_uri": section.get("token_uri"),
        "project_id": section.get("project_id"),
        "redirect_uris": section.get("redirect_uris") or [],
        "source_file": path.name,
    }


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


def _parse_origins(raw: str) -> tuple[str, ...]:
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or DEFAULT_CORS_ORIGINS


def _parse_threshold(raw: str, default: float = 0.75) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.0), 1.0)


def _parse_provider(raw: str) -> str:
    provider = (raw or "").strip().lower()
    return provider if provider in {"ollama", "mock"} else "ollama"


def _parse_float(raw: str, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_int(raw: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _parse_bool(raw: str, default: bool = False) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional(raw: str) -> Optional[str]:
    """Return a stripped value, or None when unset/blank.

    Used for credentials: a blank env var must read as "not configured"
    rather than as an empty string that looks connected.
    """
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (raw or "").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str
    api_version: str
    environment: str
    debug: bool
    host: str
    port: int
    database_path: Path
    cors_origins: tuple[str, ...]
    similarity_threshold: float
    ai_provider: str
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float

    # --- Phase 8: demo mode -------------------------------------------------
    demo_mode: bool

    # --- Phase 8: scheduler + background worker -----------------------------
    scheduler_enabled: bool
    scheduler_tick_seconds: float
    worker_enabled: bool
    worker_poll_seconds: float
    worker_batch_size: int
    job_max_retries: int
    job_retry_backoff_seconds: float
    job_stale_after_seconds: int

    # --- Phase 8: integration credentials -----------------------------------
    google_client_id: Optional[str]
    google_client_secret: Optional[str]
    google_redirect_uri: Optional[str]
    google_credentials_present: bool
    google_client_source: str
    frontend_url: Optional[str]
    slack_client_id: Optional[str]
    slack_client_secret: Optional[str]
    slack_redirect_uri: Optional[str]
    integration_token_key: Optional[str]
    integration_request_timeout: float
    gmail_poll_interval_seconds: int
    calendar_poll_interval_seconds: int
    gmail_read_query: str
    gmail_reply_to: str
    gmail_reply_subject: str
    demo_sender_email: str
    demo_recipient_email: str

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_client_id and self.slack_client_secret)


def build_settings() -> Settings:
    google_file = load_google_credentials()
    env_client_id = _parse_optional(os.getenv("GOOGLE_CLIENT_ID"))
    env_client_secret = _parse_optional(os.getenv("GOOGLE_CLIENT_SECRET"))
    client_id = env_client_id or _parse_optional(google_file.get("client_id"))
    client_secret = env_client_secret or _parse_optional(
        google_file.get("client_secret")
    )
    if env_client_id and env_client_secret:
        source = "environment"
    elif client_id and client_secret:
        source = "credentials-file"
    else:
        source = "none"

    return Settings(
        app_name="WorkFlowOS",
        api_version="v1",
        environment=os.getenv("ENVIRONMENT", "development").lower(),
        debug=os.getenv("DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"},
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        database_path=_resolve_path(os.getenv("DATABASE_PATH", "data/workflowos.db")),
        cors_origins=_parse_origins(
            os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS))
        ),
        similarity_threshold=_parse_threshold(os.getenv("SIMILARITY_THRESHOLD", "0.75")),
        ai_provider=_parse_provider(os.getenv("AI_PROVIDER", "ollama")),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b").strip(),
        ollama_timeout=_parse_float(os.getenv("OLLAMA_TIMEOUT", "60"), 60.0),
        demo_mode=_parse_bool(os.getenv("DEMO_MODE"), False),
        scheduler_enabled=_parse_bool(os.getenv("SCHEDULER_ENABLED"), True),
        scheduler_tick_seconds=_parse_float(
            os.getenv("SCHEDULER_TICK_SECONDS", "5"), 5.0
        ),
        worker_enabled=_parse_bool(os.getenv("WORKER_ENABLED"), True),
        worker_poll_seconds=_parse_float(
            os.getenv("WORKER_POLL_SECONDS", "2"), 2.0
        ),
        worker_batch_size=_parse_int(os.getenv("WORKER_BATCH_SIZE", "5"), 5, 1),
        job_max_retries=_parse_int(os.getenv("JOB_MAX_RETRIES", "2"), 2),
        job_retry_backoff_seconds=_parse_float(
            os.getenv("JOB_RETRY_BACKOFF_SECONDS", "5"), 5.0
        ),
        job_stale_after_seconds=_parse_int(
            os.getenv("JOB_STALE_AFTER_SECONDS", "900"), 900, 30
        ),
        google_client_id=client_id,
        google_client_secret=client_secret,
        google_redirect_uri=_parse_optional(os.getenv("GOOGLE_REDIRECT_URI")),
        google_credentials_present=bool(google_file.get("client_id")),
        google_client_source=source,
        frontend_url=_parse_optional(os.getenv("FRONTEND_URL")),
        slack_client_id=_parse_optional(os.getenv("SLACK_CLIENT_ID")),
        slack_client_secret=_parse_optional(os.getenv("SLACK_CLIENT_SECRET")),
        slack_redirect_uri=_parse_optional(os.getenv("SLACK_REDIRECT_URI")),
        integration_token_key=_parse_optional(
            os.getenv("INTEGRATION_TOKEN_KEY")
        ),
        integration_request_timeout=_parse_float(
            os.getenv("INTEGRATION_REQUEST_TIMEOUT", "20"), 20.0
        ),
        gmail_poll_interval_seconds=_parse_int(
            os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"), 60, 5
        ),
        gmail_read_query=os.getenv("GMAIL_READ_QUERY", "").strip(),
        gmail_reply_to=os.getenv("GMAIL_REPLY_TO", "").strip(),
        gmail_reply_subject=os.getenv("GMAIL_REPLY_SUBJECT", "").strip(),
        demo_sender_email=os.getenv("DEMO_SENDER_EMAIL", "").strip(),
        demo_recipient_email=os.getenv("DEMO_RECIPIENT_EMAIL", "").strip(),
        calendar_poll_interval_seconds=_parse_int(
            os.getenv("CALENDAR_POLL_INTERVAL_SECONDS", "60"), 60, 5
        ),
    )


settings = build_settings()


#: Subject line the demo email is expected to carry. The workflow looks for this
#: so it never picks up the reply WorkFlowOS itself sent.
DEMO_EMAIL_SUBJECT = "WorkflowOS Demo"


def demo_read_query() -> str:
    """Build the Gmail query used to find the incoming demo email.

    Deliberately narrow: the subject, the external sender, and the monitored
    recipient. Combining all three is what stops an older message — or a reply
    WorkFlowOS itself sent — from ever being picked up.

    An explicit ``GMAIL_READ_QUERY`` always wins, so an operator can override
    this without a code change.
    """
    if settings.gmail_read_query:
        return settings.gmail_read_query
    parts = [f'subject:"{DEMO_EMAIL_SUBJECT}"']
    if settings.demo_sender_email:
        parts.append(f"from:{settings.demo_sender_email}")
    if settings.demo_recipient_email:
        parts.append(f"to:{settings.demo_recipient_email}")
    if not (settings.demo_sender_email or settings.demo_recipient_email):
        # Nothing configured: still exclude our own outgoing mail.
        parts.append("-from:me")
    return " ".join(parts)
