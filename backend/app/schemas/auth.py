from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.db.models import AuthPersistence, AuthStatus
from app.schemas.base import CamelModel


class AuthStatusResponse(CamelModel):
    status: AuthStatus
    logged_in: bool
    premium: bool
    membership_type: str = "none"
    masked_account_name: str | None = None
    last_validated_at: datetime | None = None
    cookie_expires_at: datetime | None = None
    persistence: AuthPersistence | None = None
    has_credentials: bool


class AuthUploadMetadata(CamelModel):
    persistence: AuthPersistence = Field(default=AuthPersistence.SESSION)
