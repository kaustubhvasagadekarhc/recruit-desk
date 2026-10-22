"""Logic shared by the routers."""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import RefreshToken, Tenant, User, utcnow
from .schemas import TokenOut, UserOut
from .security import new_token


def client_ip(request: Request) -> str:
    """The caller's address for rate limiting: the trusted proxy header when configured, else the direct peer."""
    settings = request.app.state.settings
    if settings.use_vercel_ip:
        try:
            from vercel.functions import ip_address
            ip = ip_address(request)
        except Exception:   # the helper is missing or changed: fall back to the other sources rather than fail every login
            ip = None
        if ip:
            return str(ip)
    header = settings.trusted_ip_header
    if header:
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def norm_email(email: str) -> str:
    return email.strip().lower()


def check_password_rules(request: Request, password: str, email: str = "") -> None:
    n = request.app.state.settings.min_password_length
    problems = []
    if len(password) < n:
        problems.append(f"at least {n} characters")
    if len(password) > 128:
        problems.append("at most 128 characters")
    if email and password.lower() == email.lower():
        problems.append("not the same as the email address")
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Password must be " + ", ".join(problems))


def issue_tokens(request: Request, db: Session, user: User) -> TokenOut:
    st = request.app.state
    access, expires_in, grace = st.tokens.access(user)
    raw, h = new_token()
    db.add(RefreshToken(user_id=user.id, token_hash=h, expires_at=utcnow() + timedelta(days=st.settings.refresh_token_days)))
    db.commit()
    return TokenOut(access_token=access, refresh_token=raw, expires_in=expires_in, offline_grace_until=grace,
                    user=UserOut.model_validate(user))


def revoke_all_sessions(db: Session, user_id: int) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


def active_admins(db: Session, tenant_id: int) -> int:
    """Active admins of one tenant: every tenant must keep at least one."""
    return len(db.scalars(select(User.id).where(User.tenant_id == tenant_id, User.role == "admin", User.is_active.is_(True))).all())


# ---------- tenants ----------

class TenantError(ValueError):
    """A tenant name or setup that cannot be accepted; the message is safe to show."""


def tenant_name(raw: str) -> tuple[str, str]:
    """(name to show, key for the duplicate check). The name is required: 2 to 80 characters with at least one letter or digit.

    The key folds the name so that these are all the same tenant: different case, extra or odd spaces, punctuation ("Acme, Inc." and
    "Acme Inc"), accents ("Café" and "Cafe") and width variants ("Ａcme"). Lookalike letters from other alphabets are not folded."""
    shown = " ".join(unicodedata.normalize("NFKC", raw or "").split())
    if len(shown) < 2:
        raise TenantError("The tenant needs a name of at least 2 characters.")
    if len(shown) > 80:
        raise TenantError("The tenant name can be at most 80 characters.")
    if any(unicodedata.category(c).startswith("C") for c in shown):
        raise TenantError("The tenant name cannot contain control characters.")
    folded = "".join(c for c in unicodedata.normalize("NFKD", shown.casefold()) if not unicodedata.combining(c))
    key = re.sub(r"[\W_]+", "", folded)
    if len(key) < 2:
        raise TenantError("The tenant name needs at least 2 letters or digits.")
    return shown, key[:80]


def create_tenant(db: Session, raw_name: str) -> Tenant:
    """A new tenant. A name that is already taken (in any spelling that folds to the same key) is refused, also when two requests race:
    the unique key in the database decides."""
    shown, key = tenant_name(raw_name)
    taken = TenantError("A tenant with that name already exists.")
    if db.scalar(select(Tenant.id).where(Tenant.name_key == key)) is not None:
        raise taken
    tenant = Tenant(name=shown, name_key=key)
    db.add(tenant)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise taken from None
    return tenant


def create_tenant_with_admin(db: Session, passwords, raw_name: str, email: str, password: str, min_length: int) -> User:
    """A tenant and its first admin together (the server operator's command): both or neither."""
    email = norm_email(email)
    if "@" not in email or any(c.isspace() for c in email):
        raise TenantError("Enter a valid email address.")
    if len(password) < min_length:
        raise TenantError(f"The password needs at least {min_length} characters.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise TenantError("An account with this email already exists.")
    tenant = create_tenant(db, raw_name)
    user = User(tenant_id=tenant.id, email=email, password_hash=passwords.hash(password), role="admin")
    db.add(user)
    db.commit()
    return user
