"""Failed-login limiter kept in the database, so it works when requests land on different serverless instances
(an in-memory one would give every instance its own count)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from .models import LoginFailure, utcnow


class FailureLimiter:
    def __init__(self, sessions: sessionmaker, window_seconds: int):
        self.sessions = sessions
        self.window = timedelta(seconds=window_seconds)

    def blocked(self, key: str, limit: int) -> bool:
        with self.sessions() as db:
            n = db.scalar(select(func.count()).select_from(LoginFailure).where(LoginFailure.key == key, LoginFailure.at > utcnow() - self.window))
            return (n or 0) >= limit

    def record(self, key: str) -> None:
        with self.sessions() as db:
            db.execute(delete(LoginFailure).where(LoginFailure.key == key, LoginFailure.at <= utcnow() - self.window))   # keep the table small
            db.add(LoginFailure(key=key, at=utcnow()))
            db.commit()

    def clear(self, key: str) -> None:
        with self.sessions() as db:
            db.execute(delete(LoginFailure).where(LoginFailure.key == key))
            db.commit()

    def clear_all(self) -> None:
        with self.sessions() as db:
            db.execute(delete(LoginFailure))
            db.commit()
