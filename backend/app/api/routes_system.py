"""System status API: read-only configuration and health for the Settings page.

Exposes operational configuration and reachability only. No secret, token or
credential is ever returned.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.services.system_service import get_system_status

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
def system_status() -> dict:
    """Return application config, database state and Ollama reachability."""
    return get_system_status()


@router.post("/reset")
def reset_local_records() -> Dict[str, Any]:
    """Clear local run history so a demonstration can start clean.

    Safety contract, enforced by ``tests/test_reset.py``:

    * only WorkFlowOS's own local records are deleted (executions, steps,
      jobs, schedules, automations, drafts, understandings, candidates,
      activity events and the provider-event dedupe ledger);
    * ``integration_accounts`` is never touched, so an authorised Gmail
      account and its encrypted token survive;
    * no external API is called, so no real Gmail message can be affected.
    """
    from app.scheduler.service import scheduler
    from app.services import reset_service

    result = reset_service.reset_local_records()
    result["counts"] = reset_service.local_record_counts()
    # Forget the observation throttle too, so real activity is re-observed on
    # the next scheduler tick instead of after a full poll interval. A reset
    # that then leaves the Activity page empty for a minute reads as broken.
    scheduler.reset_poll_state()
    return result


@router.get("/reset/preview")
def reset_preview() -> Dict[str, Any]:
    """What a reset would remove, without removing anything."""
    from app.services import reset_service

    return {"counts": reset_service.local_record_counts()}
