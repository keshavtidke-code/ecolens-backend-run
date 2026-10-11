from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.core.utils import utcnow
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import RegisterRequest, TokenPair, UserUpdate
from app.storage.local import LocalFileStorage


class AuthService:
    def __init__(self, db: Session, storage: LocalFileStorage) -> None:
        self.users = UserRepository(db)
        self.storage = storage

    @staticmethod
    def issue_tokens(user: User) -> TokenPair:
        access, expires_in = create_token(user.id, "access")
        refresh, _ = create_token(user.id, "refresh")
        return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def register(self, data: RegisterRequest) -> tuple[User, TokenPair]:
        if self.users.get_by_email(data.email):
            raise ConflictError("An account with this email already exists.", code="email_taken")
        user = User(
            email=data.email,
            password_hash=hash_password(data.password),
            display_name=data.display_name,
            last_active_at=utcnow(),
        )
        try:
            self.users.add(user)
        except IntegrityError:
            self.users.db.rollback()
            raise ConflictError("An account with this email already exists.", code="email_taken") from None
        return user, self.issue_tokens(user)

    def login(self, email: str, password: str) -> tuple[User, TokenPair]:
        user = self.users.get_by_email(email)
        # verify_password runs a dummy hash when the user is missing (uniform timing).
        valid = verify_password(password, user.password_hash if user else None)
        if not user or not valid or not user.is_active:
            raise UnauthorizedError("Incorrect email or password.", code="invalid_credentials")
        user.last_active_at = utcnow()
        self.users.save()
        return user, self.issue_tokens(user)

    def create_anonymous(self) -> tuple[User, TokenPair]:
        user = User(is_anonymous=True, last_active_at=utcnow())
        self.users.add(user)
        return user, self.issue_tokens(user)

    def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, "refresh")
        user = self.users.get(int(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("Invalid or expired token.", code="invalid_token")
        user.last_active_at = utcnow()
        self.users.save()
        return self.issue_tokens(user)

    def update_profile(self, user: User, data: UserUpdate) -> User:
        if "display_name" in data.model_fields_set:
            user.display_name = data.display_name
        self.users.save()
        return user

    def delete_account(self, user: User) -> None:
        """Remove the user, all their records (FK cascade) and their stored photos."""
        self.storage.delete_tree(f"observations/{user.id}")
        self.users.delete(user)
