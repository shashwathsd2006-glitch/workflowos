# Automations, Analytics & Settings

Phase 7 fills the last three navigation surfaces. All three read **real
persisted data** — no placeholder panels, no invented statistics, no fake
toggles.

---

## Automations

An automation binds an **approved** workflow draft to a trigger. It owns no
execution logic: *Run now* calls `app.automation.engine.execute_draft`, the
same engine the Workflows page uses, so there is exactly one execution path
and one audit trail.

```
approved WorkflowDraft
        ↓
automations row (name, trigger_type, trigger_config, enabled)
        ↓  Run now
app.automation.engine.execute_draft  →  workflow_executions (new record)
        ↓
execution history + last execution shown on the automation
```

### API

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/api/automations` | 200 | optional `?enabled=` |
| `POST` | `/api/automations` | 201 | 404 unknown draft · 409 draft not approved · 422 invalid body |
| `GET` | `/api/automations/{id}` | 200 | 404 |
| `PATCH` | `/api/automations/{id}` | 200 | 404 |
| `POST` | `/api/automations/{id}/enable` | 200 | 404 |
| `POST` | `/api/automations/{id}/disable` | 200 | 404 |
| `POST` | `/api/automations/{id}/run` | 200 | 404 · 409 disabled |
| `DELETE` | `/api/automations/{id}` | 200 | 404 |

Creation is refused unless the draft exists and is `approved`, so an
automation can never be bound to an unapproved workflow.

### Trigger types

`manual`, `schedule` and `event` are recorded and displayed. **No scheduler
runs in this phase** — a `schedule` trigger stores its expression (e.g.
`0 9 * * 1-5`) for reference and does not fire on a timer. The UI states this
inline rather than implying a scheduler exists.

### Schema

`automations`: `id`, `name`, `description`, `draft_id` (FK →
`workflow_drafts`), `trigger_type`, `trigger_config`, `enabled`,
`created_at`, `updated_at`. Indexed on `draft_id` and `enabled`. A draft
cannot be deleted out from under an automation (FK constraint).

Execution history is **never copied** into this table; `execution_count` and
`last_execution` are read live from `workflow_executions`, so an automation
view cannot drift from the real records.

---

## Analytics

`GET /api/analytics?days=14` aggregates stored rows only:

- `summary` — total workflows, automations, executions, successful, failed,
  success rate
- `activity` — one bucket per day across the window (empty days included)
- `workflow_performance` — per workflow: runs, successful, failed, rate.
  Executions roll up to their **workflow candidate** via
  `draft.workflow_candidate_id`, so a workflow appears exactly once
- `recent_failures` — failed executions with the exact failed step and error
- `recent_executions` — the latest 10 runs

An empty database returns zeros, which the UI renders as an empty state. A
test asserts every summary field is a non-negative int/float derived from
rows, so a fabricated "1,248 executions" cannot pass.

The `days` window is clamped to 1–90.

---

## Settings

`GET /api/system/status` is read-only diagnostics:

| Section | Content |
|---|---|
| General | app name, API version, environment, debug flag, similarity threshold |
| AI Provider | provider, Ollama endpoint, model, timeout, **live** reachability + Test Connection |
| Backend | base URL, bind host:port, health/readiness endpoints, health |
| Database | engine, file, path, exists, status |
| Safety | approval-required, connected integrations, allowed action list |
| System | frontend / backend / database / Ollama status indicators |

The Ollama check performs a real `GET {OLLAMA_BASE_URL}/api/tags` with a 3s
timeout and reports whether the configured model is present.

**No secrets are exposed.** A test asserts no secret-looking key or value
(`password`, `api_key`, `secret`, `token`, `credential`) appears anywhere in
the payload.

Anything that does not actually control behaviour is shown as information
(e.g. similarity threshold is documented as env-driven, not presented as an
editable control).

---

## Testing

`backend/tests/test_automations.py` — 40 tests, fully offline. Covers
automation CRUD, the approved-draft gate, enable/disable, run-now delegation
and the disabled guard, history preservation on delete, FK integrity,
analytics counting/zeros/failure extraction/workflow rollup/window clamping,
"never invents numbers", and system status including the no-secrets guard and
Ollama probe both reachable and unreachable.

```bash
cd backend && .venv/bin/python -m pytest
```
