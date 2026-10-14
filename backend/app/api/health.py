import logging
import re

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, SettingsDep
from app.core.exceptions import ServiceUnavailableError
from app.db.session import _normalise_pg_url

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


def _redact(url: str) -> str:
    return re.sub(r"(:)[^:@]+(@)", r"\1***\2", url)


@router.get("/health")
def liveness(settings: SettingsDep) -> dict:
    return {"status": "ok", "version": settings.app_version, "environment": settings.environment}


@router.get("/health/ready")
def readiness(db: DbSession) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(
            "Database readiness check failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=False,
        )
        raise ServiceUnavailableError("Database unavailable.", code="db_unavailable") from exc
    return {"status": "ready"}


@router.get("/health/db-diag")
def db_diag(settings: SettingsDep, db: DbSession) -> dict:
    """
    Temporary diagnostic endpoint — returns sanitised DB info.
    Remove once the DB is confirmed working.
    """
    try:
        result = db.execute(text("SELECT current_database(), version()"))
        row = result.fetchone()
        db_name, pg_version = (row[0], row[1]) if row else ("?", "?")
        return {
            "db": "connected",
            "database": db_name,
            "pg_version": pg_version[:60],
            "url_scheme": _normalise_pg_url(settings.database_url).split("://")[0],
            "environment": settings.environment,
        }
    except Exception as exc:
        exc_str = str(exc)
        # Redact any password that might appear in psycopg2 error messages
        exc_str = re.sub(r"password=[^\s]+", "password=***", exc_str)
        exc_str = re.sub(r"(:)[^:@\s]+(@)", r"\1***\2", exc_str)
        return {
            "db": "failed",
            "error_type": type(exc).__name__,
            "error": exc_str[:400],
            "url_scheme": _normalise_pg_url(settings.database_url).split("://")[0],
            "environment": settings.environment,
        }
