from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatThread


class ChatRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_thread(self, observation_id: int) -> Optional[ChatThread]:
        stmt = select(ChatThread).where(ChatThread.observation_id == observation_id)
        return self.db.scalars(stmt).first()

    def create_thread(self, observation_id: int, user_id: int) -> ChatThread:
        thread = ChatThread(observation_id=observation_id, user_id=user_id)
        self.db.add(thread)
        self.db.commit()
        return thread

    def add_message(
        self, thread_id: int, role: str, content: str, tokens_used: Optional[int] = None
    ) -> ChatMessage:
        message = ChatMessage(thread_id=thread_id, role=role, content=content, tokens_used=tokens_used)
        self.db.add(message)
        self.db.commit()
        return message

    def list_messages(self, thread_id: int) -> list[ChatMessage]:
        stmt = select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)
        return list(self.db.scalars(stmt))

    def add_exchange(
        self, thread_id: int, user_content: str, assistant_content: str, tokens_used: Optional[int] = None
    ) -> tuple[ChatMessage, ChatMessage]:
        """Store the question and its answer in ONE transaction, so history never holds a half exchange."""
        user_message = ChatMessage(thread_id=thread_id, role="user", content=user_content)
        self.db.add(user_message)
        self.db.flush()  # user message gets the lower id
        assistant_message = ChatMessage(
            thread_id=thread_id, role="assistant", content=assistant_content, tokens_used=tokens_used
        )
        self.db.add(assistant_message)
        self.db.commit()
        return user_message, assistant_message

    def recent_messages(self, thread_id: int, limit: int) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id.desc()).limit(limit)
        )
        return list(reversed(list(self.db.scalars(stmt))))
