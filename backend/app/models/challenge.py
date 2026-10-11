from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.enums import ChallengeStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.observation import Observation


class Challenge(TimestampMixin, Base):
    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    checklist_json: Mapped[Optional[list]] = mapped_column(JSON)
    estimated_minutes: Mapped[Optional[int]]
    success_question: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ChallengeStatus.open.value)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    observation: Mapped["Observation"] = relationship(back_populates="challenges")
