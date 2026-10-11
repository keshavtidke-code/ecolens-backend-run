from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.journal import JournalEntry


class JournalRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, entry: JournalEntry) -> JournalEntry:
        self.db.add(entry)
        self.db.commit()
        return entry

    def save(self) -> None:
        self.db.commit()

    def delete(self, entry: JournalEntry) -> None:
        self.db.delete(entry)
        self.db.commit()

    def get_for_user(self, entry_id: int, user_id: int) -> Optional[JournalEntry]:
        stmt = (
            select(JournalEntry)
            .where(JournalEntry.id == entry_id, JournalEntry.user_id == user_id)
            .options(selectinload(JournalEntry.observation))
        )
        return self.db.scalars(stmt).first()

    def get_by_observation(self, observation_id: int) -> Optional[JournalEntry]:
        stmt = select(JournalEntry).where(JournalEntry.observation_id == observation_id)
        return self.db.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: Optional[int] = None,
        is_favorite: Optional[bool] = None,
    ) -> tuple[list[JournalEntry], Optional[int]]:
        stmt = (
            select(JournalEntry)
            .where(JournalEntry.user_id == user_id)
            .options(selectinload(JournalEntry.observation))
        )
        if is_favorite is not None:
            stmt = stmt.where(JournalEntry.is_favorite == is_favorite)
        if cursor:
            stmt = stmt.where(JournalEntry.id < cursor)
        rows = list(self.db.scalars(stmt.order_by(JournalEntry.id.desc()).limit(limit + 1)))
        items = rows[:limit]
        next_cursor = items[-1].id if len(rows) > limit else None
        return items, next_cursor
