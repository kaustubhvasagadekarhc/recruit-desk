"""Detect ATS boards (Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Zoho Recruit, Workday) and read
their public job feeds.

These feeds are the vendors' documented public job-board APIs, meant for embedding job lists
on company sites; no key and no login is used.

CLI:
  uv run python scripts/ats.py detect <url-or-text>...
  uv run python scripts/ats.py fetch greenhouse razorpaysoftwareprivatelimited
  uv run python scripts/ats.py fetch workday nordstrom.wd501/nordstrom_careers
  uv run python scripts/ats.py guess "Company Name"      # candidates only; confirm before use
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netsafe  # noqa: E402
import progress  # noqa: E402
from common import clean_text, normalize_url, shared_prefix, strip_labels, strip_prefix, user_agent  # noqa: E402

PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s]*&)?for=([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!api\b|j\b)([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"//(?!apply\.|www\.)([a-z0-9-]+)\.workable\.com", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("zohorecruit", re.compile(r"//([a-z0-9-]+)\.zohorecruit\.((?:com|in|eu|com\.au|com\.cn|jp))", re.I)),
    # tenant.wdN.myworkdayjobs.com/[locale/]<site>; slug is "tenant.wdN/site"
    ("workday", re.compile(r"//([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday\b|job\b|en-)([A-Za-z0-9_-]+)", re.I)),
]
SOURCE = {
    "greenhouse": "ATS: Greenhouse",
    "lever": "ATS: Lever",
    "workable": "ATS: Workable",
    "ashby": "ATS: Ashby",
    "smartrecruiters": "ATS: SmartRecruiters",
    "zohorecruit": "ATS: Zoho Recruit",
    "workday": "ATS: Workday",
}

_s = netsafe.SafeSession()  # blocks private IPs on every redirect, caps body size
_s.headers.update({"User-Agent": user_agent(), "Accept": "application/json"})
SUMMARY_LEN = 300
DESC_LEN = 4000  # plain-text description kept per job until the shared company blurb is removed


def _req(method: str, url: str, **kw) -> requests.Response:
    """One feed request with retries on 429 / 5xx / timeouts (2 s then 5 s, or Retry-After)."""
    kw.setdefault("timeout", 30)
    return netsafe.with_retries(lambda: _s.request(method, url, **kw),
                                on_retry=lambda why, wait: progress.sub(f"{why} from {url[:80]}; retrying in {wait:.0f}s"))


def _get(url: str, **kw) -> requests.Response:
    return _req("GET", url, **kw)


def detect(texts: list[str]) -> list[dict]:
    """Return unique {ats, slug, evidence} found in URLs or HTML."""
    out: dict[tuple[str, str], dict] = {}
    for t in texts:
        for ats, rx in PATTERNS:
            for m in rx.finditer(t):
                slug = m.group(1)
                if ats == "zohorecruit":
                    slug = f"{m.group(1)}.{m.group(2)}"
                elif ats == "workday":
                    slug = f"{m.group(1).lower()}.{m.group(2).lower()}/{m.group(3)}"
                out.setdefault((ats, slug.lower()), {"ats": ats, "slug": slug, "evidence": t[:300]})
    return list(out.values())


def _date(v) -> str:
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):  # epoch ms
        return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).date().isoformat()
    return str(v)[:10]


def _text(h: str | None, n: int = SUMMARY_LEN) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(h or ""))), n)


def _join(*xs) -> str:
    return " · ".join(clean_text(str(x)) for x in xs if x and clean_text(str(x)))


def greenhouse(slug: str) -> tuple[list[dict], dict]:
    r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    r.raise_for_status()
    data = r.json()
    rows = []
    names = set()
    for j in data.get("jobs", []):
        names.add(j.get("company_name") or "")
        rows.append({
            "Job Title": clean_text(j.get("title")),
            "Location / Work Mode": clean_text((j.get("location") or {}).get("name")),
            "Job / Post URL": j.get("absolute_url"),
            "Application URL": j.get("absolute_url"),
            "Posted Date": _date(j.get("first_published")),  # updated_at is not a posting date
            "_desc": _text(j.get("content"), DESC_LEN),
            "_department": [d.get("name") for d in j.get("departments") or [] if isinstance(d, dict)],
        })
    total = data.get("meta", {}).get("total")
    return rows, {"company_names": sorted(n for n in names if n), "total": total, "read": len(rows),
                  "complete": total is None or len(rows) >= total}


def lever(slug: str) -> tuple[list[dict], dict]:
    rows = []
    for host in ("api.lever.co", "api.eu.lever.co"):
        r = _get(f"https://{host}/v0/postings/{slug}", params={"mode": "json"})
        if r.status_code == 404:
            continue
        r.raise_for_status()
        for j in r.json():
            c = j.get("categories") or {}
            rows.append({
                "Job Title": clean_text(j.get("text")),
                "Location / Work Mode": _join(c.get("location") or ", ".join(c.get("allLocations") or []), (j.get("workplaceType") or "").replace("unspecified", "").title(), c.get("commitment")),
                "Job / Post URL": j.get("hostedUrl"),
                "Application URL": j.get("applyUrl"),
                "Posted Date": _date(j.get("createdAt")),
                "_desc": clean_text(j.get("descriptionPlain"), DESC_LEN),
                "_department": [c.get("department"), c.get("team")],
                "_work_mode": j.get("workplaceType"),
            })
        break
    return rows, {}


def workable(slug: str) -> tuple[list[dict], dict]:
    # This endpoint answers 200 with an empty list for ANY slug; only trust it when the
    # board was linked from the company's own site.
    r = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    r.raise_for_status()
    data = r.json()
    rows = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        rows.append({
            "Job Title": clean_text(j.get("title")),
            "Location / Work Mode": _join(loc, "Remote" if j.get("telecommuting") else "", j.get("employment_type")),
            "Job / Post URL": j.get("url") or j.get("shortlink"),
            "Application URL": j.get("application_url") or j.get("url"),
            "Posted Date": _date(j.get("published_on") or j.get("created_at")),
            "_desc": _text(j.get("description"), DESC_LEN),
            "_department": j.get("department"),
            "_work_mode": "remote" if j.get("telecommuting") else None,  # false does not say on-site
            "_places": [[j.get("city"), j.get("country")]],
        })
    return rows, {"company_names": [data.get("name") or ""]}


def _ashby_places(j: dict) -> list:
    places = []
    for loc in [j, *(j.get("secondaryLocations") or [])]:
        addr = ((loc or {}).get("address") or {}).get("postalAddress") or {}
        if addr.get("addressLocality") or addr.get("addressCountry"):
            places.append([addr.get("addressLocality"), addr.get("addressCountry")])
    return places


def _ashby_salary(j: dict) -> dict | None:
    """The first salary component Ashby publishes ({compensationType: Salary, interval, currencyCode,
    minValue, maxValue}); None when the posting states no salary."""
    for c in ((j.get("compensation") or {}).get("summaryComponents") or []):
        if isinstance(c, dict) and str(c.get("compensationType") or "").lower() == "salary":
            return {"min": c.get("minValue"), "max": c.get("maxValue"), "currency": c.get("currencyCode"), "interval": c.get("interval")}
    return None


def ashby(slug: str) -> tuple[list[dict], dict]:
    r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"})
    r.raise_for_status()
    rows = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        rows.append({
            "Job Title": clean_text(j.get("title")),
            "Location / Work Mode": _join(j.get("location"), j.get("workplaceType"), j.get("employmentType")),
            "Job / Post URL": j.get("jobUrl"),
            "Application URL": j.get("applyUrl") or j.get("jobUrl"),
            "Posted Date": _date(j.get("publishedAt")),
            "_desc": clean_text(j.get("descriptionPlain"), DESC_LEN),
            "_department": [j.get("department"), j.get("team")],
            "_work_mode": j.get("workplaceType") or ("remote" if j.get("isRemote") else None),
            "_places": _ashby_places(j),
            "_salary": _ashby_salary(j),
        })
    return rows, {}


def smartrecruiters(slug: str) -> tuple[list[dict], dict]:
    rows, offset, total = [], 0, None
    while True:
        r = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100, "offset": offset})
        r.raise_for_status()
        data = r.json()
        if total is None:
            total = data.get("totalFound")
        for j in data.get("content", []):
            loc = j.get("location") or {}
            url = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
            rows.append({
                "Job Title": clean_text(j.get("name")),
                "Location / Work Mode": _join(", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country", "").upper()) if x), "Remote" if loc.get("remote") else "", (j.get("typeOfEmployment") or {}).get("label")),
                "Job / Post URL": url,
                "Application URL": url,
                "Posted Date": _date(j.get("releasedDate")),
                "Description Summary": "",
                "_department": [(j.get("department") or {}).get("label"), (j.get("function") or {}).get("label")],
                "_work_mode": "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else None,
                "_places": [[loc.get("city"), loc.get("country")]],
            })
        offset += 100
        if offset >= data.get("totalFound", 0) or not data.get("content"):
            break
    return rows, {"total": total, "read": len(rows), "complete": total is None or len(rows) >= total}


def zohorecruit(slug: str) -> tuple[list[dict], dict]:
    """Zoho Recruit has no keyless JSON feed; read the public careers HTML (robots-checked)."""
    from crawl import Fetcher, parse_html  # local import: only needed here

    sub, tld = slug.split(".", 1)
    url = f"https://{sub}.zohorecruit.{tld}/jobs/Careers"
    got = Fetcher().get(url)
    if not got or got[0] != 200:
        return [], {"note": f"careers page not readable ({'robots' if got is None else got[0]})"}
    pg = parse_html(got[3])
    rows, seen = [], set()
    for href, text in pg.links:
        if re.search(r"/jobs/Careers/\d+", href) and text and href not in seen:
            seen.add(href)
            absu = href if href.startswith("http") else f"https://{sub}.zohorecruit.{tld}{href}"
            rows.append({"Job Title": text, "Location / Work Mode": "", "Job / Post URL": absu, "Application URL": absu, "Posted Date": "", "Description Summary": ""})
    return rows, {}


WORKDAY_PAGE = 20  # the public careers page asks for 20 at a time; larger pages are refused
WORKDAY_MAX = 5000
WORKDAY_DELAY = 1.0  # seconds between feed requests (same as the crawler's per-host rate)
WORKDAY_SUMMARIES = 40  # posting pages read per run for summaries of NEW jobs (usually a handful)
WORKDAY_BACKFILL = 10  # plus this many for known jobs whose summary is blank or a 'Requisition …' placeholder


def _workday_posted(text: str, today: dt.date) -> str:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> ISO date; '30+ Days Ago' -> ''."""
    t = (text or "").lower()
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s+days?\s+ago", t)
    if m and "+" not in t:
        return (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    return ""


def needs_summary(summary: str | None) -> bool:
    """A stored summary worth replacing: empty, or the old 'Requisition R-…' placeholder."""
    s = (summary or "").strip()
    return not s or bool(re.fullmatch(r"Requisition \S+", s))


def workday(slug: str, known: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """Workday's public job list: the same POST /wday/cxs/<tenant>/<site>/jobs call the public
    careers page makes, 20 per page, one request a second, robots.txt checked for the host.
    A posting listed as 'N Locations' gets its locations from the posting's own public JSON.

    `known` maps the normalised URL of every job already in memory to its stored summary. The
    posting JSON (one request each) is read for up to WORKDAY_SUMMARIES new jobs per run, and for
    WORKDAY_BACKFILL known jobs whose summary is still empty or a 'Requisition …' placeholder; the
    rest keep their stored summary (a blank summary here never overwrites one in memory).

    meta: total (Workday's live count), read, complete. When fewer postings were read than the
    first page's total, the total is asked again at the end (postings closed during the read lower
    it); the read is complete only if it covers that fresh total."""
    from crawl import Fetcher  # robots.txt rules for the Workday host

    host_part, _, site = slug.partition("/")
    tenant, _, wd = host_part.partition(".")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    robots = Fetcher()
    if not robots.allowed(f"{api}/jobs") or not robots.allowed(f"{base}/{site}/"):
        return [], {"note": f"robots.txt of {base} disallows the job list", "complete": False}
    known = known or {}
    today = dt.date.today()
    rows, total, offset, last = [], None, 0, 0.0
    seen: set[str] = set()  # the list can shift while it is paged; a posting is kept once
    info_by_path: dict[str, dict] = {}

    def call(method: str, url: str, **kw):
        nonlocal last
        gap = WORKDAY_DELAY - (time.monotonic() - last)
        if gap > 0:
            time.sleep(gap)
        last = time.monotonic()  # one request a second, counted from each request's start
        r = _req(method, url, **kw)
        r.raise_for_status()
        return r.json()

    def page(off: int) -> dict:
        return call("POST", f"{api}/jobs", json={"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": off, "searchText": ""})

    while offset < WORKDAY_MAX:
        data = page(offset)
        if total is None:
            total = data.get("total") or 0  # later pages may report 0; the first page has the real total
        posts = data.get("jobPostings") or []
        for j in posts:
            path = j.get("externalPath") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            loc = clean_text(j.get("locationsText"))
            if re.fullmatch(r"\d+ Locations?", loc) and robots.allowed(f"{api}{path}"):
                info = info_by_path[path] = call("GET", f"{api}{path}").get("jobPostingInfo") or {}
                locs = [info.get("location"), *(info.get("additionalLocations") or [])]
                loc = "; ".join(dict.fromkeys(clean_text(x) for x in locs if x)) or loc
            url = f"{base}/{site}{path}"
            rows.append({
                "Job Title": clean_text(j.get("title")),
                "Location / Work Mode": loc,
                "Job / Post URL": url,
                "Application URL": url,
                "Posted Date": _workday_posted(j.get("postedOn"), today),
                "_path": path,
            })
        offset += WORKDAY_PAGE
        if offset % 200 == 0:
            progress.sub(f"Workday {tenant}/{site}: {min(offset, total)}/{total} postings read")
        if not posts or offset >= total:
            break
    first_total, fresh_total = total, total
    if len(rows) < (total or 0):
        # Postings closed while the list was paged lower the live total; ask for it again.
        fresh_total = page(0).get("total") or total
    complete = len(rows) >= (fresh_total or 0)

    # Summaries from the posting JSON: new jobs first, then a few stored placeholders.
    new = [r for r in rows if normalize_url(r["Job / Post URL"]) not in known]
    old = [r for r in rows if normalize_url(r["Job / Post URL"]) in known and needs_summary(known[normalize_url(r["Job / Post URL"])])]
    todo = new + old

    def describe(group: list[dict], cap: int) -> None:
        fetched = 0
        for r in group:
            path = r["_path"]
            if path not in info_by_path:  # "N Locations" postings were read already, at no cost
                if fetched >= cap or not robots.allowed(f"{api}{path}"):
                    continue
                try:
                    info_by_path[path] = call("GET", f"{api}{path}").get("jobPostingInfo") or {}
                except (requests.RequestException, ValueError) as e:
                    progress.sub(f"Workday posting {path[-40:]} not read: {e}")
                    continue
                fetched += 1
            r["_desc"] = _text(info_by_path[path].get("jobDescription"), DESC_LEN)

    describe(new, WORKDAY_SUMMARIES)
    describe(old, WORKDAY_BACKFILL)
    if todo:
        progress.sub(f"Workday {tenant}/{site}: {sum(1 for r in todo if r.get('_desc'))} summaries read "
                     f"({len(new)} new, {len(old)} older without one; {WORKDAY_SUMMARIES} new + {WORKDAY_BACKFILL} older per run)")
    for r in rows:
        r.pop("_path", None)
    return rows, {"total": first_total, "fresh_total": fresh_total, "read": len(rows), "complete": complete}


BOARD_URL = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "workable": "https://apply.workable.com/{slug}/",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
    "zohorecruit": "https://{slug}/jobs/Careers",
}


def board_url(ats: str, slug: str) -> str:
    if ats == "zohorecruit":
        sub, tld = slug.split(".", 1)
        return f"https://{sub}.zohorecruit.{tld}/jobs/Careers"
    if ats == "workday":
        host_part, _, site = slug.partition("/")
        return f"https://{host_part}.myworkdayjobs.com/{site}"
    return BOARD_URL[ats].format(slug=slug)


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "workable": workable,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "zohorecruit": zohorecruit,
    "workday": workday,
}


def summarise(rows: list[dict]) -> None:
    """Description Summary from each row's full text (_desc), after removing the blurb most of
    the company's postings open with ("Razorpay is one of India's leading ..."). Rows without a
    _desc keep whatever summary they carry (blank = keep the stored one)."""
    descs = [r.get("_desc") or "" for r in rows]
    prefix = shared_prefix(descs)
    for r in rows:
        d = r.pop("_desc", None)
        if d:
            r["Description Summary"] = clean_text(strip_labels(strip_prefix(d, prefix)) or d, SUMMARY_LEN)
        else:
            r.setdefault("Description Summary", "")
    if prefix:
        progress.sub(f"removed a shared {len(prefix)}-word company blurb from the summaries")


def fetch(ats: str, slug: str, known: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """Read one board. meta["complete"] is False when the feed was not read in full (fewer
    postings than the feed's own total, or a robots / page note); dedupe never closes jobs from
    an incomplete read."""
    rows, meta = FETCHERS[ats](slug, known) if ats == "workday" else FETCHERS[ats](slug)
    summarise(rows)
    meta.setdefault("complete", not meta.get("note"))
    for r in rows:
        r["Source Type"] = SOURCE[ats]
        r["Posted By"] = ""
        r["Status"] = "Open"  # listed on the live board right now
        r["_evidence"] = f"{SOURCE[ats]} public feed ({slug})"
    rows = [r for r in rows if r.get("Job Title") and r.get("Job / Post URL")]
    if meta["complete"]:
        tail = ""
    else:
        why = meta.get("note") or "read {} of {}".format(meta.get("read"), meta.get("fresh_total") or meta.get("total"))
        tail = f" (INCOMPLETE: {why}; no job is closed from this read)"
    progress.sub(f"ATS feed {ats}:{slug}: {len(rows)} jobs{tail}")
    return rows, meta


def slug_candidates(name: str) -> list[str]:
    base = re.sub(r"\b(private|pvt|limited|ltd|inc|llc|technologies|technology|solutions|software|systems|services)\b\.?", "", name, flags=re.I)
    words = re.findall(r"[a-z0-9]+", base.lower())
    full = re.findall(r"[a-z0-9]+", name.lower())
    c = ["".join(words), "-".join(words), "".join(full), "-".join(full)]
    return [x for i, x in enumerate(c) if x and x not in c[:i]]


def guess(name: str) -> list[dict]:
    """Try likely slugs on boards with trustworthy 404s. Results are candidates, never ingested
    until the board is confirmed (linked from the official site, or the feed names the company)."""
    found = []
    for slug in slug_candidates(name):
        for ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            try:
                rows, meta = FETCHERS[ats](slug)
            except requests.RequestException:
                continue
            if rows:
                names = meta.get("company_names") or []
                match = any(re.sub(r"\W", "", name.lower())[:6] in re.sub(r"\W", "", n.lower()) for n in names)
                found.append({"ats": ats, "slug": slug, "jobs": len(rows), "feed_company_names": names, "name_match": match})
    return found


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "detect":
        print(json.dumps(detect(sys.argv[2:]), indent=1))
    elif cmd == "fetch":
        rows, meta = fetch(sys.argv[2], sys.argv[3])
        print(json.dumps({"meta": meta, "count": len(rows), "rows": rows}, indent=1))
    elif cmd == "guess":
        print(json.dumps(guess(" ".join(sys.argv[2:])), indent=1))
    else:
        sys.exit(__doc__)
