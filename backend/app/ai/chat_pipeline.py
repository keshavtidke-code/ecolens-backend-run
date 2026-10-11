"""Chat orchestration: context + history + question -> Gemini -> validated answer.

Resilience (all bounded by one overall deadline):
- transient provider failures (timeout, 429, 5xx, network) are retried with exponential backoff + jitter;
- each attempt gets its own timeout, never more than the time left before the deadline;
- an invalid / unsafe answer gets ONE corrective retry with the problem fed back to the model;
- auth, model-not-found and safety-block errors are never retried.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.ai.chat_schemas import ChatParseError, chat_response_schema, parse_chat_answer
from app.ai.client import AIClient, Turn
from app.ai.prompts import chat_v1
from app.ai.safety import find_unsafe_phrases
from app.core.exceptions import AppError, BadGatewayError, BadRequestError, GatewayTimeoutError

logger = logging.getLogger(__name__)

RETRYABLE_CODES = {"ai_provider_timeout", "ai_provider_rate_limited", "ai_provider_error"}
_MAX_ECHO_CHARS = 2000
_MIN_ATTEMPT_S = 1.0

_sleep = time.sleep  # tests replace this


@dataclass(frozen=True)
class ChatRunConfig:
    max_attempts: int = 3
    backoff_s: float = 0.5
    request_timeout_s: float = 25.0
    total_timeout_s: float = 50.0


@dataclass(frozen=True)
class ChatOutcome:
    answer: str
    model: str
    prompt_version: str
    attempts: int  # Gemini calls made
    total_tokens: Optional[int]
    latency_ms: int


def history_to_turns(history: Sequence[tuple[str, str]]) -> list[Turn]:
    """(role, text) pairs with role 'user' | 'assistant' -> Gemini turns that start with a user turn and alternate."""
    turns: list[Turn] = []
    for role, text in history:
        gemini_role = "model" if role == "assistant" else "user"
        if not turns and gemini_role == "model":
            continue  # the window may start mid-exchange; Gemini history must begin with the user
        if turns and turns[-1].role == gemini_role:
            turns[-1] = Turn(gemini_role, f"{turns[-1].text}\n\n{text}")
            continue
        turns.append(Turn(gemini_role, text))
    if turns and turns[-1].role == "user":
        turns.pop()  # a dangling user message (no reply) would clash with the new question
    return turns


def _format_correction(reason: str) -> str:
    return (
        f"Your previous reply could not be used: {reason}. Reply again with ONLY a JSON object "
        '{"answer": "..."} containing a plain-text answer to the same question, and follow every safety rule.'
    )


def run_chat(
    client: AIClient,
    *,
    context_package: dict[str, Any],
    history: Sequence[tuple[str, str]],
    question: str,
    config: ChatRunConfig = ChatRunConfig(),
) -> ChatOutcome:
    system_prompt = chat_v1.build_system_instruction(context_package)
    schema = chat_response_schema()
    turns = [*history_to_turns(history), Turn("user", question)]

    started = time.monotonic()
    deadline = started + config.total_timeout_s
    calls = 0
    provider_failures = 0
    corrected = False
    total_tokens: Optional[int] = None

    while True:
        remaining = deadline - time.monotonic()
        if calls > 0 and remaining < _MIN_ATTEMPT_S:
            raise GatewayTimeoutError("The AI service timed out.", code="ai_provider_timeout")
        timeout = max(_MIN_ATTEMPT_S, min(config.request_timeout_s, remaining))
        calls += 1
        try:
            reply = client.generate_json(
                system_prompt=system_prompt, turns=turns, response_schema=schema, timeout_s=timeout
            )
        except BadRequestError as exc:
            if exc.code == "analysis_blocked":  # the client's generic safety-block code
                raise BadRequestError(
                    "The AI could not answer that question. Try rephrasing it.",
                    code="chat_blocked",
                    details=exc.details,
                ) from None
            raise
        except AppError as exc:
            if exc.code not in RETRYABLE_CODES:
                raise
            provider_failures += 1
            delay = config.backoff_s * (2 ** (provider_failures - 1)) * random.uniform(1.0, 1.25)
            if provider_failures >= config.max_attempts or time.monotonic() + delay >= deadline:
                logger.warning("chat gave up after %d call(s): %s", calls, exc.code)
                raise
            logger.info("chat retry %d after %s (sleeping %.2fs)", provider_failures, exc.code, delay)
            _sleep(delay)
            continue

        if reply.total_tokens is not None:
            total_tokens = (total_tokens or 0) + reply.total_tokens

        problem: Optional[str] = None
        answer_text = ""
        try:
            answer_text = parse_chat_answer(reply.text).answer
            unsafe = find_unsafe_phrases(answer_text)
            if unsafe:
                problem = "it contained unsafe advice (" + "; ".join(unsafe[:2]) + ")"
        except ChatParseError as exc:
            problem = exc.reason

        if problem is None:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "chat ok: calls=%d tokens=%s latency=%d ms chars=%d", calls, total_tokens, latency_ms, len(answer_text)
            )
            return ChatOutcome(answer_text, client.model, chat_v1.PROMPT_VERSION, calls, total_tokens, latency_ms)

        logger.warning("chat attempt returned an unusable answer: %s (finish=%s)", problem[:200], reply.finish_reason)
        if corrected:
            raise BadGatewayError(
                "The AI returned an unusable answer. Please try again.",
                code="ai_invalid_output",
                details={"reason": problem[:300]},
            )
        corrected = True
        turns = [
            *turns,
            Turn("model", (reply.text or "(no output)")[:_MAX_ECHO_CHARS]),
            Turn("user", _format_correction(problem)),
        ]
