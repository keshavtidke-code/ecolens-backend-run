from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.enums import ObservationStatus, PhotoSource
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.challenge import Challenge
    from app.models.action import RecommendedAction
    from app.models.chat import ChatThread
    from app.models.journal import JournalEntry
    from app.models.lesson import Lesson
    from app.models.user import User
    from app.models.weather import WeatherSnapshot

_CHILD = {"cascade": "all, delete-orphan", "passive_deletes": True}


class Observation(TimestampMixin, Base):
    """One photo capture with its location. The hub every other record hangs off."""

    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_observations_user_id_captured_at", "user_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    location_accuracy_m: Mapped[Optional[float]]
    place_name: Mapped[Optional[str]] = mapped_column(String(255))
    region: Mapped[Optional[str]] = mapped_column(String(120))
    country: Mapped[Optional[str]] = mapped_column(String(120))

    photo_path: Mapped[Optional[str]] = mapped_column(String(500))
    photo_mime_type: Mapped[Optional[str]] = mapped_column(String(50))
    photo_size_bytes: Mapped[Optional[int]]
    photo_source: Mapped[str] = mapped_column(String(20), default=PhotoSource.camera.value)

    status: Mapped[str] = mapped_column(
        String(20), default=ObservationStatus.pending.value, index=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    user_note: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="observations")
    weather_snapshot: Mapped[Optional["WeatherSnapshot"]] = relationship(
        back_populates="observation", uselist=False, **_CHILD
    )
    analyses: Mapped[List["Analysis"]] = relationship(
        back_populates="observation", order_by="Analysis.id", **_CHILD
    )
    lessons: Mapped[List["Lesson"]] = relationship(
        back_populates="observation", order_by="Lesson.id", **_CHILD
    )
    challenges: Mapped[List["Challenge"]] = relationship(
        back_populates="observation", order_by="Challenge.id", **_CHILD
    )
    recommended_actions: Mapped[List["RecommendedAction"]] = relationship(
        back_populates="observation", order_by="RecommendedAction.id", **_CHILD
    )
    journal_entry: Mapped[Optional["JournalEntry"]] = relationship(
        back_populates="observation", uselist=False, **_CHILD
    )
    chat_thread: Mapped[Optional["ChatThread"]] = relationship(
        back_populates="observation", uselist=False, **_CHILD
    )

    @property
    def analysis(self) -> Optional["Analysis"]:
        """Most recent analysis run for this observation."""
        return self.analyses[-1] if self.analyses else None

    @property
    def lesson(self) -> Optional["Lesson"]:
        """Most recent lesson for this observation."""
        return self.lessons[-1] if self.lessons else None

    @property
    def recommended_action(self) -> Optional["RecommendedAction"]:
        """Most recent recommended action for this observation."""
        return self.recommended_actions[-1] if self.recommended_actions else None
