from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.observation import ObservationOut


class JournalEntryCreate(BaseModel):
    observation_id: int
    title: str = Field(min_length=1, max_length=200)
    personal_notes: Optional[str] = Field(default=None, max_length=10_000)
    is_favorite: bool = False


class JournalEntryUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    personal_notes: Optional[str] = Field(default=None, max_length=10_000)
    is_favorite: Optional[bool] = None


class JournalEntryOut(ORMModel):
    id: int
    observation_id: int
    title: str
    personal_notes: Optional[str]
    is_favorite: bool
    created_at: datetime
    updated_at: datetime
    observation: ObservationOut
