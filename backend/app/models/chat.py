from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.observation import Observation
    from app.models.user import User


class ChatThread(TimestampMixin, Base):
    """One conversation per observation."""

    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    observation: Mapped["Observation"] = relationship(back_populates="chat_thread")
    user: Mapped["User"] = relationship(back_populates="chat_threads")
    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="thread",
        order_by="ChatMessage.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread_id_created_at", "thread_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[Optional[int]]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    thread: Mapped["ChatThread"] = relationship(back_populates="messages")
