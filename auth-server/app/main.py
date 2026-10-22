"""Recruit Desk auth service: invite-only accounts with two roles, admin and user."""
from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .mail import Mailer
from .models import Base
from .ratelimit import FailureLimiter
from .routers import admin, auth, records
from .security import Passwords, Tokens


def engine_options(s: Settings) -> dict:
    """Engine arguments for the configured database.

    Postgres (Neon): Neon's pooled endpoint is a transaction-mode pooler, where a statement the driver prepared on one server
    connection may be missing on the next, so psycopg's automatic prepared statements are switched off; a short connect timeout
    keeps a request from hanging while a sleeping Neon compute wakes up. Serverless hosts get no pool of their own."""
    opts: dict = {"pool_pre_ping": True}
    if s.database_url.startswith("sqlite"):
        opts["connect_args"] = {"check_same_thread": False}
    else:
        opts["connect_args"] = {"prepare_threshold": None, "connect_timeout": 10}
    if s.serverless:
        opts["poolclass"] = NullPool
    return opts


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    app = FastAPI(title="Recruit Desk auth", version="0.1.0")
    # Serverless: a pool would hold connections that the host freezes; Neon's own pooler does the pooling.
    engine = create_engine(s.database_url, **engine_options(s))
    if s.auto_create_tables:
        Base.metadata.create_all(engine)   # first version: no migrations yet; add Alembic before the first schema change
    cols = {c["name"] for c in inspect(engine).get_columns("users")} if inspect(engine).has_table("users") else None
    if cols is not None and "tenant_id" not in cols:
        raise RuntimeError("This database was created before tenants existed. It cannot be upgraded in place: use a new database "
                           "(nothing was deployed with the old layout), then create the first tenant with "
                           "`uv run python cli.py create-tenant \"<name>\" <admin email>`.")
    app.state.settings = s
    app.state.engine = engine
    app.state.sessions = sessionmaker(engine, expire_on_commit=False)
    app.state.passwords = Passwords(s)
    app.state.tokens = Tokens(s)
    app.state.limiter = FailureLimiter(app.state.sessions, s.failure_window_seconds)
    app.state.mailer = Mailer(s)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(records.router)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
