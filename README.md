# WorkflowOS

**On-premise AI workflow automation that watches a real Gmail inbox, understands the work with a local model, proposes a workflow, and only acts after a human approves it.**

Everything runs on your own machine. The AI is a local [Ollama](https://ollama.com) model — no cloud AI provider is ever contacted. The only external service is Google's Gmail API, and only after you explicitly connect and authorise it.

---

## Overview

WorkflowOS observes real activity, finds the repeated pattern hiding in it, explains what that pattern actually *is*, drafts a workflow to handle it, and then asks a human before doing anything at all.

The core loop:

```
Observe → Discover → Understand with AI → Generate Workflow
       → Human Approval → Execute → Automate → Monitor
```

- **Observe** — a scheduler polls your connected Gmail mailbox and records genuinely new messages as activity events. Nothing synthetic is ever written to the real audit trail.
- **Discover** — activity is grouped into sequences and clustered by similarity, so a pattern that has already repeated becomes a *workflow candidate*.
- **Understand with AI** — a local Ollama model turns a candidate into an intent, a trigger, suggested steps and a recommended automation. It proposes; it never executes.
- **Generate Workflow** — the candidate plus its understanding become a structured, validated draft with an ordered, executable plan.
- **Human Approval** — the draft is `pending_approval`. Executing it at this point is **refused by design**, and the refusal is shown as a safety feature rather than an error.
- **Execute** — an approved draft runs through an allowlisted action registry. Steps that need an external service call its real API and fail loudly if it is unavailable.
- **Automate** — an approved workflow can be bound to a trigger (Gmail, calendar, schedule, manual) and run on demand or on a poll.
- **Monitor** — every step is recorded to an activity trail and aggregated into analytics.

---

## Key Features

- **Workflow discovery** — repetition detection over stored activity, with configurable similarity.
- **AI-powered workflow understanding** — local model inference producing intent, trigger, steps and confidence.
- **Local Ollama inference** — the model runs on your machine; message content never leaves it.
- **Workflow generation** — strict schema validation; invalid model output is rejected rather than persisted.
- **Human approval gate** — unapproved workflows cannot be executed; the refusal is intentional and demonstrable.
- **Workflow execution** — ordered steps, per-step status, real external API calls, and honest failure reporting.
- **Real Gmail integration** — genuine OAuth, message read, and reply send.
- **Automations** — bind an approved workflow to a trigger and run it now or on a schedule.
- **Activity tracking** — an append-only trail of every observed event and execution step.
- **Analytics** — execution counts, success rate and failure detail computed from real records, not hardcoded numbers.
- **Safe demo reset** — clears local run history so a demonstration can start clean, without touching connected accounts or real mail.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Next.js frontend  (React 19, Tailwind 4)    │
│  Overview · Activity · Workflows ·           │
│  Automations · Analytics · Settings          │
└───────────────────────┬──────────────────────┘
                        │  HTTP  (JSON)
┌───────────────────────▼──────────────────────┐
│  FastAPI backend                             │
│  ┌────────────────────────────────────────┐  │
│  │ Scheduler + background worker          │  │
│  │  · observe_providers()  → activity     │  │
│  │  · poll automations     → jobs         │  │
│  │  · recover stale jobs                  │  │
│  └───────────────┬────────────────────────┘  │
│  ┌───────────────▼────────────────────────┐  │
│  │ Discovery  →  AI understanding         │  │
│  │  pattern_detector · ollama_provider    │  │
│  └───────────────┬────────────────────────┘  │
│  ┌───────────────▼────────────────────────┐  │
│  │ Generator  →  Approval gate            │  │
│  └───────────────┬────────────────────────┘  │
│  ┌───────────────▼────────────────────────┐  │
│  │ Execution engine + allowlisted actions │  │
│  └───────────────┬────────────────────────┘  │
│  ┌───────────────▼────────────────────────┐  │
│  │ Integrations (Gmail · Calendar · Slack)│  │
│  │  encrypted tokens · OAuth · real API   │  │
│  └───────────────┬────────────────────────┘  │
│  ┌───────────────▼────────────────────────┐  │
│  │ SQLite (WAL) via stdlib sqlite3        │  │
│  └────────────────────────────────────────┘  │
└───────────┬──────────────────────┬───────────┘
            │                      │
   ┌────────▼────────┐   ┌─────────▼─────────┐
   │  Ollama         │   │  Gmail API        │
   │  :11434         │   │  (OAuth, real)    │
   │  qwen2.5-coder  │   │  readonly + send  │
   └─────────────────┘   └───────────────────┘
```

Key design decisions:

- **The AI never executes.** Understanding and generation only produce proposals; execution is a separate, explicit, human-gated action.
- **Model output is untrusted.** Generated drafts are schema-validated, and mailbox addresses or subjects a model invents are discarded rather than acted on.
- **Actions are allowlisted.** A step's action is resolved through a fixed registry and validated by the provider; it is never dispatched dynamically from model text.
- **Tokens are encrypted at rest** with a Fernet key generated locally, and never returned by any API.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, ESLint 9 |
| Backend | FastAPI, Uvicorn, Python 3.9+ |
| AI | Ollama (`qwen2.5-coder:7b` by default), local only |
| Scheduling | APScheduler (interval jobs + background worker) |
| Storage | SQLite via the standard library, WAL mode |
| Security | `cryptography` (Fernet) for OAuth token encryption |
| Testing | pytest, httpx, Playwright-driven UI checks |
| Gmail | Google Gmail API v1, OAuth 2.0 |

No database server, message broker or cloud account is required.

---

## Project Structure

```
WorkflowOS-Git/
├── backend/
│   ├── app/
│   │   ├── agents/           activity source abstraction
│   │   ├── ai/               Ollama provider, prompts, JSON extraction
│   │   ├── api/              FastAPI routers (11 route modules)
│   │   ├── automation/       execution engine + allowlisted actions
│   │   ├── database/         SQLite connection handling
│   │   ├── discovery/        sequence building, similarity, detection
│   │   ├── generator/        draft generation, prompt, parser/validator
│   │   ├── integrations/     Gmail, Calendar, Slack, OAuth, credentials
│   │   ├── models/           table definitions and row mappers
│   │   ├── scheduler/        scheduler service, queue, worker
│   │   ├── schemas/          Pydantic request/response models
│   │   ├── services/         activity, analytics, automations, system
│   │   ├── workflows/        workflow models
│   │   ├── config.py         single source of truth for env config
│   │   └── main.py           application entrypoint
│   ├── scripts/
│   │   └── prepare_demo_email.py   checks for the demo email (never sends)
│   ├── tests/                434 tests
│   ├── credentials/          drop your OAuth JSON here (git-ignored)
│   ├── data/                 SQLite database lives here (git-ignored)
│   ├── .env.example
│   ├── requirements.txt
│   └── pytest.ini
├── frontend/
│   ├── src/
│   │   ├── app/              pages: overview, activity, workflows,
│   │   │                     automations, analytics, settings
│   │   ├── components/       per-feature UI components
│   │   ├── lib/              typed API client
│   │   └── types/            shared TypeScript types
│   ├── .env.example
│   ├── package.json
│   ├── package-lock.json
│   ├── next.config.ts
│   ├── postcss.config.mjs     (Tailwind 4 via @tailwindcss/postcss)
│   ├── eslint.config.mjs
│   ├── tsconfig.json
│   └── public/
├── docs/                     architecture and per-subsystem design notes
├── .env.example              every environment variable, documented
├── .gitignore
├── Makefile
└── README.md
```

---

## Requirements

- **Python 3.9+** (developed and tested on 3.9)
- **Node.js 20+** and npm
- **[Ollama](https://ollama.com)** installed and running
- **A Gmail account** you are willing to authorise, plus a **second Gmail account** to send the incoming demo email from (the workflow must read a message that genuinely arrives from outside)
- **Google Cloud OAuth client** if you want Gmail integration (optional — the rest of the app runs without it)

---

## Installation

```bash
git clone <repository-url>
cd WorkflowOS-Git
```

Install both halves:

```bash
# Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# Frontend
cd frontend
npm install
cd ..
```

Or use the bundled Makefile:

```bash
make install        # runs install-backend and install-frontend
```

Install the Ollama model:

```bash
ollama pull qwen2.5-coder:7b
```

---

## Environment Setup

The root `.env.example` documents **every** environment variable the backend reads. The backend reads its own `.env` from `backend/`; the frontend reads `.env.local` from `frontend/`.

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

At minimum, review these in `backend/.env`:

```bash
AI_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_TIMEOUT=300
```

Keep `OLLAMA_TIMEOUT` generous — a cold 7B model can take 30–120 seconds to load, and a short timeout is the most common cause of a failed "Understand with AI" call.

To use Gmail, either drop your OAuth client JSON at
`backend/credentials/google-client-secret.json` (preferred) or set
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The registered redirect URI must
match exactly:

```
http://localhost:8000/api/integrations/gmail/callback
```

**Never commit `.env`, credentials or tokens.** They are git-ignored.

---

## Running WorkflowOS

Three terminals. Leave each running.

**Terminal 1 — Ollama**

```bash
ollama serve
```

**Terminal 2 — Backend**

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 3 — Frontend**

```bash
cd frontend
npm run dev
```

Then open:

```
http://localhost:3000
```

Verify the backend is healthy:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected: `{"status":"ok","service":"WorkflowOS"}`

Convenience targets: `make backend`, `make frontend`, `make health`, `make test`, `make lint`, `make build`.

---

## Demo Flow

The demo requires **two** real Gmail accounts. The connected account is the
mailbox WorkflowOS watches; a second, external account sends the incoming email
by hand (the backend cannot send as an account it is not authorised for).

Set them in `backend/.env`:

```bash
DEMO_SENDER_EMAIL=external.sender@example.com
DEMO_RECIPIENT_EMAIL=connected.mailbox@example.com
```

Then send this email **from the external account to the connected one**:

```
Subject: WorkflowOS Demo
Body:    I was charged twice for invoice 88213 and need a refund today.
```

Confirm it arrived:

```bash
cd backend
.venv/bin/python scripts/prepare_demo_email.py
```

Now walk the UI:

1. **Overview** — confirm the *Backend* card reads **Connected**.
2. **Settings** — connect Gmail if not already, and confirm the connected account address. Use **Test Connection** to make a real Gmail API call.
3. **Activity** — after a reset, real Gmail activity is observed within a few seconds.
4. **Workflows** — click **Discover Workflows**. A repeated pattern appears as a candidate.
5. Click **Understand with AI**. This is a real local Ollama call (roughly 30 seconds); the detail view opens by itself when it finishes.
6. Click **Generate Workflow** — a second real local Ollama call. A four-step plan appears: read → classify → send → log.
7. Show the status: **Pending Approval**.
8. Click **Execute Workflow** *before* approving.
9. The run is **refused**: *"Approval gate — run refused (by design)"*. Nothing executed and no email was sent — this is the intended security demonstration.
10. Click **Approve Workflow**. The status becomes **Approved**.
11. Click **Execute Workflow**. All four steps complete; the execution id and the real Gmail message ids are shown.
12. Confirm the reply arrived in the external account's inbox.
13. **Automations** — create an automation bound to the approved workflow with a **Gmail** trigger, then use **Run Now**.
14. **Activity** — the execution timeline with per-step events.
15. **Analytics** — executions, successes, failures and success rate, computed from real records.
16. **Settings** — integration status and the real action allowlist.

To restart the demo from a clean state:

```bash
curl -X POST http://127.0.0.1:8000/api/system/reset
```

The reset clears local run history only. It never touches the connected Gmail account, the OAuth token, the encryption key, or any real message, and it makes no external API calls.

---

## Gmail Setup

WorkflowOS uses the Gmail API with exactly two scopes:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/gmail.send`

It requests **no mailbox-modify scope**, so it cannot delete, label or alter
existing mail.

1. In [Google Cloud Console](https://console.cloud.google.com/), create (or
   pick) a project and **enable the Gmail API**.
2. Create an **OAuth client ID** of type *Web application*.
3. Add the redirect URI **exactly**:
   `http://localhost:8000/api/integrations/gmail/callback`
4. Download the client JSON and place it at
   `backend/credentials/google-client-secret.json`
   (git-ignored), or set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
5. Start the backend, open **Settings → Integrations → Gmail → Connect**, and
   approve the consent screen.
6. Confirm in **Settings** that Gmail is *connected* and shows your address.

**You must supply your own credentials.** None are included in this repository,
and none should ever be committed.

Because the authorisation is bound to a Google account, the demo mailbox is
yours. Use a dedicated or secondary account if that matters to you.

---

## Troubleshooting

**Backend not connected**

```bash
curl -s http://127.0.0.1:8000/health
lsof -tiTCP:8000 -sTCP:LISTEN
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Read the traceback in the backend terminal.

**Ollama not running or model missing**

```bash
ollama --version
curl -s http://127.0.0.1:11434/api/tags
ollama list                      # must include qwen2.5-coder:7b
ollama pull qwen2.5-coder:7b
ollama run qwen2.5-coder:7b "hi"  # warm the model before recording
```

Ollama can be *busy* rather than down: while a 7B generation is in flight it
may not answer a second request for several seconds. Warm it beforehand and keep
`OLLAMA_TIMEOUT=300`.

**Port already in use**

```bash
lsof -tiTCP:8000 -sTCP:LISTEN     # backend
lsof -tiTCP:3000 -sTCP:LISTEN     # frontend
lsof -tiTCP:11434 -sTCP:LISTEN    # ollama
kill <pid>
```

**Gmail authentication failure**

`Settings → Integrations → Gmail → Disconnect`, then **Connect** again. If the
log says *"authorization expired and could not be refreshed"*, the stored grant
is no longer usable and must be re-authorised. Then confirm:

```bash
curl -s http://127.0.0.1:8000/api/integrations/gmail/status
```

`connected` must be `true`.

**"No WorkflowOS Demo email received … yet"**

The step failed honestly because the message has not arrived. Confirm
`DEMO_SENDER_EMAIL` and `DEMO_RECIPIENT_EMAIL` match the real accounts, and
check the message really is in the connected mailbox (including Spam). The
subject must be exactly `WorkflowOS Demo`.

**Missing environment variables**

`Settings` reports integration state; the backend logs which credential source
it used at startup. Confirm `backend/.env` exists and that
`AI_PROVIDER=ollama` is set.

**Tests fail immediately after cloning**

The suite passes without any credentials — one test that inspects a real
credentials file skips itself. If many fail, confirm you installed into the
project venv:

```bash
cd backend && .venv/bin/python -m pytest
```

---

## Security

- **No credentials are committed.** The OAuth client JSON, `.env` files, the
  token encryption key and the SQLite database are all git-ignored.
- **OAuth tokens are encrypted at rest** with a Fernet key generated locally on
  first run, and are never returned by any API response.
- **Least-privilege Gmail scopes.** Read-only plus send. No modify scope, so
  existing mail cannot be altered or deleted.
- **Human approval is mandatory.** An unapproved workflow cannot be executed;
  the API refuses it and the UI presents that refusal as a safety feature.
- **Model output is never trusted.** Generated drafts are schema-validated, and
  mailbox addresses, recipients and subject placeholders invented by the model
  are discarded before anything is sent.
- **Actions are allowlisted.** Steps resolve through a fixed registry; no action
  name from model text is ever dispatched dynamically.
- **Local-first AI.** Inference runs on your machine; message content is not
  sent to a third-party AI service.
- **The demo reset is local-only.** It deletes WorkFlowOS's own records, makes no
  external API call, and preserves the authorised account and all real mail.

---

## License

No license file is included in this repository. If you intend to publish or
redistribute it, add a `LICENSE` file (for example MIT) separately.
