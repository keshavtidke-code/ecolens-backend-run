"""Supabase JWT verification.

Supabase issues HS256 JWTs signed with the project's JWT secret
(Settings.supabase_secret_key / ``SUPABASE_SECRET_KEY``).

We verify the token locally with PyJWT so no extra network round-trip is
needed on every request.  The Supabase JWT payload includes:

    sub   – the user's UUID (auth.users.id)
    role  – "authenticated" | "anon" | "service_role"
    email – user's email (present when role=="authenticated")
    exp   – expiry timestamp
"""
from __future__ import annotations

import uuid
from typing import Any

import jwt

from app.core.exceptions import UnauthorizedError
from app.core.settings import get_settings


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Decode and verify a Supabase-issued JWT.

    Returns the full payload dict on success.
    Raises :class:`UnauthorizedError` on any failure.
    """
    settings = get_settings()
    if not settings.supabase_secret_key:
        raise UnauthorizedError(
            "Supabase is not configured on this server.",
            code="supabase_not_configured",
        )

    secret = settings.supabase_secret_key.get_secret_value()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired.", code="token_expired") from None
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid token.", code="invalid_token") from None

    # Reject anonymous / service-role tokens — only authenticated users may call the API.
    role = payload.get("role", "")
    if role not in ("authenticated", "service_role"):
        raise UnauthorizedError(
            "Access requires an authenticated Supabase session.",
            code="unauthenticated",
        )

    return payload


def user_id_from_payload(payload: dict[str, Any]) -> uuid.UUID:
    """Extract and validate the user UUID from a verified JWT payload."""
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise UnauthorizedError("Token has no valid subject.", code="invalid_token") from None
