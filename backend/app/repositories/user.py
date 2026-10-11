from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: int) -> Optional[User]:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        return self.db.scalars(select(User).where(User.email == email)).first()

    def add(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        return user

    def save(self) -> None:
        self.db.commit()

    def delete(self, user: User) -> None:
        self.db.delete(user)
        self.db.commit()
