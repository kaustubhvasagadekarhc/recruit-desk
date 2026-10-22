"""Password hashing, one-time tokens and signed access tokens."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta, timezone
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import Settings
from .models import User, utcnow


def make_hasher(s: Settings) -> PasswordHasher:
    """argon2id."""
    return PasswordHasher(time_cost=s.argon2_time_cost, memory_cost=s.argon2_memory_kib)


class Passwords:
    def __init__(self, s: Settings):
        self.hasher = make_hasher(s)
        # Verified against when the email is unknown, so a missing account costs the same time as a wrong password.
        self._dummy = self.hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify(self, stored: str | None, password: str) -> bool:
        try:
            return self.hasher.verify(stored or self._dummy, password) and stored is not None
        except (VerifyMismatchError, InvalidHashError):
            return False

    def needs_rehash(self, stored: str) -> bool:
        return self.hasher.check_needs_rehash(stored)


def new_token() -> tuple[str, str]:
    """(token to give out once, sha256 to store)."""
    raw = secrets.token_urlsafe(32)
    return raw, token_hash(raw)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_keypair(directory: Path) -> tuple[Path, Path]:
    """Write an Ed25519 key pair (private key readable by the owner only). Returns (private path, public path)."""
    directory.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    priv, pub = directory / "jwt_private.pem", directory / "jwt_public.pem"
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    priv.chmod(0o600)
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv, pub


class Tokens:
    """Signed access tokens (EdDSA). The app verifies them offline with the public key."""

    def __init__(self, s: Settings):
        self.s = s
        if s.jwt_private_key is not None:
            pem = s.jwt_private_key.get_secret_value().replace("\\n", "\n").encode()
        else:
            pem = Path(s.jwt_private_key_path).read_bytes()
        self.private = serialization.load_pem_private_key(pem, password=None)
        self.public = self.private.public_key()
        self.public_pem = self.public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def access(self, user: User) -> tuple[str, int, int]:
        """(token, seconds valid, offline-grace deadline as a unix time)."""
        now = utcnow()
        exp = now + timedelta(minutes=self.s.access_token_minutes)
        grace = now + timedelta(days=self.s.offline_grace_days)
        epoch = lambda d: int(d.replace(tzinfo=timezone.utc).timestamp())  # noqa: E731
        token = jwt.encode({"sub": str(user.id), "email": user.email, "role": user.role, "tid": user.tenant_id, "tenant": user.tenant.name, "iat": epoch(now), "exp": epoch(exp),
                            "grace": epoch(grace), "iss": self.s.jwt_issuer, "aud": self.s.jwt_audience}, self.private, algorithm="EdDSA")
        return token, self.s.access_token_minutes * 60, epoch(grace)

    def decode(self, token: str) -> dict:
        return jwt.decode(token, self.public, algorithms=["EdDSA"], audience=self.s.jwt_audience, issuer=self.s.jwt_issuer,
                          options={"require": ["exp", "iat", "sub", "aud", "iss"]})
