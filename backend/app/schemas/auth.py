"""Request bodies for authentication.

Passwords are never logged, never echoed and never stored: each of these fields
exists to be read once, compared and discarded.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    #: Optional only for an account that has never had one, which is the account
    #: that predates authentication. Changing a set password always needs it.
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    role: str = Field(default="operator", max_length=32)
