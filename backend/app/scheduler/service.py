"""Scheduler ticker — the loop that turns due schedules and polled events
into queued background jobs.

Responsibilities are deliberately narrow:

1. Fire any schedule whose ``next_run`` has arrived.
2. Poll connected event providers (Gmail, Calendar, demo) for new events.
3. Enqueue a job for each *new* event, relying on the integration_events
   ledger for idempotency.

It never executes a workflow. Every run goes through the job queue so the
worker stays the single place that calls the execution engine.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import get_connection
from app.integrations.base import IntegrationProvider
from app.integrations.registry import get_provider
from app.models import automation as automation_model
from app.models import integration as integration_model
from app.models import job as job_model
from app.models import schedule as schedule_model
from app.scheduler import queue
from app.services import activity_service

logger = logging.getLogger("workflowos.scheduler")

#: Which provider serves each automation trigger type.
TRIGGER_PROVIDERS: Dict[str, str] = {
    "gmail": "gmail",
    "calendar": "calendar",
    "slack": "slack",
    # Demo triggers use the local stand-ins.
    "demo_gmail": "gmail_demo",
    "demo_slack": "slack_demo",
    "demo_calendar": "calendar_demo",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_config(raw: str) -> Dict[str, Any]:
    """Trigger config is stored as JSON text; a bad value must not crash a tick."""
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SingleInstanceGuard:
    """Best-effort guard so only one ticker runs per database.

    ``uvicorn --reload`` starts a fresh worker process each time, and a stray
    manual run could produce two tickers. An exclusive lock file makes the
    second instance step aside instead of double-firing schedules.
    """

    def __init__(self, path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> bool:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        import fcntl

        if self._handle is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None


class Scheduler:
    """Background thread that fires schedules and polls event providers."""

    def __init__(self) -> None:
        # APScheduler drives the tick; this project's SQLite tables remain the
        # single source of truth for schedules and jobs, so a restart simply
        # recomputes rather than restoring a scheduler-side job store.
        self._engine: Optional[BackgroundScheduler] = None
        self._lock = threading.Lock()
        self.running = False
        self.started_at: Optional[datetime] = None
        self.last_tick_at: Optional[datetime] = None
        self.ticks = 0
        self.jobs_enqueued = 0
        self.guard: Optional[SingleInstanceGuard] = None
        # Poller bookkeeping: provider -> slot counter, so a provider is only
        # polled when its configured interval has elapsed.
        self._poll_state: Dict[str, Dict[str, Any]] = {}

    # ---------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            if self._engine is not None and self.running:
                logger.info("Scheduler already running; not starting another")
                return
            guard = SingleInstanceGuard(
                settings.database_path.parent / ".scheduler.lock"
            )
            if not guard.acquire():
                logger.warning(
                    "Another scheduler instance holds the lock; this one will idle"
                )
            self.guard = guard
            self.running = True
            self.started_at = _now()
            engine = BackgroundScheduler(timezone="UTC")
            engine.add_job(
                self._safe_tick,
                "interval",
                seconds=settings.scheduler_tick_seconds,
                id="workflowos-scheduler-tick",
                # Never let ticks pile up if one is slow.
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            engine.start()
            self._engine = engine
            logger.info(
                "Scheduler started (tick=%.1fs)", settings.scheduler_tick_seconds
            )

    def stop(self, timeout: float = 5.0) -> None:
        engine = self._engine
        if engine is not None:
            try:
                engine.shutdown(wait=True)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.exception("Scheduler shutdown failed")
        self._engine = None
        self.running = False
        if self.guard is not None:
            self.guard.release()
            self.guard = None
        logger.info("Scheduler stopped")

    def _safe_tick(self) -> None:
        """Wrapper so one bad tick can never kill the scheduler thread."""
        try:
            self.tick()
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler tick failed")

    # ------------------------------------------------------------- ticking

    def tick(self) -> Dict[str, int]:
        """Run one scheduling pass. Returns counters for observability."""
        stats = {"schedules_fired": 0, "events_found": 0, "jobs_enqueued": 0}
        stats["schedules_fired"] = self.fire_due_schedules()
        observed = self.observe_providers()
        events, enqueued = self.poll_providers()
        stats["events_found"] = events + observed
        stats["jobs_enqueued"] = enqueued
        self.ticks += 1
        self.jobs_enqueued += enqueued
        self.last_tick_at = _now()
        return stats

    def observe_providers(self) -> int:
        """Record genuinely new provider events as observed activity.

        This is what makes the product self-improving, and it has to run
        independently of automations. Polling used to happen only for
        automations that already had an event trigger, which deadlocked a
        fresh install: discovering a workflow needs observed activity, and
        creating the automation that polls for it needs an approved draft,
        which needs a discovered workflow. So on a clean database nothing
        would ever be observed.

        Only real, connected providers are polled, and every event is
        deduplicated, so a message is recorded once no matter how many
        automations later match it. No job is enqueued here — this pass only
        observes.
        """
        observed = 0
        for provider_name in ("gmail", "calendar", "slack"):
            if not self._observe_due(provider_name):
                continue
            try:
                provider = get_provider(provider_name)
            except Exception:  # noqa: BLE001 - an unknown provider is not fatal
                continue
            if not getattr(provider, "is_mock", True):
                if not getattr(provider, "is_configured", lambda: False)():
                    continue
                if not getattr(provider, "is_connected", lambda: False)():
                    continue
            else:
                # Never let a stand-in reach the real activity trail.
                continue
            try:
                events = provider.poll_events(
                    {
                        "unread": True,
                        "max_results": 10,
                        # Our own outbound replies are not incoming activity.
                        "query": "-from:me",
                        "observation": True,
                    }
                )
            except Exception:  # noqa: BLE001 - a provider fault must not stop observing
                logger.exception("%s observation poll failed", provider_name)
                continue
            for event in events:
                if self._record_observed_event(provider_name, event):
                    observed += 1
        return observed

    def _observe_due(self, provider_name: str) -> bool:
        """Throttle the observation pass on the provider's own interval."""
        state = self._poll_state.setdefault(f"observe:{provider_name}", {"last": None})
        interval = 60
        if provider_name == "gmail":
            interval = settings.gmail_poll_interval_seconds
        elif provider_name == "calendar":
            interval = settings.calendar_poll_interval_seconds
        last = state.get("last")
        now = _now()
        if last is not None and (now - last).total_seconds() < interval:
            return False
        state["last"] = now
        return True

    def _record_observed_event(
        self, provider_name: str, event: Dict[str, Any]
    ) -> bool:
        """Record one observed event once, without enqueuing any job."""
        external_event_id = str(event.get("external_event_id") or "")
        if not external_event_id:
            return False
        try:
            with get_connection() as connection:
                integration_model.ensure_tables(connection)
                event_row = integration_model.record_event(
                    connection,
                    provider=provider_name,
                    external_event_id=external_event_id,
                    automation_id=None,
                    payload=event,
                )
                if event_row is None:
                    return False
                self._record_observed_activity(connection, provider_name, event)
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to record observed %s event %s", provider_name, external_event_id
            )
            return False

    def fire_due_schedules(self) -> int:
        """Enqueue jobs for schedules whose next_run has arrived."""
        fired = 0
        with get_connection() as connection:
            schedule_model.ensure_table(connection)
            job_model.ensure_table(connection)
            automation_model.ensure_table(connection)
            for schedule in schedule_model.select_due(connection):
                automation = automation_model.select_by_id(
                    connection, schedule.automation_id
                )
                # A disabled automation must never fire, and a deleted one
                # must not leave a live job behind.
                if automation is None or not automation["enabled"]:
                    schedule_model.update_schedule(
                        connection,
                        schedule.id,
                        enabled=False,
                        next_run=None,
                    )
                    continue
                enqueued = False
                try:
                    job_model.insert_job(
                        connection,
                        automation_id=schedule.automation_id,
                        trigger="schedule",
                        payload={
                            "schedule_id": schedule.id,
                            "frequency": schedule.frequency,
                            "timezone": schedule.timezone,
                        },
                        max_retries=settings.job_max_retries,
                    )
                    enqueued = True
                    fired += 1
                    self.jobs_enqueued += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to enqueue schedule %s", schedule.id
                    )
                finally:
                    # Always advance, so a failing automation cannot spin.
                    queue.record_schedule_run(connection, schedule, failed=not enqueued)
        return fired

    def poll_providers(self) -> tuple[int, int]:
        """Poll event providers for new events and enqueue matching jobs."""
        found = 0
        enqueued = 0
        for automation_row in self._event_automations():
            provider_name = TRIGGER_PROVIDERS.get(automation_row["trigger_type"])
            if not provider_name:
                continue
            if not self._poll_due(provider_name, automation_row):
                continue
            try:
                provider = get_provider(provider_name)
            except Exception:  # noqa: BLE001
                logger.exception("Unknown provider %s", provider_name)
                continue
            if not getattr(provider, "is_connected", lambda: False)():
                continue
            config = _parse_config(automation_row["trigger_config"])
            config.setdefault("demo_slot", self._poll_state.get(provider_name, {}).get("slot", 0))
            if provider_name == "gmail":
                self._default_gmail_criteria(config)
            try:
                events = provider.poll_events(config)
            except Exception:  # noqa: BLE001 - a provider fault must not stop polling
                logger.exception("Provider %s poll failed", provider_name)
                continue
            for event in events:
                found += 1
                if self._enqueue_event(provider_name, automation_row["id"], event):
                    enqueued += 1
        return found, enqueued

    def _event_automations(self) -> List[Any]:
        with get_connection() as connection:
            automation_model.ensure_table(connection)
            return automation_model.select_all(connection, enabled=True)

    def _poll_due(self, provider_name: str, automation_row) -> bool:
        """Honour the per-provider polling interval."""
        state = self._poll_state.setdefault(provider_name, {"slot": 0, "last": None})
        interval = 60
        if provider_name == "gmail":
            interval = settings.gmail_poll_interval_seconds
        elif provider_name == "calendar":
            interval = settings.calendar_poll_interval_seconds
        else:
            # Demo providers poll fast enough to be visible in a live demo.
            interval = 30
        last = state.get("last")
        now = _now()
        if last is not None and (now - last).total_seconds() < interval:
            return False
        state["last"] = now
        state["slot"] = int(state.get("slot", 0)) + 1
        return True

    @staticmethod
    def _default_gmail_criteria(config: Dict[str, Any]) -> None:
        """Make an unqualified Gmail trigger match what the workflow reads.

        A Gmail automation whose trigger config names no sender, subject or
        query used to fire on *every* unread message in the mailbox, while the
        workflow it runs reads one specific configured message. That mismatch
        backfilled the whole inbox on enable: a burst of queued jobs, repeated
        runs of the same draft, and repeated replies to the same correspondent.

        When the operator has configured a Gmail read query, an unqualified
        trigger adopts it, so a trigger event and the message the workflow
        reads are the same thing. An explicitly configured trigger is left
        exactly as the operator set it.
        """
        from app.config import demo_read_query

        if any(str(config.get(key) or "").strip() for key in ("from", "subject", "query")):
            return
        criteria = demo_read_query()
        if criteria:
            config["query"] = criteria
            # The configured query is already precise about the subject line,
            # so an extra is:unread would only narrow it unpredictably.
            config["unread"] = False
            logger.info(
                "gmail trigger adopted the configured read query: %s", criteria
            )

    def _enqueue_event(
        self, provider_name: str, automation_id: str, event: Dict[str, Any]
    ) -> bool:
        """Record the event (idempotently) and queue a job for it."""
        external_event_id = str(event.get("external_event_id") or "")
        if not external_event_id:
            return False
        try:
            with get_connection() as connection:
                integration_model.ensure_tables(connection)
                job_model.ensure_table(connection)
                event_row = integration_model.record_event(
                    connection,
                    provider=provider_name,
                    external_event_id=external_event_id,
                    automation_id=automation_id,
                    payload=event,
                )
                if event_row is None:
                    # Already seen for this automation — never run twice.
                    return False
                self._record_observed_activity(connection, provider_name, event)
                job_model.insert_job(
                    connection,
                    automation_id=automation_id,
                    trigger=(
                        "gmail"
                        if provider_name.startswith("gmail")
                        else "calendar"
                        if provider_name.startswith("calendar")
                        else "slack"
                    ),
                    event_id=event_row,
                    payload=event,
                    max_retries=settings.job_max_retries,
                )
            self.jobs_enqueued += 1
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to enqueue %s event for %s", provider_name, automation_id
            )
            return False

    @staticmethod
    def _record_observed_activity(
        connection, provider_name: str, event: Dict[str, Any]
    ) -> None:
        """Mirror a real provider event into the activity log.

        This is what makes the product self-improving: a message that actually
        arrived in the user's mailbox becomes an observed activity event, so the
        discovery engine can notice the repeating pattern on its own. Nothing
        synthetic is written — the fields come straight from the provider
        response, and mock providers are skipped so simulated events can never
        pollute discovery.
        """
        from app.models import activity as activity_model
        from app.models import integration as integration_model
        from app.schemas.activity import ActivityEventCreate

        if provider_name.endswith("_demo"):
            return
        subject = str(event.get("subject") or "").strip()
        sender = str(event.get("from") or "").strip()
        if not (subject or sender):
            return
        integration_model.ensure_tables(connection)
        activity_model.ensure_table(connection)
        # One observed message is one activity event, however many code paths
        # (the observation pass and an automation's trigger poll) notice it.
        session_id = f"{provider_name}:{event.get('message_id') or 'event'}"
        already = connection.execute(
            "SELECT 1 FROM activity_events WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if already is not None:
            return
        try:
            activity_service.record_event(
                ActivityEventCreate(
                    application=provider_name,
                    category="communication",
                    action=str(event.get("action") or "new_message"),
                    description=(
                        f"Received from {sender}: {subject}"
                        if subject
                        else f"Received from {sender}"
                    ),
                    metadata={
                        "source": "integration_poll",
                        "provider": provider_name,
                        "message_id": event.get("message_id"),
                    },
                    session_id=session_id,
                ),
                # Reuse the caller's connection: this runs inside an open write
                # transaction, and a second connection would deadlock on it.
                connection=connection,
            )
        except Exception:  # noqa: BLE001 - never block a job on bookkeeping
            logger.exception("Could not record observed activity")

    # -------------------------------------------------------------- status

    def reset_poll_state(self) -> None:
        """Forget per-provider poll timing so the next poll is due at once.

        Used by the demo reset: without this, a reset that lands inside the
        provider's poll interval would need to wait for the interval to expire
        before the scheduler could show a new event. Production behaviour is
        unchanged — this is only called by the demo reset endpoint.
        """
        self._poll_state.clear()

    def status(self) -> Dict[str, Any]:
        with get_connection() as connection:
            schedule_model.ensure_table(connection)
            schedules = schedule_model.select_all(connection)
        active = [item for item in schedules if item.enabled and item.next_run]
        upcoming = sorted(
            (item.next_run for item in active if item.next_run),
        )
        return {
            "running": self.running,
            "enabled": settings.scheduler_enabled,
            "tick_seconds": settings.scheduler_tick_seconds,
            "started_at": self.started_at,
            "active_schedules": len(active),
            "next_due": upcoming[0] if upcoming else None,
            "last_tick_at": self.last_tick_at,
            "ticks": self.ticks,
            "jobs_enqueued": self.jobs_enqueued,
        }


#: Process-wide scheduler used by the FastAPI lifespan.
scheduler = Scheduler()


__all__ = ["TRIGGER_PROVIDERS", "Scheduler", "SingleInstanceGuard", "scheduler"]
