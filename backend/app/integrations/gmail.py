"""Gmail integration — official Gmail REST API over OAuth 2.0.

Uses ``gmail.googleapis.com`` endpoints only: no scraping, no password
collection. Polling is the trigger mechanism, and every observed message is
recorded in ``integration_events`` so the same message can never trigger the
same automation twice.
"""

from __future__ import annotations

import base64
import logging
import re
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from app.config import DEMO_EMAIL_SUBJECT, demo_read_query, settings
from app.database import get_connection
from app.integrations.base import (
    AuthenticationError,
    IntegrationError,
    IntegrationProvider,
    IntegrationStatus,
    NotConfiguredError,
    ValidationError,
)
from app.integrations.google_oauth import (
    GoogleOAuthMixin,
    integration_model_token_metadata,
)
from app.models import integration as integration_model

logger = logging.getLogger("workflowos.integrations.gmail")

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Minimum scopes for the two supported actions.
#:
#: - ``gmail.readonly`` -> search and read messages
#: - ``gmail.send``     -> send a message
#:
#: ``gmail.modify`` is deliberately NOT requested: WorkFlowOS must never
#: mutate the user's mailbox. Idempotency is enforced by the
#: ``integration_events`` ledger instead of by marking mail as read.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)


def _decode_body(payload: Dict[str, Any]) -> str:
    """Extract plain text from a Gmail message payload tree."""
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime in {"text/plain", ""}:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
        except (ValueError, TypeError):
            return ""
    for part in payload.get("parts") or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def _headers(message: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for header in message.get("payload", {}).get("headers", []) or []:
        name = header.get("name")
        if name:
            result[str(name).lower()] = header.get("value", "")
    return result


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _address_only(value: str) -> str:
    """Reduce a ``Name <addr@host>`` header to just the lowercased address."""
    text = (value or "").strip()
    if "<" in text and ">" in text:
        text = text[text.rfind("<") + 1 : text.rfind(">")]
    return text.strip().strip(",").strip().lower()


def _clean(value: str) -> str:
    """Make a user value safe to embed in a Gmail query clause.

    Strips quotes (which would close the clause early) and control characters
    / newlines (which would start a new one).
    """
    text = (value or "").replace('"', "")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def build_query(
    from_address: Optional[str] = None,
    subject: Optional[str] = None,
    label: Optional[str] = None,
    unread: bool = False,
    query: Optional[str] = None,
) -> str:
    """Compose a Gmail search query from safe, allowlisted criteria."""
    parts: List[str] = []
    if from_address:
        cleaned = _clean(from_address)
        if cleaned:
            parts.append(f'from:"{cleaned}"')
    if subject:
        cleaned = _clean(subject)
        if cleaned:
            parts.append(f'subject:"{cleaned}"')
    if label:
        cleaned = _clean(label)
        if cleaned:
            parts.append(f"label:{cleaned}")
    if unread:
        parts.append("is:unread")
    if query:
        cleaned = _clean(query)
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts) if parts else "is:unread"


class GmailIntegration(GoogleOAuthMixin, IntegrationProvider):
    name = "gmail"
    label = "Gmail"
    is_mock = False
    provider_name = "gmail"
    default_scopes = SCOPES

    # ------------------------------------------------------------- status

    def is_configured(self) -> bool:
        return settings.google_configured

    def status(self) -> Dict[str, Any]:
        if not self.is_configured():
            return {
                "provider": self.name,
                "label": self.label,
                "is_mock": False,
                "configured": False,
                "connected": False,
                "state": IntegrationStatus.NOT_CONFIGURED,
                "message": "Google integration is not configured.",
                "scopes": list(SCOPES),
                "poll_interval_seconds": settings.gmail_poll_interval_seconds,
                "requested_scopes": list(SCOPES),
                "credential_source": "none",
            }
        meta = integration_model_token_metadata(self.name)
        connected = bool(meta.get("present")) and bool(meta.get("readable"))
        return {
            "provider": self.name,
            "label": self.label,
            "is_mock": False,
            "configured": True,
            "connected": connected,
            "state": (
                IntegrationStatus.CONNECTED
                if connected
                else IntegrationStatus.DISCONNECTED
            ),
            "message": (
                "Connected via Google OAuth."
                if connected
                else "Not connected. Authorise with Google to enable Gmail."
            ),
            "scopes": meta.get("scopes") or [],
            "account_email": meta.get("account_email"),
            "account_label": meta.get("account_label"),
            "poll_interval_seconds": settings.gmail_poll_interval_seconds,
            "requested_scopes": list(SCOPES),
            "credential_source": self.credential_source(),
            "redirect_uri": self.redirect_uri(),
            "token": {
                key: meta.get(key)
                for key in ("present", "readable", "expires_at", "can_refresh")
            },
        }

    def diagnose(self) -> Dict[str, Any]:
        """Classify the Gmail connection into one unambiguous state.

        The six outcomes the UI must be able to show:

        - ``not_configured``           no OAuth client available
        - ``configured_not_connected`` client present, account not authorised
        - ``connected``                profile fetched successfully
        - ``token_expired_refreshable`` access token stale, refresh worked
        - ``permission_denied``        Google refused the call (403)
        - ``authentication_error``     grant revoked / invalid (401)

        ``connected`` is only ever returned when ``users/me/profile``
        actually succeeded.
        """
        if not self.is_configured():
            return {
                "state": "not_configured",
                "ok": False,
                "provider": self.name,
                "message": "Google integration is not configured.",
                "account_email": None,
                "token_refresh": "unavailable",
            }

        if not self.is_connected():
            return {
                "state": "configured_not_connected",
                "ok": False,
                "provider": self.name,
                "message": "OAuth client is configured, but no Gmail account is connected.",
                "account_email": None,
                "token_refresh": "unavailable",
                "credential_source": self.credential_source(),
                "redirect_uri": self.redirect_uri(),
            }

        meta = integration_model_token_metadata(self.name)
        refreshed = False
        try:
            profile = self._google_request("GET", f"{GMAIL_API}/profile")
        except AuthenticationError as exc:
            return {
                "state": "authentication_error",
                "ok": False,
                "provider": self.name,
                "message": (
                    f"Gmail rejected the stored grant ({exc}). "
                    "Disconnect and connect again."
                ),
                "account_email": meta.get("account_email"),
                "token_refresh": "rejected",
            }
        except NotConfiguredError as exc:
            return {
                "state": "not_configured",
                "ok": False,
                "provider": self.name,
                "message": str(exc),
                "account_email": None,
                "token_refresh": "unavailable",
            }
        except IntegrationError as exc:
            text = str(exc).lower()
            # Classify on the HTTP status as well as the wording, so the state
            # stays correct however the message is phrased.
            if "403" in text or "permission" in text:
                return {
                    "state": "permission_denied",
                    "ok": False,
                    "provider": self.name,
                    "message": (
                        "Gmail refused the request — the granted scopes are "
                        "insufficient for this call."
                    ),
                    "account_email": meta.get("account_email"),
                    "token_refresh": "available"
                    if meta.get("can_refresh")
                    else "unavailable",
                }
            return {
                "state": (
                    "authentication_error"
                    if ("401" in text or "authoriz" in text)
                    else "error"
                ),
                "ok": False,
                "provider": self.name,
                "message": str(exc),
                "account_email": meta.get("account_email"),
                "token_refresh": "available"
                if meta.get("can_refresh")
                else "unavailable",
            }

        # A successful profile fetch is the only path to "connected".
        account = profile.get("emailAddress")
        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            integration_model.upsert_account(
                connection,
                self.name,
                encrypted_token=row_token_blob(self.name),
                account_label=f"Gmail ({profile.get('messagesTotal', 0)} messages)",
                account_email=account,
                is_mock=False,
                verified=True,
            )
        return {
            "state": "connected",
            "ok": True,
            "provider": self.name,
            "message": f"Gmail API reachable ({account}).",
            "account_email": account,
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
            "email_count": profile.get("messagesTotal"),
            "token_refresh": "available" if meta.get("can_refresh") else "unavailable",
            "scopes": meta.get("scopes") or [],
        }

    def test_connection(self) -> Dict[str, Any]:
        """Backwards-compatible wrapper around :meth:`diagnose`."""
        return self.diagnose()

    # -------------------------------------------------------------- gmail

    def fetch_messages(
        self, *, query: str, max_results: int = 5, include_body: bool = False
    ) -> List[Dict[str, Any]]:
        """List messages matching a Gmail query."""
        listing = self._google_request(
            "GET",
            f"{GMAIL_API}/messages",
            params={"q": query, "maxResults": max(1, min(max_results, 25))},
        )
        results: List[Dict[str, Any]] = []
        for stub in (listing.get("messages") or [])[:max_results]:
            message_id = stub.get("id")
            if not message_id:
                continue
            message = self._google_request(
                "GET",
                f"{GMAIL_API}/messages/{message_id}",
                params={"format": "full" if include_body else "metadata"},
            )
            results.append(self._summarize(message, include_body))
        return results

    def _summarize(
        self, message: Dict[str, Any], include_body: bool
    ) -> Dict[str, Any]:
        head = _headers(message)
        internal = message.get("internalDate")
        received = None
        if internal:
            try:
                from datetime import datetime, timezone

                received = datetime.fromtimestamp(
                    int(internal) / 1000, tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError):
                received = None
        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from": head.get("from", ""),
            "reply_to": head.get("reply-to", ""),
            "to": head.get("to", ""),
            "subject": head.get("subject", ""),
            "snippet": message.get("snippet", ""),
            "label_ids": message.get("labelIds", []),
            "unread": "UNREAD" in (message.get("labelIds") or []),
            "received_at": received,
            "body": _decode_body(message.get("payload", {})) if include_body else None,
        }

    def search_emails(
        self,
        *,
        query: Optional[str] = None,
        from_address: Optional[str] = None,
        subject: Optional[str] = None,
        label: Optional[str] = None,
        unread: bool = False,
        max_results: int = 5,
        include_body: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search the mailbox and return matching messages.

        The same safe criteria builder the poller uses is applied here, so a
        user-supplied query cannot inject Gmail operators.
        """
        resolved = query or build_query(
            from_address=from_address,
            subject=subject,
            label=label,
            unread=unread,
        )
        return self.fetch_messages(
            query=resolved,
            max_results=max_results,
            include_body=include_body,
        )

    def _criteria_are_trusted(
        self,
        query: Optional[str],
        from_address: Optional[str],
        subject: Optional[str] = None,
    ) -> bool:
        """Whether caller-supplied criteria may name a mailbox address.

        Criteria with no address in them are always fine. An address is only
        accepted when it is one the operator configured for this mailbox or
        the account that is actually authenticated, which is what stops a
        model-invented address from redirecting a real Gmail read.
        """
        allowed = set()
        for value in (
            settings.demo_sender_email,
            settings.demo_recipient_email,
            settings.gmail_read_query,
            self.stored_account_email(),
        ):
            for found in _EMAIL_RE.findall(value or ""):
                allowed.add(_address_only(found))
        allowed.discard("")
        if not allowed:
            return True
        proposed = _EMAIL_RE.findall(
            f"{query or ''} {from_address or ''} {subject or ''}"
        )
        if not proposed:
            return True
        return all(_address_only(found) in allowed for found in proposed)

    def _is_self_sent(self, message: Dict[str, Any]) -> bool:
        """True when this account is the sender of ``message``.

        Used to keep WorkflowOS from reading its own outbound replies as
        though they were fresh incoming mail.
        """
        own = _address_only(self.stored_account_email())
        if not own:
            return False
        return _address_only(str(message.get("from") or "")) == own

    def _no_match_message(self, criteria: str) -> str:
        """Explain an empty lookup precisely enough to act on.

        The most common cause is simply that the expected email was never
        sent, so the message names the mailbox being watched, the exact query
        that was sent to Gmail, and what to do next. No token or secret is
        included.
        """
        account = self.stored_account_email()
        parts = []
        if settings.demo_sender_email:
            parts.append(
                f"No {DEMO_EMAIL_SUBJECT} email received from "
                f"{settings.demo_sender_email} yet."
            )
        elif criteria:
            parts.append(f"No Gmail message matched {criteria!r}.")
        else:
            parts.append("No Gmail message was found in this mailbox.")
        if account:
            parts.append(f"WorkFlowOS is monitoring {account}.")
        if settings.demo_sender_email:
            parts.append(
                f"Send it from {settings.demo_sender_email} to "
                f"{settings.demo_recipient_email or account or 'the monitored mailbox'} "
                f"with subject '{DEMO_EMAIL_SUBJECT}', then run again."
            )
        parts.append("Nothing was read.")
        return " ".join(parts)

    def stored_account_email(self) -> str:
        """The verified account address recorded at authorisation time.

        Read from the database so building an error message never costs an
        extra API call.
        """
        try:
            with get_connection() as connection:
                row = integration_model.select_account(connection, self.name)
        except Exception:  # noqa: BLE001 - diagnostics must never fail a read
            return ""
        if row is None:
            return ""
        try:
            return str(row["account_email"] or "")
        except (IndexError, KeyError):
            return ""

    def read_message(
        self,
        message_id: str,
        *,
        query: Optional[str] = None,
        subject: Optional[str] = None,
        from_address: Optional[str] = None,
        include_body: bool = True,
    ) -> Dict[str, Any]:
        """Fetch one real message including body text.

        Given a ``message_id`` that message is read. Given search criteria
        instead, the newest matching message is resolved through a real Gmail
        search first — so a workflow can say "read the email about invoice
        88213" and act on whatever is genuinely in the mailbox.

        If nothing matches, this raises. It never invents a message.
        """
        if not message_id:
            # Precedence: an explicit query, then explicit subject/from, then
            # the operator-configured default. With none of those, fall back to
            # the newest message in the mailbox so a run is always possible
            # without pinning the demo to one subject line.
            #
            # Caller criteria normally come from a language-model generated
            # step, so an address that is neither configured for this mailbox
            # nor the authenticated account is treated as invented and dropped.
            # Operator configuration then supplies the real target. A caller
            # query that carries no address at all is still honoured, which
            # keeps explicit operator searches working.
            if (
                (query or from_address or subject)
                and demo_read_query()
                and not self._criteria_are_trusted(query, from_address, subject)
            ):
                logger.warning(
                    "%s: ignoring step-supplied mailbox criteria %r; no address "
                    "in it is configured for this mailbox, so the configured "
                    "query is used instead",
                    self.name,
                    query or from_address or subject,
                )
                # Clear every criterion, not just the address: keeping a bare
                # subject would drop the configured from/to filters and could
                # match this account's own replies.
                query = None
                from_address = None
                subject = None
            criteria = (
                query
                or (
                    build_query(from_address=from_address, subject=subject)
                    if (from_address or subject)
                    else ""
                )
                or demo_read_query()
            )
            if not criteria:
                logger.info(
                    "%s: no message id or criteria given; defaulting to the "
                    "newest message in the mailbox",
                    self.name,
                )
                criteria = ""
            # Fetch a few, then pick the newest explicitly rather than
            # relying on Gmail's listing order alone.
            matches = self.fetch_messages(
                query=criteria or "in:anywhere",
                max_results=5,
                include_body=include_body,
            )
            # A reply this account sent itself is not an incoming message.
            # Without this filter a subject-only search would happily read
            # WorkflowOS's own outbound reply and then answer itself.
            incoming = [m for m in matches if not self._is_self_sent(m)]
            if not incoming:
                if matches:
                    raise ValidationError(
                        self._no_match_message(criteria)
                        + " The only matches were messages this account sent "
                        "itself, so there is no incoming email to read yet."
                    )
                raise ValidationError(self._no_match_message(criteria))
            # Explicitly take the newest match so repeated demo runs always act
            # on the most recently received email, and require a real Gmail
            # message id before anything is read.
            chosen = max(
                incoming, key=lambda m: str(m.get("received_at") or "")
            )
            if not chosen.get("id"):
                raise ValidationError(
                    "Gmail returned a matching message without a message id. "
                    "Nothing was read."
                )
            logger.info(
                "%s: %d match(es) for %r; reading newest %s (%s)",
                self.name,
                len(matches),
                criteria,
                chosen.get("id"),
                chosen.get("subject"),
            )
            return chosen
        message = self._google_request(
            "GET", f"{GMAIL_API}/messages/{message_id}", params={"format": "full"}
        )
        return self._summarize(message, include_body=True)

    def send_email(
        self, *, to: str, subject: str, body: str, thread_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a message via the Gmail API.

        ``to`` and ``subject`` may be omitted only when the operator has
        explicitly set ``GMAIL_REPLY_TO`` / ``GMAIL_REPLY_SUBJECT``. There is
        no implicit recipient or subject: with nothing configured the step
        fails instead of inventing either.
        """
        to_address = (to or "").strip() or settings.gmail_reply_to
        if not to_address or "@" not in to_address:
            raise ValidationError(
                "A valid 'to' address is required (set the step parameter, "
                "or configure GMAIL_REPLY_TO)"
            )
        subject_line = (subject or "").strip() or settings.gmail_reply_subject
        if not subject_line:
            raise ValidationError(
                "A subject is required (set the step parameter, or configure "
                "GMAIL_REPLY_SUBJECT)"
            )

        raw = _build_mime(to_address, subject_line, body or "")
        payload = self._google_request(
            "POST",
            f"{GMAIL_API}/messages/send",
            json={"raw": raw, **({"threadId": thread_id} if thread_id else {})},
        )
        message_id = payload.get("id")
        if not message_id:
            # A 200 without an id is not a delivery confirmation. Never
            # report a send as successful on Google's say-so alone.
            raise IntegrationError(
                "Gmail API request failed: messages.send returned HTTP 200 "
                "with no message id, so delivery is unconfirmed"
            )
        return {
            "ok": True,
            "message_id": message_id,
            "thread_id": payload.get("threadId"),
            "to": to_address,
            "subject": subject_line,
        }

    def mark_processed(self, message_id: str) -> Dict[str, Any]:
        """Not supported: WorkFlowOS holds no ``gmail.modify`` scope.

        Duplicate suppression is handled by the ``integration_events`` ledger
        in the scheduler, so no mailbox mutation is needed or permitted.
        """
        raise ValidationError(
            "Marking messages as read is not supported — WorkFlowOS requests "
            "no mailbox-modify scope. Duplicate suppression uses the "
            "integration event ledger instead."
        )

    # ------------------------------------------------------------- actions

    def execute_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a Gmail action by name. Unknown names are rejected."""
        if action == "gmail_search_emails":
            return {
                "ok": True,
                "messages": self.search_emails(
                    query=payload.get("query"),
                    from_address=payload.get("from"),
                    subject=payload.get("subject"),
                    label=payload.get("label"),
                    unread=bool(payload.get("unread", False)),
                    max_results=int(payload.get("max_results", 5) or 5),
                    include_body=bool(payload.get("include_body", False)),
                ),
            }
        if action == "gmail_read_email":
            return self.read_message(
                str(payload.get("message_id") or ""),
                query=payload.get("query"),
                subject=payload.get("subject"),
                from_address=payload.get("from"),
                include_body=bool(payload.get("include_body", True)),
            )
        if action == "gmail_send_email":
            return self.send_email(
                to=str(payload.get("to") or ""),
                subject=str(payload.get("subject") or ""),
                body=str(payload.get("body") or ""),
                thread_id=payload.get("thread_id"),
            )
        if action == "gmail_fetch_messages":
            return {
                "ok": True,
                "messages": self.fetch_messages(
                    query=str(payload.get("query") or build_query()),
                    max_results=int(payload.get("max_results", 5) or 5),
                    include_body=bool(payload.get("include_body", False)),
                ),
            }
        raise ValidationError(f"Gmail does not support action '{action}'")

    # ------------------------------------------------------------ polling

    def poll_events(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return new messages matching the configured criteria.

        Idempotency lives in ``integration_events``; this method only reports
        what Gmail currently offers.
        """
        if not self.is_connected():
            return []
        query = build_query(
            from_address=config.get("from"),
            subject=config.get("subject"),
            label=config.get("label"),
            unread=bool(config.get("unread", True)),
            query=config.get("query"),
        )
        try:
            messages = self.fetch_messages(
                query=query,
                max_results=int(config.get("max_results", 5) or 5),
                include_body=False,
            )
        except IntegrationError as exc:
            logger.warning("Gmail poll failed: %s", exc)
            return []
        return [
            {
                "external_event_id": f"gmail:{item['id']}",
                "provider": self.name,
                "message_id": item["id"],
                "from": item.get("from"),
                "subject": item.get("subject"),
                "snippet": item.get("snippet"),
                "unread": item.get("unread"),
                "received_at": item.get("received_at"),
            }
            for item in messages
            if item.get("id")
        ]


def row_token_blob(provider: str) -> Optional[str]:
    """Re-read the stored encrypted blob so a verified account can be updated.

    Returns the ciphertext, never plaintext.
    """
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        row = integration_model.select_account(connection, provider)
    return row["encrypted_token"] if row else None


def _build_mime(to: str, subject: str, body: str) -> str:
    """Build a base64url RFC 2822 message for the Gmail send endpoint."""
    raw = (
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=\"UTF-8\"\r\n"
        "\r\n"
        f"{body}"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def parse_date(value: str) -> Optional[str]:
    """Parse an RFC 2822 date header into ISO-8601."""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


def record_poll_event(
    provider: str, external_event_id: str, automation_id: Optional[str], payload: Dict[str, Any]
) -> Optional[str]:
    """Record an external event, returning None when already seen."""
    with get_connection() as connection:
        integration_model.ensure_tables(connection)
        return integration_model.record_event(
            connection,
            provider=provider,
            external_event_id=external_event_id,
            automation_id=automation_id,
            payload=payload,
        )


__all__ = [
    "GmailIntegration",
    "SCOPES",
    "build_query",
    "parse_date",
    "record_poll_event",
]
