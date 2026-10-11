from fastapi import APIRouter, Response, status

from app.api.deps import AuthServiceDep, CurrentUser
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_response(user, tokens) -> AuthResponse:
    return AuthResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, service: AuthServiceDep) -> AuthResponse:
    return _auth_response(*service.register(data))


@router.post("/login", response_model=AuthResponse)
def login(data: LoginRequest, service: AuthServiceDep) -> AuthResponse:
    return _auth_response(*service.login(data.email, data.password))


@router.post("/anonymous", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def anonymous(service: AuthServiceDep) -> AuthResponse:
    """Create a device-only account (no email/password)."""
    return _auth_response(*service.create_anonymous())


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    return service.refresh(data.refresh_token)


@router.get("/me", response_model=UserOut)
def read_me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
def update_me(data: UserUpdate, user: CurrentUser, service: AuthServiceDep) -> UserOut:
    return UserOut.model_validate(service.update_profile(user, data))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(user: CurrentUser, service: AuthServiceDep) -> Response:
    """Permanently delete the account, its data and stored photos."""
    service.delete_account(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
