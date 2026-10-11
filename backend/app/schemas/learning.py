from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.ai.learning_schemas import KnowledgeLevel
from app.models.action import RecommendedAction
from app.models.challenge import Challenge
from app.models.lesson import Lesson


class LearningExperienceRequest(BaseModel):
    """Optional personalisation. Everything has a default, so an empty body is fine."""

    knowledge_level: KnowledgeLevel = "beginner"
    interests: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("interests")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            text = " ".join(item.split())[:40]
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
        return out


class LessonContentOut(BaseModel):
    id: int
    title: str
    ecosystem_summary: str
    what_you_are_seeing: str
    why_it_matters: str
    weather_connection: str
    interesting_fact: str
    key_concepts: list[str]
    knowledge_level: Optional[str]
    created_at: datetime

    @classmethod
    def from_lesson(cls, lesson: Lesson) -> "LessonContentOut":
        content = lesson.content_json or {}
        return cls(
            id=lesson.id,
            title=lesson.title,
            ecosystem_summary=content.get("ecosystem_summary", ""),
            what_you_are_seeing=content.get("what_you_are_seeing", ""),
            why_it_matters=content.get("why_it_matters", ""),
            weather_connection=content.get("weather_connection", ""),
            interesting_fact=content.get("interesting_fact", ""),
            key_concepts=list(lesson.key_concepts or []),
            knowledge_level=lesson.difficulty_level,
            created_at=lesson.created_at,
        )


class ChallengeContentOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    instructions: list[str]
    estimated_minutes: Optional[int]
    success_question: Optional[str]
    status: str

    @classmethod
    def from_challenge(cls, challenge: Challenge) -> "ChallengeContentOut":
        return cls(
            id=challenge.id,
            title=challenge.title,
            description=challenge.description,
            instructions=list(challenge.checklist_json or []),
            estimated_minutes=challenge.estimated_minutes,
            success_question=challenge.success_question,
            status=challenge.status,
        )


class ActionContentOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    category: Optional[str]
    impact_level: Optional[str]
    status: str

    @classmethod
    def from_action(cls, action: RecommendedAction) -> "ActionContentOut":
        return cls(
            id=action.id,
            title=action.title,
            description=action.description,
            category=action.category,
            impact_level=action.impact_level,
            status=action.status,
        )


class LearningMeta(BaseModel):
    analysis_id: int
    prompt_version: Optional[str]
    model: Optional[str]
    cautious_mode: bool  # True when the photo analysis was low-confidence
    weather_available: bool
    reused: bool  # True when an existing result was returned instead of calling Gemini


class LearningExperienceOut(BaseModel):
    lesson: LessonContentOut
    challenge: ChallengeContentOut
    recommended_action: ActionContentOut
    meta: LearningMeta
