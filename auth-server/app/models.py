"""Tables. Times are naive UTC, so SQLite and Postgres behave the same."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("admin", "user")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """An organisation. Its name is required and unique: `name_key` is the name folded so that spellings that differ only in case,
    spacing, punctuation or accents are the same tenant (the database refuses a second one)."""
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    name_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    tenant: Mapped[Tenant] = relationship(lazy="joined")
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)   # stored lower-case
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(10), default="user")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)   # sha256 of the token; the token itself is never stored
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)


class ResetToken(Base):
    __tablename__ = "reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)      # set when it is exchanged for a new one
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)   # logout, password change, account disabled


class LoginFailure(Base):
    """One failed login or reset request, for the rate limit. Keys are "email:<address>", "ip:<address>" or "forgot:<address>"."""
    __tablename__ = "login_failures"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(400), index=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class LastRecord(Base):
    """The latest record an assistant created, one row per assistant (the rest of the data stays on each person's computer)."""
    __tablename__ = "last_records"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)   # one row per assistant per tenant
    agent: Mapped[str] = mapped_column(String(20), primary_key=True)             # candidate | company | account
    record_json: Mapped[str] = mapped_column(String(4000))                       # whitelisted fields only; no contact details
    subject_hash: Mapped[str] = mapped_column(String(64))                        # sha256 of the record's key: lets an erasure remove exactly this record
    created_at: Mapped[datetime]                                                 # when the assistant created the record
    stored_at: Mapped[datetime] = mapped_column(default=utcnow)
    stored_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
