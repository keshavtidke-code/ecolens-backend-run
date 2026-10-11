"""Learning-experience output (lesson + challenge + action): strict Pydantic models + Gemini schema."""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from app.ai.safety import find_unsafe_phrases
from app.ai.schemas import _FENCE, _normalize_enum

ActionCategory = Literal[
    "conservation", "water", "biodiversity", "waste_reduction", "citizen_science", "awareness"
]
ImpactLevel = Literal["low", "medium", "high"]
KnowledgeLevel = Literal["beginner", "intermediate", "advanced"]

# ~45-90 s of reading is roughly 150-300 words; these bounds are a sanity check, the prompt asks for 130-230.
LESSON_MIN_WORDS = 60
LESSON_MAX_WORDS = 400


def _collapse(value: Any) -> Any:
    """Collapse runs of whitespace/newlines inside strings."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _clean_items(max_items: int):
    def clean(value: Any) -> Any:
        if not isinstance(value, list):
            return value
        out: list[Any] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                return value  # let Pydantic report the wrong type
            text = " ".join(item.split())
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
        return out[:max_items]

    return clean


def _text(max_length: int):
    return Annotated[str, BeforeValidator(_collapse), Field(min_length=1, max_length=max_length)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LessonResult(_Strict):
    title: _text(100)  # type: ignore[valid-type]
    ecosystem_summary: _text(500)  # type: ignore[valid-type]
    what_you_are_seeing: _text(700)  # type: ignore[valid-type]
    why_it_matters: _text(700)  # type: ignore[valid-type]
    weather_connection: _text(600)  # type: ignore[valid-type]
    interesting_fact: _text(500)  # type: ignore[valid-type]
    key_concepts: Annotated[
        list[Annotated[str, Field(max_length=60)]],
        BeforeValidator(_clean_items(5)),
        Field(min_length=2, max_length=5),
    ]

    @model_validator(mode="after")
    def _reading_length(self) -> "LessonResult":
        words = sum(
            len(getattr(self, f).split())
            for f in ("ecosystem_summary", "what_you_are_seeing", "why_it_matters", "weather_connection", "interesting_fact")
        )
        if not LESSON_MIN_WORDS <= words <= LESSON_MAX_WORDS:
            raise ValueError(
                f"lesson text is {words} words; it must be {LESSON_MIN_WORDS}-{LESSON_MAX_WORDS} (aim for 130-230)"
            )
        return self


class ChallengeResult(_Strict):
    title: _text(100)  # type: ignore[valid-type]
    description: _text(500)  # type: ignore[valid-type]
    instructions: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=250)]],
        BeforeValidator(_clean_items(6)),
        Field(min_length=2, max_length=6),
    ]
    estimated_minutes: int = Field(ge=1, le=15)
    success_question: _text(250)  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _safe(self) -> "ChallengeResult":
        problems = find_unsafe_phrases(
            "\n".join([self.title, self.description, *self.instructions, self.success_question])
        )
        if problems:
            raise ValueError("safety check failed - the challenge asks the user to do something unsafe: " + "; ".join(problems[:3]))
        return self


class RecommendedActionResult(_Strict):
    title: _text(100)  # type: ignore[valid-type]
    description: _text(500)  # type: ignore[valid-type]
    category: Annotated[ActionCategory, BeforeValidator(_normalize_enum)]
    impact_level: Annotated[ImpactLevel, BeforeValidator(_normalize_enum)]

    @model_validator(mode="after")
    def _safe(self) -> "RecommendedActionResult":
        problems = find_unsafe_phrases(f"{self.title}\n{self.description}")
        if problems:
            raise ValueError("safety check failed - the action asks the user to do something unsafe: " + "; ".join(problems[:3]))
        return self


class LearningExperienceResult(_Strict):
    lesson: LessonResult
    challenge: ChallengeResult
    recommended_action: RecommendedActionResult


class LearningParseError(ValueError):
    """Gemini's reply was not a usable LearningExperienceResult. `reason` is safe to show the model."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_learning_experience(raw: str | None) -> LearningExperienceResult:
    text = (raw or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text:
        raise LearningParseError("the reply was empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LearningParseError(f"the reply was not valid JSON ({exc.msg} at position {exc.pos})") from None
    if not isinstance(data, dict):
        raise LearningParseError("the top-level JSON value must be an object")
    try:
        return LearningExperienceResult.model_validate(data)
    except ValidationError as exc:
        problems = []
        for err in exc.errors()[:5]:
            where = ".".join(str(p) for p in err["loc"]) or "root"
            message = re.sub(r"^Value error, ", "", err["msg"])
            problems.append(f"{where}: {message}")
        raise LearningParseError("schema validation failed - " + "; ".join(problems)[:900]) from None


def learning_response_schema() -> dict[str, Any]:
    """Gemini `responseSchema` for LearningExperienceResult. Pydantic remains the source of truth."""

    def string(description: str) -> dict[str, Any]:
        return {"type": "STRING", "description": description}

    def strings(description: str) -> dict[str, Any]:
        return {"type": "ARRAY", "items": {"type": "STRING"}, "description": description}

    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": properties,
            "required": list(properties),
            "propertyOrdering": list(properties),
        }

    lesson = obj(
        {
            "title": string("Short friendly title, at most 8 words."),
            "ecosystem_summary": string("1-2 sentences naming the ecosystem and what defines it."),
            "what_you_are_seeing": string("2-3 sentences about the specific things visible in this photo."),
            "why_it_matters": string("2-3 sentences on why these features matter ecologically."),
            "weather_connection": string("2 sentences linking the weather and season to what is visible."),
            "interesting_fact": string("1-2 sentences, a well-established general fact tied to the evidence."),
            "key_concepts": strings("2-4 short lowercase concept names."),
        }
    )
    challenge = obj(
        {
            "title": string("Short challenge title."),
            "description": string("1-2 sentences saying what to observe and why."),
            "instructions": strings("3-5 short imperative steps, safe for anyone."),
            "estimated_minutes": {"type": "INTEGER", "description": "Whole minutes, 3-10."},
            "success_question": string("An open observation question with no wrong answer."),
        }
    )
    action = obj(
        {
            "title": string("Short action title."),
            "description": string("1-3 sentences: one simple, safe action and why it helps."),
            "category": {"type": "STRING", "enum": list(get_args(ActionCategory)), "description": "Action category."},
            "impact_level": {"type": "STRING", "enum": list(get_args(ImpactLevel)), "description": "Honest impact."},
        }
    )
    return obj({"lesson": lesson, "challenge": challenge, "recommended_action": action})
