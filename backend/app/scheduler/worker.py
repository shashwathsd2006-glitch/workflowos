"""Background worker — processes queued jobs by calling the existing engine.

Design notes:

- **One execution path.** The worker does not know how to run workflow steps.
  It calls ``app.automation.engine.execute_draft``, exactly like the manual
  "Run now" button, so there is still only one engine.
- **Exactly-once claiming.** ``claim_next_job`` flips ``queued -> running``
  with a conditional UPDATE; a second worker cannot win the same row.
- **Retry policy.** Retryable failures back off exponentially; validation,
  auth, unsupported-action and not-configured failures never retry, because
  retrying cannot fix them.
- **Restart safety.** Jobs left ``running`` by a crashed process are requeued
  at startup by ``recover_stale_jobs``.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.automation.engine import (
    DraftNotApprovedError,
    DraftNotFoundError,
    execute_draft,
)
from app.config import settings
from app.database import get_connection
from app.models import job as job_model
from app.models import schedule as schedule_model
from app.scheduler import queue

logger = logging.getLogger("workflowos.scheduler.worker")

#: Failure classes that must never be retried — the cause is permanent.
NON_RETRYABLE_TYPES = {
    "UnsupportedAction",
    "NonRetryableActionError",
    "ValidationError",
    "AuthenticationError",
    "NotConfiguredError",
    "DraftNotApprovedError",
    "DraftNotFoundError",
    "AutomationDisabledError",
    "AutomationNotFoundError",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backoff_seconds(retry_count: int) -> float:
    """Exponential backoff: base * 2**(retry_count-1), capped."""
    base = max(0.1, settings.job_retry_backoff_seconds)
    exponent = max(0, retry_count - 1)
    return min(base * (2**exponent), 3600.0)


class Worker:
    """Single background thread that drains the job queue."""

    def __init__(self) -> None:
        # APScheduler drives the drain loop; the queue itself is this
        # project's own SQLite table, so restarts are naturally safe.
        self._engine: Optional[BackgroundScheduler] = None
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[datetime] = None
        self.jobs_processed = 0
        self.last_processed_job_id: Optional[str] = None
        self.last_processed_at: Optional[datetime] = None

    # ---------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            if self._engine is not None and self.running:
                logger.info("Worker already running; not starting another")
                return
            self.running = True
            self.started_at = _now()
            engine = BackgroundScheduler(timezone="UTC")
            engine.add_job(
                self._safe_drain,
                "interval",
                seconds=settings.worker_poll_seconds,
                id="workflowos-worker-drain",
                # One drain at a time: this is what keeps a job from being
                # picked up twice.
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            engine.start()
            self._engine = engine
            logger.info(
                "Background worker started (poll=%.1fs)", settings.worker_poll_seconds
            )

    def stop(self, timeout: float = 5.0) -> None:
        engine = self._engine
        if engine is not None:
            try:
                engine.shutdown(wait=True)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.exception("Worker shutdown failed")
        self._engine = None
        self.running = False
        logger.info("Background worker stopped")

    def recover_and_start(self) -> int:
        """Requeue anything a previous process left mid-flight."""
        return self.recover_stale()

    def _safe_drain(self) -> None:
        """One bounded drain pass; never raises into the scheduler thread."""
        try:
            for _ in range(settings.worker_batch_size):
                if self.process_one() is None:
                    break
        except Exception:  # noqa: BLE001
            logger.exception("Worker drain failed")

    # ------------------------------------------------------------- status

    def status(self) -> Dict[str, Any]:
        with get_connection() as connection:
            job_model.ensure_table(connection)
            counts = job_model.counts(connection)
        return {
            "running": self.running,
            "enabled": settings.worker_enabled,
            "poll_seconds": settings.worker_poll_seconds,
            "started_at": self.started_at,
            "last_processed_job_id": self.last_processed_job_id,
            "last_processed_at": self.last_processed_at,
            "jobs_processed": self.jobs_processed,
            "queue": counts,
        }

    def recover_stale(self) -> int:
        """Requeue jobs stranded in ``running`` by a crash or restart."""
        with get_connection() as connection:
            job_model.ensure_table(connection)
            return job_model.recover_stale_jobs(
                connection, settings.job_stale_after_seconds
            )

    # ---------------------------------------------------------- processing

    def process_one(self) -> Optional[str]:
        """Claim and run at most one job. Returns the job id, or None."""
        with get_connection() as connection:
            job_model.ensure_table(connection)
            job = job_model.claim_next_job(connection)
        if job is None:
            return None
        self._process(job)
        return job.id

    def drain(self, limit: int = 25) -> int:
        """Process up to ``limit`` queued jobs synchronously.

        Used by tests and by the "Run demo" path so a demo does not have to
        wait for the polling interval.
        """
        processed = 0
        for _ in range(limit):
            if self.process_one() is None:
                break
            processed += 1
        return processed

    def _process(self, job) -> None:
        started = time.monotonic()
        job_id = job.id
        logger.info(
            "job_id=%s automation_id=%s trigger=%s status=started attempt=%d/%d",
            job_id,
            job.automation_id,
            job.trigger,
            job.retry_count + 1,
            job.max_retries,
        )
        try:
            detail = execute_draft(
                _draft_id_for(job.automation_id),
                trigger_payload=dict(job.payload or {}),
                source=str(job.trigger),
            )
        except Exception as exc:  # noqa: BLE001 - classify below
            duration = int((time.monotonic() - started) * 1000)
            self._record_failure(job, exc, duration)
            return

        duration = int((time.monotonic() - started) * 1000)
        if detail.status == "completed":
            with get_connection() as connection:
                job_model.ensure_table(connection)
                job_model.complete_job(
                    connection, job_id, execution_id=detail.id, duration_ms=duration
                )
            self._mark_event(job)
            self._after_run(job, failed=False)
            logger.info(
                "job_id=%s automation_id=%s execution_id=%s status=completed duration_ms=%d",
                job_id,
                job.automation_id,
                detail.id,
                duration,
            )
        else:
            error = detail.error or f"execution {detail.status}"
            # Only an explicit True from the engine means "worth retrying".
            retryable = detail.retryable is True
            self._record_failure(
                job,
                _ExecutionFailure(error, type(detail.error_type), retryable),
                duration,
                execution_id=detail.id,
            )

        self.jobs_processed += 1
        self.last_processed_job_id = job_id
        self.last_processed_at = _now()

    def _record_failure(
        self, job, exc: BaseException, duration: int, execution_id: Optional[str] = None
    ) -> None:
        error_type = type(exc).__name__
        message = str(exc)
        retryable = getattr(exc, "retryable", None)
        if retryable is None:
            retryable = error_type not in NON_RETRYABLE_TYPES
        if error_type in NON_RETRYABLE_TYPES:
            retryable = False

        attempt = job.retry_count + 1
        will_retry = retryable and attempt <= job.max_retries
        next_attempt = (
            _now() + timedelta(seconds=backoff_seconds(attempt))
            if will_retry
            else None
        )
        with get_connection() as connection:
            job_model.ensure_table(connection)
            if execution_id:
                connection.execute(
                    "UPDATE background_jobs SET execution_id = ? WHERE id = ?",
                    (execution_id, job.id),
                )
            job_model.fail_job(
                connection,
                job.id,
                error=message,
                error_type=error_type,
                retryable=retryable,
                retry_count=attempt if will_retry else job.retry_count,
                max_retries=job.max_retries,
                next_attempt_at=next_attempt,
                duration_ms=duration,
            )
        self._after_run(job, failed=True)
        logger.warning(
            "job_id=%s automation_id=%s status=%s retryable=%s attempt=%d/%d "
            "error_type=%s duration_ms=%d error=%s",
            job.id,
            job.automation_id,
            "queued" if will_retry else "failed",
            retryable,
            attempt,
            job.max_retries,
            error_type,
            duration,
            message,
        )
        self.jobs_processed += 1
        self.last_processed_job_id = job.id
        self.last_processed_at = _now()

    def _mark_event(self, job) -> None:
        if not job.event_id:
            return
        from app.models import integration as integration_model

        with get_connection() as connection:
            integration_model.ensure_tables(connection)
            row = connection.execute(
                "SELECT id FROM integration_events WHERE id = ?", (job.event_id,)
            ).fetchone()
            if row is not None:
                integration_model.mark_event_processed(connection, row["id"])

    def _after_run(self, job, *, failed: bool) -> None:
        """Update schedule counters so last_run/failure_count stay truthful."""
        with get_connection() as connection:
            schedule_model.ensure_table(connection)
            schedule = schedule_model.select_by_automation(
                connection, job.automation_id
            )
            if schedule is not None:
                queue.record_schedule_run(connection, schedule, failed=failed)


class _ExecutionFailure(Exception):
    """Carries an execution's own failure classification to the worker."""

    def __init__(self, message: str, error_type: Optional[str], retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_type = error_type or "ExecutionFailed"


def _draft_id_for(automation_id: str) -> str:
    """Resolve the approved draft an automation points at."""
    from app.models import automation as automation_model

    with get_connection() as connection:
        automation_model.ensure_table(connection)
        row = automation_model.select_by_id(connection, automation_id)
    if row is None:
        raise queue.AutomationNotFoundError(
            f"Automation '{automation_id}' not found"
        )
    return str(row["draft_id"])


#: Process-wide worker instance used by the FastAPI lifespan.
worker = Worker()


__all__ = [
    "NON_RETRYABLE_TYPES",
    "Worker",
    "backoff_seconds",
    "worker",
]
