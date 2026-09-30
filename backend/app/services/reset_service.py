"""Safe local reset.

Clears WorkFlowOS's own run history so a demo can start from a clean,
well-understood state. It is deliberately narrow:

* it deletes **only** rows WorkFlowOS created locally — executions, execution
  steps, background jobs, schedules, automations, workflow drafts, AI
  understandings, discovered candidates and activity events;
* it **never** touches ``integration_accounts``, so an authorised Gmail
  connection and its encrypted refresh token survive untouched;
* it never reads, deletes or modifies a real Gmail message, and never calls
  any external API;
* it never deletes the OAuth credentials file or the token encryption key.

In other words: it resets the demo's local records and nothing else.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.database import get_connection
from app.models import activity as activity_model
from app.models import automation as automation_model
from app.models import execution as execution_model
from app.models import generator as draft_model
from app.models import integration as integration_model
from app.models import job as job_model
from app.models import schedule as schedule_model
from app.models import understanding as understanding_model
from app.models import workflow_candidate as candidate_model

logger = logging.getLogger("workflowos.system")

#: Child rows first: these tables reference the ones listed after them.
#: ``integration_accounts`` is intentionally absent — see the module docstring.
#: ``integration_events`` IS cleared: it is only the provider-event dedupe
#: ledger, not the Gmail connection. Leaving it behind made a reset a one-way
#: door — the messages were already "seen", so the observation pass skipped
#: them and the Activity page stayed permanently empty after a reset.
_TABLES_IN_DELETE_ORDER = (
    ("execution_steps", execution_model.STEPS_TABLE),
    ("executions", execution_model.EXECUTIONS_TABLE),
    ("background_jobs", job_model.TABLE),
    ("schedules", schedule_model.TABLE),
    ("automations", automation_model.TABLE),
    ("drafts", draft_model.TABLE),
    ("understandings", understanding_model.TABLE),
    ("candidates", candidate_model.TABLE),
    ("activity_events", activity_model.TABLE),
    ("integration_events", integration_model.EVENTS_TABLE),
)


def reset_local_records() -> Dict[str, Any]:
    """Delete local run history. Returns what was removed."""
    removed: Dict[str, int] = {}
    with get_connection() as connection:
        for key, table in _TABLES_IN_DELETE_ORDER:
            cursor = connection.execute(f"DELETE FROM {table}")
            removed[key] = cursor.rowcount if cursor.rowcount > 0 else 0
        connection.commit()
    logger.info("Local records reset: %s", removed)
    return {
        "reset": True,
        "scope": "local-records-only",
        "preserved": [
            "integration_accounts (Gmail connection and tokens)",
            "OAuth credentials file",
            "token encryption key",
            "all real Gmail messages",
        ],
        "external_calls_made": 0,
    }


def local_record_counts() -> Dict[str, int]:
    """Row counts per cleared table, for confirming a reset."""
    counts: Dict[str, int] = {}
    with get_connection() as connection:
        for key, table in _TABLES_IN_DELETE_ORDER:
            row = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[key] = int(row["n"])
    return counts


__all__: List[str] = ["local_record_counts", "reset_local_records"]
