"""Auth service — profile management only.

Registration, login, token refresh and anonymous accounts are all handled by
Supabase Auth.  This service only manages the local app-level profile row
(display_name) and account deletion.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import UserUpdate
from app.storage.local import LocalFileStorage


class AuthService:
    def __init__(self, db: Session, storage: LocalFileStorage) -> None:
        self.users = UserRepository(db)
        self.storage = storage

    def update_profile(self, user: User, data: UserUpdate) -> User:
        if "display_name" in data.model_fields_set:
            user.display_name = data.display_name
        self.users.save()
        return user

    def delete_account(self, user: User) -> None:
        """Remove the local profile row, all their records (FK cascade) and stored photos.

        Note: This deletes only the app-side data.  To fully remove the user
        from Supabase Auth you must also call the Supabase Admin API
        (DELETE /auth/v1/admin/users/{uid}) from a trusted server context.
        """
        self.storage.delete_tree(f"observations/{user.id}")
        self.users.delete(user)
