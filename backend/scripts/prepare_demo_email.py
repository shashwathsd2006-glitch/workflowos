"""Prepare the *incoming* demo email — without sending it.

WorkFlowOS can only send as the Gmail account it is authorised for. Sending
the demo message from that same account would make it a self-sent note, not a
genuine incoming email, so this script deliberately **does not send anything**.

It verifies the setup and then tells you exactly what to do by hand:

    from  DEMO_SENDER_EMAIL   (an external/test Gmail account)
    to    DEMO_RECIPIENT_EMAIL (the account WorkFlowOS is authorised for)
    subject "WorkflowOS Demo"
    body    "I was charged twice for invoice 88213 and need a refund today."

Once that message arrives, WorkFlowOS finds it with the real Gmail API, reads
it, classifies it with the local model, and (only after a human approves)
replies to the original sender.

Usage
-----
    cd backend
    DEMO_SENDER_EMAIL=you+test@gmail.com \\
    DEMO_REPLY_TO_UNUSED= .venv/bin/python scripts/prepare_demo_email.py

Environment
-----------
    DEMO_SENDER_EMAIL     external account that sends the test email
    DEMO_RECIPIENT_EMAIL  the connected WorkFlowOS mailbox (defaults to the
                          authenticated account)
    GMAIL_READ_QUERY      optional override of the search query
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEMO_EMAIL_SUBJECT, demo_read_query, settings  # noqa: E402
from app.integrations.registry import get_provider  # noqa: E402

DEMO_EMAIL_BODY = "I was charged twice for invoice 88213 and need a refund today."


def _fail(message: str) -> None:
    print(f"  ERROR: {message}")
    raise SystemExit(1)


def main() -> int:
    provider = get_provider("gmail")
    print("== WorkFlowOS incoming-demo email check ==")
    print(f"  provider        : {provider.name} (is_mock={provider.is_mock})")

    if not provider.is_configured():
        _fail(
            "no Google OAuth credentials. Expected "
            "backend/credentials/credentials.json or "
            "backend/credentials/google-client-secret.json"
        )
    if not provider.is_connected():
        _fail(
            "Gmail is not connected. Open Settings -> Integrations -> Gmail and "
            "click 'Connect Gmail (Google OAuth)' first."
        )

    diagnosis = provider.diagnose()
    if not diagnosis.get("ok"):
        _fail(f"Gmail profile call failed: {diagnosis.get('message')}")
    account = str(diagnosis.get("account_email") or "")
    print(f"  connected inbox : {account}   (via users.getProfile)")

    recipient = os.getenv("DEMO_RECIPIENT_EMAIL", "").strip() or account
    sender = os.getenv("DEMO_SENDER_EMAIL", "").strip() or settings.demo_sender_email
    if recipient != account:
        print(
            f"  WARNING: DEMO_RECIPIENT_EMAIL ({recipient}) is not the "
            f"connected account ({account}); the message would not arrive."
        )

    query = demo_read_query()
    print(f"  search query    : {query}")

    # Has the external message already arrived?
    try:
        found = provider.search_emails(query=query, max_results=5)
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        _fail(f"Gmail search failed: {exc}")

    if found:
        message = found[0]
        read = provider.read_message(message["id"])
        print("  status          : incoming demo email already present")
        print(f"  message id      : {message['id']}")
        print(f"  from            : {read.get('from')}")
        print(f"  to              : {read.get('to')}")
        print(f"  subject         : {read.get('subject')}")
        print(f"  body characters : {len(read.get('body') or '')}")
        print("== ready: no email was sent by this script ==")
        return 0

    print("  status          : NOT ARRIVED YET")
    print()
    print("  ================= DEMO EMAIL =================")
    print("   WorkFlowOS cannot send this for you. It only holds a token for")
    print("   the monitored mailbox, so it can never send *from* the external")
    print("   account. Sign in to that account and send this by hand:")
    print()
    print(f"     From     : {sender or '<DEMO_SENDER_EMAIL>'}")
    print(f"     To       : {recipient}")
    print(f"     Subject  : {DEMO_EMAIL_SUBJECT}")
    print(f"     Body     : {DEMO_EMAIL_BODY}")
    print("  ===============================================")
    print()
    print("  Then run this script again, or press Refresh in Select Email.")
    print("  WorkFlowOS will read it, classify it with the local model, and")
    print("  reply to the sender only after you approve the workflow.")
    print("== waiting for the external email ==")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
