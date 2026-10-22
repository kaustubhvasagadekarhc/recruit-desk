"""Offline tests for the improvement-plan items built on 2026-10-15:
item 3 (ATS feed first, crawl only when needed), item 4 (safe Closed: complete reads only, two
misses in a row), item 5 (one-command pipeline, short output) and quick wins 1 (shared company blurb
removed from summaries), 2 (feed-complete check), 3 (retries with backoff and 429 handling) and
8 (Workday summaries from the posting JSON).

Run: uv run python scripts/test_improvements.py   (no network; scratch copies only)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats  # noqa: E402
import crawl  # noqa: E402
import dedupe  # noqa: E402
import memory  # noqa: E402
import netsafe  # noqa: E402
import progress  # noqa: E402
import scan  # noqa: E402
from common import ROOT, shared_prefix, strip_prefix  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name)


tmp = Path(tempfile.mkdtemp(prefix="ci-"))
# Never touch the real progress state, memory.db or runs/ from this process.
progress.STATE = tmp / ".current.json"
_connect = memory.connect
DB = tmp / "m.db"
memory.connect = lambda path=DB: _connect(path)
scan.RUNS_DIR = dedupe.RUNS_DIR = tmp / "runs"


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers, self.closed = status, body, headers or {}, False

    def close(self):
        self.closed = True

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


try:
    # ---- quick win 1: shared company blurb -----------------------------------------------------
    blurb = ("Razorpay is one of India's leading full-stack financial technology companies. "
             "We power payments for millions of businesses. ")
    descs = [blurb + f"As {r} you will own {x}." for r, x in
             [("Analyst", "partner reporting"), ("Engineer", "the payments API"), ("Designer", "checkout flows"),
              ("Manager", "the banking team"), ("Recruiter", "campus hiring")]]
    pre = shared_prefix(descs)
    check("blurb: shared prefix found and cut at a sentence end", " ".join(pre) == blurb.strip())
    check("blurb: summary starts with the role text", strip_prefix(descs[1], pre) == "As Engineer you will own the payments API.")
    check("blurb: no prefix when texts do not share one", shared_prefix(["a b c d e f g h.", "x y z", "p q r s"]) == [])
    from common import strip_labels  # noqa: E402
    check("labels: leading 'Job Description JOB SUMMARY' removed",
          strip_labels("Job Description JOB SUMMARY The Maintenance Technician engages") == "The Maintenance Technician engages")
    rows = [{"Job Title": f"R{i}", "_desc": d} for i, d in enumerate(descs)] + [{"Job Title": "No text"}]
    ats.summarise(rows)
    check("blurb: ats.summarise fills Description Summary without the blurb, drops _desc",
          rows[0]["Description Summary"].startswith("As Analyst") and "_desc" not in rows[0] and rows[-1]["Description Summary"] == "")

    # ---- quick win 3: retries -----------------------------------------------------------------
    waits: list[float] = []

    def seq(*answers):
        it = iter(answers)

        def send():
            a = next(it)
            if isinstance(a, Exception):
                raise a
            return a
        return send

    r = netsafe.with_retries(seq(FakeResp(429, headers={"retry-after": "3"}), FakeResp(200)), sleep=waits.append)
    check("retry: 429 waits Retry-After seconds, then succeeds", r.status_code == 200 and waits == [3.0])
    waits.clear()
    r = netsafe.with_retries(seq(FakeResp(503), FakeResp(502), FakeResp(503)), sleep=waits.append)
    check("retry: 5xx retried twice with 2 s then 5 s, last answer returned", r.status_code == 503 and waits == [2.0, 5.0])
    waits.clear()
    r = netsafe.with_retries(seq(requests.Timeout("t"), FakeResp(200)), sleep=waits.append)
    check("retry: a timeout is retried", r.status_code == 200 and waits == [2.0])
    waits.clear()
    r = netsafe.with_retries(seq(FakeResp(429, headers={"retry-after": "600"}), FakeResp(200)), sleep=waits.append)
    check("retry: Retry-After capped at 30 s", waits == [30.0])
    for exc in (netsafe.Unresolvable("dns"), netsafe.BlockedAddress("private")):
        waits.clear()
        try:
            netsafe.with_retries(seq(exc, FakeResp(200)), sleep=waits.append)
            raised = False
        except requests.RequestException:
            raised = True
        check(f"retry: {type(exc).__name__} is never retried", raised and waits == [])
    r = netsafe.with_retries(seq(FakeResp(404), FakeResp(200)), sleep=waits.append)
    check("retry: 404 is not retried", r.status_code == 404)

    # ---- quick win 2 + 8: Workday completeness and summaries (fake feed) ----------------------
    ats.WORKDAY_DELAY = 0
    crawl.Fetcher.allowed = lambda self, url: True
    BASE = "https://shop.wd1.myworkdayjobs.com"

    def fake_workday(n_list: int, total: int, fresh_total: int, details: dict[str, str]):
        calls = {"POST0": 0, "GET": []}
        posts = [{"title": f"Role {i}", "externalPath": f"/job/City/Role-{i}_R-{i}", "locationsText": "City, ST",
                  "postedOn": "Posted Today", "bulletFields": [f"R-{i}"]} for i in range(n_list)]

        def req(method, url, **kw):
            if method == "POST":
                off = kw["json"]["offset"]
                if off == 0:
                    calls["POST0"] += 1
                t = total if calls["POST0"] == 1 else fresh_total
                return FakeResp(200, {"total": t if off == 0 else 0, "jobPostings": posts[off:off + ats.WORKDAY_PAGE]})
            path = url.split("/ext", 1)[1]
            calls["GET"].append(path)
            return FakeResp(200, {"jobPostingInfo": {"jobDescription": details.get(path, "<p>Plain role text.</p>")}})
        return req, calls

    ats._req, calls = fake_workday(45, 45, 45, {})
    url = lambda i: f"{BASE}/ext/job/City/Role-{i}_R-{i}"  # noqa: E731
    known = {url(0): "Good summary", url(1): "Requisition R-1", url(2): ""}
    from common import normalize_url  # noqa: E402
    known = {normalize_url(k): v for k, v in known.items()}
    ats.WORKDAY_SUMMARIES, ats.WORKDAY_BACKFILL = 3, 0
    got, meta = ats.fetch("workday", "shop.wd1/ext", known=known)
    check("workday: full read is complete", meta["complete"] and meta["read"] == 45 and calls["POST0"] == 1)
    got_by = {r["Job Title"]: r for r in got}
    check("workday: no 'Requisition R-…' placeholder summaries any more",
          not any(r["Description Summary"].startswith("Requisition") for r in got))
    check("workday: new jobs get summaries first, capped per run", len(calls["GET"]) == 3
          and all(p.endswith(("Role-3_R-3", "Role-4_R-4", "Role-5_R-5")) for p in calls["GET"]))
    check("workday: a known job with a good summary is not re-read", got_by["Role 0"]["Description Summary"] == "")
    ats.WORKDAY_SUMMARIES, ats.WORKDAY_BACKFILL = 100, 1
    ats._req, calls = fake_workday(45, 45, 45, {})
    got, meta = ats.fetch("workday", "shop.wd1/ext", known=known)
    paths = calls["GET"]
    check("workday: placeholders ('Requisition …', blank) are backfilled after new jobs, capped separately",
          paths[-1] == "/job/City/Role-1_R-1" and len(paths) == 43 and "/job/City/Role-0_R-0" not in paths)
    ats._req, calls = fake_workday(43, 45, 45, {})
    got, meta = ats.fetch("workday", "shop.wd1/ext", known=known)
    check("workday: 43 of 45 read and the total is still 45 -> incomplete", not meta["complete"] and calls["POST0"] == 2)
    ats._req, calls = fake_workday(43, 45, 43, {})
    got, meta = ats.fetch("workday", "shop.wd1/ext", known=known)
    check("workday: 43 of 45 read but the fresh total is 43 (2 closed meanwhile) -> complete", meta["complete"])
    ats.FETCHERS["greenhouse"] = lambda slug: ([{"Job Title": "A", "Job / Post URL": "https://x/1"}], {"total": 2, "read": 1, "complete": False})
    _, meta = ats.fetch("greenhouse", "x")
    check("feed check: greenhouse read 1 of 2 -> complete False", meta["complete"] is False)
    ats.FETCHERS["greenhouse"] = lambda slug: ([], {"note": "robots.txt disallows"})
    _, meta = ats.fetch("greenhouse", "x")
    check("feed check: a robots note is never complete", meta["complete"] is False)

    # ---- item 3: feed first, crawl only when needed --------------------------------------------
    crawls: list[dict] = []
    feed_answer: dict = {}

    def fake_crawl(domain, max_pages=150, careers_hint=None, sitemaps=True, stop_on=None):
        crawls.append({"max_pages": max_pages, "sitemaps": sitemaps, "stop_on": stop_on is not None})
        links = [{"url": "https://job-boards.greenhouse.io/shop", "found_on": "https://shop.com/careers"}]
        stopped = bool(stop_on and stop_on([x["url"] for x in links]))
        return {"domain": domain, "official_website": f"https://{domain}/", "careers_page": "https://shop.com/careers",
                "sitemap_urls": 0, "pages_fetched": 5 if not sitemaps else (12 if stopped else 150), "blocked_by_robots": 0,
                "career_urls_unvisited": 0, "requests": 5, "ats_links": links, "stopped_early": stopped,
                "jobs": [{"Job Title": "Role 1", "Job / Post URL": "https://shop.com/careers/role-1", "Status": "Open"},
                         {"Job Title": "Store Greeter", "Job / Post URL": "https://shop.com/careers/greeter", "Status": "Open"}],
                "pages": []}

    def fake_fetch(kind, slug, known=None):
        rows, meta = feed_answer.get(kind, ([], {"complete": True}))
        out = [dict(r, **{"Source Type": ats.SOURCE[kind], "Posted By": "", "Status": "Open"}) for r in rows]
        return out, dict(meta)

    scan.crawl, scan.atsmod.fetch = fake_crawl, fake_fetch
    feed_rows = [{"Job Title": f"Role {i}", "Job / Post URL": f"https://job-boards.greenhouse.io/shop/jobs/{i}"} for i in range(3)]

    def run_scan(*extra) -> dict:
        crawls.clear()
        sys.argv = ["scan.py", "--company", "Shop", "--domain", "shop.com", *extra]
        scan.main()
        f = sorted((tmp / "runs").glob("shop-*.rows.json"), key=lambda p: p.stat().st_mtime)[-1]
        payload = json.loads(f.read_text())
        f.unlink()
        return payload

    feed_answer["greenhouse"] = (feed_rows, {"complete": True})
    p1 = run_scan()
    check("new company: crawl stops at the careers page that links the board",
          len(crawls) == 1 and crawls[0]["stop_on"] and p1["meta"]["crawl_mode"] == "stopped at ATS link")
    check("new company: feed rows used, own-site role not on the board kept as Careers Page",
          sorted(r["Job Title"] for r in p1["rows"]) == ["Role 0", "Role 1", "Role 2", "Store Greeter"]
          and next(r for r in p1["rows"] if r["Job Title"] == "Store Greeter")["Source Type"] == "Careers Page")
    p2 = run_scan()
    check("known board: feed first, then a light 5-page check without sitemap",
          len(crawls) == 1 and crawls[0] == {"max_pages": 5, "sitemaps": False, "stop_on": False} and p2["meta"]["crawl_mode"] == "light")
    check("known board, complete feed: ATS and Careers Page are complete sources",
          p2["meta"]["complete_sources"] == ["ATS: Greenhouse", "Careers Page"] and p2["meta"]["incomplete_sources"] == [])
    feed_answer["greenhouse"] = (feed_rows[:2], {"complete": False, "read": 2, "total": 3})
    p3 = run_scan()
    check("incomplete feed: no complete source, listed as incomplete",
          p3["meta"]["complete_sources"] == [] and p3["meta"]["incomplete_sources"] == ["ATS: Greenhouse"])
    p4 = run_scan("--full-crawl")
    check("--full-crawl: whole site crawled even with a known feed", crawls[0]["max_pages"] == 150 and not crawls[0]["stop_on"])

    # empty feed for a board that had open jobs: never a complete list
    con = memory.connect()
    cid = memory.company_by_domain(con, "shop.com")["id"]
    con.execute("INSERT INTO jobs (company_id, job_key, url, title, source_type, date_discovered, last_checked, status) VALUES (?,?,?,?,?,?,?,?)",
                (cid, "https://job-boards.greenhouse.io/shop/jobs/0", "https://job-boards.greenhouse.io/shop/jobs/0", "Role 0", "ATS: Greenhouse", "2026-10-01", "2026-10-01", "Open"))
    con.commit()
    con.close()
    feed_answer["greenhouse"] = ([], {"complete": True})
    p5 = run_scan()
    check("empty feed while jobs were open: treated as incomplete, full crawl after the light path failed",
          p5["meta"]["incomplete_sources"] == ["ATS: Greenhouse"] and "ATS: Greenhouse" not in p5["meta"]["complete_sources"])
    con = memory.connect()
    con.execute("DELETE FROM jobs")
    con.execute("UPDATE companies SET ats_type=NULL, ats_slug=NULL")
    con.commit()
    con.close()
    p6 = run_scan()
    check("board linked but empty: crawl stops early, then the whole site is crawled",
          len(crawls) == 2 and crawls[0]["stop_on"] and crawls[1]["max_pages"] == 150 and p6["meta"]["crawl_mode"] == "full (board empty)")

    # ---- item 4: two misses, complete reads only -----------------------------------------------
    today = dt.date.today().isoformat()

    def frow(title, n):
        return {"Company Name": "Shop", "Official Website": "https://shop.com/", "Career Page": "https://shop.com/careers",
                "Job Title": title, "Location / Work Mode": "", "Source Type": "ATS: Greenhouse",
                "Job / Post URL": f"https://job-boards.greenhouse.io/shop/jobs/{n}", "Date Discovered": today,
                "Last Checked": today, "Status": "Open"}

    def run_dedupe(rows, complete, incomplete=()):
        f = tmp / "batch.rows.json"
        f.write_text(json.dumps({"meta": {"company": "Shop", "domain": "shop.com", "complete_sources": list(complete),
                                          "incomplete_sources": list(incomplete)}, "rows": rows}))
        dedupe.main([str(f)])
        final = sorted((tmp / "runs").glob("shop-*.final.rows.json"))[-1]
        payload = json.loads(final.read_text())
        return {r["Job Title"]: r["Status"] for r in payload["rows"]}, payload["meta"]

    def next_day():
        # Simulate a later calendar day: move every counted miss one day back.
        c_ = memory.connect()
        c_.execute("UPDATE jobs SET last_miss_date=date(last_miss_date, '-1 day') WHERE last_miss_date IS NOT NULL")
        c_.commit()
        c_.close()

    both = [frow("Alpha", 1), frow("Beta", 2)]
    run_dedupe(both, ["ATS: Greenhouse"])
    st, meta = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    check("two-miss: missing once stays Open and is reported", st["Beta"] == "Open" and meta["missing_once_titles"] == ["Beta"])
    st, meta = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"], ["ATS: Greenhouse"])
    check("two-miss: an incomplete read neither closes nor counts", st["Beta"] == "Open" and meta["missing_once_titles"] == [])
    st, meta = run_dedupe([frow("Alpha", 1)], [])
    check("two-miss: a source not read in full is never used to close", st["Beta"] == "Open")
    next_day()
    st, meta = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    check("two-miss: second complete miss in a row closes it", st["Beta"] == "Closed" and meta["closed_titles"] == ["Beta"])
    run_dedupe([frow("Alpha", 1), frow("Gamma", 3)], ["ATS: Greenhouse"])
    st, _ = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    run_dedupe([frow("Alpha", 1), frow("Gamma", 3)], ["ATS: Greenhouse"])
    st, _ = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    check("two-miss: seen again in between resets the count (miss, seen, miss = still Open)", st["Gamma"] == "Open")
    run_dedupe([frow("Alpha", 1), frow("Delta", 4)], ["ATS: Greenhouse"])
    run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    st, meta = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    c_ = memory.connect()
    dj = c_.execute("SELECT missing_count, last_miss_date FROM jobs WHERE title='Delta'").fetchone()
    c_.close()
    check("same-day: two complete misses on the same day keep the job Open (counted once)",
          st["Delta"] == "Open" and dj["missing_count"] == 1 and dj["last_miss_date"] == today and "Delta" in meta["missing_once_titles"])
    next_day()
    st, meta = run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    check("same-day: complete misses on two different days close it", st["Delta"] == "Closed" and "Delta" in meta["closed_titles"])
    run_dedupe([frow("Alpha", 1), frow("Eps", 5)], ["ATS: Greenhouse"])
    run_dedupe([frow("Alpha", 1)], ["ATS: Greenhouse"])
    run_dedupe([frow("Alpha", 1), frow("Eps", 5)], ["ATS: Greenhouse"])
    c_ = memory.connect()
    ej = c_.execute("SELECT missing_count, last_miss_date FROM jobs WHERE title='Eps'").fetchone()
    c_.close()
    check("same-day: seen again clears the miss date", ej["missing_count"] == 0 and ej["last_miss_date"] is None)

    old = tmp / "old.db"
    c = sqlite3.connect(old)
    c.executescript("CREATE TABLE jobs (id INTEGER PRIMARY KEY, company_id INTEGER, job_key TEXT, url TEXT, title TEXT, location TEXT, "
                    "source_type TEXT, posted_by TEXT, posted_date TEXT, date_discovered TEXT, last_checked TEXT, "
                    "description_summary TEXT, application_url TEXT, status TEXT, raw_json TEXT);")
    c.close()
    c = _connect(old)
    check("migration: an existing memory.db gains jobs.missing_count", "missing_count" in {r["name"] for r in c.execute("PRAGMA table_info(jobs)")})
    check("migration: an existing memory.db gains jobs.last_miss_date", "last_miss_date" in {r["name"] for r in c.execute("PRAGMA table_info(jobs)")})
    c.close()

    # ---- item 5: one command, short output (scratch copy of the project, no network) ----------
    proj = tmp / "proj"
    (proj / "scripts").mkdir(parents=True)
    for f in (ROOT / "scripts").glob("*.py"):
        shutil.copy(f, proj / "scripts" / f.name)
    shutil.copytree(ROOT / "db", proj / "db")
    (proj / "runs").mkdir()
    pc = _connect(proj / "memory.db")
    memory.upsert_company(pc, "Shop", "shop.com")
    pc.close()
    many = [frow(f"Role {i}", i) for i in range(300)]
    (proj / "runs" / "shop-20261015-010101.rows.json").write_text(json.dumps(
        {"meta": {"company": "Shop", "domain": "shop.com", "complete_sources": ["ATS: Greenhouse"], "crawl_mode": "light",
                  "pages_fetched": 5, "seconds": 3.2, "careers_page": "https://shop.com/careers"}, "rows": many}))
    out = subprocess.run([sys.executable, "scripts/run_company.py", "--company", "Shop", "--domain", "shop.com", "--reuse-scan"],
                         cwd=proj, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines = out.stdout.strip().splitlines()
    summary = lines[lines.index(next(x for x in lines if x.startswith("SUMMARY"))):] if any(x.startswith("SUMMARY") for x in lines) else []
    check("run_company: one command runs dedupe and Excel (exit 0, workbook written)",
          out.returncode == 0 and (proj / "Company_Hiring_Intelligence.xlsx").exists())
    check("run_company: 300 rows in, output stays short (summary <= 20 lines, all output < 40 lines)",
          0 < len(summary) <= 20 and len(lines) < 40 and "Role 299" not in out.stdout)
    check("run_company: checkpoint lines for steps 2-5 are shown", all(any(x.startswith(m) or x.lstrip().startswith(m) for x in lines) for m in ("▶", "✓")))
    if out.returncode:
        print(out.stdout[-2000:], out.stderr[-2000:])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
