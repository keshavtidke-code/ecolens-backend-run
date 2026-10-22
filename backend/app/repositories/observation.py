import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.utils import utcnow
from app.models.enums import ObservationStatus
from app.models.observation import Observation


class ObservationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, observation: Observation) -> Observation:
        self.db.add(observation)
        self.db.commit()
        return observation

    def save(self) -> None:
        self.db.commit()

    def delete(self, observation: Observation) -> None:
        self.db.delete(observation)
        self.db.commit()

    def get_for_user(
        self, observation_id: int, user_id: uuid.UUID, *, detail: bool = False
    ) -> Optional[Observation]:
        stmt = select(Observation).where(
            Observation.id == observation_id, Observation.user_id == user_id
        )
        if detail:
            stmt = stmt.options(
                selectinload(Observation.weather_snapshot),
                selectinload(Observation.analyses),
                selectinload(Observation.lessons),
                selectinload(Observation.challenges),
                selectinload(Observation.recommended_actions),
            )
        return self.db.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        cursor: Optional[int] = None,
        status: Optional[str] = None,
        captured_from: Optional[datetime] = None,
        captured_to: Optional[datetime] = None,
    ) -> tuple[list[Observation], Optional[int]]:
        stmt = select(Observation).where(Observation.user_id == user_id)
        if status:
            stmt = stmt.where(Observation.status == status)
        if captured_from:
            stmt = stmt.where(Observation.captured_at >= captured_from)
        if captured_to:
            stmt = stmt.where(Observation.captured_at <= captured_to)
        if cursor:
            stmt = stmt.where(Observation.id < cursor)
        rows = list(self.db.scalars(stmt.order_by(Observation.id.desc()).limit(limit + 1)))
        items = rows[:limit]
        next_cursor = items[-1].id if len(rows) > limit else None
        return items, next_cursor

    def claim_for_analysis(self, observation_id: int, stale_before: datetime) -> bool:
        """Atomically flip status to `analyzing`.

        Returns False when another analysis is already running (status `analyzing` and
        updated more recently than `stale_before`), so concurrent requests cannot double-bill.
        """
        result = self.db.execute(
            update(Observation)
            .where(
                Observation.id == observation_id,
                or_(
                    Observation.status != ObservationStatus.analyzing.value,
                    Observation.updated_at < stale_before,
                ),
            )
            .values(
                status=ObservationStatus.analyzing.value, failure_reason=None, updated_at=utcnow()
            )
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return result.rowcount == 1
