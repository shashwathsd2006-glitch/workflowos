# WorkFlowOS Phases

Numbering follows the session plan (Phase 1 = requirements analysis,
Phase 2 = foundation).

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Requirements analysis + architecture proposal | done |
| 2 | Project foundation: FastAPI + health + SQLite config + config/env + error handling; Next.js dashboard shell, nav, live backend status | **done** |
| 3 | Desktop Activity Agent: `ActivityEvent` model, simulated source, ingestion API, fixtures, Activity page | **done** |
| 4 | Workflow Discovery Engine: sequence grouping, repetition detection, confidence, Sequences view | **done** |
| 5 | AI Workflow Understanding: provider abstraction (Ollama + Mock), strict JSON schema, SQLite persistence, `/api/ai`, Workflows UI | **done** |
| 5b | Workflow Generation + Human Approval: generator (Ollama + Mock), `workflow_drafts` table, `/api/workflows/drafts`, approval state machine, draft review UI | **done** |
| 6 | Workflow Execution: approved-draft-only engine, controlled action registry (mock actions), `workflow_executions` + `workflow_execution_steps`, execution API, execution review UI, activity audit | **done** |
| 7 | Automations, Analytics and Settings: `automations` table + API bound to approved drafts, analytics aggregation from real rows, read-only system status | **done** |
| 8 | Scheduling, background execution & integrations: persistent scheduler, durable job queue + worker with retry, Gmail/Slack/Calendar via OAuth, demo mode, idempotent event ledger | **done** |
| 9 | Real Gmail OAuth 2.0 (credentials file, minimum scopes, state validation, encrypted refresh token) wired to the action registry | **done** |
| 10 | Job cancellation, Slack event polling, schedule editing UI, retention policy | pending |

## Rules carried across phases

- Phase-by-phase delivery; no future-phase features in the current phase
- Simulated activity only until a dedicated monitoring phase
- No real integrations; mock adapters behind interfaces
- Explicit approval required before any automation executes
- Minimal dependencies; every module independently testable
- Servers started only for verification, never left running
