"""Offline tests for the self-learning loop (2026-10-15): the reader-spec schema, spec_reader on saved
fixtures, the blocker detector, site_profile reuse (the second run skips the crawl), the tool forge
(trial activates a spec, editing it deactivates it) and the guard hook's new blocks.

Run: uv run python scripts/test_self_learning.py   (no network; scratch copies only)
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blockers  # noqa: E402
import memory  # noqa: E402
import progress  # noqa: E402
import scan  # noqa: E402
import spec_reader  # noqa: E402
import tools  # noqa: E402
from common import ROOT  # noqa: E402
from spec_schema import validate_fingerprints, validate_spec  # noqa: E402
from validate_rows import validate_row  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name)


tmp = Path(tempfile.mkdtemp(prefix="sl-"))
progress.STATE = tmp / ".current.json"
_connect = memory.connect
DB = tmp / "m.db"
memory.connect = lambda path=DB: _connect(path)
scan.RUNS_DIR = tmp / "runs"
tools.TRIALS_DIR = tmp / "runs" / "trials"
spec_reader.SPEC_DIR = tmp / "readers"
spec_reader.SPEC_DIR.mkdir()
KEKA = json.loads((ROOT / "schema" / "readers" / "keka.json").read_text())


def errs_of(mut) -> list[str]:
    s = copy.deepcopy(KEKA)
    mut(s)
    return validate_spec(s, "keka.json")


# ---- 1. spec schema ------------------------------------------------------------------------------
check("schema: the shipped keka.json is valid", validate_spec(KEKA, "keka.json") == [])
check("schema: name must equal the file name", validate_spec(KEKA, "other.json") != [])
check("schema: request to another host is rejected",
      errs_of(lambda s: s["request"].update(url="https://evil.example.org/careers/api/jobs")) != [])
check("schema: plain http is rejected", errs_of(lambda s: s["request"].update(url="http://{slug}.keka.com/careers/api/x")) != [])
check("schema: file: URL is rejected", errs_of(lambda s: s["request"].update(url="file:///etc/passwd")) != [])
check("schema: user:password@ in a URL is rejected",
      errs_of(lambda s: s["request"].update(url="https://a:b@{slug}.keka.com/careers/api/x")) != [])
check("schema: headers (credentials) are rejected",
      errs_of(lambda s: s["request"].update(headers={"Authorization": "Bearer abcdefghijk"})) != [])
check("schema: a key-like query parameter is rejected", errs_of(lambda s: s["request"].update(query={"api_key": "x"})) != [])
check("schema: a token-like value is rejected",
      errs_of(lambda s: s["request"].update(method="POST", json={"q": "Bearer abcdefghijklmnop"})) != [])
check("schema: POST without a JSON body (form / raw) is rejected", errs_of(lambda s: s["request"].update(method="POST")) != [])
check("schema: POST with a string body is rejected", errs_of(lambda s: s["request"].update(method="POST", json="a=b")) != [])
check("schema: GET with a body is rejected", errs_of(lambda s: s["request"].update(json={"a": 1})) != [])
check("schema: PUT is rejected", errs_of(lambda s: s["request"].update(method="PUT")) != [])
check("schema: a host_pattern covering any .com host is rejected", errs_of(lambda s: s.update(host_pattern="^[a-z0-9-]+\\.com$")) != [])
check("schema: a host_pattern with .* is rejected", errs_of(lambda s: s.update(host_pattern="^.*\\.keka\\.com$")) != [])
check("schema: {slug} glued into a host label is rejected",
      errs_of(lambda s: s["request"].update(url="https://x{slug}.keka.com/careers/api/x")) != [])
check("schema: unknown top-level key (code) is rejected", errs_of(lambda s: s.update(code="import os")) != [])
check("schema: more than 50 pages is rejected",
      errs_of(lambda s: s.update(pagination={"type": "offset", "param": "offset", "size": 50, "max_pages": 500})) != [])
post_ok = copy.deepcopy(KEKA)
post_ok["request"] = {"method": "POST", "url": "https://{slug}.keka.com/careers/api/jobs", "json": {"limit": "{size}", "offset": 0}}
post_ok["pagination"] = {"type": "offset", "param": "offset", "size": 20, "in": "json", "max_pages": 5}
check("schema: POST-JSON with paging in the body is accepted", validate_spec(post_ok, "keka.json") == [])
fp_doc = json.loads((ROOT / "schema" / "ats_fingerprints.json").read_text())
check("fingerprints: the shipped registry is valid", validate_fingerprints(fp_doc) == [])
bad_fp = copy.deepcopy(fp_doc)
bad_fp["fingerprints"].append({"ats": "evil", "host": "^[a-z]+\\.github\\.io$"})
check("fingerprints: a shared-hosting host regex is rejected", validate_fingerprints(bad_fp) != [])

# ---- 2. spec_reader on saved fixtures -------------------------------------------------------------
ACTIVE = json.loads((FIX / "keka_active.json").read_text())
sent: list[tuple] = []


class FakeFetcher:
    def allowed(self, url):
        return "/forbidden" not in url


def fake_send_factory(pages):
    def send(self, method, url, params, body):
        self.check_url(url)
        sent.append((method, url, params, copy.deepcopy(body)))
        i = min(len(sent) - 1, len(pages) - 1)
        return pages[i], url
    return send


_orig_send, _orig_init = spec_reader._Reader.send, spec_reader._Reader.__init__


def patched_init(self, spec, slug, allowed_host):
    _orig_init(self, spec, slug, allowed_host)
    self.robots = FakeFetcher()


spec_reader._Reader.__init__ = patched_init
spec_reader._Reader.send = fake_send_factory([ACTIVE])
rows, meta = spec_reader.fetch(KEKA, "perennial", allowed_host="perennial.keka.com", site_domain="perennialsys.com")
check("reader: Keka fixture gives its 2 jobs", [r["Job Title"] for r in rows] == ["Video Creator", "Graphic Designer -Intern"])
check("reader: location, posted date and job URL mapped",
      rows[0]["Location / Work Mode"] == "Pune" and rows[0]["Posted Date"] == "2025-05-14"
      and rows[0]["Job / Post URL"] == "https://perennial.keka.com/careers/jobdetails/23585")
check("reader: department kept in the description, complete read, 1 request",
      rows[0]["_desc"].startswith("Design Track — ") and meta["complete"] and len(sent) == 1)
sent.clear()
rows2, _ = spec_reader.fetch_rows(KEKA, "perennial", site_domain="perennialsys.com")
row = {**rows2[0], "Company Name": "Perennial Systems", "Date Discovered": "2026-10-15"}
check("reader: fetch_rows labels rows 'ATS: Keka' and they pass validate_rows",
      row["Source Type"] == "ATS: Keka" and validate_row({k: v for k, v in row.items() if not k.startswith("_")}, 1) == [])
r_ = spec_reader._Reader(KEKA, "perennial", "perennial.keka.com")
refused = []
for u in ("https://evil.example.org/x", "https://other.keka.com/careers/api/x", "http://perennial.keka.com/careers/api/x",
          "https://perennial.keka.com/forbidden"):
    try:
        r_.check_url(u)
        refused.append(False)
    except spec_reader.SpecError:
        refused.append(True)
check("reader: other host, other tenant, http and robots-disallowed URLs are refused", all(refused))
bad_slug = []
for s_ in ("../etc", "a.b", "evil.com#", ""):
    try:
        spec_reader.fetch(KEKA, s_)
        bad_slug.append(False)
    except spec_reader.SpecError:
        bad_slug.append(True)
check("reader: slugs outside slug_pattern (dots, paths) are refused", all(bad_slug))
# Paging: two full pages then a short one; the cap stops a board that never ends.
paged = copy.deepcopy(post_ok)
paged["response"] = {"format": "json", "items": "data.jobs", "total": "data.total"}
mk = lambda a, b: {"data": {"total": 45, "jobs": [{"id": i, "title": f"Engineer {i}", "publishedOn": "2026-10-01"} for i in range(a, b)]}}  # noqa: E731
sent.clear()
spec_reader._Reader.send = fake_send_factory([mk(0, 20), mk(20, 40), mk(40, 45)])
rows3, meta3 = spec_reader.fetch(paged, "acme")
check("reader: offset paging in the POST body reads 45 jobs in 3 requests",
      len(rows3) == 45 and len(sent) == 3 and [b["offset"] for _, _, _, b in sent] == [0, 20, 40] and sent[0][3]["limit"] == 20 and meta3["complete"])
sent.clear()
endless = copy.deepcopy(paged)
endless["response"].pop("total")
endless["pagination"]["max_pages"] = 2
spec_reader._Reader.send = fake_send_factory([mk(0, 20), mk(20, 40), mk(40, 60)])
rows4, meta4 = spec_reader.fetch(endless, "acme")
check("reader: the page cap stops a board and marks the read incomplete", len(sent) == 2 and not meta4["complete"] and "cap" in meta4.get("note", ""))
html_spec = {"name": "acmehtml", "ats": "acmehtml", "label": "Acme Jobs", "host_pattern": "^jobs\\.acmeboard\\.com$",
             "slug_pattern": "^[a-z0-9-]+$", "board_url": "https://jobs.acmeboard.com/{slug}",
             "request": {"method": "GET", "url": "https://jobs.acmeboard.com/{slug}"},
             "response": {"format": "html", "items": "ul.jobs > li"},
             "fields": {"title": {"css": "a.title"}, "url": {"css": "a.title", "attr": "href"}, "location": {"css": "span.loc"}}}
check("schema: an HTML (CSS selector) spec is accepted", validate_spec(html_spec, "acmehtml.json") == [])
page = (b'<ul class="jobs"><li><a class="title" href="/acme/1">Data Engineer</a><span class="loc">Pune</span></li>'
        b'<li><a class="title" href="https://evil.example.org/x">Phish</a></li>'
        b'<li><a class="title" href="/acme/2">QA Lead</a><span class="loc">Remote</span></li></ul><script>alert(1)</script>')
sent.clear()
spec_reader._Reader.send = fake_send_factory([page])
spec_reader._Reader.send = (lambda f: (lambda self, m, u, p, b: (f(self, m, u, p, b)[0], "https://jobs.acmeboard.com/acme")))(fake_send_factory([page]))
rows5, _ = spec_reader.fetch(html_spec, "acme")
check("reader: HTML spec maps cards; a link to another host is dropped",
      [(r["Job Title"], r["Location / Work Mode"], r["Job / Post URL"]) for r in rows5]
      == [("Data Engineer", "Pune", "https://jobs.acmeboard.com/acme/1"), ("QA Lead", "Remote", "https://jobs.acmeboard.com/acme/2")])

# robots.txt, RFC 9309: the longest matching rule wins (Keka: "Disallow: /" then "Allow: /careers")
import crawl  # noqa: E402
import urllib.robotparser  # noqa: E402


def _rp(text: str):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return rp


kr = _rp("User-agent: *\nDisallow: /\nAllow: /careers\nAllow: /careers/")
check("robots: a longer Allow beats Disallow: / (Keka careers API allowed, the rest refused)",
      crawl.robots_allows(kr, "Bot", "https://perennial.keka.com/careers/api/jobs/default/active")
      and not crawl.robots_allows(kr, "Bot", "https://perennial.keka.com/admin"))
pr_ = _rp("User-agent: *\nAllow: /\nDisallow: /private\n\nUser-agent: Bot\nDisallow: /")
check("robots: a longer Disallow still refuses; a group naming the agent is used first",
      not crawl.robots_allows(pr_, "Mozilla", "https://a.com/private/x") and crawl.robots_allows(pr_, "Mozilla", "https://a.com/ok")
      and not crawl.robots_allows(pr_, "Bot/1.0", "https://a.com/ok"))

# ---- 3. blocker detector --------------------------------------------------------------------------
html = (FIX / "perennial_job_openings.html").read_bytes()
urls = blockers.urls_in_html(html, "https://perennialsys.com/job-openings/")
det = blockers.detect_boards(urls, "perennialsys.com", {})
keka = [b for b in det["boards"] if b["ats"] == "keka"]
check("detector: the Keka embed is found with slug 'perennial' and no reader yet",
      len(keka) == 1 and keka[0]["slug"] == "perennial" and keka[0]["host"] == "perennial.keka.com" and keka[0]["reader"] == "")
check("detector: an unknown job-board host is reported; analytics, fonts and LinkedIn are ignored",
      [u["host"] for u in det["unknown"]] == ["jobs.example-unknown-ats.io"])
det2 = blockers.detect_boards(urls, "perennialsys.com", {"keka": KEKA})
check("detector: with an active spec the board's reader is spec:keka", det2["boards"][0]["reader"] == "spec:keka")
det3 = blockers.detect_boards(["https://job-boards.greenhouse.io/acme", "https://cdn.keka.com/careers/v/2026/app.js"], "acme.com", {})
check("detector: built-in boards keep their reader; a vendor CDN URL is no board",
      [(b["ats"], b["slug"], b["reader"]) for b in det3["boards"]] == [("greenhouse", "acme", "builtin:greenhouse")])

# ---- 4. scan: blockers, forge, site_profile reuse -------------------------------------------------
crawls: list[dict] = []
EXTERNAL = [{"url": u, "found_on": "https://perennialsys.com/job-openings/", "embed": "embedjobs/js" in u} for u in urls]


def fake_crawl(domain, max_pages=150, careers_hint=None, sitemaps=True, stop_on=None):
    crawls.append({"max_pages": max_pages, "sitemaps": sitemaps})
    stopped = bool(stop_on and stop_on([x["url"] for x in EXTERNAL]))
    return {"domain": domain, "official_website": f"https://{domain}/", "careers_page": "https://perennialsys.com/job-openings/",
            "sitemap_urls": 92, "pages_fetched": 4 if stopped else 146, "blocked_by_robots": 0, "career_urls_unvisited": 0,
            "requests": 5, "ats_links": [x for x in EXTERNAL if "keka" in x["url"]], "careers_external": EXTERNAL,
            "stopped_early": stopped, "jobs": [], "pages": [{"url": "https://perennialsys.com/job-openings/",
                                                             "final_url": "https://perennialsys.com/job-openings/", "status": 200, "careers": True}]}


scan.crawl = fake_crawl
spec_reader._Reader.send = fake_send_factory([ACTIVE])


def run_scan(*extra) -> dict:
    crawls.clear()
    sys.argv = ["scan.py", "--company", "Perennial Systems", "--domain", "perennialsys.com", *extra]
    scan.main()
    f = sorted((tmp / "runs").glob("perennial-systems-*.rows.json"), key=lambda p: p.stat().st_mtime)[-1]
    payload = json.loads(f.read_text())
    f.unlink()
    return payload


p1 = run_scan()
con = memory.connect()
open1 = blockers.open_blockers(con, "perennialsys.com")
kinds1 = sorted(b["kind"] for b in open1)
check("scan 1 (no reader): full crawl, 0 rows, blockers no_reader + js_rendered + unknown_ats_host recorded",
      p1["meta"]["crawl_mode"] == "full" and p1["rows"] == [] and kinds1 == ["js_rendered", "no_reader", "unknown_ats_host"])
prof1 = blockers.get_profile(con, "perennialsys.com")
check("scan 1: site profile keeps the careers page and the Keka board (no reader yet)",
      prof1["careers_url"] == "https://perennialsys.com/job-openings/" and prof1["ats_type"] == "keka" and prof1["ats_slug"] == "perennial" and prof1["reader"] == "")

# Forge: the spec is copied into the (scratch) readers folder, validated and trialled.
spath = spec_reader.SPEC_DIR / "keka.json"
spath.write_text(json.dumps(KEKA))
tools.spec_reader.SPEC_DIR = spec_reader.SPEC_DIR


class A:  # argparse stand-in
    pass


a = A()
a.spec, a.company, a.domain, a.blocker, a.slug, a.builder = str(spath), "Perennial Systems", None, None, None, False
check("forge: validate accepts the spec", tools.cmd_validate(a) == 0)
rc = tools.cmd_trial(a)
reg = con.execute("SELECT * FROM tools_registry WHERE name='keka'").fetchone()
check("forge: trial with plausible jobs activates the spec with provenance",
      rc == 0 and reg and reg["status"] == "active" and reg["created_by"] == "agent" and reg["trial_plausible"] == 2 and reg["blocker_id"])
check("forge: the trial wrote only runs/trials", len(list((tmp / "runs" / "trials").glob("keka-*.json"))) == 1)
open2 = {b["kind"] for b in blockers.open_blockers(con, "perennialsys.com")}
check("forge: blockers on the Keka host are resolved by spec:keka; the unknown host stays open", open2 == {"unknown_ats_host"})
evil = copy.deepcopy(KEKA)
evil["name"] = "evil"
evil["host_pattern"] = "^[a-z0-9-]+\\.evilboard\\.net$"
evil["request"]["url"] = "https://{slug}.evilboard.net/api"
evil["board_url"] = "https://{slug}.evilboard.net/"
(spec_reader.SPEC_DIR / "evil.json").write_text(json.dumps(evil))
a2 = A()
a2.spec, a2.company, a2.domain, a2.blocker, a2.slug, a2.builder = str(spec_reader.SPEC_DIR / "evil.json"), "Perennial Systems", None, None, None, False
check("forge: a spec whose host is not a discovered blocker host is refused", tools.cmd_trial(a2) != 0
      and not con.execute("SELECT 1 FROM tools_registry WHERE name='evil'").fetchone())

p2 = run_scan()
check("scan 2 (re-read): the learned board is read first with spec:keka, crawl skipped, 2 Keka rows",
      p2["meta"]["crawl_mode"] == "skipped (site profile)" and crawls == []
      and sorted(r["Job Title"] for r in p2["rows"]) == ["Graphic Designer -Intern", "Video Creator"]
      and {r["Source Type"] for r in p2["rows"]} == {"ATS: Keka"})
p3 = run_scan()
prof3 = blockers.get_profile(con, "perennialsys.com")
check("scan 3: the second run after learning also skips the crawl; profile says what worked",
      p3["meta"]["crawl_mode"] == "skipped (site profile)" and crawls == [] and prof3["reader"] == "spec:keka"
      and "spec:keka feed perennial (2 jobs)" in prof3["what_worked"])
spath.write_text(json.dumps({**KEKA, "description": "edited after the trial"}))
check("forge: editing an active spec switches it off (sha256 no longer matches)", "keka" not in spec_reader.active_specs(con))
a.builder = False
for _ in range(3):
    tools.cmd_trial(a)
check("forge: at most 2 trials per company per scan run", tools.cmd_trial(a) == 2)
con.close()

# ---- 5. guard hook --------------------------------------------------------------------------------
HOOK = ROOT / "hooks" / "pre_tool_use.py"
GUARD = [
    (True, "Write", {"file_path": str(ROOT / "runs" / "reader.py"), "content": "print(1)"}),
    (True, "Write", {"file_path": str(ROOT / "proposals" / "keka_reader.py"), "content": "x"}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "readers" / "keka.py"), "content": "x"}),
    (True, "Edit", {"file_path": str(ROOT / "scripts" / "spec_reader.py"), "old_string": "a", "new_string": "b"}),
    (True, "Bash", {"command": "echo 'import os' > runs/x.py"}),
    (True, "Bash", {"command": "cp runs/a.txt runs/a.py"}),
    (False, "Write", {"file_path": str(ROOT / "proposals" / "keka-reader.md"), "content": "# proposal"}),
    (False, "Write", {"file_path": str(ROOT / "schema" / "readers" / "keka.json"), "content": json.dumps(KEKA)}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "readers" / "keka.json"), "content": json.dumps({**KEKA, "request": {"method": "GET", "url": "https://evil.example.org/x"}})}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "readers" / "keka.json"), "content": "not json"}),
    (False, "Write", {"file_path": str(ROOT / "schema" / "ats_fingerprints.json"), "content": json.dumps(fp_doc)}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "ats_fingerprints.json"), "content": json.dumps(bad_fp)}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "other.json"), "content": "{}"}),
    (False, "Edit", {"file_path": str(ROOT / "schema" / "readers" / "keka.json"), "old_string": "a", "new_string": "b"}),
    (True, "Bash", {"command": "echo '{}' > schema/readers/k.json"}),
    (True, "Bash", {"command": "cp runs/x.json schema/ats_fingerprints.json"}),
    (True, "Bash", {"command": "uv run python scripts/tools.py trial schema/readers/keka.json --company X --builder"}),
    (False, "Bash", {"command": "uv run python scripts/tools.py trial schema/readers/keka.json --company \"Perennial Systems\""}),
    (False, "Bash", {"command": "uv run python scripts/tools.py validate schema/readers/keka.json"}),
    (False, "Bash", {"command": "uv run python scripts/blockers.py list --domain perennialsys.com"}),
    (True, "Bash", {"command": "rm backups/memory-pre-self-learning-20261015.db"}),
]
fails = 0
for expect, tool, ti in GUARD:
    pr = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(ROOT)}),
                        capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    ok = (pr.returncode == 2) == expect
    fails += not ok
    if not ok:
        print("   guard mismatch:", tool, json.dumps(ti)[:100], pr.stderr.strip()[:120])
check(f"guard: {len(GUARD) - fails}/{len(GUARD)} cases (no .py writes anywhere, specs and registry schema-checked, no Bash writes under schema/, no --builder)", fails == 0)

passed = sum(ok for _, ok in results)
print(f"{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
