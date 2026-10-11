"""Stage 2 service: stored perception + place + weather + season -> lesson, challenge, action."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.client import AIClient
from app.ai.learning_schemas import KnowledgeLevel
from app.ai.pipeline import run_learning_experience, season_context
from app.ai.prompts.lesson_v1 import LessonContext
from app.ai.schemas import PerceptionResult
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.core.settings import Settings
from app.integrations.weather import MEASUREMENT_FIELDS
from app.models.action import RecommendedAction
from app.models.analysis import Analysis
from app.models.challenge import Challenge
from app.models.enums import ActionStatus, ChallengeStatus, ObservationStatus
from app.models.lesson import Lesson
from app.models.observation import Observation
from app.models.user import User
from app.repositories.learning import LearningRepository
from app.repositories.observation import ObservationRepository

logger = logging.getLogger(__name__)


@dataclass
class LearningExperience:
    lesson: Lesson
    challenge: Challenge
    action: RecommendedAction
    analysis: Analysis
    cautious: bool
    weather_available: bool
    reused: bool


def is_low_confidence(perception: PerceptionResult, threshold: float) -> bool:
    return (
        perception.confidence < threshold
        or perception.ecosystem_confidence < threshold
        or perception.ecosystem_type == "unclear"
    )


class LearningService:
    def __init__(self, db: Session, settings: Settings, ai_client: Optional[AIClient]) -> None:
        self.observations = ObservationRepository(db)
        self.learning = LearningRepository(db)
        self.settings = settings
        self.ai_client = ai_client

    def generate(
        self,
        user: User,
        observation_id: int,
        *,
        knowledge_level: KnowledgeLevel = "beginner",
        interests: Sequence[str] = (),
        regenerate: bool = False,
    ) -> LearningExperience:
        observation = self.observations.get_for_user(observation_id, user.id, detail=True)
        if observation is None:
            raise NotFoundError("Observation not found.", code="observation_not_found")

        analysis = observation.analysis
        if observation.status == ObservationStatus.analyzing.value:
            raise ConflictError(
                "The photo analysis is still running. Try again when it has finished.",
                code="analysis_in_progress",
            )
        if analysis is None:
            raise ConflictError(
                f"This observation has not been analyzed yet. Call POST /observations/{observation.id}/analyze first.",
                code="analysis_required",
            )
        try:
            perception = PerceptionResult.model_validate(analysis.perception_json or {})
        except ValidationError:
            logger.warning("stored perception_json is unusable: analysis=%s", analysis.id)
            raise ConflictError(
                "The stored photo analysis is incomplete. Re-run the analysis.", code="analysis_invalid"
            ) from None

        cautious = is_low_confidence(perception, self.settings.low_confidence_threshold)
        weather_available = observation.weather_snapshot is not None

        existing = self._reusable(observation, analysis, knowledge_level)
        if existing is not None and not regenerate:
            lesson, challenge, action = existing
            logger.info(
                "learning reused: observation=%s analysis=%s lesson=%s", observation.id, analysis.id, lesson.id
            )
            return LearningExperience(lesson, challenge, action, analysis, cautious, weather_available, True)

        if self.ai_client is None:
            raise ServiceUnavailableError(
                "AI generation is not configured on this server.", code="ai_not_configured"
            )

        context = self._build_context(observation, analysis, perception, cautious, knowledge_level, interests)
        logger.info(
            "learning started: observation=%s analysis=%s weather=%s cautious=%s level=%s regenerate=%s",
            observation.id, analysis.id, weather_available, cautious, knowledge_level, regenerate,
        )
        outcome = run_learning_experience(self.ai_client, context=context)  # raises AppError on failure

        r = outcome.result
        lesson = Lesson(
            observation_id=observation.id,
            analysis_id=analysis.id,
            title=r.lesson.title,
            difficulty_level=knowledge_level,
            content_json={
                "ecosystem_summary": r.lesson.ecosystem_summary,
                "what_you_are_seeing": r.lesson.what_you_are_seeing,
                "why_it_matters": r.lesson.why_it_matters,
                "weather_connection": r.lesson.weather_connection,
                "interesting_fact": r.lesson.interesting_fact,
            },
            key_concepts=list(r.lesson.key_concepts),
            prompt_version=outcome.prompt_version,
            model_name=outcome.model,
        )
        challenge = Challenge(
            observation_id=observation.id,
            title=r.challenge.title,
            description=r.challenge.description,
            checklist_json=list(r.challenge.instructions),
            estimated_minutes=r.challenge.estimated_minutes,
            success_question=r.challenge.success_question,
            status=ChallengeStatus.open.value,
        )
        action = RecommendedAction(
            observation_id=observation.id,
            title=r.recommended_action.title,
            description=r.recommended_action.description,
            category=r.recommended_action.category,
            impact_level=r.recommended_action.impact_level,
            status=ActionStatus.open.value,
        )
        self.learning.save_experience(lesson, challenge, action)
        logger.info(
            "learning stored: observation=%s lesson=%s challenge=%s action=%s attempts=%d tokens=%s",
            observation.id, lesson.id, challenge.id, action.id, outcome.attempts, outcome.total_tokens,
        )
        return LearningExperience(lesson, challenge, action, analysis, cautious, weather_available, False)

    # ---- helpers ----
    @staticmethod
    def _reusable(
        observation: Observation, analysis: Analysis, knowledge_level: str
    ) -> Optional[tuple[Lesson, Challenge, RecommendedAction]]:
        """The three records are written in one transaction, so the latest of each belong together."""
        lesson = observation.lesson
        challenge = observation.challenges[-1] if observation.challenges else None
        action = observation.recommended_action
        if (
            lesson is not None
            and challenge is not None
            and action is not None
            and lesson.analysis_id == analysis.id
            and lesson.difficulty_level == knowledge_level
        ):
            return lesson, challenge, action
        return None

    @staticmethod
    def _build_context(
        observation: Observation,
        analysis: Analysis,
        perception: PerceptionResult,
        cautious: bool,
        knowledge_level: KnowledgeLevel,
        interests: Sequence[str],
    ) -> LessonContext:
        snapshot = observation.weather_snapshot
        weather = {name: getattr(snapshot, name) for name in MEASUREMENT_FIELDS} if snapshot else None
        return LessonContext(
            place_name=observation.place_name,
            region=observation.region,
            country=observation.country,
            season_context=analysis.season_context or season_context(observation.latitude, observation.captured_at),
            weather=weather,
            perception=perception,
            cautious=cautious,
            knowledge_level=knowledge_level,
            interests=list(interests),
            user_note=observation.user_note,
        )
