from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Base, User
from app.security import generate_keypair

PASSWORD = "correct horse battery"


@pytest.fixture()
def settings(tmp_path):
    """SQLite by default. Set TEST_DATABASE_URL to a throwaway Postgres to run the same tests against it."""
    priv, _ = generate_keypair(tmp_path / "keys")
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'auth.db'}"
    return Settings(database_url=url, jwt_private_key_path=str(priv), base_url="https://auth.test",
                    argon2_time_cost=1, argon2_memory_kib=8192)


@pytest.fixture()
def app(settings):
    application = create_app(settings)
    if os.environ.get("TEST_DATABASE_URL"):   # a shared database: start every test from empty tables
        Base.metadata.drop_all(application.state.engine)
        Base.metadata.create_all(application.state.engine)
    yield application
    if os.environ.get("TEST_DATABASE_URL"):
        Base.metadata.drop_all(application.state.engine)
    application.state.engine.dispose()


@pytest.fixture()
def client(app):
    return TestClient(app)


def make_user(app, email: str, role: str = "user", password: str = PASSWORD, active: bool = True, tenant: str = "Test Tenant") -> int:
    """A user in a tenant (created on first use, found again by its folded name)."""
    from sqlalchemy import select
    from app.models import Tenant
    from app.services import create_tenant, tenant_name
    with app.state.sessions() as db:
        t = db.scalar(select(Tenant).where(Tenant.name_key == tenant_name(tenant)[1])) or create_tenant(db, tenant)
        u = User(tenant_id=t.id, email=email, password_hash=app.state.passwords.hash(password), role=role, is_active=active)
        db.add(u)
        db.commit()
        return u.id


def login(client, email: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture()
def admin(app, client):
    make_user(app, "admin@example.com", "admin")
    return login(client, "admin@example.com").json()
