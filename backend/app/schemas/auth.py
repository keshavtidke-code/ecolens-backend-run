"""Auth-related Pydantic schemas.

Registration, login and token management are handled by Supabase Auth on the
client side.  This module only contains schemas for the profile endpoints
(/auth/me GET / PATCH / DELETE).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)


class UserOut(ORMModel):
    id: uuid.UUID
    display_name: Optional[str]
    created_at: datetime
