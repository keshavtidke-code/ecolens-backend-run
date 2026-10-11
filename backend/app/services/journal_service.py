from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.journal import JournalEntry
from app.models.user import User
from app.repositories.journal import JournalRepository
from app.repositories.observation import ObservationRepository
from app.schemas.journal import JournalEntryCreate, JournalEntryUpdate


class JournalService:
    def __init__(self, db: Session) -> None:
        self.journal = JournalRepository(db)
        self.observations = ObservationRepository(db)

    def create(self, user: User, data: JournalEntryCreate) -> JournalEntry:
        observation = self.observations.get_for_user(data.observation_id, user.id)
        if observation is None:
            raise NotFoundError("Observation not found.", code="observation_not_found")
        if self.journal.get_by_observation(observation.id):
            raise ConflictError(
                "This observation is already in your journal.", code="journal_entry_exists"
            )
        entry = JournalEntry(
            user_id=user.id,
            observation_id=observation.id,
            title=data.title,
            personal_notes=data.personal_notes,
            is_favorite=data.is_favorite,
        )
        try:
            self.journal.add(entry)
        except IntegrityError:
            self.journal.db.rollback()
            raise ConflictError(
                "This observation is already in your journal.", code="journal_entry_exists"
            ) from None
        entry.observation = observation
        return entry

    def get(self, user: User, entry_id: int) -> JournalEntry:
        entry = self.journal.get_for_user(entry_id, user.id)
        if entry is None:
            raise NotFoundError("Journal entry not found.", code="journal_entry_not_found")
        return entry

    def list(
        self, user: User, *, limit: int, cursor: Optional[int], is_favorite: Optional[bool]
    ) -> tuple[list[JournalEntry], Optional[int]]:
        return self.journal.list_for_user(
            user.id, limit=limit, cursor=cursor, is_favorite=is_favorite
        )

    def update(self, user: User, entry_id: int, data: JournalEntryUpdate) -> JournalEntry:
        entry = self.get(user, entry_id)
        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            if field in ("title", "is_favorite") and value is None:
                continue  # these columns are not nullable
            setattr(entry, field, value)
        self.journal.save()
        return entry

    def delete(self, user: User, entry_id: int) -> None:
        self.journal.delete(self.get(user, entry_id))
