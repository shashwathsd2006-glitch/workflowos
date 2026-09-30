"""WorkFlowOS FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.api.errors import register_exception_handlers
from app.api.routes_health import router as health_router
from app.config import settings
from app.database import init_db

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("workflowos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info(
        "%s started (env=%s, db=%s)",
        settings.app_name,
        settings.environment,
        settings.database_path,
    )

    # Phase 8 background runtime. Each component is independently gated so a
    # test or a constrained environment can run with the scheduler off.
    from app.scheduler.service import scheduler
    from app.scheduler.worker import worker

    if settings.scheduler_enabled:
        scheduler.start()
    else:
        logger.info("Scheduler disabled by configuration")
    if settings.worker_enabled:
        # Requeue jobs stranded 'running' by a previous process before draining.
        try:
            recovered = worker.recover_and_start()
            if recovered:
                logger.warning("Requeued %d stale job(s) after restart", recovered)
        except Exception:  # noqa: BLE001 - startup must not be blocked
            logger.exception("Stale job recovery failed")
        worker.start()
    else:
        logger.info("Background worker disabled by configuration")

    _log_gmail_oauth_setup()

    try:
        yield
    finally:
        scheduler.stop()
        worker.stop()
        logger.info("%s shutting down", settings.app_name)



def _log_gmail_oauth_setup() -> None:
    """Print the exact Gmail OAuth wiring at startup.

    A failed connection is almost always a mismatch between the redirect URI
    registered in the Google Cloud Console and the one sent here, so it is
    logged loudly and in copy-pasteable form rather than left to be inferred.
    """
    from app.config import google_credentials_path
    from app.integrations.google_oauth import DEFAULT_REDIRECT_URI, redirect_uri

    uri = redirect_uri()
    path = google_credentials_path()
    logger.info("Gmail OAuth: redirect URI = %s", uri)
    logger.info(
        "Gmail OAuth: register that exact string under the OAuth client's "
        "'Authorized redirect URIs' in the Google Cloud Console",
    )
    if path is None:
        logger.warning(
            "Gmail OAuth: no credentials file found. Expected "
            "backend/credentials/credentials.json or "
            "backend/credentials/google-client-secret.json"
        )
    else:
        logger.info("Gmail OAuth: credentials file = %s", path.name)
    if uri != DEFAULT_REDIRECT_URI:
        logger.warning(
            "Gmail OAuth: GOOGLE_REDIRECT_URI overrides the default and is "
            "set to %s. It must match the Cloud Console exactly.",
            uri,
        )


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        description="AI-powered workflow automation that learns from how you work.",
        version=settings.api_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
    )
