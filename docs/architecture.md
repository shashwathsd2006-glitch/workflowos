# WorkFlowOS Architecture

## Pipeline

```
[Simulated Activity Source]
        │
        ▼
1. Desktop Activity Agent   → ActivityEvent (structured)
        ▼
2. Workflow Discovery Engine → repeated SequenceCandidate + confidence
        ▼
3. AI Workflow Understanding → intent + step mapping (LLM, mock first)
        ▼
4. Workflow Generator        → WorkflowSpec JSON
        ▼
5. User Approval             → draft → pending_approval → approved | rejected
        ▼
6. Automation Engine         → ExecutionRun records (mock integrations)
        ▼
7. Learning / Analytics      → reliability stats back into discovery
```

Nothing executes before step 5 returns an explicit approval.

## Backend layering

```
api/        routers only: validation + orchestration, no business logic
services/   business logic per module
models/     persistence (tables)
schemas/    pydantic request/response contracts
agents|discovery|ai|workflows|automation/   one folder per core module
database/   connection + init
config.py   environment settings
main.py     app factory, CORS, error handlers, lifespan
```

Dependency direction: `api → services → (models, schemas, module folders, database)`.
Module folders depend only on `database`/`schemas`, never on `api`.

## Module ↔ folder map

| # | Module                | Folder                    | First phase | Status |
|---|-----------------------|---------------------------|-------------|--------|
| 1 | Desktop Activity Agent| `app/agents/`             | 3           | done   |
| 2 | Workflow Discovery    | `app/discovery/`          | 4           | done   |
| 3 | AI Understanding      | `app/ai/`                 | 5           | done   |
| 4 | Workflow Generator    | `app/workflows/`          | 5b          | pending|
| 5 | User Approval         | `app/workflows/` (state)  | 5b          | pending|
| 6 | Automation Engine     | `app/automation/`         | 6           | pending|
| 7 | Learning/Analytics    | `app/services/analytics`  | 7           | pending|

External effects (LLM, integrations, activity source) sit behind small interfaces so
mock and real implementations swap without touching services.

### Activity flow (module 1)

```
Activity Source → ActivityEventCreate → ActivityService → activity_events (SQLite)
                                                          → /api/activity… → UI
```

`SimulatedActivitySource` implements the `ActivitySource` protocol; a future
`MacOSActivitySource` / `BrowserActivitySource` drops in without changes to the
service, API or UI. See `docs/activity.md` for the schema and endpoints.

### Discovery flow (module 2)

```
activity_events → build_sequences → normalize → similarity → detect_patterns
                → WorkflowCandidate → workflow_candidates (SQLite) → /api/workflows/…
```

Deterministic only: no LLM, no background jobs; discovery runs when
`POST /api/workflows/discover` is called and replaces previously *detected*
candidates. See `docs/discovery.md` for the similarity/confidence formulas.

### AI understanding flow (module 3)

```
WorkflowCandidate → build_input → AIProvider (ollama | mock) → raw text
                  → JSON extraction → WorkflowUnderstanding → workflow_understandings
                  → /api/ai/… → UI
```

Understanding only: no execution, no approval, no integrations. See
`docs/ai-understanding.md`.

### Workflow generation + approval flow (module 4)

```
WorkflowCandidate + WorkflowUnderstanding → build_generation_input
                  → AIProvider (ollama | mock) → raw text
                  → JSON extraction → normalize_draft → WorkflowDraft
                  → workflow_drafts → /api/workflows/drafts/… → review UI
                  → explicit approve / reject
```

Generation proposes only. `approved`/`rejected` are terminal states recorded
by a human. See `docs/workflow-generation.md`.

### Workflow execution flow (module 6)

```
approved WorkflowDraft → validate status → create execution + steps
        → per step: resolve_action() → ACTION_REGISTRY handler → record result
        → completed | failed  → activity_events audit
        → /api/workflows/executions/… → execution review UI
```

Only `approved` drafts run; every other state is refused with 409 before a
step exists. Steps keep generated order and are never retried. Model output
cannot select a callable — the engine resolves it through
`app.automation.actions.ACTION_REGISTRY`, which contains only local mock
handlers. No shell, HTTP, subprocess or dynamic import path exists. See
`docs/workflow-execution.md`.

### Automations, analytics and system status (module 7)

```
approved WorkflowDraft → automations (trigger_type, trigger_config, enabled)
        → run → delegates to app.automation.engine.execute_draft  (one execution path)
        → workflow_executions (history) → /api/automations/… → Automations page

workflow_candidates + automations + workflow_executions
        → GET /api/analytics → Analytics page (counts real rows only)

config + database_ready() + Ollama /api/tags probe
        → GET /api/system/status → Settings page (read-only, no secrets)
```

Automations own no execution logic: running one delegates to the existing
engine, so the system keeps a single execution path and audit trail.
`trigger_type` is recorded and displayed, but no scheduler runs — a
`schedule` trigger does not fire on a timer. Analytics counts stored rows and
returns zeros for an empty database rather than inventing activity.

### Scheduling + background execution (module 5, Phase 8)

```
approved draft → automation (trigger_type, trigger_config, enabled)
   → automation_schedules (frequency, timezone, next_run)
   → Scheduler tick → integration_events (UNIQUE dedupe) → background_jobs
   → Worker (atomic queued→running, exponential backoff, restart recovery)
   → app.automation.engine.execute_draft   (SAME engine as manual run)
   → ACTION_REGISTRY → IntegrationProvider → steps → audit → analytics
```

The engine is shared with manual execution, so there is one execution path.
OAuth tokens are Fernet-encrypted at rest and never leave the API in
plaintext. Unconfigured providers report `not_configured`; demo stand-ins are
always labelled `is_mock`. See `docs/scheduling-integrations.md`.

## Contracts (frozen early, JSON)

- `ActivityEvent` — `{id, ts, app, window, action_type, target, metadata, session_id}`
- `SequenceCandidate` — `{event_ids[], count, first_seen, last_seen, confidence}`
- `WorkflowSpec` — `{trigger, steps[], conditions[], integrations[], intent, name}`
- `Workflow` — `{spec, status, version}`
- `ExecutionRun` — `{workflow_id, step_results[], status, started_at, finished_at}`

## API convention

- Health at root: `GET /health`, `GET /health/ready`
- Feature routes under `/api` (mounted from `app/api/router.py`)
  - `/api/activity`, `/api/activity/simulate`, `/api/activity/stats`
  - `/api/workflows/discovered`, `/api/workflows/discover`
  - `/api/ai/understandings`, `/api/ai/understandings/{id}`, `/api/ai/understand/{id}`
  - `/api/workflows/drafts`, `/api/workflows/drafts/{id}`,
    `/api/workflows/drafts/{candidate_id}/generate`,
    `/api/workflows/drafts/{id}/approve`, `/api/workflows/drafts/{id}/reject`
- Errors always JSON: `{"error": {"type", "detail", "path"}}`
- CORS restricted to the configured frontend origins (default localhost:3000)

## Frontend

- App Router; one route per navigation item
- `src/lib/api.ts` is the single place that knows the backend base URL
- `src/types/api.ts` mirrors backend response types
- Layout shell (`components/layout/`) is client-side only for nav state;
  page content stays server-rendered by default
