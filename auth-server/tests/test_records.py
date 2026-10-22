from __future__ import annotations

import hashlib

from conftest import bearer, login, make_user

H1 = hashlib.sha256(b"candidate:url:https://www.linkedin.com/in/ann").hexdigest()
H2 = hashlib.sha256(b"candidate:url:https://www.linkedin.com/in/bob").hexdigest()
CAND = {"name": "Ann Rao", "title": "Senior Frontend Engineer", "company": "Acme", "location": "Pune, India", "match_score": 85,
        "profile_url": "https://www.linkedin.com/in/ann", "jd": "Senior React Frontend Developer", "date_found": "2026-10-18"}


def body(record=None, h=H1, at="2026-10-18T10:00:00"):
    return {"record": record or CAND, "subject_hash": h, "created_at": at}


def user(app, client, email="u@example.com"):
    make_user(app, email)
    return bearer(login(client, email).json())


def test_a_signed_in_person_stores_the_last_record_and_anyone_signed_in_can_read_it(app, client):
    h = user(app, client)
    r = client.put("/records/last/candidate", json=body(), headers=h)
    assert r.status_code == 200 and r.json()["stored_by"] == "u@example.com" and r.json()["record"]["name"] == "Ann Rao"
    other = user(app, client, "v@example.com")
    rows = client.get("/records/last", headers=other).json()
    assert [(x["agent"], x["record"]["match_score"], x["subject_hash"]) for x in rows] == [("candidate", 85, H1)]


def test_it_needs_a_sign_in_and_a_known_assistant(app, client):
    assert client.put("/records/last/candidate", json=body()).status_code == 401
    assert client.get("/records/last").status_code == 401
    assert client.delete(f"/records/last/candidate?subject_hash={H1}").status_code == 401
    h = user(app, client)
    assert client.put("/records/last/robot", json=body(), headers=h).status_code == 404
    assert client.delete(f"/records/last/robot?subject_hash={H1}", headers=h).status_code == 404


def test_a_switched_off_person_cannot_store(app, client):
    h = user(app, client)
    with app.state.sessions() as db:
        from app.models import User
        from sqlalchemy import select
        u = db.scalar(select(User)); u.is_active = False; db.commit()
    assert client.put("/records/last/candidate", json=body(), headers=h).status_code == 401


def test_only_the_listed_fields_are_accepted(app, client):
    h = user(app, client)
    for extra in ({"email": "ann@example.com"}, {"phone": "98765 43210"}, {"evidence": "quote"}, {"notes": "x"}):
        r = client.put("/records/last/candidate", json=body({**CAND, **extra}), headers=h)
        assert r.status_code == 422 and "not accepted" in r.json()["detail"], extra
    assert client.get("/records/last", headers=h).json() == []
    # the same name is not valid for another assistant
    assert client.put("/records/last/company", json=body({"name": "x"}), headers=h).status_code == 422
    assert client.put("/records/last/company", json=body({"job_title": "SRE", "company": "Acme", "status": "Open"}), headers=h).status_code == 200


def test_contact_details_hidden_in_a_value_are_refused(app, client):
    h = user(app, client)
    for field, value in (("title", "Engineer, mail ann@example.com"), ("location", "call +91 98765 43210"), ("company", "Acme 415.555.0100")):
        r = client.put("/records/last/candidate", json=body({**CAND, field: value}), headers=h)
        assert r.status_code == 422 and r.json()["detail"] == "Contact details are not accepted", value
    # a long job id in a URL is not a phone number, and a year range is not either
    ok = {**CAND, "profile_url": "https://boards.greenhouse.io/acme/jobs/4739199005", "title": "Lead 2019-2023"}
    assert client.put("/records/last/candidate", json=body(ok), headers=h).status_code == 200


def test_limits_on_size_and_shape(app, client):
    h = user(app, client)
    assert client.put("/records/last/candidate", json=body({**CAND, "title": "x" * 301}), headers=h).status_code == 422
    assert client.put("/records/last/candidate", json=body({**CAND, "profile_url": "https://x.test/" + "a" * 700}), headers=h).status_code == 422
    assert client.put("/records/last/candidate", json=body({**CAND, "title": ["a", "list"]}), headers=h).status_code == 422
    assert client.put("/records/last/candidate", json=body({**CAND, "title": {"a": 1}}), headers=h).status_code == 422
    assert client.put("/records/last/candidate", json=body(h="not-a-hash"), headers=h).status_code == 422
    assert client.put("/records/last/candidate", json={"record": CAND, "subject_hash": H1}, headers=h).status_code == 422
    assert client.put("/records/last/candidate", json=body({**CAND, "title": None, "company": 5}), headers=h).status_code == 200


def test_a_newer_record_replaces_it_and_an_older_one_never_does(app, client):
    h = user(app, client)
    client.put("/records/last/candidate", json=body(at="2026-10-18T10:00:00"), headers=h)
    newer = {**CAND, "name": "Bob Sen"}
    assert client.put("/records/last/candidate", json=body(newer, H2, "2026-10-18T11:00:00"), headers=h).status_code == 200
    older = client.put("/records/last/candidate", json=body(CAND, H1, "2026-10-18T09:00:00"), headers=h)
    assert older.status_code == 409
    rows = client.get("/records/last", headers=h).json()
    assert len(rows) == 1 and rows[0]["record"]["name"] == "Bob Sen" and rows[0]["subject_hash"] == H2


def test_sending_the_same_record_again_changes_nothing_and_time_zones_are_compared_in_utc(app, client):
    h = user(app, client)
    a = client.put("/records/last/candidate", json=body(), headers=h).json()
    b = client.put("/records/last/candidate", json=body(), headers=h).json()
    assert a["stored_at"] == b["stored_at"], "an identical record is not rewritten"
    # 15:30+05:30 is 10:00 UTC: the same instant, so not older
    assert client.put("/records/last/candidate", json=body(at="2026-10-18T15:30:00+05:30"), headers=h).status_code == 200
    # 15:00+05:30 is 09:30 UTC: older
    assert client.put("/records/last/candidate", json=body({**CAND, "name": "Old"}, H2, "2026-10-18T15:00:00+05:30"), headers=h).status_code == 409


def test_one_row_per_assistant(app, client):
    h = user(app, client)
    client.put("/records/last/candidate", json=body(), headers=h)
    client.put("/records/last/company", json=body({"job_title": "SRE", "company": "Acme"}, H2), headers=h)
    client.put("/records/last/account", json=body({"company": "Acme", "domain": "acme.com"}, H1), headers=h)
    client.put("/records/last/account", json=body({"company": "Beta", "domain": "beta.io"}, H2, "2026-10-18T12:00:00"), headers=h)
    rows = client.get("/records/last", headers=h).json()
    assert [r["agent"] for r in rows] == ["account", "candidate", "company"]
    assert next(r for r in rows if r["agent"] == "account")["record"]["company"] == "Beta"


def test_delete_removes_exactly_the_record_asked_for(app, client):
    h = user(app, client)
    client.put("/records/last/candidate", json=body(), headers=h)
    assert client.delete(f"/records/last/candidate?subject_hash={H2}", headers=h).status_code == 409   # another record: untouched
    assert len(client.get("/records/last", headers=h).json()) == 1
    assert client.delete("/records/last/candidate", headers=h).status_code == 422                       # the hash is required
    assert client.delete(f"/records/last/candidate?subject_hash={H1}", headers=h).status_code == 204
    assert client.get("/records/last", headers=h).json() == []
    assert client.delete(f"/records/last/candidate?subject_hash={H1}", headers=h).status_code == 404


def test_the_table_holds_no_contact_columns(app):
    from app.models import LastRecord
    assert {c.name for c in LastRecord.__table__.columns} == {"tenant_id", "agent", "record_json", "subject_hash", "created_at", "stored_at", "stored_by"}
