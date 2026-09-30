# Workflow Discovery (Phase 4)

Deterministic repetition detection over stored activity events. No LLM, no
model calls, no background jobs — discovery runs only when it is explicitly
asked to, and every run is reproducible.

## Architecture

```
Activity Events              (activity_events — Phase 3)
      ↓  build_sequences()    grouped per session_id, ordered by timestamp
Sequence Builder             (app/discovery/sequence_builder.py)
      ↓  normalize_sequence() lowercase (application, category, action)
Normalizer                   (app/discovery/normalizer.py)
      ↓  sequence_similarity() pairwise comparison
Similarity                   (app/discovery/similarity.py)
      ↓  detect_patterns()    clustering + confidence + naming
Pattern Detector             (app/discovery/pattern_detector.py)
      ↓  WorkflowCandidate
SQLite                       (workflow_candidates table)
      ↓  REST API             (/api/workflows/…)
Frontend                     (Workflows page, Overview statistics)
```

The pipeline is pure functions over data: `sequence_builder` and `normalizer`
never touch the database, `similarity` never knows where sequences came from,
and `pattern_detector` only sees normalized steps. Only
`discovery_service.py` reads/writes SQLite.

## Sequence grouping

Sequences are grouped by `session_id` and ordered by timestamp. Each
simulated repetition produces its own session, so three repetitions of
`customer_request` become three 6-step sequences — the minimum input for a
candidate.

Normalization drops everything that changes between runs (event ids,
timestamps, descriptions, metadata) and keeps only the workflow-relevant part:

| Kept (normalized)              | Dropped                              |
|--------------------------------|--------------------------------------|
| `application` (lowercased)     | `id`, `timestamp`                    |
| `category` (lowercased)        | `description`, `metadata`            |
| `action` (lowercased, trimmed) | `session_id` (tracked separately)    |

## Similarity

```
sequence_similarity(a, b) = 0.7 * edit_similarity(a, b)
                          + 0.3 * jaccard(a, b)
```

- `edit_similarity` — Levenshtein distance over normalized steps,
  `1 - distance / max(len)`.
- `jaccard` — size of the shared step set over the union of both step sets.
- Empty sequence on either side → `0.0`.
- Identical sequences → `1.0`; unrelated sequences → `0.0`.

Clustering is greedy exemplar-based: each sequence joins the first cluster
whose exemplar is at least `SIMILARITY_THRESHOLD` similar, otherwise it opens
a new cluster. The threshold comes from the environment
(`SIMILARITY_THRESHOLD`, default `0.75`) and can be overridden per test.

## Pattern detection

A cluster only becomes a candidate at `MIN_OCCURRENCES = 2` members.

```
confidence = 0.50 * similarity_score
           + 0.30 * repetition_score
           + 0.20 * sequence_consistency

repetition_score     = min(1, (occurrence_count - 1) / 4)
sequence_consistency = mean positional agreement vs the earliest sequence
```

Labels: `< 0.60 → low`, `< 0.85 → medium`, else `high`.

Three identical runs score `similarity = 1.0`, `repetition = 0.5`,
`consistency = 1.0` → **confidence 0.85 ("high")**.

Naming is deterministic — a lookup on known application signatures, otherwise
a generated chain:

| Applications           | Name                           |
|------------------------|--------------------------------|
| Gmail + CRM + Slack    | Customer Request Processing    |
| Gmail + Finder + Excel | Document Processing            |
| Calendar + Gmail       | Meeting Follow-up              |
| anything else          | `Repeated Workflow — A → B`    |

Candidates are sorted by `occurrence_count` (desc), then `first_seen`, and
stored with ids `workflow-001`, `workflow-002`, …

## API

| Method | Path                            | Purpose                                  |
|--------|---------------------------------|------------------------------------------|
| GET    | `/api/workflows/discovered`     | stored candidates → `{workflows, count}` |
| POST   | `/api/workflows/discover`       | run discovery → `{workflows, count, sequences_analyzed, threshold}` |

```bash
curl http://127.0.0.1:8000/api/workflows/discovered
curl -X POST http://127.0.0.1:8000/api/workflows/discover
```

Each run deletes previously **detected** candidates and re-inserts the fresh
result, so repeated runs on unchanged data are identical. Rows that have been
reviewed/approved/rejected (Phase 5+) are never overwritten.

### Candidate shape

```json
{
  "id": "workflow-001",
  "name": "Customer Request Processing",
  "sequence": [
    { "application": "Gmail", "category": "communication",
      "action": "open_email", "description": "Opened customer request email" }
  ],
  "occurrence_count": 3,
  "session_ids": ["session-001", "session-002", "session-003"],
  "similarity_score": 1.0,
  "confidence": 0.85,
  "confidence_label": "high",
  "applications": ["Gmail", "CRM", "Slack"],
  "first_seen": "2026-09-26T10:00:00+00:00",
  "last_seen": "2026-09-26T10:16:00+00:00",
  "status": "detected"
}
```

## Frontend

- **Workflows page** — four statistics (workflows detected, total repeated
  sequences, most frequent workflow, average confidence), workflow cards with
  the application chain, occurrence count, similarity/confidence bars, status
  badge and an expandable details panel showing sessions, first/last seen and
  the vertical `↓` sequence (application → action → action → next
  application), plus the *Discover Workflows* button with loading, success and
  error states.
- **Overview page** — a *Workflows* section with the detected count and
  empty state text *"No repeated workflows detected yet."*
- Discovery is manual only: nothing on the UI polls `/discover`.

## Configuration

`backend/.env`: `SIMILARITY_THRESHOLD=0.75` (0.0–1.0). Clustering also
depends on `MIN_OCCURRENCES = 2`, defined in `pattern_detector.py`.

## Tests

```bash
cd backend && .venv/bin/python -m pytest
```

Phase 4 covers sequence building (ordering, session grouping, empty/no-session
inputs), normalization (case trimming, dropped fields), similarity (identical
1.0, unrelated 0.0, empty 0.0, one-step difference stays above threshold,
partial sequence falls below it), confidence math and labels, naming
(fallback and known signatures), clustering (same/different workflows,
metadata variance ignored, threshold configurability), the two API endpoints
(including deterministic repeated runs), discovery on empty activity, and
frontend data-flow types.
