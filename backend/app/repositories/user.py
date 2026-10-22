"""User repository — thin DB access layer for Supabase-auth users.

Users are identified by their Supabase Auth UUID.  Email / password lookup
is handled entirely by Supabase and is not exposed here.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: uuid.UUID) -> Optional[User]:
        """Fetch by Supabase Auth UUID."""
        return self.db.get(User, user_id)

    def get_or_create(self, user_id: uuid.UUID) -> tuple[User, bool]:
        """Return (user, created).  Creates a stub row on first login."""
        user = self.get(user_id)
        if user is not None:
            return user, False
        user = User(id=user_id)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user, True

    def save(self) -> None:
        self.db.commit()

    def delete(self, user: User) -> None:
        self.db.delete(user)
        self.db.commit()
