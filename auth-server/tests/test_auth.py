from __future__ import annotations

import time
from datetime import timedelta
from unittest import mock

import jwt
from pydantic import SecretStr
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.mail import Mailer
from app.models import Invite, RefreshToken, User, utcnow
from app.security import token_hash
from conftest import PASSWORD, bearer, login, make_user


# ---------- basics and login ----------

def test_health_and_public_key(client):
    assert client.get("/health").json() == {"ok": True}
    pem = client.get("/auth/public-key").text
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_wrong_password_and_unknown_email_look_the_same(app, client):
    make_user(app, "a@example.com")
    bad_pw = login(client, "a@example.com", "not the password")
    no_user = login(client, "nobody@example.com")
    assert (bad_pw.status_code, bad_pw.json()) == (no_user.status_code, no_user.json()) == (401, {"detail": "Invalid email or password"})


def test_login_returns_tokens_and_me_works(app, client):
    make_user(app, "a@example.com")
    r = login(client, "A@Example.com ")   # case and spaces do not matter
    assert r.status_code == 200
    t = r.json()
    assert t["user"]["email"] == "a@example.com" and t["user"]["role"] == "user"
    assert client.get("/auth/me", headers=bearer(t)).json()["email"] == "a@example.com"


def test_access_token_is_signed_and_carries_an_offline_grace_deadline(app, client):
    make_user(app, "a@example.com")
    t = login(client, "a@example.com").json()
    pub = app.state.tokens.public
    claims = jwt.decode(t["access_token"], pub, algorithms=["EdDSA"], audience="recruit-desk", issuer="recruit-desk-auth")
    assert claims["role"] == "user" and claims["grace"] == t["offline_grace_until"]
    assert abs(claims["grace"] - (time.time() + 7 * 86400)) < 60
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_stored_secrets_are_hashed(app, client, admin):
    with app.state.sessions() as db:
        u = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert u.password_hash.startswith("$argon2id$") and PASSWORD not in u.password_hash
        assert all(len(r.token_hash) == 64 and r.token_hash != admin["refresh_token"] for r in db.scalars(select(RefreshToken)))


def test_rejected_tokens(app, client):
    make_user(app, "a@example.com")
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    other = jwt.encode({"sub": "1", "iat": int(time.time()), "exp": int(time.time()) + 600, "aud": "recruit-desk", "iss": "recruit-desk-auth"},
                       Ed25519PrivateKey.generate(), algorithm="EdDSA")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 401   # signed by another key
    app.state.settings.access_token_minutes = -1
    expired = login(client, "a@example.com").json()
    assert client.get("/auth/me", headers=bearer(expired)).status_code == 401


def test_too_many_failures_lock_the_email_even_for_the_right_password(app, client):
    make_user(app, "a@example.com")
    for _ in range(5):
        assert login(client, "a@example.com", "wrong").status_code == 401
    assert login(client, "a@example.com").status_code == 429


def test_a_good_login_clears_the_email_failures(app, client):
    make_user(app, "a@example.com")
    for _ in range(4):
        login(client, "a@example.com", "wrong")
    assert login(client, "a@example.com").status_code == 200
    for _ in range(4):
        login(client, "a@example.com", "wrong")
    assert login(client, "a@example.com").status_code == 200


# ---------- refresh and logout ----------

def test_refresh_rotates_and_a_reused_token_ends_every_session(app, client):
    make_user(app, "a@example.com")
    first = login(client, "a@example.com").json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401   # reuse
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401   # the family is gone


def test_a_revoked_token_is_refused_without_ending_the_other_sessions(app, client):
    make_user(app, "a@example.com")
    gone = login(client, "a@example.com").json()
    keep = login(client, "a@example.com").json()
    client.post("/auth/logout", json={"refresh_token": gone["refresh_token"]})
    assert client.post("/auth/refresh", json={"refresh_token": gone["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": keep["refresh_token"]}).status_code == 200   # unaffected


def test_refresh_with_an_unknown_or_expired_token(app, client):
    make_user(app, "a@example.com")
    assert client.post("/auth/refresh", json={"refresh_token": "x" * 40}).status_code == 401
    t = login(client, "a@example.com").json()
    with app.state.sessions() as db:
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(t["refresh_token"])))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401


def test_logout_revokes_the_refresh_token(app, client):
    make_user(app, "a@example.com")
    t = login(client, "a@example.com").json()
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204   # again: still fine
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401


# ---------- invitations ----------

def invite(client, admin, email="new@example.com", role="user"):
    return client.post("/admin/invites", json={"email": email, "role": role}, headers=bearer(admin))


def token_of(url: str) -> str:
    return url.split("token=")[1]


def test_invite_accept_and_login(app, client, admin):
    r = invite(client, admin)
    assert r.status_code == 201 and r.json()["email_sent"] is False and r.json()["invite_url"].startswith("https://auth.test/accept-invite?token=")
    tok = token_of(r.json()["invite_url"])
    with app.state.sessions() as db:
        assert tok not in {i.token_hash for i in db.scalars(select(Invite))}   # only the hash is stored
    acc = client.post("/auth/accept-invite", json={"token": tok, "password": "a long enough password"})
    assert acc.status_code == 201 and acc.json()["user"]["role"] == "user"
    assert login(client, "new@example.com", "a long enough password").status_code == 200
    assert client.post("/auth/accept-invite", json={"token": tok, "password": "another long password"}).status_code == 400   # single use


def test_invite_rules(app, client, admin):
    assert client.post("/admin/invites", json={"email": "x@example.com", "role": "owner"}, headers=bearer(admin)).status_code == 422
    assert invite(client, admin, "admin@example.com").status_code == 409   # already has an account
    first = invite(client, admin, "n@example.com").json()
    second = invite(client, admin, "n@example.com").json()   # replaces the open one
    assert client.post("/auth/accept-invite", json={"token": token_of(first["invite_url"]), "password": "a long enough password"}).status_code == 400
    assert client.post("/auth/accept-invite", json={"token": token_of(second["invite_url"]), "password": "short"}).status_code == 422
    assert len(client.get("/admin/invites", headers=bearer(admin)).json()) == 1


def test_expired_invite_is_refused(app, client, admin):
    url = invite(client, admin).json()["invite_url"]
    with app.state.sessions() as db:
        inv = db.scalar(select(Invite))
        inv.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/auth/accept-invite", json={"token": token_of(url), "password": "a long enough password"}).status_code == 400


def test_an_admin_can_invite_another_admin(app, client, admin):
    tok = token_of(invite(client, admin, "boss@example.com", "admin").json()["invite_url"])
    assert client.post("/auth/accept-invite", json={"token": tok, "password": "a long enough password"}).json()["user"]["role"] == "admin"


# ---------- roles ----------

def test_users_cannot_use_admin_endpoints(app, client):
    make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    assert client.get("/admin/users", headers=bearer(t)).status_code == 403
    assert client.post("/admin/invites", json={"email": "x@example.com"}, headers=bearer(t)).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_role_change_takes_effect_at_once(app, client, admin):
    uid = make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    assert client.get("/admin/users", headers=bearer(t)).status_code == 403
    assert client.patch(f"/admin/users/{uid}", json={"role": "admin"}, headers=bearer(admin)).json()["role"] == "admin"
    assert client.get("/admin/users", headers=bearer(t)).status_code == 200   # same token, new role from the database


def test_disabling_a_user_ends_their_sessions_and_blocks_login(app, client, admin):
    uid = make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    assert client.patch(f"/admin/users/{uid}", json={"is_active": False}, headers=bearer(admin)).status_code == 200
    assert client.get("/auth/me", headers=bearer(t)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert login(client, "u@example.com").status_code == 403
    client.patch(f"/admin/users/{uid}", json={"is_active": True}, headers=bearer(admin))
    assert login(client, "u@example.com").status_code == 200


def test_an_admin_cannot_demote_or_disable_themselves(app, client, admin):
    me = admin["user"]["id"]
    assert client.patch(f"/admin/users/{me}", json={"role": "user"}, headers=bearer(admin)).status_code == 400
    assert client.patch(f"/admin/users/{me}", json={"is_active": False}, headers=bearer(admin)).status_code == 400
    assert client.patch("/admin/users/9999", json={"role": "user"}, headers=bearer(admin)).status_code == 404


# ---------- passwords ----------

def test_change_password(app, client):
    make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    other_device = login(client, "u@example.com").json()
    assert client.post("/auth/change-password", json={"current_password": "wrong", "new_password": "a brand new password"}, headers=bearer(t)).status_code == 401
    assert client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}, headers=bearer(t)).status_code == 422
    r = client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "a brand new password"}, headers=bearer(t))
    assert r.status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": other_device["refresh_token"]}).status_code == 401   # other devices sign in again
    assert client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 200
    assert login(client, "u@example.com").status_code == 401 and login(client, "u@example.com", "a brand new password").status_code == 200


def test_forgot_password_never_says_whether_the_email_exists(app, client):
    make_user(app, "u@example.com")
    a = client.post("/auth/forgot-password", json={"email": "u@example.com"})
    b = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) and a.status_code == 202


def test_admin_reset_link_resets_once_and_ends_sessions(app, client, admin):
    uid = make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    link = client.post(f"/admin/users/{uid}/reset-link", headers=bearer(admin)).json()
    tok = token_of(link["reset_url"])
    assert client.post("/auth/reset-password", json={"token": tok, "password": "short"}).status_code == 422
    assert client.post("/auth/reset-password", json={"token": tok, "password": "a brand new password"}).status_code == 204
    assert client.post("/auth/reset-password", json={"token": tok, "password": "another new password"}).status_code == 400   # single use
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert login(client, "u@example.com", "a brand new password").status_code == 200


def test_expired_reset_token_is_refused(app, client, admin):
    from app.models import ResetToken
    uid = make_user(app, "u@example.com")
    tok = token_of(client.post(f"/admin/users/{uid}/reset-link", headers=bearer(admin)).json()["reset_url"])
    with app.state.sessions() as db:
        row = db.scalar(select(ResetToken))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/auth/reset-password", json={"token": tok, "password": "a brand new password"}).status_code == 400


# ---------- mail ----------

def test_mailer_is_off_without_smtp_and_never_logs_links(app, caplog):
    assert Mailer(app.state.settings).send("a@example.com", "s", "https://x/?token=secret") is False
    assert "secret" not in caplog.text


def test_mailer_sends_through_smtp_when_configured(settings):
    settings.smtp_host = "smtp.test"
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent.append(("tls",))
        def login(self, u, p): sent.append(("login",))
        def send_message(self, msg): sent.append(("msg", msg["To"], msg.get_content()))

    with mock.patch("app.mail.smtplib.SMTP", FakeSMTP):
        assert Mailer(settings).send("a@example.com", "Hi", "link: https://x/?token=abc") is True
    assert ("tls",) in sent and ("msg", "a@example.com", "link: https://x/?token=abc\n") in sent

    class Broken(FakeSMTP):
        def send_message(self, msg): raise OSError("down")
    with mock.patch("app.mail.smtplib.SMTP", Broken):
        assert Mailer(settings).send("a@example.com", "Hi", "x") is False   # a refusal is reported, not raised


# ---------- behind a proxy (Cloudflare Tunnel) ----------

def test_rate_limit_uses_the_trusted_proxy_header_only_when_configured(app, client):
    make_user(app, "a@example.com")
    make_user(app, "b@example.com")
    # Not configured: the header is ignored, every request is one client
    app.state.settings.trusted_ip_header = None
    for i in range(20):
        client.post("/auth/login", json={"email": f"nobody{i}@example.com", "password": "x"}, headers={"CF-Connecting-IP": f"1.1.1.{i}"})
    assert login(client, "b@example.com").status_code == 429   # 20 failures from "one" address blocked it
    # Configured: each real address has its own bucket
    app.state.limiter.clear_all()
    app.state.settings.trusted_ip_header = "CF-Connecting-IP"
    for i in range(20):
        client.post("/auth/login", json={"email": f"nobody{i}@example.com", "password": "x"}, headers={"CF-Connecting-IP": "9.9.9.9"})
    assert client.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD}, headers={"CF-Connecting-IP": "9.9.9.9"}).status_code == 429
    assert client.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD}, headers={"CF-Connecting-IP": "8.8.8.8"}).status_code == 200


# ---------- keys never reach this server ----------

def test_the_server_has_no_place_for_a_users_own_api_keys(app):
    """Each person keeps their own Exa/Apollo/other keys on their own machine; none may be stored here."""
    from app.models import Base
    cols = {t.name: {c.name for c in t.columns} for t in Base.metadata.tables.values()}
    assert cols["users"] == {"id", "tenant_id", "email", "password_hash", "role", "is_active", "created_at", "last_login_at"}
    # login_failures.key is a rate-limit bucket name ("email:...", "ip:...") and tenants.name_key is the folded tenant name: neither is an API key
    assert not [(t, c) for t, names in cols.items() for c in names if "key" in c.lower() and (t, c) not in {("login_failures", "key"), ("tenants", "name_key")}]
    paths = set(app.openapi()["paths"])
    assert not [p for p in paths if "key" in p and p != "/auth/public-key"]


# ---------- serverless and Vercel ----------

def test_failure_counts_are_shared_through_the_database(settings):
    """Two instances (as on a serverless host) must see each other's failed logins."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    a, b = create_app(settings), create_app(settings)
    make_user(a, "a@example.com")
    ca, cb = TestClient(a), TestClient(b)
    for _ in range(5):
        assert login(ca, "a@example.com", "wrong").status_code == 401
    assert login(cb, "a@example.com").status_code == 429


def test_database_urls_get_the_psycopg_driver():
    from app.config import Settings
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url="postgresql://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_signing_key_can_come_from_an_env_var(settings, tmp_path):
    from app.main import create_app
    pem = open(settings.jwt_private_key_path).read()
    s2 = settings.model_copy(update={"jwt_private_key_path": str(tmp_path / "missing.pem"), "jwt_private_key": SecretStr(pem.replace("\n", "\\n"))})
    app2 = create_app(s2)   # no file, escaped newlines, still loads
    assert app2.state.tokens.public_pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_vercel_ip_helper_is_used_when_enabled_and_never_breaks_login(app, client):
    make_user(app, "a@example.com")
    app.state.settings.use_vercel_ip = True
    with mock.patch("vercel.functions.ip_address", return_value="7.7.7.7"):
        for _ in range(20):
            client.post("/auth/login", json={"email": "x@example.com", "password": "x"})
    with app.state.sessions() as db:
        from app.models import LoginFailure
        assert "ip:7.7.7.7" in {r.key for r in db.scalars(select(LoginFailure))}
    with mock.patch("vercel.functions.ip_address", side_effect=RuntimeError("changed")):
        assert login(client, "a@example.com").status_code == 200   # falls back to the peer address


def test_engine_options_for_sqlite_and_for_postgres_on_neon():
    from sqlalchemy.pool import NullPool
    from app.config import Settings
    from app.main import engine_options
    lite = engine_options(Settings(database_url="sqlite:///x.db", serverless=False))
    assert lite["connect_args"] == {"check_same_thread": False} and "poolclass" not in lite
    neon = engine_options(Settings(database_url="postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/db?sslmode=require", serverless=True))
    assert neon["connect_args"]["prepare_threshold"] is None     # safe behind Neon's transaction-mode pooler
    assert neon["connect_args"]["connect_timeout"] == 10 and neon["poolclass"] is NullPool
    assert Settings(database_url="postgresql://u:p@h/db?sslmode=require").database_url.startswith("postgresql+psycopg://")


def test_a_postgres_url_builds_an_engine_without_connecting(settings):
    from app.config import Settings
    from app.main import engine_options
    from sqlalchemy import create_engine
    s = Settings(database_url="postgres://u:p@ep-x-pooler.neon.tech/db?sslmode=require&channel_binding=require", serverless=True)
    engine = create_engine(s.database_url, **engine_options(s))   # lazy: no network
    assert engine.dialect.name == "postgresql" and engine.dialect.driver == "psycopg"


def test_every_table_compiles_to_postgres_ddl():
    """No server needed: catches a column type or default Postgres would refuse."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable
    from app.models import Base
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert table.name in ddl and "CREATE TABLE" in ddl
        for ix in table.indexes:
            assert "CREATE" in str(CreateIndex(ix).compile(dialect=postgresql.dialect()))


def test_neon_variables_are_read_from_env_local_and_the_unpooled_url_is_normalised(tmp_path, monkeypatch):
    from app.config import Settings
    (tmp_path / ".env.local").write_text("DATABASE_URL=postgres://u:p@ep-x-pooler.neon.tech/db?sslmode=require\n"
                                         "DATABASE_URL_UNPOOLED=postgres://u:p@ep-x.neon.tech/db?sslmode=require\n")
    monkeypatch.chdir(tmp_path)
    for var in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@ep-x-pooler.neon.tech/db?sslmode=require"
    assert s.database_url_unpooled == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    assert "-pooler" not in s.database_url_unpooled
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///from-dotenv.db\n")
    assert Settings().database_url.startswith("postgresql+psycopg://"), ".env.local wins over .env"


def test_an_admin_can_withdraw_an_invitation(app, client, admin):
    r = invite(client, admin, "later@example.com").json()
    rows = client.get("/admin/invites", headers=bearer(admin)).json()
    assert [i["email"] for i in rows] == ["later@example.com"]
    assert client.delete(f"/admin/invites/{rows[0]['id']}", headers=bearer(admin)).status_code == 204
    assert client.get("/admin/invites", headers=bearer(admin)).json() == []
    assert client.post("/auth/accept-invite", json={"token": token_of(r["invite_url"]), "password": "a long enough password"}).status_code == 400
    assert client.delete(f"/admin/invites/{rows[0]['id']}", headers=bearer(admin)).status_code == 404   # already withdrawn
    assert client.delete("/admin/invites/9999", headers=bearer(admin)).status_code == 404


def test_withdrawing_an_invitation_needs_an_admin_and_never_touches_a_used_one(app, client, admin):
    make_user(app, "u@example.com")
    t = login(client, "u@example.com").json()
    r = invite(client, admin, "n@example.com").json()
    iid = client.get("/admin/invites", headers=bearer(admin)).json()[0]["id"]
    assert client.delete(f"/admin/invites/{iid}", headers=bearer(t)).status_code == 403
    assert client.delete(f"/admin/invites/{iid}").status_code == 401
    client.post("/auth/accept-invite", json={"token": token_of(r["invite_url"]), "password": "a long enough password"})
    assert client.delete(f"/admin/invites/{iid}", headers=bearer(admin)).status_code == 404   # used: nothing to withdraw
    assert login(client, "n@example.com", "a long enough password").status_code == 200       # and the account is untouched
