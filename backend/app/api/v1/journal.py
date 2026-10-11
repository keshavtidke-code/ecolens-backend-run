from typing import Annotated, Optional

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, JournalServiceDep
from app.schemas.common import Page
from app.schemas.journal import JournalEntryCreate, JournalEntryOut, JournalEntryUpdate

router = APIRouter(prefix="/journal", tags=["journal"])


@router.post("", response_model=JournalEntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(
    data: JournalEntryCreate, user: CurrentUser, service: JournalServiceDep
) -> JournalEntryOut:
    return JournalEntryOut.model_validate(service.create(user, data))


@router.get("", response_model=Page[JournalEntryOut])
def list_entries(
    user: CurrentUser,
    service: JournalServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Optional[int] = None,
    is_favorite: Optional[bool] = None,
) -> Page[JournalEntryOut]:
    items, next_cursor = service.list(user, limit=limit, cursor=cursor, is_favorite=is_favorite)
    return Page[JournalEntryOut](
        items=[JournalEntryOut.model_validate(e) for e in items], next_cursor=next_cursor
    )


@router.get("/{entry_id}", response_model=JournalEntryOut)
def get_entry(entry_id: int, user: CurrentUser, service: JournalServiceDep) -> JournalEntryOut:
    return JournalEntryOut.model_validate(service.get(user, entry_id))


@router.patch("/{entry_id}", response_model=JournalEntryOut)
def update_entry(
    entry_id: int, data: JournalEntryUpdate, user: CurrentUser, service: JournalServiceDep
) -> JournalEntryOut:
    return JournalEntryOut.model_validate(service.update(user, entry_id, data))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: int, user: CurrentUser, service: JournalServiceDep) -> Response:
    service.delete(user, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
