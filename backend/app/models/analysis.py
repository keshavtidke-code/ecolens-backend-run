from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.lesson import Lesson
    from app.models.observation import Observation


class Analysis(TimestampMixin, Base):
    """Output of the AI perception stage (populated once Gemini is wired in)."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    habitat_type: Mapped[Optional[str]] = mapped_column(String(100))
    habitat_confidence: Mapped[Optional[float]]
    summary: Mapped[Optional[str]] = mapped_column(Text)
    perception_json: Mapped[Optional[dict]] = mapped_column(JSON)
    season_context: Mapped[Optional[str]] = mapped_column(String(100))
    tokens_used: Mapped[Optional[int]]
    latency_ms: Mapped[Optional[int]]

    observation: Mapped["Observation"] = relationship(back_populates="analyses")
    lessons: Mapped[List["Lesson"]] = relationship(back_populates="analysis")
