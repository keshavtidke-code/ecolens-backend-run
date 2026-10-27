
"""Verify Supabase Auth JWTs using the project's public JWKS."""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.exceptions import UnauthorizedError
from app.core.settings import get_settings


@lru_cache(maxsize=1)
def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Reuse the JWKS client instead of rebuilding it on every request."""
    return PyJWKClient(jwks_url, cache_keys=True)


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Verify a Supabase Auth JWT and return its claims."""
    settings = get_settings()

    if not settings.supabase_url:
        raise UnauthorizedError(
            "Supabase is not configured on this server.",
            code="supabase_not_configured",
        )

    base_url = settings.supabase_url.rstrip("/")
    jwks_url = settings.supabase_jwks_url or (
        f"{base_url}/auth/v1/.well-known/jwks.json"
    )
    issuer = f"{base_url}/auth/v1"

    try:
        signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError(
            "Token has expired.", code="token_expired"
        ) from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise UnauthorizedError(
            "Invalid token.", code="invalid_token"
        ) from None
    except Exception:
        # Includes JWKS/network/key-fetch failures; never accept an unverified token.
        raise UnauthorizedError(
            "Unable to verify Supabase token.", code="token_verification_failed"
        ) from None

    if payload.get("role") != "authenticated":
        raise UnauthorizedError(
            "Access requires a Supabase authenticated-user session.",
            code="unauthenticated",
        )

    try:
        uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise UnauthorizedError(
            "Token has no valid subject.", code="invalid_token"
        ) from None

    return payload


def user_id_from_payload(payload: dict[str, Any]) -> uuid.UUID:
    """Extract the UUID from a verified JWT payload."""
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise UnauthorizedError(
            "Token has no valid subject.", code="invalid_token"
        ) from None
