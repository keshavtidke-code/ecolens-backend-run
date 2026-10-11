"""Password hashing and JWT helpers."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.exceptions import UnauthorizedError
from app.core.settings import get_settings
from app.core.utils import utcnow

TokenType = Literal["access", "refresh"]

# Used to keep login timing similar whether or not the email exists.
_DUMMY_HASH = bcrypt.hashpw(b"ecolens-dummy", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (password_hash or _DUMMY_HASH).encode("utf-8")) and (
            password_hash is not None
        )
    except ValueError:
        return False


def create_token(subject: str | int, token_type: TokenType) -> tuple[str, int]:
    """Return (encoded_token, lifetime_in_seconds)."""
    settings = get_settings()
    lifetime = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_expire_days)
    )
    now = utcnow()
    payload = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid or expired token.", code="invalid_token") from None
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise UnauthorizedError("Invalid token type.", code="invalid_token")
    return payload
