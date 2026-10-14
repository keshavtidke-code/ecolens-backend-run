import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, SettingsDep
from app.core.exceptions import ServiceUnavailableError

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
def liveness(settings: SettingsDep) -> dict:
    return {"status": "ok", "version": settings.app_version, "environment": settings.environment}


@router.get("/health/ready")
def readiness(db: DbSession) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        # Log the real exception so it appears in Render logs.
        # The password is already redacted in session.py; the URL is not echoed here.
        logger.error(
            "Database readiness check failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=False,  # stack trace is noisy; class+message is enough
        )
        raise ServiceUnavailableError("Database unavailable.", code="db_unavailable") from exc
    return {"status": "ready"}
