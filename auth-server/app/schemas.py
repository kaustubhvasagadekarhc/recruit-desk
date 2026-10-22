from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Role = Literal["admin", "user"]


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TenantOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: int
    tenant: TenantOut
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    offline_grace_until: int      # unix time: the app may work without the server until then
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=256)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = "user"


class InviteOut(BaseModel):
    email: str
    role: Role
    expires_at: datetime
    invite_url: str
    email_sent: bool


class InviteRow(BaseModel):
    id: int
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class ResetLinkOut(BaseModel):
    reset_url: str
    expires_at: datetime
    email_sent: bool
