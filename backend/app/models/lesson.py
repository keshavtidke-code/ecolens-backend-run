from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.observation import Observation


class Lesson(TimestampMixin, Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    analysis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("analyses.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    difficulty_level: Mapped[Optional[str]] = mapped_column(String(30))
    content_json: Mapped[Optional[dict]] = mapped_column(JSON)  # ordered lesson cards
    key_concepts: Mapped[Optional[list]] = mapped_column(JSON)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50))
    model_name: Mapped[Optional[str]] = mapped_column(String(100))

    observation: Mapped["Observation"] = relationship(back_populates="lessons")
    analysis: Mapped[Optional["Analysis"]] = relationship(back_populates="lessons")
