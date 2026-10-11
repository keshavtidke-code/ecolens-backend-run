from datetime import datetime
from typing import Any, Optional

from pydantic import Field, computed_field

from app.core.settings import get_settings
from app.models.enums import ChallengeStatus, ObservationStatus, PhotoSource
from app.schemas.common import ORMModel
from app.schemas.weather import WeatherSnapshotOut


class AnalysisOut(ORMModel):
    id: int
    observation_id: int
    model_name: str
    prompt_version: str
    habitat_type: Optional[str]
    habitat_confidence: Optional[float]
    summary: Optional[str]
    perception_json: Optional[dict[str, Any]]
    season_context: Optional[str]
    created_at: datetime


class LessonOut(ORMModel):
    id: int
    title: str
    difficulty_level: Optional[str]
    content_json: Optional[dict[str, Any]]
    key_concepts: Optional[list[Any]]
    created_at: datetime


class ChallengeOut(ORMModel):
    id: int
    title: str
    description: Optional[str]
    checklist_json: Optional[list[Any]]
    estimated_minutes: Optional[int]
    success_question: Optional[str] = None
    status: ChallengeStatus
    completed_at: Optional[datetime]


class RecommendedActionOut(ORMModel):
    id: int
    title: str
    description: Optional[str]
    category: Optional[str]
    impact_level: Optional[str]
    status: str
    completed_at: Optional[datetime]


class ObservationOut(ORMModel):
    id: int
    captured_at: datetime
    latitude: float
    longitude: float
    location_accuracy_m: Optional[float]
    place_name: Optional[str]
    region: Optional[str]
    country: Optional[str]
    photo_source: PhotoSource
    status: ObservationStatus
    user_note: Optional[str]
    created_at: datetime
    updated_at: datetime
    photo_path: Optional[str] = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def photo_url(self) -> Optional[str]:
        if not self.photo_path:
            return None
        return f"{get_settings().api_v1_prefix}/observations/{self.id}/photo"


class ObservationDetail(ObservationOut):
    weather_snapshot: Optional[WeatherSnapshotOut] = None
    analysis: Optional[AnalysisOut] = None
    lesson: Optional[LessonOut] = None
    challenges: list[ChallengeOut] = []
    recommended_action: Optional[RecommendedActionOut] = None


class ObservationStatusOut(ORMModel):
    id: int
    status: ObservationStatus
    failure_reason: Optional[str]
