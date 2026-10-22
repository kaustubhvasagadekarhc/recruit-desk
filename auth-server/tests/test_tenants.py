from __future__ import annotations

import hashlib

import jwt
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Tenant, User
from app.services import TenantError, active_admins, create_tenant, create_tenant_with_admin, tenant_name
from conftest import PASSWORD, bearer, login, make_user

H = hashlib.sha256(b"candidate:k").hexdigest()


# ---------- the name: required, and unique in every spelling that folds the same ----------

@pytest.mark.parametrize("a,b", [
    ("Acme Corp", "acme corp"),
    ("Acme Corp", "  Acme    Corp  "),
    ("Acme Corp", "ACME CORP."),
    ("Acme, Inc.", "Acme Inc"),
    ("Acme-Corp", "Acme Corp"),
    ("Acme & Sons", "Acme Sons"),
    ("Café Rouge", "Cafe Rouge"),
    ("Café Rouge", "CAFÉ  ROUGE"),
    ("Ａcme Corp", "Acme Corp"),          # a full-width letter
    ("Acme Corp", "Acme Corp"),     # a non-breaking space
    ("Acme_Corp", "Acme Corp"),
    ("Straße Bau", "Strasse Bau"),
])
def test_spellings_that_differ_only_in_case_spacing_punctuation_or_accents_are_the_same_tenant(a, b):
    assert tenant_name(a)[1] == tenant_name(b)[1]


@pytest.mark.parametrize("a,b", [("Acme Corp", "Acme Corporation"), ("Acme", "Acme Labs"), ("Beta Ltd", "Beta Systems"), ("Zeta 1", "Zeta 2"), ("Ab", "Ba")])
def test_different_names_stay_different(a, b):
    assert tenant_name(a)[1] != tenant_name(b)[1]


@pytest.mark.parametrize("bad,why", [
    ("", "at least 2 characters"), ("   ", "at least 2 characters"), ("A", "at least 2 characters"), (None, "at least 2 characters"),
    ("!!!", "at least 2 letters or digits"), ("... ---", "at least 2 letters or digits"), ("A.", "at least 2 letters or digits"),
    ("x" * 81, "at most 80"), ("Acme\x00Corp", "control characters"), ("Acme\nCorp\x07", "control characters"),
])
def test_a_missing_or_unusable_name_is_refused_with_a_reason(bad, why):
    with pytest.raises(TenantError) as e:
        tenant_name(bad)
    assert why in str(e.value)


def test_the_name_is_shown_as_typed_but_tidied(app):
    assert tenant_name("  Acme    Corp\t")[0] == "Acme Corp"
    assert tenant_name("Ａcme Corp")[0] == "Acme Corp"
    assert tenant_name("Café Rouge")[0] == "Café Rouge"


def test_a_second_tenant_with_the_same_name_is_refused(app):
    with app.state.sessions() as db:
        create_tenant(db, "Acme Corp")
        db.commit()
        for dup in ("Acme Corp", "acme corp", " ACME   CORP. ", "Acme, Corp"):
            with pytest.raises(TenantError, match="already exists"):
                create_tenant(db, dup)
        assert len(db.scalars(select(Tenant)).all()) == 1
        create_tenant(db, "Acme Corporation")   # a different name is fine
        db.commit()
        assert len(db.scalars(select(Tenant)).all()) == 2


def test_the_database_itself_refuses_a_duplicate_even_if_the_check_is_skipped(app):
    """Two requests racing past the check: the unique key decides."""
    with app.state.sessions() as db:
        db.add(Tenant(name="Acme Corp", name_key="acmecorp"))
        db.commit()
        db.add(Tenant(name="ACME corp", name_key="acmecorp"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_creating_a_tenant_with_its_first_admin_is_all_or_nothing(app):
    s = app.state.settings
    with app.state.sessions() as db:
        u = create_tenant_with_admin(db, app.state.passwords, "Acme Corp", "Boss@Acme.com", "a long enough password", s.min_password_length)
        assert (u.email, u.role, u.is_active, u.tenant.name) == ("boss@acme.com", "admin", True, "Acme Corp")
        bad_cases = [("Acme Corp", "other@acme.com", "a long enough password", "already exists"),     # duplicate name
                     ("Other Co", "boss@acme.com", "a long enough password", "email already exists"),  # email taken
                     ("Other Co", "no-at-sign", "a long enough password", "valid email"),
                     ("Other Co", "x@y.zz", "short", "at least"),
                     ("", "x@y.zz", "a long enough password", "at least 2 characters")]
        for name, email, pw, why in bad_cases:
            with pytest.raises(TenantError, match=why):
                create_tenant_with_admin(db, app.state.passwords, name, email, pw, s.min_password_length)
            db.rollback()
        assert [t.name for t in db.scalars(select(Tenant))] == ["Acme Corp"], "a refused attempt leaves no half-made tenant"
        assert len(db.scalars(select(User)).all()) == 1


# ---------- the tenant travels with the person ----------

def test_the_token_and_the_profile_carry_the_tenant(app, client):
    make_user(app, "a@example.com", tenant="Acme Corp")
    t = login(client, "a@example.com").json()
    assert t["user"]["tenant"]["name"] == "Acme Corp"
    claims = jwt.decode(t["access_token"], app.state.tokens.public, algorithms=["EdDSA"], audience="recruit-desk", issuer="recruit-desk-auth")
    assert claims["tenant"] == "Acme Corp" and claims["tid"] == t["user"]["tenant"]["id"]
    assert client.get("/auth/me", headers=bearer(t)).json()["tenant"]["name"] == "Acme Corp"


def test_the_first_admin_signs_in_and_invites_into_their_own_tenant(app, client):
    with app.state.sessions() as db:
        create_tenant_with_admin(db, app.state.passwords, "Acme Corp", "boss@acme.com", PASSWORD, 10)
    boss = login(client, "boss@acme.com").json()
    inv = client.post("/admin/invites", json={"email": "ann@acme.com", "role": "user"}, headers=bearer(boss)).json()
    acc = client.post("/auth/accept-invite", json={"token": inv["invite_url"].split("token=")[1], "password": "a long enough password"})
    assert acc.status_code == 201 and acc.json()["user"]["tenant"]["name"] == "Acme Corp", "the invitee lands in the inviter's tenant"
    assert [u["email"] for u in client.get("/admin/users", headers=bearer(boss)).json()] == ["boss@acme.com", "ann@acme.com"]


# ---------- tenants cannot see or touch each other ----------

def two_tenants(app, client):
    a_admin = make_user(app, "boss@acme.com", "admin", tenant="Acme Corp")
    a_user = make_user(app, "ann@acme.com", tenant="Acme Corp")
    b_admin = make_user(app, "chief@beta.io", "admin", tenant="Beta Ltd")
    b_user = make_user(app, "bob@beta.io", tenant="Beta Ltd")
    return {"a": bearer(login(client, "boss@acme.com").json()), "b": bearer(login(client, "chief@beta.io").json()),
            "ids": {"a_admin": a_admin, "a_user": a_user, "b_admin": b_admin, "b_user": b_user}}


def test_an_admin_sees_only_their_own_tenants_people_and_invitations(app, client):
    t = two_tenants(app, client)
    client.post("/admin/invites", json={"email": "new@acme.com"}, headers=t["a"])
    client.post("/admin/invites", json={"email": "new@beta.io"}, headers=t["b"])
    assert {u["email"] for u in client.get("/admin/users", headers=t["a"]).json()} == {"boss@acme.com", "ann@acme.com"}
    assert {u["email"] for u in client.get("/admin/users", headers=t["b"]).json()} == {"chief@beta.io", "bob@beta.io"}
    assert [i["email"] for i in client.get("/admin/invites", headers=t["a"]).json()] == ["new@acme.com"]
    assert [i["email"] for i in client.get("/admin/invites", headers=t["b"]).json()] == ["new@beta.io"]


def test_an_admin_cannot_change_withdraw_or_reset_another_tenants_people(app, client):
    t = two_tenants(app, client)
    other_user = t["ids"]["b_user"]
    for r in (client.patch(f"/admin/users/{other_user}", json={"role": "admin"}, headers=t["a"]),
              client.patch(f"/admin/users/{other_user}", json={"is_active": False}, headers=t["a"]),
              client.post(f"/admin/users/{other_user}/reset-link", headers=t["a"])):
        assert r.status_code == 404, r.request.url
    client.post("/admin/invites", json={"email": "new@beta.io"}, headers=t["b"])
    b_invite = client.get("/admin/invites", headers=t["b"]).json()[0]["id"]
    assert client.delete(f"/admin/invites/{b_invite}", headers=t["a"]).status_code == 404
    assert len(client.get("/admin/invites", headers=t["b"]).json()) == 1, "the other tenant's invitation is untouched"
    with app.state.sessions() as db:
        bob = db.get(User, other_user)
        assert (bob.role, bob.is_active) == ("user", True)


def test_one_email_belongs_to_one_tenant(app, client):
    t = two_tenants(app, client)
    assert client.post("/admin/invites", json={"email": "bob@beta.io"}, headers=t["a"]).status_code == 409   # already someone's account
    # both tenants may invite the same new address; whoever accepts first gets the account, the other invitation then fails
    a = client.post("/admin/invites", json={"email": "shared@x.io"}, headers=t["a"]).json()
    b = client.post("/admin/invites", json={"email": "shared@x.io"}, headers=t["b"]).json()
    tok = lambda r: r["invite_url"].split("token=")[1]
    first = client.post("/auth/accept-invite", json={"token": tok(b), "password": "a long enough password"})
    assert first.status_code == 201 and first.json()["user"]["tenant"]["name"] == "Beta Ltd"
    assert client.post("/auth/accept-invite", json={"token": tok(a), "password": "a long enough password"}).status_code == 409
    assert "shared@x.io" not in {u["email"] for u in client.get("/admin/users", headers=t["a"]).json()}


def test_a_new_invitation_replaces_only_the_same_tenants_open_one(app, client):
    t = two_tenants(app, client)
    a1 = client.post("/admin/invites", json={"email": "new@x.io"}, headers=t["a"]).json()
    client.post("/admin/invites", json={"email": "new@x.io"}, headers=t["b"])
    assert client.post("/auth/accept-invite", json={"token": a1["invite_url"].split("token=")[1], "password": "a long enough password"}).status_code == 201, \
        "the other tenant's invitation did not void this one"


def test_every_tenant_keeps_at_least_one_active_admin(app, client):
    t = two_tenants(app, client)
    make_user(app, "second@acme.com", "admin", tenant="Acme Corp")
    with app.state.sessions() as db:
        a = db.scalar(select(User.tenant_id).where(User.email == "boss@acme.com"))
        b = db.scalar(select(User.tenant_id).where(User.email == "chief@beta.io"))
        assert (active_admins(db, a), active_admins(db, b)) == (2, 1), "counted per tenant, not for the whole server"
    # the admin of another tenant is not a second admin for this one
    assert client.patch(f"/admin/users/{t['ids']['a_admin']}", json={"is_active": False}, headers=t["a"]).status_code == 400   # cannot switch off yourself


def test_last_records_are_kept_per_tenant(app, client):
    t = two_tenants(app, client)
    rec = {"name": "Ann Rao"}
    body = lambda who, h: {"record": {"name": who}, "subject_hash": h, "created_at": "2026-10-18T10:00:00"}
    assert client.put("/records/last/candidate", json=body("Ann Rao", H), headers=t["a"]).status_code == 200
    assert client.get("/records/last", headers=t["b"]).json() == [], "another tenant sees nothing"
    h2 = hashlib.sha256(b"candidate:other").hexdigest()
    assert client.put("/records/last/candidate", json=body("Bob Sen", h2), headers=t["b"]).status_code == 200   # a separate row for the same assistant
    assert [r["record"]["name"] for r in client.get("/records/last", headers=t["a"]).json()] == ["Ann Rao"]
    assert [r["record"]["name"] for r in client.get("/records/last", headers=t["b"]).json()] == ["Bob Sen"]
    # an older record in B does not touch A, and A's delete cannot remove B's record
    assert client.delete(f"/records/last/candidate?subject_hash={h2}", headers=t["a"]).status_code == 409
    assert client.delete(f"/records/last/candidate?subject_hash={H}", headers=t["b"]).status_code == 409
    assert len(client.get("/records/last", headers=t["a"]).json()) == 1 and len(client.get("/records/last", headers=t["b"]).json()) == 1
    assert client.delete(f"/records/last/candidate?subject_hash={H}", headers=t["a"]).status_code == 204
    assert [r["record"]["name"] for r in client.get("/records/last", headers=t["b"]).json()] == ["Bob Sen"]


def test_a_database_from_before_tenants_is_refused_with_a_clear_message(settings, tmp_path):
    from sqlalchemy import create_engine
    from app.main import create_app
    old = tmp_path / "old.db"
    e = create_engine(f"sqlite:///{old}")
    with e.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT, role TEXT, is_active BOOLEAN, created_at DATETIME, last_login_at DATETIME)"))
    with pytest.raises(RuntimeError, match="before tenants existed"):
        create_app(settings.model_copy(update={"database_url": f"sqlite:///{old}"}))
