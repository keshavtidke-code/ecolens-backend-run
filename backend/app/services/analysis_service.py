"""Perception stage service: observation + weather + location + photo -> stored Analysis."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.ai.client import AIClient
from app.ai.pipeline import run_perception, season_context
from app.ai.prompts.perception_v1 import PerceptionContext
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.settings import Settings
from app.core.utils import utcnow
from app.integrations.weather import MEASUREMENT_FIELDS
from app.models.analysis import Analysis
from app.models.enums import ObservationStatus
from app.models.observation import Observation
from app.models.user import User
from app.repositories.analysis import AnalysisRepository
from app.repositories.observation import ObservationRepository
from app.storage.images import sniff_image_type
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(
        self,
        db: Session,
        storage: LocalFileStorage,
        settings: Settings,
        ai_client: Optional[AIClient],
    ) -> None:
        self.observations = ObservationRepository(db)
        self.analyses = AnalysisRepository(db)
        self.storage = storage
        self.settings = settings
        self.ai_client = ai_client

    def analyze(self, user: User, observation_id: int) -> Analysis:
        observation = self.observations.get_for_user(observation_id, user.id, detail=True)
        if observation is None:
            raise NotFoundError("Observation not found.", code="observation_not_found")
        if self.ai_client is None:
            raise ServiceUnavailableError(
                "AI analysis is not configured on this server.", code="ai_not_configured"
            )
        image_bytes, mime_type = self._load_photo(observation)

        # Atomic claim: concurrent requests must not run (and bill) the same analysis twice.
        stale_before = utcnow() - timedelta(seconds=self.settings.analysis_stale_after_s)
        if not self.observations.claim_for_analysis(observation.id, stale_before):
            raise ConflictError(
                "An analysis for this observation is already in progress.", code="analysis_in_progress"
            )
        self.observations.db.refresh(observation)

        context = self._build_context(observation)
        logger.info(
            "analysis started: observation=%s user=%s location=%s weather=%s photo_bytes=%d",
            observation.id, user.id,
            "yes" if observation.place_name else "no",
            "yes" if observation.weather_snapshot else "no",
            len(image_bytes),
        )
        try:
            outcome = run_perception(
                self.ai_client, image_bytes=image_bytes, mime_type=mime_type, context=context
            )
            analysis = Analysis(
                observation_id=observation.id,
                model_name=outcome.model,
                prompt_version=outcome.prompt_version,
                habitat_type=outcome.result.ecosystem_type,
                habitat_confidence=outcome.result.ecosystem_confidence,
                summary=None,  # lesson stage's job; perception only stores structured data
                perception_json=outcome.result.model_dump(mode="json"),
                season_context=context.season_context,
                tokens_used=outcome.total_tokens,
                latency_ms=outcome.latency_ms,
            )
            self.analyses.save_result(observation, analysis)
        except Exception as exc:
            self._mark_failed(observation, exc)
            raise
        logger.info(
            "analysis stored: id=%s observation=%s ecosystem=%r attempts=%d tokens=%s latency=%d ms",
            analysis.id, observation.id, analysis.habitat_type, outcome.attempts,
            analysis.tokens_used, analysis.latency_ms,
        )
        return analysis

    # ---- helpers ----
    def _load_photo(self, observation: Observation) -> tuple[bytes, str]:
        try:
            path = self.storage.path_for(observation.photo_path) if observation.photo_path else None
            data = path.read_bytes() if path else None
        except (OSError, ValueError):
            data = None
        if not data:
            raise NotFoundError("This observation's photo is not available.", code="photo_not_found")
        mime = observation.photo_mime_type or sniff_image_type(data[:16]) or "image/jpeg"
        return data, mime

    @staticmethod
    def _build_context(observation: Observation) -> PerceptionContext:
        snapshot = observation.weather_snapshot
        weather = {name: getattr(snapshot, name) for name in MEASUREMENT_FIELDS} if snapshot else None
        return PerceptionContext(
            place_name=observation.place_name,
            region=observation.region,
            country=observation.country,
            # ~11 km precision is enough for climate/season interpretation; exact GPS stays on our server.
            approx_latitude=round(observation.latitude, 1),
            approx_longitude=round(observation.longitude, 1),
            captured_at=observation.captured_at,
            season_context=season_context(observation.latitude, observation.captured_at),
            weather=weather,
            user_note=observation.user_note,
        )

    def _mark_failed(self, observation: Observation, exc: Exception) -> None:
        code = getattr(exc, "code", "internal_error")
        message = getattr(exc, "message", "Unexpected error")
        logger.warning("analysis failed: observation=%s code=%s", observation.id, code)
        try:
            self.observations.db.rollback()
            observation.status = ObservationStatus.failed.value
            observation.failure_reason = f"{code}: {message}"[:500]
            self.observations.save()
        except Exception:  # never mask the original error
            logger.exception("could not record analysis failure for observation %s", observation.id)
