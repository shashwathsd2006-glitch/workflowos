# AI Understanding (Phase 5)

The understanding layer: it converts a **detected workflow candidate**
(Phase 4) into structured, validated workflow intent using a **local** LLM
through a provider abstraction.

> **Phase 5 understands workflows. It does NOT execute them.** No emails are
> sent, no records are changed, no approval or automation logic exists here —
> those belong to later phases. AI output is stored and displayed as *data*.

## Architecture

```
Workflow Candidate            (workflow_candidates — Phase 4)
      ↓  build_input()         compact evidence: scores, apps, ordered steps
AI Service                    (app/ai/service.py)
      ↓  get_provider()        chosen by AI_PROVIDER: ollama | mock
AIProvider                    (app/ai/provider.py — interface)
      ├── OllamaProvider       (app/ai/ollama_provider.py, local Ollama HTTP API)
      └── MockProvider         (app/ai/mock_provider.py, deterministic, tests only)
      ↓  raw model text
JSON extraction + validation  (app/ai/parsing.py → WorkflowUnderstanding)
      ↓
SQLite                        (workflow_understandings table)
      ↓  REST API              (/api/ai/…)
Frontend                     (AI Understanding section on the Workflows page)
```

Routes call the service; the service calls a provider; the provider only
talks to the model. Parsing/validation/persistence happen **once** in the
service path, so no provider can bypass them.

## Provider abstraction

```python
class AIProvider(ABC):
    name: str
    model: str
    async def generate_workflow_understanding(payload) -> str: ...
```

- **`OllamaProvider`** — POST `{OLLAMA_BASE_URL}/api/chat` with
  `stream: false`, `format: "json"`, `temperature: 0`. Connection failures,
  timeouts, non-2xx answers and empty responses raise `AIProviderError`.
- **`MockProvider`** — derives a response *from the evidence payload* with
  simple rules (no workflow is hardcoded) and returns the same strict JSON
  schema. Used only when `AI_PROVIDER=mock`, which the test suite forces.
- `get_provider()` in `app/ai/provider.py` is the single factory.

## Configuration

`backend/.env.example`:

```bash
AI_PROVIDER=ollama            # ollama | mock
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_TIMEOUT=60
```

Read through the existing `app/config.py` settings object — no second
configuration mechanism. This project never calls OpenAI, Gemini, Claude or
any other cloud API.

## AI input (evidence)

Built by `service.build_input()` — compact and normalized, no database
internals:

```json
{
  "workflow_id": "workflow-001",
  "workflow_name": "Customer Request Processing",
  "occurrence_count": 3,
  "similarity_score": 1.0,
  "confidence": 0.85,
  "session_count": 3,
  "applications": ["Gmail", "CRM", "Slack"],
  "categories": ["communication", "crm"],
  "steps": [
    { "order": 1, "application": "Gmail", "category": "communication",
      "action": "open_email", "description": "Opened customer request email" }
  ]
}
```

Excluded: event ids, timestamps, session id lists, metadata payloads.

## AI output schema (`WorkflowUnderstanding`)

| Field | Type | Notes |
|-------|------|-------|
| `workflow_id` | string | enforced back to the candidate id |
| `workflow_name` | string | |
| `intent` | string | what the workflow achieves |
| `description` | string | short prose summary |
| `trigger` | string | what starts it |
| `steps[]` | objects | `order`, `application`, `category`, `action`, `purpose`, `input?`, `output?` |
| `applications`, `categories` | string[] | |
| `inputs`, `outputs` | string[] | required inputs / expected results |
| `dependencies`, `assumptions` | string[] | assumptions capture inferences |
| `confidence` | 0.0–1.0 | model's confidence in the interpretation |
| `suggested_automation` | string | description only — nothing is automated |

Strictness rules: required fields missing → validation error; wrong types or
out-of-range confidence → validation error; unknown extra fields are
ignored; `steps` must be non-empty.

## Response handling and errors

```
raw response → extract JSON (plain, ```json fences, surrounding prose)
            → Pydantic validation
            → WorkflowUnderstanding  (or AIResponseError)
```

Failures never crash FastAPI and are never persisted:

| Condition | HTTP | Detail |
|-----------|------|--------|
| Workflow candidate missing | 404 | `Workflow candidate '…' not found` |
| Ollama down / timeout / empty | 502 | `AI understanding unavailable: …` |
| Unparseable or schema-invalid output | 502 | `AI returned an invalid response: …` |
| Anything unexpected | 500 | existing global handler |

All use the project's standard envelope:
`{"error": {"type", "detail", "path"}}`. The UI renders the `detail` as a
clean "AI understanding unavailable" style message instead of breaking.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ai/understandings` | list all understandings |
| GET | `/api/ai/understandings/{id}` | one understanding (404 if unknown) |
| POST | `/api/ai/understand/{workflow_candidate_id}` | generate + persist |

```bash
curl http://127.0.0.1:8000/api/ai/understandings
curl -X POST http://127.0.0.1:8000/api/ai/understand/workflow-001
```

`POST` reads the candidate, calls the provider, validates, then **upserts**
— one row per workflow candidate, `created_at` kept on re-runs.

## Persistence

Table `workflow_understandings` (existing SQLite file, created idempotently
in `init_db()` — no migration needed, demo data untouched):

| Column | Type |
|--------|------|
| `id` | TEXT PK (`understanding-NNN`) |
| `workflow_candidate_id` | TEXT UNIQUE |
| `workflow_name`, `intent`, `description`, `trigger` | TEXT |
| `steps_json`, `applications_json`, `categories_json`, `inputs_json`, `outputs_json`, `dependencies_json`, `assumptions_json` | TEXT (JSON) |
| `confidence` | REAL |
| `suggested_automation` | TEXT |
| `provider`, `model` | TEXT |
| `created_at`, `updated_at` | TEXT (UTC ISO-8601) |

## Frontend

The Workflows page details panel gained an **AI Understanding** section:

- *Understand with AI* button → *Understanding…* loading state → structured
  result (name, intent, description, trigger, ordered steps with purpose,
  applications, categories, inputs, outputs, dependencies, assumptions,
  confidence bar, suggested automation).
- Previously generated understandings load with the page (from
  `GET /api/ai/understandings`).
- Error states (Ollama down, invalid response, network) render inline with
  *Try again*; buttons are never left permanently disabled.
- *Re-run* regenerates; *View JSON* toggles the raw JSON for developers.
- All calls go through `lib/api.ts` (`understandWorkflow`,
  `getWorkflowUnderstandings`, `getWorkflowUnderstanding`).

## Testing strategy

`backend/tests/test_ai.py` — 35 tests, **offline**: `conftest.py` forces
`AI_PROVIDER=mock`, so pytest needs no Ollama, model, internet or keys.

Covered: provider interface, MockProvider determinism (and that it is not
hardcoded to the three demo workflows), schema validation, valid JSON,
invalid JSON, fenced/prose-wrapped JSON, missing fields, wrong types,
candidate-not-found 404, successful generation, persistence, list/get,
multiple candidates, upsert-per-candidate, Ollama configuration and request
contract, connection-refused → `AIProviderError` → HTTP 502, invalid
response → 502, and that failed generations persist nothing. Phases 3 and 4
tests run unchanged in the same suite.

## Demo procedure

```bash
# backend (AI_PROVIDER defaults to ollama)
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend
cd frontend && npm run dev
```

1. Ollama running with `qwen2.5-coder:7b` (`ollama list`).
2. Activity → *Simulate 3 Repetitions* (if you need candidates).
3. Workflows → *Discover Workflows* → expand a workflow → *Understand with
   AI*.
4. Read the structured result; optionally *View JSON*.
5. Stop Ollama and click *Re-run* to see the clean *AI understanding
   unavailable* error state.
