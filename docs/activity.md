# Activity Module (Phase 3)

The first functional WorkFlowOS module: structured activity events produced by a
**simulated** activity source, stored in SQLite, exposed over REST and shown in
the dashboard.

## Architecture

```
Activity Source            (SimulatedActivitySource — swap for MacOS/Browser later)
      ↓  ActivityEventCreate
Activity Service           (app/services/activity_service.py)
      ↓
SQLite                     (activity_events table)
      ↓
REST API                   (/api/activity…)
      ↓
Frontend                   (Activity page, Overview statistics)
```

The source never touches storage and the service never knows how events were
produced, so replacing simulation with real capture only means writing a new
class that satisfies the `ActivitySource` protocol
(`app/agents/activity_source.py`).

**Privacy:** only high-level actions are recorded — open, read, download,
search, update, send. No keystrokes, clipboard, passwords, message bodies or
other sensitive content.

## ActivityEvent schema

```json
{
  "id": "event-0001",
  "timestamp": "2026-09-26T10:00:00+00:00",
  "application": "Gmail",
  "category": "communication",
  "action": "open_email",
  "description": "Opened customer request email",
  "metadata": { "subject": "Customer Request - ABC Industries" },
  "session_id": "session-001"
}
```

| Field         | Type            | Notes                                            |
|---------------|-----------------|--------------------------------------------------|
| `id`          | string          | `event-NNNN`, generated when omitted             |
| `timestamp`   | ISO-8601 UTC    | generated when omitted                           |
| `application` | string          | Gmail, CRM, Slack, Finder, Calendar, Excel, …     |
| `category`    | enum            | `application`, `browser`, `file`, `communication`, `crm`, `ui`, `system` |
| `action`      | string          | `open_email`, `find_customer`, `send_notification`, … |
| `description` | string          | human-readable, high-level only                  |
| `metadata`    | JSON object     | stored as JSON text in SQLite                    |
| `session_id`  | string          | `session-NNN`; each simulated repetition is its own session |

## API

| Method | Path                      | Purpose                                        |
|--------|---------------------------|------------------------------------------------|
| GET    | `/api/activity`           | recent events (chronological order)            |
| POST   | `/api/activity`           | ingest one event (201)                         |
| DELETE | `/api/activity`           | clear all events → `{"cleared": n}`            |
| POST   | `/api/activity/simulate`  | generate + store a predefined sequence         |
| GET    | `/api/activity/stats`     | totals for the dashboards                      |

`GET /api/activity` query parameters: `limit` (1–500, default 50),
`application` (case-insensitive), `category`, `session_id`.

Response shape: `{"events": [...], "count": n, "total": n_without_limit}`.

```bash
# simulate one customer-request sequence
curl -X POST http://127.0.0.1:8000/api/activity/simulate \
     -H 'Content-Type: application/json' \
     -d '{"workflow": "customer_request", "repetitions": 3}'

curl 'http://127.0.0.1:8000/api/activity?limit=50&application=Gmail'
curl http://127.0.0.1:8000/api/activity/stats
curl -X DELETE http://127.0.0.1:8000/api/activity
```

Unknown workflows return `400` with the list of available ones.

## Simulated workflows

| Key                   | Steps | Applications            |
|-----------------------|-------|-------------------------|
| `customer_request`    | 6     | Gmail → CRM → Slack     |
| `document_processing` | 5     | Gmail → Finder → Excel  |
| `meeting_followup`    | 5     | Calendar → Gmail        |

`workflow` defaults to `customer_request`; `repetitions` defaults to 1 (max 20).
Each repetition is written to its own session, and sequences are placed in the
recent past with realistic spacing (steps seconds/minutes apart, repetitions
120 s apart) — never identical or future timestamps.

### Example: `customer_request`

| t (s) | Application | Category      | Action              | Description                          |
|--------|-------------|---------------|---------------------|--------------------------------------|
| 0      | Gmail       | communication | open_email          | Opened customer request email        |
| 23     | Gmail       | communication | read_email          | Read customer request                |
| 51     | Gmail       | file          | download_attachment | Downloaded customer_request.pdf      |
| 74     | CRM         | crm           | find_customer       | Found customer ABC Industries        |
| 130    | CRM         | crm           | update_customer     | Updated customer record              |
| 146    | Slack       | communication | send_notification   | Sent notification to support team    |

Repeating this three times (the "Simulate 3 Repetitions" button) produces
3 sessions × 6 events — the input Phase 4 repetition detection will consume.

## Frontend

- **Activity page** — overview counters, application/category filters that query
  the backend, chronological timeline, *Simulate Activity* / *Simulate 3
  Repetitions* buttons, 2.5 s polling while the page is open.
- **Overview page** — live activity statistics (`/api/activity/stats`) in the
  *Activity* section: activities today, applications used, last activity,
  current activity count.
- Backend connection status still comes from `GET /health` only.

## Tests

```bash
cd backend && .venv/bin/python -m pytest
```

Covers: event creation, metadata round-trip, retrieval order, limit
validation, application/category/session filtering, simulation (default,
repetitions, timestamp spacing, all workflows, unknown workflow), statistics
and clearing.
