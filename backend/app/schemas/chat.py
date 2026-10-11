import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import ChatRole
from app.schemas.common import ORMModel

MAX_MESSAGE_CHARS = 1000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ChatMessageCreate(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="The user's question about this observation.",
        examples=["Why are these leaves turning yellow?"],
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_content_key(cls, data: Any) -> Any:
        # The first chat stub used `content`; keep accepting it so existing clients keep working.
        if isinstance(data, dict) and "message" not in data and "content" in data:
            return {"message": data["content"]}
        return data

    @field_validator("message", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = _CONTROL.sub("", value).replace("\r\n", "\n").strip()
        if not value:
            raise ValueError("Message must not be blank.")
        if not any(ch.isalnum() for ch in value):
            raise ValueError("Message must contain at least one letter or number.")
        return value


class ChatMessageOut(ORMModel):
    id: int
    role: ChatRole
    content: str
    created_at: datetime


class ChatMeta(BaseModel):
    model: str
    prompt_version: str
    attempts: int
    tokens_used: Optional[int] = None
    latency_ms: int


class ChatExchangeOut(BaseModel):
    thread_id: int
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    meta: ChatMeta


class ChatHistoryOut(BaseModel):
    thread_id: Optional[int]
    messages: list[ChatMessageOut]
