"""Central API router.

Feature routers are mounted here under /api so that new modules (events,
sequences, workflows, runs, analytics) slot in without touching main.py.
Health endpoints are mounted at the root by design.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes_activity import router as activity_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_automations import router as automations_router
from app.api.routes_ai import router as ai_router
from app.api.routes_drafts import router as drafts_router
from app.api.routes_executions import router as executions_router
from app.api.routes_integrations import router as integrations_router
from app.api.routes_scheduler import router as scheduler_router
from app.api.routes_system import router as system_router
from app.api.routes_workflows import router as workflows_router

api_router = APIRouter(prefix="/api")
api_router.include_router(activity_router)
api_router.include_router(workflows_router)
api_router.include_router(ai_router)
api_router.include_router(drafts_router)
api_router.include_router(executions_router)
api_router.include_router(automations_router)
api_router.include_router(analytics_router)
api_router.include_router(system_router)
api_router.include_router(integrations_router)
api_router.include_router(scheduler_router)
