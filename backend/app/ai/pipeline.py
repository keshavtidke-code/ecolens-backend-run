"""Perception stage orchestration: prompt -> Gemini -> Pydantic validation, with one corrective retry."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.ai.client import AIClient, ImagePart, Turn
from app.ai.learning_schemas import (
    LearningExperienceResult,
    LearningParseError,
    learning_response_schema,
    parse_learning_experience,
)
from app.ai.prompts import lesson_v1
from app.ai.prompts.perception_v1 import PROMPT_VERSION, SYSTEM_PROMPT, PerceptionContext, build_user_prompt
from app.ai.schemas import PerceptionParseError, PerceptionResult, gemini_response_schema, parse_perception
from app.core.exceptions import BadGatewayError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # first try + one retry when the JSON is invalid
_MAX_ECHO_CHARS = 4000


@dataclass(frozen=True)
class PerceptionOutcome:
    result: PerceptionResult
    model: str
    prompt_version: str
    attempts: int
    total_tokens: Optional[int]
    latency_ms: int


def season_context(latitude: float, at: datetime) -> str:
    month = at.strftime("%B")
    if abs(latitude) <= 23.5:
        return f"{month}, tropical zone"
    northern = latitude > 0
    m = at.month
    if m in (12, 1, 2):
        season = "winter" if northern else "summer"
    elif m in (3, 4, 5):
        season = "spring" if northern else "autumn"
    elif m in (6, 7, 8):
        season = "summer" if northern else "winter"
    else:
        season = "autumn" if northern else "spring"
    return f"{month}, {season} ({'northern' if northern else 'southern'} hemisphere)"


def _correction(reason: str) -> str:
    return (
        f"Your previous reply could not be used: {reason}. "
        "Reply again with ONLY a single valid JSON object that matches the schema exactly: "
        "every field present, confidence values between 0.0 and 1.0, and enum fields using the allowed values. "
        "No markdown, no commentary."
    )


def run_perception(
    client: AIClient, *, image_bytes: bytes, mime_type: str, context: PerceptionContext
) -> PerceptionOutcome:
    schema = gemini_response_schema()
    turns = [Turn("user", build_user_prompt(context), ImagePart(image_bytes, mime_type))]
    started = time.perf_counter()
    total_tokens: Optional[int] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = client.generate_json(system_prompt=SYSTEM_PROMPT, turns=turns, response_schema=schema)
        if reply.total_tokens is not None:
            total_tokens = (total_tokens or 0) + reply.total_tokens
        try:
            result = parse_perception(reply.text)
        except PerceptionParseError as exc:
            logger.warning(
                "perception attempt %d/%d returned invalid output: %s (finish=%s)",
                attempt, MAX_ATTEMPTS, exc.reason, reply.finish_reason,
            )
            logger.debug("invalid perception output (truncated): %r", (reply.text or "")[:300])
            if attempt == MAX_ATTEMPTS:
                raise BadGatewayError(
                    "The AI returned an invalid analysis. Please try again.",
                    code="ai_invalid_output",
                    details={"reason": exc.reason},
                ) from None
            turns = [
                *turns,
                Turn("model", (reply.text or "(no output)")[:_MAX_ECHO_CHARS]),
                Turn("user", _correction(exc.reason)),
            ]
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "perception ok on attempt %d: ecosystem=%r confidence=%.2f tokens=%s latency=%d ms",
            attempt, result.ecosystem_type, result.confidence, total_tokens, latency_ms,
        )
        return PerceptionOutcome(
            result=result,
            model=client.model,
            prompt_version=PROMPT_VERSION,
            attempts=attempt,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class LearningOutcome:
    result: LearningExperienceResult
    model: str
    prompt_version: str
    attempts: int
    total_tokens: Optional[int]
    latency_ms: int


def _learning_correction(reason: str) -> str:
    return (
        f"Your previous reply could not be used: {reason}. "
        "Reply again with ONLY a single valid JSON object that matches the schema exactly, with every field "
        "present. Keep the lesson text to 130-230 words, use the allowed enum values, and follow every safety "
        "rule: the challenge and action must never ask the user to touch or capture animals, taste plants, "
        "enter water, climb, enter restricted areas, handle garbage or chemicals, damage plants or step into "
        "traffic. No markdown, no commentary."
    )


def run_learning_experience(client: AIClient, *, context: lesson_v1.LessonContext) -> LearningOutcome:
    """Stage 2: perception + place + weather + season -> lesson, challenge and action.

    Text-only call. Validates with Pydantic (including the safety guardrails) and retries once with the
    validation error fed back to the model.
    """
    schema = learning_response_schema()
    turns = [Turn("user", lesson_v1.build_lesson_prompt(context))]
    started = time.perf_counter()
    total_tokens: Optional[int] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = client.generate_json(system_prompt=lesson_v1.SYSTEM_PROMPT, turns=turns, response_schema=schema)
        if reply.total_tokens is not None:
            total_tokens = (total_tokens or 0) + reply.total_tokens
        try:
            result = parse_learning_experience(reply.text)
        except LearningParseError as exc:
            logger.warning(
                "learning attempt %d/%d returned invalid output: %s (finish=%s)",
                attempt, MAX_ATTEMPTS, exc.reason, reply.finish_reason,
            )
            logger.debug("invalid learning output (truncated): %r", (reply.text or "")[:300])
            if attempt == MAX_ATTEMPTS:
                raise BadGatewayError(
                    "The AI returned an invalid lesson. Please try again.",
                    code="ai_invalid_output",
                    details={"reason": exc.reason},
                ) from None
            turns = [
                *turns,
                Turn("model", (reply.text or "(no output)")[:_MAX_ECHO_CHARS]),
                Turn("user", _learning_correction(exc.reason)),
            ]
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "learning ok on attempt %d: lesson=%r challenge=%r action=%s/%s cautious=%s tokens=%s latency=%d ms",
            attempt, result.lesson.title, result.challenge.title, result.recommended_action.category,
            result.recommended_action.impact_level, context.cautious, total_tokens, latency_ms,
        )
        return LearningOutcome(
            result=result,
            model=client.model,
            prompt_version=lesson_v1.PROMPT_VERSION,
            attempts=attempt,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )
    raise AssertionError("unreachable")  # pragma: no cover
