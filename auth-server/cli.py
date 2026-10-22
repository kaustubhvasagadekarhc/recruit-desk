"""Server-side admin tasks. Run from this folder:

uv run python cli.py gen-keys                                  # once: writes keys/jwt_private.pem and keys/jwt_public.pem
uv run python cli.py init-db                                   # create the tables (run once against a new database)
uv run python cli.py create-tenant "Acme Corp" boss@acme.com   # a new tenant and its first admin (asks for the password)
uv run python cli.py create-admin boss2@acme.com --tenant "Acme Corp"   # another admin of an existing tenant (recovery)
uv run python cli.py list-tenants
uv run python cli.py list-users

The tenant name is required and unique: "Acme Corp", "ACME  corp." and "Acme, Corp" are the same tenant, so the second is refused.
That admin signs in to Recruit Desk and invites everyone else into the tenant. There is no public sign-up.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Tenant, User, utcnow
from app.security import generate_keypair
from app.services import TenantError, create_tenant_with_admin, norm_email, tenant_name


def ask_password(min_length: int) -> str:
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Again: "):
        sys.exit("the passwords differ")
    if len(pw) < min_length:
        sys.exit(f"the password needs at least {min_length} characters")
    return pw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-keys")
    sub.add_parser("init-db")
    ct = sub.add_parser("create-tenant")
    ct.add_argument("name", help="the tenant's name (required, unique)")
    ct.add_argument("email", help="the first admin's email")
    ca = sub.add_parser("create-admin")
    ca.add_argument("email")
    ca.add_argument("--tenant", required=True, help="the name of an existing tenant")
    sub.add_parser("list-tenants")
    sub.add_parser("list-users")
    a = ap.parse_args()
    s = Settings()
    if s.database_url_unpooled:
        s.database_url = s.database_url_unpooled   # setup commands use Neon's direct address, not the pooler
    if a.cmd == "gen-keys":
        target = Path(s.jwt_private_key_path)
        if target.exists():
            sys.exit(f"{target} already exists; refusing to replace the signing key (it would sign out everyone)")
        priv, pub = generate_keypair(target.parent)
        print(f"wrote {priv} (keep it secret) and {pub} (the app checks tokens with this one)")
        return
    s.auto_create_tables = True   # the CLI always makes sure the tables exist
    app = create_app(s)
    if a.cmd == "init-db":
        print("tables ready")
        return
    with app.state.sessions() as db:
        if a.cmd == "list-tenants":
            for t in db.scalars(select(Tenant).order_by(Tenant.id)):
                admins = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == t.id, User.role == "admin", User.is_active.is_(True)))
                people = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == t.id))
                print(f"{t.id:>3}  {t.name:<40} {people} people, {admins} active admin(s)")
            return
        if a.cmd == "list-users":
            for u in db.scalars(select(User).order_by(User.tenant_id, User.id)):
                print(f"{u.id:>3}  {u.tenant.name:<28} {u.email:<36} {u.role:<6} {'active' if u.is_active else 'DISABLED'}  last login {u.last_login_at or 'never'}")
            return
        if a.cmd == "create-tenant":
            try:
                tenant_name(a.name)   # say what is wrong with the name before asking for a password
                pw = ask_password(s.min_password_length)
                u = create_tenant_with_admin(db, app.state.passwords, a.name, a.email, pw, s.min_password_length)
            except TenantError as e:
                db.rollback()
                sys.exit(str(e))
            print(f"tenant '{u.tenant.name}' created with admin {u.email}")
            return
        # create-admin: another admin for an existing tenant
        _, key = tenant_name(a.tenant)
        tenant = db.scalar(select(Tenant).where(Tenant.name_key == key))
        if tenant is None:
            sys.exit(f"no tenant called '{a.tenant}'. Tenants: {', '.join(t.name for t in db.scalars(select(Tenant))) or 'none yet'}")
        email = norm_email(a.email)
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            sys.exit(f"{email} already has an account")
        pw = ask_password(s.min_password_length)
        db.add(User(tenant_id=tenant.id, email=email, password_hash=app.state.passwords.hash(pw), role="admin", created_at=utcnow()))
        db.commit()
        print(f"admin {email} added to tenant '{tenant.name}'")


if __name__ == "__main__":
    main()
