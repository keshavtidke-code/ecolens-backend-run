from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.chat import ChatThread
    from app.models.journal import JournalEntry
    from app.models.observation import Observation


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    is_anonymous: Mapped[bool] = mapped_column(default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    last_active_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    observations: Mapped[List["Observation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    journal_entries: Mapped[List["JournalEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    chat_threads: Mapped[List["ChatThread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
