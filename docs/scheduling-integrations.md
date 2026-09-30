# Phase 8 — Scheduling, Background Execution & Integrations

WorkFlowOS now runs approved workflows on a schedule or in response to an
external event, through a durable queue and a background worker.

```
Workflow Candidate → Understanding → Generation → Human Approval → APPROVED
        ↓
Automation (trigger_type + trigger_config)
        ↓
Scheduler tick ──→ event poller (Gmail / Calendar)
        ↓
background_jobs row (queued)
        ↓
Worker claims the job (queued → running, atomic)
        ↓
EXISTING execution engine (app.automation.engine.execute_draft)
        ↓
Action registry → IntegrationProvider
        ↓
Steps + audit events + analytics
```

**The execution engine was not replaced.** The worker calls
`app.automation.engine.execute_draft`, the same function the manual "Run now"
button uses, so there is exactly one execution path and one audit trail.

---

## Scheduler

`app/scheduler/service.py` — a background ticker (APScheduler
`BackgroundScheduler` drives the interval; all state lives in this project's
own SQLite tables, so a restart recomputes rather than restoring a
scheduler-side job store).

Each tick:

1. Fires any `automation_schedules` row whose `next_run` has arrived.
2. Polls connected event providers for new external events.
3. Enqueues a job for each *new* event.

Recurrence arithmetic lives in `app/scheduler/recurrence.py` and is pure and
unit-tested:

| Frequency | Required config |
|---|---|
| `once` | `run_at` (ISO-8601) |
| `interval` | `interval_seconds` (≥ 10) |
| `daily` | `time_of_day` (HH:MM) |
| `weekly` | `time_of_day` + `days_of_week` (0 = Monday) |

Every schedule carries an IANA `timezone` (validated via `zoneinfo`), so
`09:00` in `Asia/Tokyo` really is `00:00 UTC`.

Safety rules enforced in the tick:

- A **disabled** automation never fires; its schedule is disabled and queued
  jobs are cancelled.
- A **deleted** automation leaves no schedule and no pending job.
- `next_run` always advances, so a failing automation cannot spin on the same
  due time.
- A single-instance lock file (`data/.scheduler.lock`) stops a second ticker
  from double-firing schedules under `uvicorn --reload`.

## Background worker

`app/scheduler/worker.py` — drains `background_jobs`.

Job states: `queued` → `running` → `completed` | `failed` | `cancelled`.

**Exactly-once:** `claim_next_job` performs a conditional
`UPDATE ... WHERE id = ? AND status = 'queued'`. Two workers racing for the
same row cannot both win, and a second claim returns nothing.

**Restart safety:** jobs left `running` by a crashed process are requeued at
startup by `recover_stale_jobs` (called from the FastAPI lifespan), with their
retry counter bumped so a poison job cannot loop forever.

**Retry policy:** retryable failures (network / 5xx) back off exponentially —
`base * 2**(retry_count-1)`, capped at one hour. Permanent failures are never
retried:

| Not retried | Why |
|---|---|
| `UnsupportedAction` | no registered implementation exists |
| `ValidationError` | the arguments are wrong |
| `AuthenticationError` | credentials are rejected/expired |
| `NotConfiguredError` | no credentials in the environment |
| `DraftNotApprovedError` / `DraftNotFoundError` | approval cannot appear later |

Every job logs `job_id`, `automation_id`, `execution_id`, trigger, status,
duration and error — never a token or secret.

## Integrations

`IntegrationProvider` (`app/integrations/base.py`) is the common surface:
`is_configured`, `status`, `get_authorize_url`, `exchange_code`,
`disconnect`, `test_connection`, `execute_action`, `poll_events`.

`app/integrations/registry.py` maps an action name to its provider. The engine
asks the registry; it never imports a vendor SDK.

| Provider | Transport | Credentials |
|---|---|---|
| `gmail` | Gmail REST API v1 over OAuth 2.0 | `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` |
| `calendar` | Calendar REST API v3 over OAuth 2.0 | same Google credentials |
| `slack` | Slack Web API (`oauth.v2.access`, `chat.postMessage`, `conversations.list`) | `SLACK_CLIENT_ID/SECRET/REDIRECT_URI` |

Gmail/Slack/Calendar polling, event dedupe and token refresh are implemented
against the documented APIs with `httpx` — no scraping, no passwords, no new
heavy dependency.

### Token security

- Tokens are encrypted with **Fernet** (authenticated AES) before touching
  SQLite. The key comes from `INTEGRATION_TOKEN_KEY`, or a gitignored local
  key file is generated once.
- No API response ever returns an access or refresh token; only metadata
  (`present`, `readable`, `can_refresh`, `expires_at`, scopes).
- Credentials live in the environment only — nothing is hardcoded.

### Honest status

A provider with no credentials reports `state: "not_configured"` and the
message *"Google integration is not configured."* It is never presented as
connected. A test asserts this for all three real providers.

## Demo mode

`DEMO_MODE=true` enables clearly-labelled local stand-ins
(`gmail_demo`, `slack_demo`, `calendar_demo`) so the whole pipeline can be
demonstrated on a machine with no Google or Slack credentials.

- `is_mock: true` and the status text always says
  *"DEMO MODE — simulated Gmail. No real account is connected and nothing
  leaves this machine."*
- When a workflow calls a real action (`gmail_read_email`) and the real
  provider is unconfigured, the handler routes to the demo stand-in and tags
  the result `is_mock: true` with the stand-in's provider name.
- A demo provider is never counted as a real connection.

## Idempotency

`integration_events` has `UNIQUE (dedupe_key)` where the key is
`provider:external_event_id:automation_id`. Re-observing the same event is a
no-op, so:

- a message triggers its automation once, however many times it is polled;
- a worker restart cannot cause a duplicate run;
- the manual "Run demo" button returns **409** if that event was already
  processed.

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/integrations` | every provider with measured status |
| `GET` | `/api/integrations/actions` | the action allowlist |
| `GET` | `/api/integrations/{provider}` | one provider's status |
| `POST` | `/api/integrations/{provider}/connect` | demo connect, or OAuth URL / code exchange |
| `POST` | `/api/integrations/{provider}/disconnect` | revoke + delete |
| `POST` | `/api/integrations/{provider}/test` | cheap authenticated call |
| `GET` | `/api/integrations/{provider}/events` | idempotency ledger |
| `GET` | `/api/scheduler/status` | live scheduler state + queue |
| `POST` | `/api/scheduler/tick` | run one pass now |
| `GET` | `/api/schedules` · `/api/schedules/{automation_id}` | schedules |
| `PUT` | `/api/schedules/{automation_id}` | create/replace (422 on invalid) |
| `GET` | `/api/background-jobs` · `/{id}` | job history |
| `GET` | `/api/worker/status` | live worker state + queue depth |
| `POST` | `/api/worker/drain` | process the queue synchronously (demo) |
| `POST` | `/api/automations/{id}/demo` | synthetic event → queue → worker |

## Security

- Only `approved` drafts execute; the engine refuses anything else, and the
  worker relies on that same guard.
- The action registry is a closed, code-controlled allowlist of 21 actions.
  An unknown action fails the step and persists an error; the AI cannot
  introduce an action.
- No `eval`, `exec`, `subprocess`, `os.system`, `shell=True`, dynamic import,
  browser automation or arbitrary HTTP dispatch from model output. A static
  AST test asserts this for every Phase 8 module.
- Gmail query values are sanitised: quotes and non-printable characters are
  stripped so a crafted criterion cannot inject a new operator.

## Testing

`backend/tests/test_scheduler.py` — 62 tests, fully offline. Covers
recurrence arithmetic and timezones, schedule persistence/advance/retirement,
disabled and deleted automations, atomic job claiming, stale-job recovery,
retry vs non-retry classification, credential encryption and metadata safety,
real-provider not-configured paths, demo provider honesty, event idempotency
and the 409 replay guard, analytics/system payloads, and the static security
scans.

**Real-credential status:** no Google or Slack credentials were available in
this environment, so the real providers were verified through their
configuration and not-configured paths; provider *behaviour* was verified with
the mock adapters. No successful real-integration test is claimed.

## Limitations

- No scheduler process runs a workflow **inline** in an HTTP request; long
  runs are always queued.
- `once` schedules retire after firing; there is no edit-in-place UI beyond
  re-creating the schedule.
- Slack polling is not implemented (Slack events are demo-only or manual);
  Slack is supported as an action, not a trigger.
- No cancellation of a job already `running`.
- Retry backoff is in-process; a restart resets `next_attempt_at` to now.
- Integration events are retained indefinitely; no retention policy.
