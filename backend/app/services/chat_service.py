"""Contextual Ask-AI chat: observation + analysis + lesson + challenge + action + weather -> Gemini."""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.chat_pipeline import ChatOutcome, ChatRunConfig, run_chat
from app.ai.client import AIClient
from app.ai.pipeline import season_context
from app.ai.prompts.chat_v1 import build_context_package
from app.ai.schemas import PerceptionResult
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.settings import Settings
from app.integrations.weather import MEASUREMENT_FIELDS
from app.models.chat import ChatMessage, ChatThread
from app.models.enums import ObservationStatus
from app.models.observation import Observation
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.repositories.observation import ObservationRepository
from app.services.chat_suggestions import build_suggestions
from app.services.learning_service import is_low_confidence

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        ai_client: Optional[AIClient],
        rate_limiter: SlidingWindowRateLimiter,
    ) -> None:
        self.chats = ChatRepository(db)
        self.observations = ObservationRepository(db)
        self.settings = settings
        self.ai_client = ai_client
        self.rate_limiter = rate_limiter

    # ---- lookups ----
    def _observation(self, user: User, observation_id: int) -> Observation:
        observation = self.observations.get_for_user(observation_id, user.id, detail=True)
        if observation is None:
            raise NotFoundError("Observation not found.", code="observation_not_found")
        return observation

    def _thread_for_write(self, observation: Observation, user: User) -> ChatThread:
        thread = self.chats.get_thread(observation.id)
        if thread is not None:
            return thread
        try:
            return self.chats.create_thread(observation.id, user.id)
        except IntegrityError:  # concurrent first message created it
            self.chats.db.rollback()
            thread = self.chats.get_thread(observation.id)
            assert thread is not None
            return thread

    def _perception(self, observation: Observation) -> tuple[PerceptionResult, Any]:
        """The chat needs a finished photo analysis; the lesson stage is optional."""
        if observation.status == ObservationStatus.analyzing.value:
            raise ConflictError(
                "The photo analysis is still running. Try again when it has finished.",
                code="analysis_in_progress",
            )
        analysis = observation.analysis
        if analysis is None:
            raise ConflictError(
                f"This observation has not been analyzed yet. Call POST /observations/{observation.id}/analyze first.",
                code="analysis_required",
            )
        try:
            return PerceptionResult.model_validate(analysis.perception_json or {}), analysis
        except ValidationError:
            raise ConflictError(
                "The stored photo analysis is incomplete. Re-run the analysis.", code="analysis_invalid"
            ) from None

    # ---- context ----
    def build_context(self, observation: Observation) -> dict[str, Any]:
        perception, analysis = self._perception(observation)
        snapshot = observation.weather_snapshot
        weather = {name: getattr(snapshot, name) for name in MEASUREMENT_FIELDS} if snapshot else None
        lesson = observation.lesson
        challenge = observation.challenges[-1] if observation.challenges else None
        action = observation.recommended_action
        low = is_low_confidence(perception, self.settings.low_confidence_threshold)
        return build_context_package(
            perception=perception.model_dump(mode="json"),
            analysis_confidence="low" if low else "ok",
            season=analysis.season_context or season_context(observation.latitude, observation.captured_at),
            place={
                "place_name": observation.place_name,
                "region": observation.region,
                "country": observation.country,
            },
            weather=weather,
            lesson=(
                {"title": lesson.title, **(lesson.content_json or {}), "key_concepts": lesson.key_concepts or []}
                if lesson
                else None
            ),
            challenge=(
                {
                    "title": challenge.title,
                    "description": challenge.description,
                    "instructions": challenge.checklist_json or [],
                    "success_question": challenge.success_question,
                    "status": challenge.status,
                }
                if challenge
                else None
            ),
            action=(
                {
                    "title": action.title,
                    "description": action.description,
                    "category": action.category,
                    "impact_level": action.impact_level,
                    "status": action.status,
                }
                if action
                else None
            ),
            user_note=observation.user_note,
        )

    # ---- public API ----
    def history(self, user: User, observation_id: int) -> tuple[Optional[int], list[ChatMessage]]:
        observation = self._observation(user, observation_id)
        thread = self.chats.get_thread(observation.id)
        if thread is None:
            return None, []
        return thread.id, self.chats.list_messages(thread.id)

    def send_message(
        self, user: User, observation_id: int, message: str
    ) -> tuple[int, ChatMessage, ChatMessage, ChatOutcome]:
        observation = self._observation(user, observation_id)
        context = self.build_context(observation)  # 409 if the analysis is missing / running / unusable
        if self.ai_client is None:
            raise ServiceUnavailableError("AI chat is not configured on this server.", code="ai_not_configured")

        s = self.settings
        self.rate_limiter.check(
            user.id,
            limits=[(s.chat_rate_limit_per_minute, 60), (s.chat_rate_limit_per_hour, 3600)],
            what="chat messages",
        )

        thread = self._thread_for_write(observation, user)
        previous = self.chats.recent_messages(thread.id, s.chat_history_messages)
        logger.info(
            "chat started: observation=%s user=%s history=%d question_chars=%d",
            observation.id, user.id, len(previous), len(message),
        )
        outcome = run_chat(
            self.ai_client,
            context_package=context,
            history=[(m.role, m.content) for m in previous],
            question=message,
            config=ChatRunConfig(
                max_attempts=s.chat_max_attempts,
                backoff_s=s.chat_retry_backoff_s,
                request_timeout_s=s.chat_request_timeout_s,
                total_timeout_s=s.chat_total_timeout_s,
            ),
        )  # raises AppError on failure; nothing has been stored yet
        user_message, assistant_message = self.chats.add_exchange(
            thread.id, message, outcome.answer, outcome.total_tokens
        )
        return thread.id, user_message, assistant_message, outcome

    def suggestions(self, user: User, observation_id: int) -> list[str]:
        observation = self._observation(user, observation_id)
        perception, _ = self._perception(observation)
        context = self.build_context(observation)
        thread = self.chats.get_thread(observation.id)
        asked = [m.content for m in self.chats.list_messages(thread.id) if m.role == "user"] if thread else []
        return build_suggestions(
            ecosystem=perception.ecosystem_type,
            perception=perception.model_dump(mode="json"),
            weather=observation.weather_snapshot and {
                name: getattr(observation.weather_snapshot, name) for name in MEASUREMENT_FIELDS
            },
            challenge_title=context["challenge"].get("title"),
            action_title=context["action"].get("title"),
            low_confidence=context["ecosystem"]["analysis_confidence"] == "low",
            already_asked=asked,
        )
