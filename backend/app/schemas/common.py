from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """Cursor page. Pass `next_cursor` back as `cursor` to fetch the next page."""

    items: list[T]
    next_cursor: Optional[int] = None
