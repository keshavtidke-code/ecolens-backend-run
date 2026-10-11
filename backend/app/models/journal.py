from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.observation import Observation
    from app.models.user import User


class JournalEntry(TimestampMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_entries_user_id_updated_at", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    personal_notes: Mapped[Optional[str]] = mapped_column(Text)
    is_favorite: Mapped[bool] = mapped_column(default=False, server_default=false())

    user: Mapped["User"] = relationship(back_populates="journal_entries")
    observation: Mapped["Observation"] = relationship(back_populates="journal_entry")
