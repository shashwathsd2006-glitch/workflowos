# Phase 6 — Workflow Execution

WorkFlowOS executes an **approved** workflow draft through a controlled
action registry.

> **This phase does not connect to any real external service.** No Gmail,
> Slack, Calendar, CRM, browser automation, shell command, HTTP request or
> dynamic import is reachable. Execution proves the architecture using
> deterministic local mock actions.

---

## Safety rule

Only a draft with `status = "approved"` may execute. `draft`,
`pending_approval` and `rejected` are all refused with **409** *before a
single execution or step row is created*, so a non-approved workflow can
never run a step.

```
Workflow Candidate → AI Understanding → Workflow Draft → Human Review
                                                            ↓
                                                       APPROVED
                                                            ↓
                                                Execution Request
                                                            ↓
                                                  Execution Engine
                                                            ↓
                                              Individual Steps (in order)
                                                            ↓
                                          Execution Events / Results
                                                            ↓
                                                  Execution Summary
```

## Execution states

`queued` → `running` → `completed` | `failed`, plus `cancelled` reserved for
a future manual stop. Every run is a new record; history is never rewritten.

## Step states

`pending` → `running` → `completed` | `failed`, plus `skipped`.

Steps run in the **exact generated order** and are never reordered or
retried. On failure the run stops, the failing step is recorded, and later
steps stay `pending` so the trace shows precisely where it stopped.

## Action registry — the security boundary

`app/automation/actions.py` holds the only callables the engine can invoke:

| Action | Behaviour |
|---|---|
| `simulate` | Pretends to perform an external app action. Changes nothing. |
| `log` | Records a step result with no side effects. |
| `transform` | Passes structured data to the next step. |
| `notify_mock` | Queues a mock notification; nothing is delivered. |
| `delay_mock` | Records a mock wait without blocking. |

The engine resolves a generated step to a registry name via a fixed,
inspectable rule set: exact registry name or alias, otherwise the first
matching keyword rule. **Model output never selects a Python callable.**

Anything unresolved fails the step:

```
Step 3 — Compose email
Unsupported action 'Compose email'. No registered implementation —
allowed actions: delay_mock, log, notify_mock, simulate, transform
```

This is deliberate: a visible failure is safer than guessing what the model
"meant" and running something the user never approved.

## Database

`workflow_executions` — `id`, `draft_id` (FK), `workflow_name`, `status`,
`current_step`, `total_steps`, `completed_steps`, `failed_step`, `error`,
`result_summary`, `started_at`, `completed_at`, `duration_ms`, `created_at`,
`updated_at`. Indexed on `draft_id` and `status`.

`workflow_execution_steps` — `id`, `execution_id` (FK), `step_number`,
`application`, `action`, `purpose`, `status`, `action_type`, `input`,
`output`, `error`, `started_at`, `completed_at`, `duration_ms`.
**UNIQUE `(execution_id, step_number)`** makes a double-executed step
impossible at the storage layer, not just in the engine.

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| `POST` | `/api/workflows/drafts/{draft_id}/execute` | 200 | 404 unknown draft · 409 not approved |
| `GET` | `/api/workflows/executions` | 200 | optional `?draft_id=` / `?status=` |
| `GET` | `/api/workflows/executions/{execution_id}` | 200 | 404 |
| `GET` | `/api/workflows/executions/{execution_id}/steps` | 200 | 404 |

Unexpected engine failures return 500 with a generic message; stack traces
are never exposed.

## Activity audit

Execution writes to the **existing** activity timeline rather than a parallel
log: `execution_created`, `execution_started`, `step_started`,
`step_completed`, `step_failed`, `execution_completed`, `execution_failed`.
Each event carries `metadata.execution_id` and shares the session
`execution-<id>`, so a run reads as a coherent story on the Activity page.

## Testing

`backend/tests/test_execution.py` — 38 tests, fully offline. Covers the
approval gate for all four states, persistence, step ordering, failure paths,
unsupported and malicious action refusal, rerun/history safety, restart
survival, activity auditing, and static scans asserting no `subprocess`,
`eval`, `exec`, `importlib` or network client exists in Phase 6 code.

```bash
cd backend && .venv/bin/python -m pytest
```

## Demo

```bash
ollama serve                                    # only for generate/understand
cd backend
AI_PROVIDER=ollama .venv/bin/python -m uvicorn app.main:app --reload
cd frontend && npm run dev
```

Open <http://localhost:3000/workflows>:

1. *Discover Workflows*
2. Expand a candidate → *Understand with AI*
3. *Generate Workflow* → *Approve Workflow*
4. *Execute Workflow* → watch status, progress and per-step results
5. Open an earlier run from **History**
6. Check the Activity page for the execution timeline

Execution itself needs no Ollama — it consumes the persisted draft and
completes even with Ollama stopped.

## Security limitations

- Mock actions only: no real integration exists yet.
- The action keyword table is intentionally small, so unfamiliar phrasing
  fails rather than being coerced.
- No cancellation endpoint; `cancelled` is reserved.
- No retry. A failed run stays failed; rerunning creates a new execution.
