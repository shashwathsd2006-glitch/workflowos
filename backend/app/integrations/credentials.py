"""Encrypted credential storage for OAuth tokens.

Tokens are encrypted with Fernet (authenticated AES) before they touch
SQLite. The key comes from ``INTEGRATION_TOKEN_KEY``; when that is absent a
key is generated once and written to a gitignored local file so a local
developer never has to hand-manage it.

Two invariants this module exists to enforce:

1. A stored token is never returned in plaintext by any API response — only
   metadata (present/expiry/scopes) leaves this module.
2. Plaintext tokens exist only in memory for the duration of a call.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import BACKEND_DIR, settings

logger = logging.getLogger("workflowos.integrations.credentials")

#: Local fallback key location. Gitignored; never committed.
LOCAL_KEY_PATH = BACKEND_DIR / "data" / ".integration_key"

_cached_fernet: Optional[Fernet] = None


class CredentialError(Exception):
    """Raised when a credential cannot be encrypted or decrypted."""


def _load_or_create_key() -> bytes:
    """Return the Fernet key from env, or a locally generated one."""
    if settings.integration_token_key:
        raw = settings.integration_token_key.strip()
        try:
            return base64_key(raw)
        except (ValueError, TypeError) as exc:
            raise CredentialError(
                "INTEGRATION_TOKEN_KEY must be a valid Fernet key "
                "(python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\")"
            ) from exc

    LOCAL_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_KEY_PATH.exists():
        return LOCAL_KEY_PATH.read_bytes().strip()

    key = Fernet.generate_key()
    LOCAL_KEY_PATH.write_bytes(key)
    # Owner-only: this file protects OAuth tokens.
    os.chmod(LOCAL_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Generated local integration key at %s", LOCAL_KEY_PATH)
    return key


def base64_key(raw: str) -> bytes:
    """Validate a Fernet key supplied as a string."""
    key = raw.encode("utf-8")
    Fernet(key)  # raises ValueError when malformed
    return key


def _cipher() -> Fernet:
    global _cached_fernet
    if _cached_fernet is None:
        _cached_fernet = Fernet(_load_or_create_key())
    return _cached_fernet


def encrypt_token(payload: Dict[str, Any]) -> str:
    """Encrypt a token bundle into an opaque string for storage."""
    if not isinstance(payload, dict):
        raise CredentialError("Token payload must be a mapping")
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return _cipher().encrypt(raw).decode("utf-8")


def decrypt_token(blob: str) -> Dict[str, Any]:
    """Decrypt a stored token bundle. Raises CredentialError when unusable."""
    if not blob:
        raise CredentialError("Empty credential blob")
    try:
        raw = _cipher().decrypt(blob.encode("utf-8"))
        decoded = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise CredentialError(
            "Stored credential could not be decrypted — reconnect the integration"
        ) from exc
    if not isinstance(decoded, dict):
        raise CredentialError("Stored credential is malformed")
    return decoded


def token_metadata(blob: Optional[str]) -> Dict[str, Any]:
    """Return safe metadata about a stored token. Never returns secrets."""
    if not blob:
        return {"present": False}
    try:
        payload = decrypt_token(blob)
    except CredentialError:
        return {"present": True, "readable": False}
    expires_at = payload.get("expires_at")
    # The stored token bundle records the space-delimited "scope" string that
    # Google returns; accept a pre-split "scopes" list too.
    scopes = payload.get("scopes") or payload.get("scope") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return {
        "present": True,
        "readable": True,
        "scopes": sorted(str(scope) for scope in scopes),
        "expires_at": expires_at,
        "can_refresh": bool(payload.get("refresh_token")),
        # Deliberately absent: access_token, refresh_token.
    }


__all__ = [
    "CredentialError",
    "LOCAL_KEY_PATH",
    "decrypt_token",
    "encrypt_token",
    "token_metadata",
]
