"""Settings, read from the environment or a .env file (never committed)."""
from __future__ import annotations

import os

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `neon deploy` writes the Neon variables to .env.local (or .env when that exists); a value in .env.local wins.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = "sqlite:///./auth.db"
    # Serverless hosts (Vercel) have no lasting disk and no memory shared between requests: use Postgres, keep no pool, and
    # do not create tables on every cold start (run `cli.py init-db` once).
    # Neon also gives a direct (non-pooled) address. Schema setup and other session-level work must use it, not the pooler.
    database_url_unpooled: str | None = None
    serverless: bool = Field(default_factory=lambda: bool(os.environ.get("VERCEL")))
    auto_create_tables: bool = True
    base_url: str = "http://localhost:8000"
    jwt_private_key_path: str = "keys/jwt_private.pem"
    # The private key as PEM text (a hosting env var), used instead of the file when set. "\n" sequences are turned into newlines.
    jwt_private_key: SecretStr | None = None
    jwt_issuer: str = "recruit-desk-auth"
    jwt_audience: str = "recruit-desk"

    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # How long the app may keep working without reaching the server. It is a signed claim in the access token, so the
    # app checks it offline with the public key and a user cannot extend it by editing a file.
    offline_grace_days: int = 7
    invite_days: int = 7
    reset_minutes: int = 60

    min_password_length: int = 10
    login_failures_per_email: int = 5
    login_failures_per_ip: int = 20
    failure_window_seconds: int = 900

    # Behind a reverse proxy every request seems to come from the proxy, which would put all users in one rate-limit bucket.
    # Name the header the proxy sets (Cloudflare: CF-Connecting-IP) ONLY when the server cannot be reached except through
    # that proxy (bind to 127.0.0.1 and use a Cloudflare Tunnel); otherwise anyone could send a fake one.
    trusted_ip_header: str | None = None
    # On Vercel the real address comes from vercel.functions.ip_address (documented by Vercel); turn this on there.
    use_vercel_ip: bool = False

    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "Recruit Desk <no-reply@localhost>"

    @field_validator("database_url", "database_url_unpooled")
    @classmethod
    def _postgres_driver(cls, v: str | None) -> str | None:
        """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg 3 driver named."""
        if v is None:
            return v
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v
