# WorkFlowOS — hackathon demo runbook

Everything below was executed on this machine. Where a result is quoted it is
copied from a real run. Anything that could not be verified is stated as such
in [§15 What is real vs. local](#15-what-is-real-vs-local).

---

## 1. System requirements

| Component | Version used | Notes |
| --- | --- | --- |
| Python | 3.x + `backend/.venv` | dependencies already installed |
| Node.js | Next.js 15 app in `frontend/` | `npm run dev` |
| Ollama | local server on `127.0.0.1:11434` | model `qwen2.5-coder:7b` |
| Google account | one Gmail account you control | needed once, to authorise OAuth |

No cloud service, no external API other than Google, nothing leaves the machine
except the Gmail calls you explicitly approve.

## 2. Environment variables

Read from the environment, or from `backend/.env` (copy `backend/.env.example`).
**There is deliberately no `.env` in the repo, so the defaults below are what a
fresh clone uses.**

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_PROVIDER` | `ollama` | `ollama` = real local inference. `mock` exists only for the test suite. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | local Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | the model actually loaded |
| `DEMO_MODE` | `false` | **Leave off.** Enables labelled local stand-ins for offline tests. |
| `DATABASE_PATH` | `backend/data/workflowos.db` | SQLite file |
| `DEMO_SENDER_EMAIL` | *(empty)* | External Gmail account that **sends** the demo email |
| `DEMO_RECIPIENT_EMAIL` | *(empty)* | The connected WorkFlowOS mailbox (defaults to the authorised account) |
| `GMAIL_READ_QUERY` | *(empty)* | Overrides the derived query below |
| `GMAIL_REPLY_TO` | *(empty)* | The only address a send step may fall back to. Empty ⇒ a send step fails rather than guessing. |
| `GMAIL_POLL_INTERVAL_SECONDS` | `60` | provider poll throttle |
| `SCHEDULER_ENABLED` / `WORKER_ENABLED` | `true` | background scheduler and worker |

When `GMAIL_READ_QUERY` is empty the workflow searches for the incoming demo
email automatically:

```
subject:"WorkflowOS Demo" from:<DEMO_SENDER_EMAIL>
```

That deliberately excludes the replies WorkFlowOS itself sends, which live in
the same mailbox. A reply is addressed to the **sender of the message that was
read**, so no recipient has to be configured at all.

The effective query is visible at `GET /api/system/status` → `gmail`
(`effective_read_query`, `demo_sender_email`, `demo_recipient_email`). None of
these are secrets.

## 3. Gmail OAuth setup

1. Google Cloud Console → **APIs & Services → Credentials**.
2. Create an **OAuth client ID** → type **Desktop app** (or Web, if you prefer).
3. Download the JSON and save it as:

   ```
   backend/credentials/google-client-secret.json
   ```

   That path is gitignored. The file may be either the `installed` or the `web`
   shape; both are supported.
4. Enable the **Gmail API** in the same project.

## 4. Google Cloud redirect URI

Add this **exact** string under the OAuth client's *Authorized redirect URIs*:

```
http://localhost:8000/api/integrations/gmail/callback
```

Port 8000 is not optional — the backend must run there.

Required scopes (requested automatically, nothing more):

```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
```

`gmail.modify` is **not** requested, so WorkFlowOS cannot delete, label or mark
mail as read. Verified from the live authorize URL:

```
scope=https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send
redirect_uri=http://localhost:8000/api/integrations/gmail/callback
```

## 5. Start Ollama

```bash
ollama serve          # if it is not already running
ollama list           # must show qwen2.5-coder:7b
```

## 6. Start the backend

```bash
cd backend

# exactly what the demo run used
AI_PROVIDER=ollama \
GMAIL_READ_QUERY='subject:"WorkFlowOS Demo"' \
GMAIL_REPLY_TO='you@yourdomain.com' \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`DEMO_MODE` is intentionally not set → mock adapters are neither listed nor
runnable.

## 7. Start the frontend

```bash
cd frontend
npm run dev
```

## 8. Connect Gmail

1. Open **http://localhost:3000/settings** → **Integrations** → **Gmail**.
2. Click **Connect Gmail (Google OAuth)**.
3. Sign in as the Gmail account you want WorkFlowOS to use.
4. The consent screen must show exactly two permissions.
5. You land back on Settings. The card reads **Gmail connected** and
   **Connected account: your@gmail.com**.
6. Click **Test Connection**. This performs a real `users.getProfile` call; the
   result is the real address or the real error.

Tokens are encrypted at rest (Fernet) and no endpoint ever returns them.

## 9. Prepare the real demo email (DEMO EMAIL)

The demo uses **two real Gmail accounts**:

| Role | Address | Purpose |
| --- | --- | --- |
| Connected / monitored | `demo.recipient@example.com` | the mailbox WorkFlowOS reads and replies from |
| External / demo sender | `demo.sender@example.com` | sends the incoming demo email by hand |

WorkFlowOS holds an OAuth token **only** for the monitored account, so it can
never send *from* the external account. That message must be sent by hand.

### DEMO EMAIL

```
From     : demo.sender@example.com
To       : demo.recipient@example.com
Subject  : WorkflowOS Demo
Body     : I was charged twice for invoice 88213 and need a refund today.
```

### Start the backend configured for it

```bash
cd backend
DEMO_SENDER_EMAIL='demo.sender@example.com' \
DEMO_RECIPIENT_EMAIL='demo.recipient@example.com' \
AI_PROVIDER=ollama OLLAMA_TIMEOUT=300 \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

WorkFlowOS then searches Gmail with exactly:

```
subject:"WorkflowOS Demo" from:demo.sender@example.com to:demo.recipient@example.com
```

All three filters together mean it can never pick up an older message, or a
reply it sent itself. When several match, the **newest** is used.

### Confirm it arrived

```bash
cd backend
DEMO_SENDER_EMAIL='demo.sender@example.com' \
DEMO_RECIPIENT_EMAIL='demo.recipient@example.com' \
.venv/bin/python scripts/prepare_demo_email.py
```

The script never sends. It prints the real Gmail message id once the mail has
arrived, or the exact email to send. While waiting it says:

> No WorkflowOS Demo email received from demo.sender@example.com yet.

## 10. The demo workflow

The workflow is **generated by the local model from your real observed
activity** — it is not a shipped fixture. To have something to discover, the
activity agent ingests the events of a triage session you actually perform:

```bash
cd backend
B=http://127.0.0.1:8000

# the real sequence you perform for every support email, 3 sessions
python3 - <<'PY'
import json, urllib.request
B = "http://127.0.0.1:8000"
seq = [("Gmail","communication","read_email","Read the inbound support email"),
       ("WorkFlowOS AI","system","classify_priority","Classify the email priority with local AI"),
       ("Gmail","communication","send_email","Send the triage reply"),
       ("WorkFlowOS","system","log_result","Record the triage result")]
for s in range(1, 4):
    for app, cat, action, desc in seq:
        body = json.dumps({"application": app, "category": cat, "action": action,
                           "description": desc,
                           "session_id": f"triage-session-{s:02d}"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"{B}/api/activity", data=body,
            headers={"Content-Type": "application/json"}, method="POST"), timeout=20)
print("recorded 12 real events")
PY
```

From then on the model proposes the plan. A real run produced:

| # | Application | Action | Resolves to | Calls |
| --- | --- | --- | --- | --- |
| 1 | Gmail | `read_email` | `gmail_read_email` | real Gmail `messages.list` + `messages.get` |
| 2 | WorkFlowOS AI | `classify_priority` | `ai_analyze_content` | **real local Ollama inference** |
| 3 | Gmail | `send_email` | `gmail_send_email` | real Gmail `messages.send` |
| 4 | WorkFlowOS | `log_result` | `log` | local record |

Because a real Gmail message arriving also feeds the activity log, a connected
mailbox keeps producing discovery input on its own.

## 11. Exact clicks

1. **Workflows** → *Repeated Workflow* → **Understand with AI** (≈30 s, real
   Ollama) → **Generate workflow** (≈25 s, real Ollama).
2. Read the generated steps. Confirm the status is **Pending approval**.
3. **Approve**. Status becomes **Approved**.
4. **Execute Workflow**. Watch the six-step-equivalent plan resolve; the Gmail
   steps call the real API.
5. **Settings → Integrations → Gmail → Test Connection** for a real
   `users.getProfile`.
6. **Automations → Create Automation** → workflow = the approved draft,
   trigger = **Gmail**, subject contains `WorkFlowOS Demo` → **Create** →
   **Run now**.
7. **Analytics**, then **Activity**, then **Settings**.

## 12. Expected results

| Action | Expected |
| --- | --- |
| Understand with AI | `provider: ollama`, `model: qwen2.5-coder:7b`, ~30 s |
| Generate workflow | `generated_by: ollama`, `status: pending_approval` |
| Execute before approval | **HTTP 409**, zero executions created |
| Execute after approval, Gmail connected | steps `completed`; step 2 output contains `provider: ollama` and a real `priority` |
| Execute with Gmail **not** connected | step 1 **fails**: `Gmail authentication required. Connect Gmail in Settings.` Later steps stay `pending` |
| AI step, Ollama stopped | step **fails**: `Local AI provider 'ollama' failed: Local Ollama server unavailable at …` |
| Test Connection | real profile address, or the real API error |
| Analytics | totals move to match the executions you just ran |

A real verified run of the AI step, on a real Gmail payload:

```
provider : ollama  model: qwen2.5-coder:7b
analysed : 334 chars from the Gmail payload
summary            : Customer needs a refund for an invoice that was charged twice.
category           : Billing Issue
priority           : high
reasoning          : The customer is requesting an urgent refund, and the issue has already occurred twice.
recommended_action : Escalate the issue to the billing department immediately.
```

## 13. Reset between takes

```bash
curl -X POST http://127.0.0.1:8000/api/system/reset
curl      http://127.0.0.1:8000/api/system/reset/preview   # what would be removed
```

Removes local executions, steps, jobs, schedules, automations, drafts,
understandings, candidates and activity events. It **never** touches
`integration_accounts`, the credentials file, the encryption key, or any real
Gmail message, and it makes no external call. Guaranteed by
`tests/test_reset.py`.

## 14. Emergency troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Backend unreachable" in the UI | backend down or wrong port | restart on **8000**; the redirect URI requires it |
| Gmail card says "Not configured" | credentials file missing/misnamed | check `backend/credentials/google-client-secret.json` |
| OAuth returns `redirect_uri_mismatch` | URI not registered | add the exact URI from §4 |
| `Gmail authentication required` | not authorised, or token revoked | Settings → Connect Gmail again |
| `Local Ollama server unavailable` | Ollama not running | `ollama serve`; check `ollama list` |
| Understand takes >60 s | cold model / small machine | raise `OLLAMA_TIMEOUT`, or warm the model first with `ollama run qwen2.5-coder:7b "hi"` |
| `No Gmail message matched …` | §9 email not sent, or query differs | send the email; confirm the subject matches `GMAIL_READ_QUERY` |
| Send step: "A valid 'to' address is required" | `GMAIL_REPLY_TO` unset | set it, or add a `to` parameter |
| Scheduler hasn't fired | 60 s poll throttle | use **Run now** — no waiting needed |
| Step "not in the registry" | model invented an action | that is the allowlist working; re-generate or adjust |

## 15. What is real vs. local

**Real**
- Local Ollama inference (`qwen2.5-coder:7b`) for Understand, Generate, and the
  in-execution `ai_analyze_content` step.
- Gmail OAuth: state, code exchange, encrypted token storage, refresh, revoke.
- Gmail API: profile, search/list, read, send.
- Execution, approval gate, scheduler, worker, dedupe ledger.
- Analytics, per-workflow performance, failure analysis, audit trail — all from
  stored rows.

**Local / simulated — and only reachable with `DEMO_MODE=true`**
- `gmail_demo_*`, `slack_demo_*`, `calendar_demo_*` stand-ins.
- `simulate`, `notify_mock`, `delay_mock` handlers.
- `MockProvider` (`AI_PROVIDER=mock`), used by the test suite.
- `POST /api/activity/simulate` (refused unless `DEMO_MODE=true`; the product UI
  does not call it).

With the default configuration none of these are listed in `/api/integrations`,
none appear in the action allowlist, and none can be selected in the UI.

**Not implemented**
- Real Slack and Google Calendar credentials (both report `not_configured`).
- A generic "draft editing" endpoint, so a generated plan's step *parameters*
  cannot be edited after generation. That is why `GMAIL_READ_QUERY` and
  `GMAIL_REPLY_TO` exist as explicit, visible configuration.
- Live telemetry collection from other applications; discovery consumes the
  activity log, which real Gmail events now feed automatically.

## 16. Architecture, briefly

```
Gmail / Calendar / Slack  ──poll──▶  Scheduler  ──▶  integration_events (dedupe)
                                              │
                                              ▼
                                          background_jobs
                                              │  claim
                                              ▼
                                             Worker
                                              ▼
                              Execution engine (approved drafts only)
                                 │            │              │
                            real Gmail   local Ollama    log
                                 │            │              │
                                 └────────────┴──────────────┘
                                              ▼
                          executions + steps + activity_events
                                              ▼
                              Analytics  ·  Activity  ·  Settings
```

Discovery reads `activity_events`; the local model turns a repeated sequence
into an understanding and a draft; nothing executes until a human approves.
