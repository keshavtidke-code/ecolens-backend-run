from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.enums import ObservationStatus
from app.models.observation import Observation


class AnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def save_result(self, observation: Observation, analysis: Analysis) -> Analysis:
        """Store the analysis and mark the observation complete in one transaction."""
        self.db.add(analysis)
        observation.status = ObservationStatus.complete.value
        observation.failure_reason = None
        self.db.commit()
        return analysis
