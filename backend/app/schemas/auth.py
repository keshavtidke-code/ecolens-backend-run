from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel


def _check_password(value: str) -> str:
    if len(value.encode("utf-8")) > 72:  # bcrypt limit
        raise ValueError("Password must be at most 72 bytes.")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=100)

    _password = field_validator("password")(_check_password)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime, seconds


class UserOut(ORMModel):
    id: int
    email: Optional[str]
    display_name: Optional[str]
    is_anonymous: bool
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
    tokens: TokenPair
