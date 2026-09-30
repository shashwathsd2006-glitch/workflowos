# Workflow Generation + Human Approval

WorkFlowOS **generates workflow proposals only**. This phase produces a
structured draft and records a human decision. It never executes a workflow
and never touches Gmail, Slack, Calendar, CRM, a browser or a shell.

---

## Pipeline

```
WorkflowCandidate
        ↓
WorkflowUnderstanding          (Phase 5 — must already exist)
        ↓
WorkflowGenerationInput        app/generator/service.py::build_generation_input
        ↓
Generator prompt               app/generator/prompts.py
        ↓
AIProvider                     app/ai/provider.py
   ├── OllamaProvider          local qwen2.5-coder:7b
   └── MockProvider            deterministic, offline
        ↓
Raw text response
        ↓
JSON extraction                app/ai/parsing.py::extract_json_object
        ↓
Normalization                  app/generator/parser.py::normalize_draft
        ↓
Pydantic validation            app/schemas/generator.py::WorkflowDraft
        ↓
SQLite persistence             app/models/generator.py
        ↓
API response                   app/api/routes_drafts.py
        ↓
Frontend review                frontend/src/components/workflows/draft-panel.tsx
        ↓
Approve / Reject               human decision, recorded
```

Generation requires an existing understanding: the generator plans from the
*interpreted* workflow, so the user reviews meaning before structure. Calling
`generate` on a candidate that was never understood returns **409**.

## AIProvider

The single `AIProvider` abstraction from Phase 5 is reused — there is no
second AI implementation.

| Method | Purpose |
|---|---|
| `generate_workflow_understanding(payload)` | Phase 5 — explain the observed workflow |
| `generate_workflow_draft(payload)` | Phase 5b — propose a structured workflow plan |

`OllamaProvider` shares one `_chat()` helper for both, so timeouts, HTTP
errors, unreachable hosts and empty responses all become a single
`AIProviderError` → HTTP 502. Connection details come from configuration
(`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`); nothing is hardcoded
and no cloud provider is used.

`MockProvider` derives its draft from the evidence payload with simple rules
— no workflow is hardcoded — and returns the same strict JSON a real model
must produce, so parsing, validation and persistence are exercised
identically offline.

## JSON validation

`extract_json_object` handles bare JSON, ```` ```json ```` fences and JSON
wrapped in prose. `normalize_draft` then repairs presentation problems real
models produce — a `trigger` sentence instead of an object, non-1-based step
numbers, non-dict `parameters` — and drops unknown keys.

Anything that is still wrong (no steps, no name, out-of-range confidence,
wrong types) raises `AIResponseError` → HTTP 502 and **is never persisted**.
Failed generation leaves the drafts table untouched.

## Database

`workflow_drafts`, created idempotently in `init_db()`:

| Column | Notes |
|---|---|
| `id` | `draft-0001`, … |
| `workflow_candidate_id` | **UNIQUE** — one draft per candidate |
| `understanding_id` | source understanding |
| `name`, `description` | draft identity |
| `trigger_json` | `{type, application, action}` |
| `steps_json` | ordered `[{step_number, application, action, purpose, input, output, parameters}]` |
| `inputs_json`, `outputs_json`, `applications_json`, `conditions_json`, `dependencies_json`, `assumptions_json` | plan metadata |
| `confidence` | 0.0–1.0 |
| `status` | lifecycle state |
| `rejection_reason` | optional |
| `generated_by`, `model` | provider provenance |
| `created_at`, `updated_at`, `approved_at` | timestamps |

Indexed on `status` and `workflow_candidate_id`. Re-generating refreshes the
existing row in place rather than creating a duplicate, and preserves a
human decision that has already been made.

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/api/workflows/drafts` | 200 | — |
| `GET` | `/api/workflows/drafts/{draft_id}` | 200 | 404 unknown draft |
| `POST` | `/api/workflows/drafts/{workflow_candidate_id}/generate` | 200 | 404 unknown candidate · 409 no understanding · 502 provider/response |
| `POST` | `/api/workflows/drafts/{draft_id}/approve` | 200 | 404 · 409 invalid transition |
| `POST` | `/api/workflows/drafts/{draft_id}/reject` | 200 | 404 · 409 invalid transition |
| `DELETE` | `/api/workflows/drafts/{draft_id}` | 200 | 404 |

`GET` accepts an optional `?status=` filter. `reject` accepts an optional
`{"reason": "..."}` body. Errors use the project convention
(`{"error": {"type", "detail", "path"}}`); stack traces are never returned.

## Approval state machine

Generation produces **`pending_approval`**. Approval is always explicit.

```
                 generate
                     ↓
             pending_approval ──approve──→ approved  (terminal)
                     │
                     └───reject────→ rejected  (terminal)

draft ──→ pending_approval
```

`approved` and `rejected` are terminal. Every other move — re-approving,
rejecting an approved draft, approving a rejected one — returns **409**.
`approved`/`rejected` drafts are also protected from deletion so decisions are
kept for the record.

There is **no automatic execution after approval**. Approval writes a status.

## Testing

`backend/tests/test_generator.py` — 45 tests, fully offline (no Ollama, no
internet, no API keys). Covers schema validation, malformed/missing/wrong-typed
JSON, fences and prose, normalization, prompt contents, MockProvider
determinism, Ollama request-body contract and failure paths, generation
success and 404/409/502 mapping, persistence, list/get, the full state
machine, delete, and two explicit guards that approval never calls the AI
provider and that steps carry no execution hooks.

```bash
cd backend && .venv/bin/python -m pytest
```

## Demo

```bash
# 1. Ollama (must already be running)
ollama serve

# 2. Backend — real local model
cd backend
.venv/bin/python -m uvicorn app.main:app --reload

# 3. Frontend
cd frontend && npm run dev
```

Then open <http://localhost:3000/workflows>:

1. **Activity** → *Simulate* a repeated sequence (e.g. customer requests).
2. **Workflows** → *Discover Workflows*.
3. Expand a candidate → *Understand with AI* (Ollama, ~30s).
4. *Generate Workflow* (~30s) → review trigger, steps, inputs/outputs,
   assumptions, confidence, provider and model.
5. *Approve Workflow* → badge becomes **Approved**; or *Reject Workflow* →
   **Rejected**.
6. Reload — the status persists.

Browser URL: <http://localhost:3000/workflows>

## Limitations

- Generation depends on a local model; a 7B model needs ~30s per call and may
  return a draft needing regeneration.
- Draft **editing** is not implemented; a draft can only be regenerated,
  approved, rejected or deleted.
- One draft per candidate by design — no draft history or versioning.
- No execution. Phase 6 will consume approved drafts; nothing here runs.
