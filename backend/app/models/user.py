"""User model — identity is fully owned by Supabase Auth.

The `id` column is a UUID that mirrors `auth.users.id` in Supabase.
We do NOT store passwords, email or auth state here; Supabase Auth owns all of that.
We keep only app-level profile data (display name, last-active timestamp).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.chat import ChatThread
    from app.models.journal import JournalEntry
    from app.models.observation import Observation


class User(TimestampMixin, Base):
    """One row per Supabase Auth user.  Created on first authenticated request."""

    __tablename__ = "users"

    # UUID primary key — mirrors auth.users.id in Supabase.
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
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
