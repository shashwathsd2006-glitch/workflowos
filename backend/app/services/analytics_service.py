"""Analytics service — aggregates real persisted records only.

Every number here is derived by counting rows in ``workflow_candidates``,
``automations`` and ``workflow_executions``. Nothing is sampled, estimated or
hardcoded: an empty database yields zeros, which the UI renders as an empty
state rather than inventing activity.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.database import get_connection
from app.models import automation as automation_model
from app.models import execution as execution_model
from app.models import integration as integration_model
from app.models import job as job_model
from app.models import workflow_candidate as candidate_model


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def get_analytics(days: int = 14) -> Dict[str, Any]:
    """Build the full analytics payload from stored rows."""
    window = max(1, min(days, 90))
    since = _now() - timedelta(days=window)

    with get_connection() as connection:
        candidate_model.ensure_table(connection)
        execution_model.ensure_tables(connection)
        automation_model.ensure_table(connection)
        job_model.ensure_table(connection)
        integration_model.ensure_tables(connection)

        candidates = candidate_model.select_candidates(connection)
        executions = execution_model.select_executions(connection)
        job_counts = job_model.counts(connection)
        jobs = job_model.select_all(connection, limit=50)
        integration_events = _integration_activity(connection)
        automations = automation_model.select_all(connection)
        draft_names = _draft_names(connection)
        draft_to_candidate = _draft_to_candidate(connection)
        steps_by_execution = _steps_by_execution(connection)

    total_executions = len(executions)
    successful = sum(1 for item in executions if item.status == "completed")
    failed = sum(1 for item in executions if item.status == "failed")
    other = total_executions - successful - failed

    return {
        "summary": {
            "total_workflows": len(candidates),
            "total_automations": len(automations),
            "enabled_automations": sum(1 for row in automations if row["enabled"]),
            "total_executions": total_executions,
            "successful_executions": successful,
            "failed_executions": failed,
            "other_executions": other,
            "success_rate": _percent(successful, total_executions),
            "queued_jobs": job_counts.get("queued", 0),
            "running_jobs": job_counts.get("running", 0),
            "failed_jobs": job_counts.get("failed", 0),
            "completed_jobs": job_counts.get("completed", 0),
            "active_automations": sum(1 for row in automations if row["enabled"]),
            "total_jobs": sum(job_counts.values()),
        },
        "activity": _activity_series(executions, since, window),
        "workflow_performance": _workflow_performance(
            executions, draft_names, draft_to_candidate, candidates
        ),
        "recent_failures": _recent_failures(
            executions, steps_by_execution, draft_names, jobs
        ),
        "jobs": {
            "counts": job_counts,
            "recent": [
                {
                    "id": item.id,
                    "automation_id": item.automation_id,
                    "execution_id": item.execution_id,
                    "trigger": item.trigger,
                    "status": item.status,
                    "retry_count": item.retry_count,
                    "max_retries": item.max_retries,
                    "error": item.error,
                    "error_type": item.error_type,
                    "created_at": _iso(item.created_at),
                    "completed_at": (
                        _iso(item.completed_at) if item.completed_at else None
                    ),
                    "duration_ms": item.duration_ms,
                }
                for item in jobs
            ],
        },
        "integrations": integration_events,
        "recent_executions": [
            {
                "id": item.id,
                "draft_id": item.draft_id,
                "workflow_name": draft_names.get(item.draft_id, "(unknown workflow)"),
                "status": item.status,
                "completed_steps": item.completed_steps,
                "total_steps": item.total_steps,
                "started_at": _iso(item.started_at) if item.started_at else None,
                "completed_at": (
                    _iso(item.completed_at) if item.completed_at else None
                ),
                "error": item.error,
            }
            for item in executions[:10]
        ],
        "window_days": window,
        "generated_at": _iso(_now()),
    }


def _integration_activity(connection) -> List[Dict[str, Any]]:
    """Per-provider event counts — real rows only."""
    activity: List[Dict[str, Any]] = []
    for row in connection.execute(
        "SELECT provider, status, COUNT(*) AS total "
        "FROM integration_events GROUP BY provider, status"
    ):
        activity.append(
            {
                "provider": row["provider"],
                "status": row["status"],
                "count": int(row["total"]),
            }
        )
    activity.sort(key=lambda item: (-item["count"], item["provider"]))
    return activity


def _draft_names(connection) -> Dict[str, str]:
    """Map draft_id -> workflow name for display."""
    rows = connection.execute(
        "SELECT id, name FROM workflow_drafts"
    ).fetchall()
    return {row["id"]: row["name"] for row in rows}


def _draft_to_candidate(connection) -> Dict[str, str]:
    """Map draft_id -> workflow_candidate_id.

    Analytics is per *workflow*, but executions reference a draft. Rolling up
    through this map keeps one row per candidate instead of showing the same
    workflow twice (once as a candidate, once per draft).
    """
    rows = connection.execute(
        "SELECT id, workflow_candidate_id FROM workflow_drafts"
    ).fetchall()
    return {row["id"]: row["workflow_candidate_id"] for row in rows}


def _steps_by_execution(connection) -> Dict[str, List[Any]]:
    """Group step rows by execution id (used to name the failed step)."""
    result: Dict[str, List[Any]] = {}
    rows = connection.execute(
        f"SELECT * FROM {execution_model.STEPS_TABLE} ORDER BY step_number ASC"
    ).fetchall()
    for row in rows:
        result.setdefault(row["execution_id"], []).append(row)
    return result


def _activity_series(
    executions, since: datetime, window: int
) -> List[Dict[str, Any]]:
    """One bucket per day across the window, including empty days."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for offset in range(window - 1, -1, -1):
        day = (_now() - timedelta(days=offset)).date().isoformat()
        buckets[day] = {"date": day, "completed": 0, "failed": 0, "other": 0}

    for item in executions:
        if item.started_at is None:
            continue
        started = item.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started < since:
            continue
        key = started.date().isoformat()
        bucket = buckets.get(key)
        if bucket is None:
            continue
        if item.status == "completed":
            bucket["completed"] += 1
        elif item.status == "failed":
            bucket["failed"] += 1
        else:
            bucket["other"] += 1

    series = list(buckets.values())
    for bucket in series:
        bucket["total"] = bucket["completed"] + bucket["failed"] + bucket["other"]
    return series


def _workflow_performance(
    executions,
    draft_names: Dict[str, str],
    draft_to_candidate: Dict[str, str],
    candidates,
) -> List[Dict[str, Any]]:
    """Per-workflow execution counts and success rate.

    Executions are rolled up to their workflow candidate so each workflow
    appears exactly once.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        grouped[candidate.id] = {
            "workflow_candidate_id": candidate.id,
            "workflow_name": candidate.name,
            "status": candidate.status,
            "executions": 0,
            "successful": 0,
            "failed": 0,
        }

    for item in executions:
        candidate_id = draft_to_candidate.get(item.draft_id)
        if candidate_id is not None and candidate_id in grouped:
            entry = grouped[candidate_id]
        else:
            # Execution whose draft no longer maps to a known candidate.
            key = f"__orphan__{item.draft_id}"
            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "workflow_candidate_id": None,
                    "workflow_name": draft_names.get(item.draft_id, "(unlinked draft)"),
                    "status": "unknown",
                    "executions": 0,
                    "successful": 0,
                    "failed": 0,
                }
                grouped[key] = entry
        entry["executions"] += 1
        if item.status == "completed":
            entry["successful"] += 1
        elif item.status == "failed":
            entry["failed"] += 1

    rows = list(grouped.values())
    for entry in rows:
        entry["success_rate"] = _percent(entry["successful"], entry["executions"])
    rows.sort(key=lambda entry: (-entry["executions"], entry["workflow_name"]))
    return rows


def _recent_failures(
    executions, steps_by_execution, draft_names, jobs
) -> List[Dict[str, Any]]:
    """Most recent failed executions with the step and job that failed."""
    job_by_execution = {
        item.execution_id: item for item in jobs if item.execution_id
    }
    failures = [item for item in executions if item.status == "failed"]
    result: List[Dict[str, Any]] = []
    for item in failures[:10]:
        failed_step = None
        for row in steps_by_execution.get(item.id, []):
            if row["status"] == "failed":
                import json as _json

                audit_hint = {}
                failed_step = {
                    "step_number": row["step_number"],
                    "application": row["application"],
                    "action": row["action"],
                    "error": row["error"],
                    "action_type": row["action_type"],
                    "integration": _integration_from_action(row["action_type"]),
                }
                break
        related_job = job_by_execution.get(item.id)
        result.append(
            {
                "execution_id": item.id,
                "job_id": related_job.id if related_job else None,
                "automation_id": related_job.automation_id if related_job else None,
                "trigger": related_job.trigger if related_job else None,
                "retry_count": related_job.retry_count if related_job else None,
                "error_type": (failed_step or {}).get("error_type")
                or (related_job.error_type if related_job else None),
                "integration": (failed_step or {}).get("integration"),
                "draft_id": item.draft_id,
                "workflow_name": draft_names.get(
                    item.draft_id, "(unknown workflow)"
                ),
                "failed_step": failed_step,
                "error": item.error,
                "failed_step_label": item.failed_step,
                "completed_steps": item.completed_steps,
                "total_steps": item.total_steps,
                "completed_at": (
                    _iso(item.completed_at) if item.completed_at else None
                ),
            }
        )
    return result


def _integration_from_action(action_type: Optional[str]) -> Optional[str]:
    """Name the integration behind a failed step, when there was one."""
    if not action_type:
        return None
    if action_type.startswith("gmail"):
        return "gmail"
    if action_type.startswith("slack"):
        return "slack"
    if action_type.startswith("calendar"):
        return "calendar"
    return None


__all__ = ["get_analytics"]
