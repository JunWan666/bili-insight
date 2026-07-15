from __future__ import annotations

import re
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.schemas.base import CamelModel

_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


class AppCredentials(CamelModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip()
        if not _USERNAME.fullmatch(normalized):
            raise ValueError("username contains unsupported characters")
        return normalized


class AppSetupRequest(AppCredentials):
    confirm_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> AppSetupRequest:
        if self.password != self.confirm_password:
            raise ValueError("password confirmation does not match")
        return self


class AppLoginRequest(AppCredentials):
    pass


class AppPasswordChangeRequest(CamelModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> AppPasswordChangeRequest:
        if self.new_password != self.confirm_password:
            raise ValueError("password confirmation does not match")
        if self.current_password == self.new_password:
            raise ValueError("new password must differ from current password")
        return self


class AppAuthStatusRead(CamelModel):
    initialized: bool
    authenticated: bool
    username: str | None = None
    csrf_token: str | None = None
    session_expires_at: datetime | None = None
