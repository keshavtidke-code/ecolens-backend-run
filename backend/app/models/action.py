from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.enums import ActionStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.observation import Observation


class RecommendedAction(TimestampMixin, Base):
    """One simple real-world environmental action suggested for an observation."""

    __tablename__ = "recommended_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(40))
    impact_level: Mapped[Optional[str]] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default=ActionStatus.open.value)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    observation: Mapped["Observation"] = relationship(back_populates="recommended_actions")
