"""Auth endpoints — profile management only.

Authentication (sign-up, sign-in, token refresh, anonymous sessions) is
handled entirely by the Supabase Auth client SDK on mobile/web.
These endpoints only manage the local app-level profile row for an already-
authenticated Supabase user.
"""
from fastapi import APIRouter, Response, status

from app.api.deps import AuthServiceDep, CurrentUser
from app.schemas.auth import UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get current user profile",
)
def read_me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's app-level profile.

    The Supabase access token must be sent as a Bearer token in the
    Authorization header.  Email / auth metadata is managed by Supabase Auth.
    """
    return UserOut.model_validate(user)


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Update current user profile",
)
def update_me(data: UserUpdate, user: CurrentUser, service: AuthServiceDep) -> UserOut:
    """Update mutable profile fields (e.g. display_name)."""
    return UserOut.model_validate(service.update_profile(user, data))


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete account",
)
def delete_me(user: CurrentUser, service: AuthServiceDep) -> Response:
    """Permanently delete the account, all observations and stored photos.

    This removes app-side data only.  The Supabase Auth user record must be
    deleted separately via the Supabase Admin API.
    """
    service.delete_account(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
