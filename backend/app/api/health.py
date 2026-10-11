from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, SettingsDep
from app.core.exceptions import ServiceUnavailableError

router = APIRouter(tags=["health"])


@router.get("/health")
def liveness(settings: SettingsDep) -> dict:
    return {"status": "ok", "version": settings.app_version, "environment": settings.environment}


@router.get("/health/ready")
def readiness(db: DbSession) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise ServiceUnavailableError("Database unavailable.", code="db_unavailable") from None
    return {"status": "ready"}
