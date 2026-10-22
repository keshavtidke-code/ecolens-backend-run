"""Dependency-injection wiring: sessions, settings, storage, current user, services."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.ai.client import AIClient, get_gemini_client
from app.core.exceptions import UnauthorizedError
from app.core.rate_limit import SlidingWindowRateLimiter, get_chat_rate_limiter
from app.core.security import user_id_from_payload, verify_supabase_token
from app.core.settings import Settings, get_settings
from app.core.utils import utcnow
from app.db.session import get_db
from app.integrations.geocoding import GeocodingProvider
from app.integrations.providers import get_geocoding_provider, get_weather_provider
from app.integrations.weather import WeatherProvider
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.analysis_service import AnalysisService
from app.services.auth_service import AuthService
from app.services.capture_service import CaptureService
from app.services.chat_service import ChatService
from app.services.context_service import ContextService
from app.services.journal_service import JournalService
from app.services.learning_service import LearningService
from app.storage.local import LocalFileStorage

bearer_scheme = HTTPBearer(auto_error=False, description="Supabase JWT access token")

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_storage(settings: SettingsDep) -> LocalFileStorage:
    return LocalFileStorage(settings.storage_dir)


StorageDep = Annotated[LocalFileStorage, Depends(get_storage)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    """Verify the Supabase JWT and return (or auto-create) the local profile row.

    Supabase Auth issues the token; we only verify it locally using the
    SUPABASE_SECRET_KEY (HS256). No network call is needed on each request.
    On the very first authenticated request the user's profile row is created.
    """
    if credentials is None:
        raise UnauthorizedError("Missing bearer token.", code="missing_token")

    payload = verify_supabase_token(credentials.credentials)
    user_id = user_id_from_payload(payload)

    repo = UserRepository(db)
    user, created = repo.get_or_create(user_id)
    if created or user.last_active_at is None:
        user.last_active_at = utcnow()
        repo.save()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_auth_service(db: DbSession, storage: StorageDep) -> AuthService:
    return AuthService(db, storage)


def get_context_service(
    geocoder: Annotated[GeocodingProvider, Depends(get_geocoding_provider)],
    weather: Annotated[WeatherProvider, Depends(get_weather_provider)],
) -> ContextService:
    return ContextService(geocoder, weather)


ContextServiceDep = Annotated[ContextService, Depends(get_context_service)]


def get_capture_service(
    db: DbSession, storage: StorageDep, settings: SettingsDep, context: ContextServiceDep
) -> CaptureService:
    return CaptureService(db, storage, settings, context)


def get_journal_service(db: DbSession) -> JournalService:
    return JournalService(db)


def get_chat_service(
    db: DbSession,
    settings: SettingsDep,
    ai_client: Annotated[Optional[AIClient], Depends(get_gemini_client)],
    rate_limiter: Annotated[SlidingWindowRateLimiter, Depends(get_chat_rate_limiter)],
) -> ChatService:
    return ChatService(db, settings, ai_client, rate_limiter)


def get_analysis_service(
    db: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
    ai_client: Annotated[Optional[AIClient], Depends(get_gemini_client)],
) -> AnalysisService:
    return AnalysisService(db, storage, settings, ai_client)


def get_learning_service(
    db: DbSession,
    settings: SettingsDep,
    ai_client: Annotated[Optional[AIClient], Depends(get_gemini_client)],
) -> LearningService:
    return LearningService(db, settings, ai_client)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CaptureServiceDep = Annotated[CaptureService, Depends(get_capture_service)]
JournalServiceDep = Annotated[JournalService, Depends(get_journal_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
LearningServiceDep = Annotated[LearningService, Depends(get_learning_service)]
