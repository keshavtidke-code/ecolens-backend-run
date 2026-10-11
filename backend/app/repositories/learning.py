from sqlalchemy.orm import Session

from app.models.action import RecommendedAction
from app.models.challenge import Challenge
from app.models.lesson import Lesson


class LearningRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save_experience(
        self, lesson: Lesson, challenge: Challenge, action: RecommendedAction
    ) -> None:
        """Persist all three records in one transaction (all or nothing)."""
        self.db.add_all([lesson, challenge, action])
        self.db.commit()
