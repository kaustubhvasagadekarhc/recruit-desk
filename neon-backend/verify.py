"""Live checks of the whole sign-in and data flow against a Neon BRANCH (never production: it creates test companies and accounts).

    AUTH_URL=https://<ep>.neonauth.<region>.aws.neon.tech/<db>/auth \
    API_URL=https://<ep>.apirest.<region>.aws.neon.tech/<db>/rest/v1 \
    ORIGIN=https://recruitdesk.example DATABASE_URL_UNPOOLED=postgresql://... \
    uv run --with psycopg[binary] python verify.py

Uses a random suffix for every name and address, so it can run again on the same branch. Real mail is never needed or sent.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request

import psycopg

AUTH, API, ORIGIN = os.environ["AUTH_URL"].rstrip("/"), os.environ["API_URL"].rstrip("/"), os.environ["ORIGIN"]
DB = os.environ["DATABASE_URL_UNPOOLED"]
PW = "a long test password"
SUFFIX = secrets.token_hex(3)
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   <- {detail}"))


def call(method: str, url: str, body=None, cookie: str | None = None, bearer: str | None = None, extra: dict | None = None, _retries: int = 0):
    h = {"Origin": ORIGIN, "Content-Type": "application/json", **(extra or {})}
    if cookie:
        h["Cookie"] = cookie
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, method=method, headers=h, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw, code, hdrs = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, code, hdrs = e.read(), e.code, e.headers
    if code == 429 and _retries < 6:   # Neon throttles sign-up and sign-in per address: wait as told, then try again
        time.sleep(min(float(hdrs.get("x-retry-after") or hdrs.get("retry-after") or 5) + 0.5, 30))
        return call(method, url, body, cookie, bearer, extra, _retries + 1)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = raw.decode(errors="replace")
    return code, data, hdrs


class Person:
    def __init__(self, email: str):
        self.email, self.cookie, self.jwt = email, None, None

    def sign_up(self):
        return call("POST", f"{AUTH}/sign-up/email", {"email": self.email, "password": PW, "name": self.email.split("@")[0]})

    def sign_in(self):
        code, data, hdrs = call("POST", f"{AUTH}/sign-in/email", {"email": self.email, "password": PW})
        if code == 200:
            sc = hdrs.get("set-cookie", "")
            self.cookie = sc.split(";")[0]
        return code, data

    def api(self, path: str, body=None, method: str = "POST"):
        return call(method, f"{AUTH}/{path}", body, cookie=self.cookie)

    def token(self) -> str:
        code, data, _ = call("GET", f"{AUTH}/token", cookie=self.cookie)
        self.jwt = data["token"]
        return self.jwt

    def rest(self, method: str, path: str, body=None, prefer: str | None = None):
        return call(method, f"{API}/{path}", body, bearer=self.jwt or self.token(), extra={"Prefer": prefer} if prefer else None)


def person(prefix: str, domain: str) -> Person:
    p = Person(f"{prefix}-{SUFFIX}@{domain}")
    code, _, _ = p.sign_up()
    assert code == 200, f"sign-up failed {code}"
    code, _ = p.sign_in()
    assert code == 200, f"sign-in failed {code}"
    return p


def operator(sql: str, params=()):
    with psycopg.connect(DB, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else None


def make_company(name: str, admin_email: str) -> str:
    return str(operator("select public.operator_create_company(%s, %s)", (name, admin_email))[0][0])


def org_id_of(p: Person) -> str:
    code, data, _ = call("GET", f"{AUTH}/organization/list", cookie=p.cookie)
    return data[0]["id"] if code == 200 and data else ""


def fresh(agent: str, tenant: str, **rec):
    return {"tenant_id": tenant, "agent": agent, "record": rec, "subject_hash": secrets.token_hex(32), "created_at": "2026-10-19T10:00:00Z"}


# ---------- 1. a company and its first admin ----------
ACME, BETA = f"Acme Corp {SUFFIX}", f"Beta Ltd {SUFFIX}"
boss = person("boss", "acme.test")
code_acme = make_company(ACME, boss.email)
check("a stranger cannot create a company through the API", person("rogue", "x.test").api("organization/create", {"name": f"Rogue {SUFFIX}", "slug": f"rogue-{SUFFIX}"})[0] >= 400)
try:
    make_company(ACME.upper() + ".", "other@x.test")
    check("a company name that folds to an existing one is refused", False, "accepted")
except psycopg.errors.UniqueViolation:
    check("a company name that folds to an existing one is refused", True)
check("before accepting, the first admin sees no company and no data", org_id_of(boss) == "" and boss.rest("GET", "last_records?select=agent")[1] == [])

squatter = person("squat", "evil.test")
check("someone with another address cannot use the first admin's code", squatter.api("organization/accept-invitation", {"invitationId": code_acme})[0] >= 400)
code, data, _ = boss.api("organization/accept-invitation", {"invitationId": code_acme})
check("the first admin accepts the code and becomes owner", code == 200 and org_id_of(boss) != "")
ACME_ID = org_id_of(boss)
check("the code cannot be used twice", boss.api("organization/accept-invitation", {"invitationId": code_acme})[0] >= 400)

# ---------- 2. the admin invites people into their own company ----------
ann = person("ann", "acme.test")
code, inv, _ = boss.api("organization/invite-member", {"email": ann.email, "role": "member", "organizationId": ACME_ID})
check("the admin invites a member and gets an invitation code", code == 200 and inv.get("id"))
code, listing, _ = call("GET", f"{AUTH}/organization/list-invitations?organizationId={ACME_ID}", cookie=boss.cookie)
check("the admin lists pending invitations", code == 200 and any(i["email"] == ann.email and i["status"] == "pending" for i in listing), str(listing)[:120])
check("a member cannot list invitations", call("GET", f"{AUTH}/organization/list-invitations?organizationId={ACME_ID}", cookie=ann.cookie)[0] >= 400)
check("a member accepts with the code", ann.api("organization/accept-invitation", {"invitationId": inv["id"]})[0] == 200 and org_id_of(ann) == ACME_ID)
code, fo, _ = call("GET", f"{AUTH}/organization/list-members?organizationId={ACME_ID}", cookie=boss.cookie)
members = {m["user"]["email"]: m["role"] for m in fo["members"]} if code == 200 else {}
check("the admin sees the people of their company with roles", members == {boss.email: "owner", ann.email: "member"}, str(members))
check("a member cannot invite", ann.api("organization/invite-member", {"email": f"z-{SUFFIX}@acme.test", "role": "member", "organizationId": ACME_ID})[0] >= 400)
check("a member cannot make themselves admin", ann.api("organization/update-member-role", {"memberId": next(m["id"] for m in fo["members"] if m["user"]["email"] == ann.email), "role": "admin", "organizationId": ACME_ID})[0] >= 400)

# ---------- 3. one company per person; companies cannot see each other ----------
make_company(BETA, f"chief-{SUFFIX}@beta.test")
chief = person("chief", "beta.test")
code_beta = str(operator("select id from neon_auth.invitation where email = %s", (chief.email,))[0][0])
check("the other company's admin accepts their own code", chief.api("organization/accept-invitation", {"invitationId": code_beta})[0] == 200)
BETA_ID = org_id_of(chief)
check("an admin cannot invite into another company", boss.api("organization/invite-member", {"email": f"q-{SUFFIX}@x.test", "role": "member", "organizationId": BETA_ID})[0] >= 400)
check("an admin cannot invite someone who already belongs to a company", boss.api("organization/invite-member", {"email": chief.email, "role": "member", "organizationId": ACME_ID})[0] >= 400)
check("a company's people are not visible to another company", call("GET", f"{AUTH}/organization/list-members?organizationId={ACME_ID}", cookie=chief.cookie)[0] >= 400)

# ---------- 4. the last record ----------
r = fresh("candidate", ACME_ID, name="Ann Rao", title="Engineer", profile_url="https://example.com/in/ann?id=123456789012")
for _try in range(8):   # right after joining, Neon Auth and the Data API can take a moment to agree; the app retries too
    code, body, _ = ann.rest("POST", "last_records?on_conflict=tenant_id,agent", r, "resolution=merge-duplicates,return=minimal")
    if code in (200, 201, 204):
        break
    time.sleep(1.5)
check("a member stores the last record", code in (200, 201, 204), f"{code} {body}")
code, rows, _ = boss.rest("GET", "last_records?select=agent,record,stored_by_email")
check("the admin sees it, with who stored it", code == 200 and len(rows) == 1 and rows[0]["stored_by_email"] == ann.email, str(rows)[:150])
check("another company sees nothing", chief.rest("GET", "last_records?select=agent")[1] == [])
check("another company cannot write into this one", chief.rest("POST", "last_records", fresh("candidate", ACME_ID, name="X"), "return=minimal")[0] == 403)
check("a stranger with no company cannot write", squatter.rest("POST", "last_records", fresh("candidate", ACME_ID, name="X"), "return=minimal")[0] == 403)
newer = {**r, "record": {"name": "Bo Sen"}, "subject_hash": secrets.token_hex(32), "created_at": "2026-10-19T11:00:00Z"}
check("a newer record replaces it", boss.rest("POST", "last_records?on_conflict=tenant_id,agent", newer, "resolution=merge-duplicates,return=minimal")[0] in (200, 201, 204))
older = {**r, "record": {"name": "Old One"}, "subject_hash": secrets.token_hex(32), "created_at": "2026-10-19T09:00:00Z"}
check("an older record is refused with 409", boss.rest("POST", "last_records?on_conflict=tenant_id,agent", older, "resolution=merge-duplicates,return=minimal")[0] == 409)
check("the same record again changes nothing", boss.rest("POST", "last_records?on_conflict=tenant_id,agent", newer, "resolution=merge-duplicates,return=minimal")[0] in (200, 201, 204))
check("the newer record is the one stored", boss.rest("GET", "last_records?select=record")[1][0]["record"] == {"name": "Bo Sen"})
for label, rec in [("a field that is not on the list", {"notes": "x"}), ("an email address", {"company": "ann@x.com"}), ("a phone number", {"company": "call +91 98765 43210"}),
                   ("a value that is too long", {"company": "x" * 301}), ("a nested value", {"company": {"a": 1}})]:
    bad = fresh("account", ACME_ID, **rec)
    check(f"{label} is refused", boss.rest("POST", "last_records", bad, "return=minimal")[0] in (400, 422), "accepted")
check("a long url value and digits inside a url are accepted", boss.rest("POST", "last_records?on_conflict=tenant_id,agent", fresh("company", ACME_ID, listing_url="https://x.com/j/12345678901234" + "a" * 100), "resolution=merge-duplicates,return=minimal")[0] in (200, 201, 204))
code, gone, _ = boss.rest("DELETE", f"last_records?tenant_id=eq.{ACME_ID}&agent=eq.candidate&subject_hash=eq.{secrets.token_hex(32)}", prefer="return=representation")
check("deleting with the wrong hash removes nothing", code == 200 and gone == [], str(gone))
code, gone, _ = boss.rest("DELETE", f"last_records?tenant_id=eq.{ACME_ID}&agent=eq.candidate&subject_hash=eq.{newer['subject_hash']}", prefer="return=representation")
check("deleting with the right hash removes that record", code == 200 and len(gone) == 1)

# ---------- 4b. search history: own searches and rows, admins see everyone's ----------
bob = person("bob", "acme.test")
_, binv, _ = boss.api("organization/invite-member", {"email": bob.email, "role": "member", "organizationId": ACME_ID})
bob.api("organization/accept-invitation", {"invitationId": binv["id"]})
import uuid as _uuid
def new_search(person_, agent="candidate", **inputs):
    sid = str(_uuid.uuid4())
    body = {"id": sid, "tenant_id": ACME_ID, "agent": agent, "inputs": inputs or {"jd": "Senior React developer"}, "started_at": "2026-10-19T10:00:00Z",
            "user_id": "someone-else", "user_email": "boss@example.com"}   # the database must ignore who the app claims to be
    return sid, person_.rest("POST", "searches?on_conflict=id", body, "resolution=merge-duplicates,return=minimal")
def new_row(person_, search_id, agent="candidate", **rec):
    body = {"tenant_id": ACME_ID, "agent": agent, "search_id": search_id, "subject_hash": secrets.token_hex(32), "created_at": "2026-10-19T10:05:00Z", "record": rec or {"name": "Row " + secrets.token_hex(2)}}
    return body, person_.rest("POST", "shared_rows?on_conflict=tenant_id,user_id,agent,subject_hash", body, "resolution=ignore-duplicates,return=minimal")
ann2 = person("ann2", "acme.test")   # a member who is not removed later
_, a2inv, _ = boss.api("organization/invite-member", {"email": ann2.email, "role": "member", "organizationId": ACME_ID})
ann2.api("organization/accept-invitation", {"invitationId": a2inv["id"]})
s_ann, r = new_search(ann2)
check("a member stores their search", r[0] in (200, 201, 204), str(r[:2]))
code, mine, _ = ann2.rest("GET", f"searches?id=eq.{s_ann}&select=user_email,user_id,status")
check("the database stamps who searched from the sign-in, not from what the app says", code == 200 and len(mine) == 1 and mine[0]["user_email"] == ann2.email and mine[0]["user_id"] != "someone-else", str(mine))
rows_ann = [new_row(ann2, s_ann, name=f"Cand {i}", title="Engineer", match_score=80 + i)[0] for i in range(3)]
check("a member stores result rows linked to their search", len(ann2.rest("GET", f"shared_rows?search_id=eq.{s_ann}&select=id")[1]) == 3)
dup_body = rows_ann[0]
check("the same row sent again changes nothing", new_row.__call__ and ann2.rest("POST", "shared_rows?on_conflict=tenant_id,user_id,agent,subject_hash", dup_body, "resolution=ignore-duplicates,return=minimal")[0] in (200, 201, 204) and len(ann2.rest("GET", f"shared_rows?search_id=eq.{s_ann}&select=id")[1]) == 3)
s_bob, _ = new_search(bob, jd="Backend engineer, Go")
new_row(bob, s_bob, name="Bob's find")
body_x, rx = new_row(bob, s_ann, name="Linked to someone else's search")
check("a row cannot be linked to another person's search", rx[0] in (200, 201, 204) and bob.rest("GET", f"shared_rows?search_id=eq.{s_ann}&select=id")[1] == [])
check("a member sees only their own searches", [x["id"] for x in ann2.rest("GET", "searches?select=id")[1]] == [s_ann])
check("a member sees only their own rows", len(ann2.rest("GET", "shared_rows?select=id")[1]) == 3 and all(x["user_email"] == ann2.email for x in ann2.rest("GET", "shared_rows?select=user_email")[1]))
check("another member sees none of those", s_ann not in [x["id"] for x in bob.rest("GET", "searches?select=id")[1]])
adm = boss.rest("GET", "searches?select=id,user_email")[1]
check("the admin sees everyone's searches in the company", {x["user_email"] for x in adm} >= {ann2.email, bob.email})
check("the admin sees everyone's rows in the company", {x["user_email"] for x in boss.rest("GET", "shared_rows?select=user_email")[1]} >= {ann2.email, bob.email})
check("another company sees nothing", chief.rest("GET", "searches?select=id")[1] == [] and chief.rest("GET", "shared_rows?select=id")[1] == [])
check("another company cannot write into this one", new_search(chief)[1][0] == 403 and new_row(chief, None)[1][0] == 403)
check("the embedded count is available for the list", ann2.rest("GET", f"searches?id=eq.{s_ann}&select=id,shared_rows(count)")[1][0]["shared_rows"][0]["count"] == 3)
check("a member updates their own search status", ann2.rest("PATCH", f"searches?id=eq.{s_ann}", {"status": "done", "finished_at": "2026-10-19T10:30:00Z", "user_email": "x@y.zz"}, "return=representation")[1][0]["status"] == "done"
      and ann2.rest("GET", f"searches?id=eq.{s_ann}&select=user_email")[1][0]["user_email"] == ann2.email)
check("another member cannot change it", bob.rest("PATCH", f"searches?id=eq.{s_ann}", {"status": "failed"}, "return=representation")[1] == [] and ann2.rest("GET", f"searches?id=eq.{s_ann}&select=status")[1][0]["status"] == "done")
check("another member cannot delete it", bob.rest("DELETE", f"searches?id=eq.{s_ann}", prefer="return=representation")[1] == [] and bob.rest("DELETE", f"shared_rows?search_id=eq.{s_ann}", prefer="return=representation")[1] == [])
for label, rec in [("a field that is not on the list", {"notes": "x"}), ("an email address", {"company": "ann@x.com"}), ("a phone number", {"company": "call +91 98765 43210"}), ("a nested value", {"company": {"a": 1}})]:
    check(f"a result row with {label} is refused", new_row(ann2, s_ann, **rec)[1][0] in (400, 422))
check("a search with a field that is not allowed is refused", new_search(ann2, notes="secret")[1][0] in (400, 422) and new_search(ann2, jd={"a": 1})[1][0] in (400, 422) and new_search(ann2, agent="candidate", csv="x")[1][0] in (400, 422))
check("a search for a company agent keeps its own fields", new_search(ann2, agent="company", company="Razorpay", domain="razorpay.com", publicPosts=True)[1][0] in (200, 201, 204))
check("a person erases a result by its hash, only their own", len(ann2.rest("DELETE", f"shared_rows?agent=eq.candidate&subject_hash=eq.{rows_ann[0]['subject_hash']}", prefer="return=representation")[1]) == 1 and len(ann2.rest("GET", f"shared_rows?search_id=eq.{s_ann}&select=id")[1]) == 2)
check("the admin can remove anyone's search and its rows", len(boss.rest("DELETE", f"searches?id=eq.{s_ann}", prefer="return=representation")[1]) == 1 and boss.rest("GET", f"shared_rows?search_id=eq.{s_ann}&select=id")[1] == [])
s_own, _ = new_search(bob, jd="to delete myself"); new_row(bob, s_own, name="x")
check("a member can remove their own search and its rows", len(bob.rest("DELETE", f"searches?id=eq.{s_own}", prefer="return=representation")[1]) == 1)

# ---------- 4c. the allowance: each person's own cap and window, counted over all the assistants together ----------
def pool(person_):
    code, st, _ = person_.rest("POST", "rpc/allowance_state", {})
    return code, st
def add_tokens(person_, n, agent="candidate"):
    code, st, _ = person_.rest("POST", "rpc/allowance_add", {"p_agent": agent, "p_tokens": n})
    return code, st
set_user = lambda who, email, cap, minutes: who.rest("POST", "rpc/allowance_set_user", {"p_email": email, "p_cap": cap, "p_window_minutes": minutes})
people = lambda who: {x["email"]: x for x in who.rest("POST", "rpc/allowance_people", {})[1]["people"]}
code, st = pool(ann2)
check("a member reads their own allowance; a new person starts at 3,000,000 tokens per 4 hours", code == 200 and (st["cap"], st["window_minutes"], st["counted"], st["window_started_ms"], st["locked_at_ms"]) == (3000000, 240, 0, None, None), str(st)[:200])
check("the database's own clock is reported", isinstance(st["now_ms"], int) and abs(st["now_ms"] / 1000 - time.time()) < 300)
check("an account with no company cannot read it", pool(squatter)[0] in (400, 403))
add_tokens(ann2, 1000, "candidate")
_, a2 = add_tokens(ann2, 500, "company")
check("one person's tokens add up over all the assistants", a2["counted"] == 1500, str(a2))
_, b1 = add_tokens(bob, 200, "account")
check("another person has their own count", b1["counted"] == 200 and pool(ann2)[1]["counted"] == 1500 and pool(boss)[1]["counted"] == 0)
check("each person has their own window", pool(ann2)[1]["window_started_ms"] and pool(bob)[1]["window_started_ms"] and pool(boss)[1]["window_started_ms"] is None)
check("another company has its own counts", pool(chief)[1]["counted"] == 0 and add_tokens(chief, 700)[1]["counted"] == 700 and pool(ann2)[1]["counted"] == 1500)
check("a count of zero opens no window", add_tokens(boss, 0, "company")[1]["window_started_ms"] is None)
for label, n in [("a negative number of tokens", -5), ("an enormous number of tokens", 5000001)]:
    check(f"{label} is refused", add_tokens(ann2, n)[0] in (400, 422))
check("an unknown assistant is refused", ann2.rest("POST", "rpc/allowance_add", {"p_agent": "robot", "p_tokens": 1})[0] in (400, 422))
check("there is no company-wide setting any more", boss.rest("POST", "rpc/allowance_set", {"p_cap": 99999, "p_window_minutes": 60})[0] in (404, 400))
check("a member cannot set anyone's cap or window, not even their own", set_user(ann2, bob.email, 1000, 15)[0] == 403 and set_user(ann2, ann2.email, 1000000000, 1440)[0] == 403 and pool(bob)[1]["cap"] == 3000000)
check("a member cannot reset anyone", ann2.rest("POST", "rpc/allowance_reset", {})[0] == 403 and ann2.rest("POST", "rpc/allowance_reset", {"p_email": bob.email})[0] == 403 and pool(ann2)[1]["counted"] == 1500 and pool(bob)[1]["counted"] == 200)
check("a member cannot list everyone's allowance, an admin can", ann2.rest("POST", "rpc/allowance_people", {})[0] == 403 and {x["email"] for x in boss.rest("POST", "rpc/allowance_people", {})[1]["people"]} >= {ann2.email, bob.email})
check("an admin gives one person their own cap and window, and nobody else's changes", set_user(boss, bob.email, 1500, 30)[0] == 200 and (pool(bob)[1]["cap"], pool(bob)[1]["window_minutes"]) == (1500, 30) and (pool(ann2)[1]["cap"], pool(ann2)[1]["window_minutes"]) == (3000000, 240), str((pool(bob), pool(ann2))))
bp = people(boss)
check("the overview shows each person's own cap, window and count", (bp[bob.email]["cap"], bp[bob.email]["window_minutes"], bp[bob.email]["counted"]) == (1500, 30, 200) and (bp[ann2.email]["cap"], bp[ann2.email]["counted"]) == (3000000, 1500))
check("the overview lists every member, also one who has used nothing", boss.email in bp and bp[boss.email]["counted"] == 0 and bp[boss.email]["cap"] == 3000000)
_, lb = add_tokens(bob, 1400, "account")
check("a person's own cap decides their lock, over all assistants, and nobody else's", lb["counted"] == 1600 and lb["locked_at_ms"] and pool(ann2)[1]["locked_at_ms"] is None and pool(boss)[1]["locked_at_ms"] is None)
check("raising their cap lifts their lock", set_user(boss, bob.email, 5000, 30)[0] == 200 and pool(bob)[1]["locked_at_ms"] is None)
check("lowering it below their count locks them again", set_user(boss, bob.email, 1600, 30)[0] == 200 and pool(bob)[1]["locked_at_ms"] and pool(ann2)[1]["locked_at_ms"] is None)
check("an admin sets their own cap too", set_user(boss, boss.email, 2000, 60)[0] == 200 and (pool(boss)[1]["cap"], pool(boss)[1]["window_minutes"]) == (2000, 60))
check("an admin can set a person who has not used the app yet", set_user(boss, ann2.email, 2100, 60)[0] == 200 and (pool(ann2)[1]["cap"], pool(ann2)[1]["window_minutes"], pool(ann2)[1]["counted"]) == (2100, 60, 1500))
check("a person from another company or nobody is refused", set_user(boss, chief.email, 5000, 60)[0] in (400, 422) and set_user(boss, "nobody@nowhere.example", 5000, 60)[0] in (400, 422) and pool(chief)[1]["cap"] == 3000000)
for label, body in [("a cap under 1,000", (999, 60)), ("a cap over 1,000,000,000", (1000000001, 60)), ("a window under 15 minutes", (5000, 14)), ("a window over 24 hours", (5000, 1441)), ("an empty cap", (None, 60)), ("an empty window", (5000, None))]:
    check(f"{label} is refused", set_user(boss, bob.email, *body)[0] in (400, 422))
check("a person's window ends on the database's clock with their own window length: their count returns to zero and their lock lifts, others are untouched",
      operator("update public.allowance_use set window_started = now() - interval '40 minutes' where tenant_id = %s and lower(user_email) = lower(%s) returning 1", (ACME_ID, bob.email)) is not None
      and pool(bob)[1]["counted"] == 0 and pool(bob)[1]["locked_at_ms"] is None and pool(bob)[1]["window_started_ms"] is None and pool(ann2)[1]["counted"] == 1500)
add_tokens(bob, 40)
check("the next tokens open a new window", pool(bob)[1]["counted"] == 40 and pool(bob)[1]["window_started_ms"])
check("an admin resets one person at once", boss.rest("POST", "rpc/allowance_reset", {"p_email": ann2.email})[0] == 200 and pool(ann2)[1]["counted"] == 0 and pool(bob)[1]["counted"] == 40)
check("a reset keeps their own cap and window", (pool(ann2)[1]["cap"], pool(ann2)[1]["window_minutes"]) == (2100, 60))
check("an admin resets everyone at once", boss.rest("POST", "rpc/allowance_reset", {})[0] == 200 and pool(bob)[1]["counted"] == 0)
check("a member cannot write the tables directly", all(ann2.rest("PATCH", f"{t}?tenant_id=eq.{ACME_ID}", {k: 1000000000}, "return=representation")[0] in (401, 403) or ann2.rest("GET", f"{t}?tenant_id=eq.{ACME_ID}&select={k}")[1] == [] or all(r[k] != 1000000000 for r in ann2.rest("GET", f"{t}?tenant_id=eq.{ACME_ID}&select={k}")[1]) for t, k in [("allowance_use", "cap"), ("allowance_use", "counted")]))
check("nor the log", ann2.rest("POST", "allowance_log", {"tenant_id": ACME_ID, "user_id": "x", "agent": "candidate", "tokens": 1}, "return=minimal")[0] in (401, 403))
check("a member reads their own part of the log, an admin reads everyone's", {x["user_email"] for x in ann2.rest("GET", "allowance_log?select=user_email")[1]} == {ann2.email} and {x["user_email"] for x in boss.rest("GET", "allowance_log?select=user_email")[1]} >= {ann2.email, bob.email})
check("another company sees none of this log", chief.rest("GET", "allowance_log?select=id")[1] != [] and all(x["user_email"] == chief.email for x in chief.rest("GET", "allowance_log?select=user_email")[1]))
check("without a token the functions refuse", call("POST", f"{API}/rpc/allowance_state", {})[0] in (400, 401, 403))

# ---------- 4d. Excel results are not kept in the database ----------
check("there is no table for workbook rows, nor an archive of them", operator("select count(*) from information_schema.tables where table_schema = 'public' and starts_with(table_name, 'workbook')")[0][0] == 0)
check("storing workbook rows through the API is refused", ann2.rest("POST", "workbook_rows", {"tenant_id": ACME_ID, "agent": "candidate", "sheet": "S", "row_no": 2, "data": {"a": 1}}, "return=minimal")[0] in (400, 401, 403, 404))
check("the workbook functions are gone", all(ann2.rest("POST", f"rpc/{fn}", {})[0] in (400, 404) for fn in ("workbook_trim", "workbook_drop_sheets", "workbook_stamp")))

# ---------- 4e. application logs: anyone in a company writes their own lines, only admins read ----------
logw = lambda who, items: who.rest("POST", "rpc/log_write", {"p_items": items})
check("a member writes log lines and gets how many were kept", logw(ann2, [{"kind": "event", "level": "info", "message": "App started", "details": {"version": "0.1.0", "os": "windows"}}, {"kind": "run", "level": "warn", "agent": "candidate", "message": "Step 3/6 \u2713 scored", "at_ms": int(time.time() * 1000)}])[1] == 2)
check("a member cannot read the logs, not even their own", ann2.rest("GET", "app_logs?select=id")[1] == [] or ann2.rest("GET", "app_logs?select=id")[0] in (401, 403))
rows = boss.rest("GET", "app_logs?select=user_email,kind,level,agent,message,details,client_at&order=id.asc")[1]
check("an admin reads everyone's lines, stamped with who wrote them", [(r["user_email"], r["kind"], r["message"]) for r in rows if r["user_email"] == ann2.email] == [(ann2.email, "event", "App started"), (ann2.email, "run", "Step 3/6 \u2713 scored")] and rows[0]["details"] == {"version": "0.1.0", "os": "windows"} and rows[1]["client_at"])
check("another company sees none of them", chief.rest("GET", "app_logs?select=id")[1] == [])
check("an unknown kind or level is refused", logw(ann2, [{"kind": "secret", "message": "x"}])[0] in (400, 422) and logw(ann2, [{"kind": "event", "level": "fatal", "message": "x"}])[0] in (400, 422))
check("an empty message is refused", logw(ann2, [{"kind": "event", "message": ""}])[0] in (400, 422))
check("more than 50 lines in one call is refused", logw(ann2, [{"kind": "event", "message": "x"}] * 51)[0] in (400, 422))
check("a long message is cut, oversized details are dropped", logw(ann2, [{"kind": "event", "message": "m" * 700, "details": {"x": "y" * 5000}}])[1] == 1 and boss.rest("GET", "app_logs?select=message,details&order=id.desc&limit=1")[1][0] == {"message": "m" * 600, "details": None})
check("an account with no company cannot write", logw(squatter, [{"kind": "event", "message": "x"}])[0] in (400, 403))
def _refused(r): return r[0] in (401, 403) or r[1] == []
check("nobody writes, changes or deletes lines through the API", _refused(ann2.rest("POST", "app_logs", {"tenant_id": ACME_ID, "user_id": "x", "kind": "event", "level": "info", "message": "forged"}, "return=minimal")) and _refused(boss.rest("DELETE", "app_logs?id=gt.0", prefer="return=representation")) and _refused(boss.rest("PATCH", "app_logs?id=gt.0", {"message": "changed"}, "return=representation")) and all(r["message"] not in ("forged", "changed") for r in boss.rest("GET", "app_logs?select=message")[1]) and len(boss.rest("GET", "app_logs?select=id")[1]) >= 3)
check("without a token the function refuses", call("POST", f"{API}/rpc/log_write", {"p_items": []})[0] in (400, 401, 403))
operator("update public.app_logs set at = now() - interval '31 days' where message = 'App started' and tenant_id = %s", (ACME_ID,))
logw(ann2, [{"kind": "signin", "message": "Signed in"}])
check("lines older than 30 days are removed by the next write", all(r["message"] != "App started" for r in boss.rest("GET", "app_logs?select=message")[1]))

# ---------- 5. leaving: removing a person cuts access at once ----------
ann.token()
check("a member can read before being removed", ann.rest("GET", "last_records?select=agent")[0] == 200 and len(ann.rest("GET", "last_records?select=agent")[1]) == 1)
check("the admin removes a member", boss.api("organization/remove-member", {"memberIdOrEmail": ann.email, "organizationId": ACME_ID})[0] == 200)
check("the removed person's still-valid token now sees nothing", ann.rest("GET", "last_records?select=agent")[1] == [])
check("the removed person's session still answers normally and sees no company",
      call("GET", f"{AUTH}/get-session", cookie=ann.cookie)[0] == 200 and call("GET", f"{AUTH}/token", cookie=ann.cookie)[0] == 200
      and call("GET", f"{AUTH}/organization/list", cookie=ann.cookie)[:2] == (200, []))
check("the only owner cannot be removed", boss.api("organization/remove-member", {"memberIdOrEmail": boss.email, "organizationId": ACME_ID})[0] >= 400)

# ---------- 6. session ----------
code, sess, _ = call("GET", f"{AUTH}/get-session", cookie=boss.cookie)
check("the session cookie identifies the user", code == 200 and sess["user"]["email"] == boss.email)
call("POST", f"{AUTH}/sign-out", {}, cookie=boss.cookie)
check("after sign-out the cookie is dead", call("GET", f"{AUTH}/get-session", cookie=boss.cookie)[1] is None)
check("a wrong password is refused", call("POST", f"{AUTH}/sign-in/email", {"email": chief.email, "password": "wrong password here"})[0] == 401)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
raise SystemExit(1 if failed else 0)
