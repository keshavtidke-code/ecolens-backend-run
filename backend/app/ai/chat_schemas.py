"""Chat reply: Pydantic validation + the matching Gemini response schema."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_ANSWER_CHARS = 3000


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)

    @field_validator("answer", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(r"\n{3,}", "\n\n", value.strip())
        return value


class ChatParseError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_chat_answer(raw: str | None) -> ChatAnswer:
    text = (raw or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text:
        raise ChatParseError("the reply was empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChatParseError(f"the reply was not valid JSON ({exc.msg} at position {exc.pos})") from None
    if not isinstance(data, dict):
        raise ChatParseError("the top-level JSON value must be an object")
    try:
        return ChatAnswer.model_validate(data)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()[:4]]
        raise ChatParseError("schema validation failed - " + "; ".join(problems)[:400]) from None


def chat_response_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "answer": {"type": "STRING", "description": "Plain-text reply, usually 80-180 words."}
        },
        "required": ["answer"],
        "propertyOrdering": ["answer"],
    }
