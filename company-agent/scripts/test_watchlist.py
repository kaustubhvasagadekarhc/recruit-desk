"""Offline tests for the watchlist, the digest and the weekly re-scan (2026-10-18). Temp databases only; no network.

Run: uv run python scripts/test_watchlist.py
"""
from __future__ import annotations

import datetime as dt
import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digest  # noqa: E402
import memory  # noqa: E402
import rescan_watchlist as rw  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name)


tmp = Path(tempfile.mkdtemp(prefix="wl-"))
con = memory.connect(tmp / "memory.db")
TODAY = dt.date(2026, 10, 18)
SINCE = digest.parse_since("7d", TODAY)   # the 7 days ending today: 2026-10-12 .. 2026-10-18

# ---- since parsing ----
check("7d is the 7 days ending today, 2w the 14, an ISO date is itself", SINCE == dt.date(2026, 10, 12) and digest.parse_since("2w", TODAY) == dt.date(2026, 10, 5)
      and digest.parse_since("2026-09-01", TODAY) == dt.date(2026, 9, 1))
try:
    digest.parse_since("last week", TODAY)
    bad = False
except ValueError:
    bad = True
check("a vague period is refused", bad)
try:
    digest.parse_since("99999d", TODAY)
    huge = False
except ValueError:
    huge = True
check("an absurd period is a clean error, not an OverflowError", huge)
check("two weekly runs a week apart share no day", digest.parse_since("7d", TODAY - dt.timedelta(days=7)) + dt.timedelta(days=7) == SINCE)
check("a UTC scan time is converted to the local day, not cut at ten characters",
      digest.local_day("2026-10-16T20:30:00+00:00") == dt.datetime(2026, 10, 16, 20, 30, tzinfo=dt.timezone.utc).astimezone().date().isoformat()
      and digest.local_day("") == "" and digest.local_day("2026-10-16") == "2026-10-16")

# ---- watchlist ----
try:
    memory.watch_add(con, "newco.example")
    refused = False
except ValueError:
    refused = True
check("a never-scanned company needs --name", refused)
try:
    memory.watch_add(con, "not a domain", name="X")
    refused = False
except ValueError:
    refused = True
check("a non-domain is refused", refused)


def company(name, domain, first, last):
    cid = memory.upsert_company(con, name, domain)
    con.execute("UPDATE companies SET first_scanned=?, last_scanned=? WHERE id=?", (first, last, cid))
    con.commit()
    return cid


def job(cid, key, title, found, status="Open", url=None, last=None, loc="Pune", mode="Unknown", posted=None):
    con.execute("INSERT INTO jobs (company_id, job_key, url, title, location, source_type, posted_date, date_discovered, last_checked, status, work_mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, key, url if url is not None else f"https://boards.greenhouse.io/acme/jobs/{key}", title, loc, "ATS: Greenhouse", posted,
                 found, last or found, status, mode))
    con.commit()


acme = company("Acme", "acme.com", "2026-09-01", "2026-10-17")
job(acme, "1", "Backend Engineer", "2026-09-01")                                       # baseline, old
job(acme, "2", "Senior Designer", "2026-10-14", posted="2026-10-13", mode="Remote")     # new, in window
job(acme, "3", "Data Engineer", "2026-10-16", url="https://www.linkedin.com/jobs/search?keywords=acme")   # new, aggregate listing
job(acme, "4", "Office Manager", "2026-10-15")                                          # new, not matching keywords
job(acme, "5", "QA Lead", "2026-10-02", status="Closed", last="2026-10-14")             # closed in window
job(acme, "6", "Old Closed Role", "2026-08-01", status="Closed", last="2026-09-10")     # closed before the window
job(acme, "7", "Brand New But Closed", "2026-10-15", status="Closed", last="2026-10-16")  # new and already closed
beta = company("Beta", "beta.io", "2026-10-16", "2026-10-16")                           # first scan in the window
job(beta, "b1", "SRE", "2026-10-16")
gamma = company("Gamma", "gamma.dev", "2026-08-01", "2026-09-20")                       # not scanned in the window
job(gamma, "g1", "Intern", "2026-08-01")

r = memory.watch_add(con, "https://www.Acme.com/careers", keywords=" engineer , designer ,")
check("watch add normalises the domain, takes the stored name and cleans the keywords",
      r == {"domain": "acme.com", "name": "Acme", "keywords": "engineer,designer", "scanned_before": True})
memory.watch_add(con, "beta.io")
memory.watch_add(con, "gamma.dev")
memory.watch_add(con, "newco.example", name="NewCo")
check("adding again updates, not duplicates", memory.watch_add(con, "acme.com", keywords="engineer,designer")["keywords"] == "engineer,designer"
      and len(memory.watch_list(con)) == 4)
check("adding again without --keywords keeps them; an empty --keywords clears them",
      memory.watch_add(con, "acme.com")["keywords"] == "engineer,designer" and memory.watch_add(con, "acme.com", keywords="")["keywords"] == ""
      and memory.watch_add(con, "acme.com", keywords="engineer,designer")["keywords"] == "engineer,designer")
wl = {w["domain"]: w for w in memory.watch_list(con)}
check("watch list shows open job counts and last scan", wl["acme.com"]["open_jobs"] == 4 and wl["newco.example"]["open_jobs"] is None)

# ---- digest ----
text, total = digest.build(con, SINCE, today=TODAY)
check("totals: 2 new listed, 1 closed, 1 baseline company, 1 stale, 1 never scanned",
      (total["companies"], total["new"], total["closed"], total["stale"], total["unscanned"]) == (4, 2, 1, 1, 1))
check("a new role in the window is listed with its posted date and work mode",
      "**Senior Designer** · Pune · Remote · posted 2026-10-13" in text)
check("every per-job URL gets a ready /scout line", "`/scout https://boards.greenhouse.io/acme/jobs/2`" in text)
check("a LinkedIn listing page gets no /scout line, only a note",
      "**Data Engineer**" in text and "/scout https://www.linkedin.com" not in text and "this listing cannot be fetched" in text)
check("keywords hide non-matching new roles but count them", "Office Manager" not in text and "1 other new role(s) did not match the keywords" in text)
check("old roles and a baseline role are not new", "Backend Engineer" not in text and "SRE" not in text.split("## Beta")[1].split("##")[0].replace("baseline", ""))
check("a role closed in the window is listed; one closed earlier is not", "- QA Lead" in text and "Old Closed Role" not in text)
check("a role found and closed inside the period is reported nowhere", "Brand New But Closed" not in text)
check("a company's first scan is a baseline, counted but not listed as new",
      "First scan on 2026-10-16: 1 open roles are the baseline" in text)
check("a company not scanned in the period is warned about", "Last scanned 2026-09-20, before 2026-10-12" in text)
check("a never-scanned watch says so", "## NewCo (newco.example)" in text and "Not scanned yet" in text)

alltext, alltotal = digest.build(con, SINCE, all_companies=True, today=TODAY)
check("--all covers every company, with no keyword filter", alltotal["companies"] == 3 and "Office Manager" in alltext)

empty = memory.connect(tmp / "empty.db")
t2, tot2 = digest.build(empty, SINCE, today=TODAY)
check("an empty watchlist says how to add one", tot2["companies"] == 0 and "watchlist is empty" in t2)

# ---- remove ----
check("watch remove takes a company off by domain, and says no when absent",
      memory.watch_remove(con, "newco.example") and not memory.watch_remove(con, "newco.example"))

# ---- the CLI refuses to write outside runs/ ----
sys.argv = ["digest.py", "--out", "/tmp/not-runs.md"]
try:
    with redirect_stdout(io.StringIO()):
        digest.main()
    outside = False
except SystemExit as e:
    outside = "runs/" in str(e)
check("the digest is written under runs/ only", outside)

# ---- weekly re-scan: one failure does not stop the others ----
calls = []


def fake_run_one(name, domain):
    calls.append(domain)
    return (domain != "beta.io", "jobs: 4 (Open 4)" if domain != "beta.io" else "scan failed: HTTP 503")


rw.run_one = fake_run_one
rw.memory.connect = lambda *a, **k: con
sys.argv = ["rescan_watchlist.py", "--no-digest"]
out = io.StringIO()
with redirect_stdout(out):
    try:
        rw.main()
        code = 0
    except SystemExit as e:
        code = e.code
check("re-scan visits every watched company in order and reports each", calls == ["acme.com", "beta.io", "gamma.dev"]
      and "✗ beta.io: scan failed: HTTP 503" in out.getvalue() and "✓ acme.com: jobs: 4 (Open 4)" in out.getvalue())
check("it exits 2 when some companies failed (the others are saved)", code == 2)
calls.clear()
sys.argv = ["rescan_watchlist.py", "--no-digest", "--domain", "beta.io"]
with redirect_stdout(io.StringIO()):
    try:
        rw.main()
        code = 0
    except SystemExit as e:
        code = e.code
check("--domain limits the re-scan, and all-failed exits 1", calls == ["beta.io"] and code == 1)
sys.argv = ["rescan_watchlist.py", "--no-digest", "--domain", "typo.example"]
try:
    with redirect_stdout(io.StringIO()):
        rw.main()
    msg = ""
except SystemExit as e:
    msg = str(e.code)
check("a --domain that is not watched says so, not 'the watchlist is empty'", "none of typo.example is on the watchlist" in msg)

# merge moves the watch to the company's final domain
con.execute("INSERT INTO companies (name, domain, first_scanned, last_scanned) VALUES ('Old Name','old-acme.com','2026-09-01','2026-09-02')")
con.commit()
memory.watch_add(con, "old-acme.com", name="Old Name")
memory.merge_company(con, "old-acme.com", "acme.com")
check("after a merge the watch is on the final domain, once", [w["domain"] for w in memory.watch_list(con)].count("old-acme.com") == 0
      and [w["domain"] for w in memory.watch_list(con)].count("acme.com") == 1)

shutil.rmtree(tmp, ignore_errors=True)
bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
