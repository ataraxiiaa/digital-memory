"""Public authenticated-user response; no tokens or internal metadata."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class AuthUser(BaseModel):
    id: UUID
    email: str | None = None
    role: Literal["authenticated"]
    email_confirmed_at: datetime | None = None
