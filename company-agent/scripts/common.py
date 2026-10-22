"""Shared helpers for the company agent. Standard library only, so the hook can import it."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from jobfields import TYPED_COLUMNS

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "memory.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
# Recruit Desk keeps the Excel files in its data folder (Documents/RecruitDesk-Data/<assistant>) and says where in this variable; run by hand
# without it, the file stays in this folder as before.
WORKBOOK_PATH = (Path(os.environ["RECRUIT_DESK_WORKBOOK_DIR"]) if os.environ.get("RECRUIT_DESK_WORKBOOK_DIR") else ROOT) / "Company_Hiring_Intelligence.xlsx"
RUNS_DIR = ROOT / "runs"

# Column order of every company sheet (from BRIEF.md).
COLUMNS = [
    "Company Name",
    "Official Website",
    "Career Page",
    "Job Title",
    "Location / Work Mode",
    "Source Type",
    "Job / Post URL",
    "Posted By",
    "Posted Date",
    "Date Discovered",
    "Last Checked",
    "Description Summary",
    "Application URL",
    "Status",
    # Added 2026-10-15, appended last so 14-column sheets migrate on their next upsert.
    "Posted Date Basis",
    # Added 2026-10-16, appended last: typed fields filled by jobfields.fill_typed (never by the LLM).
    *TYPED_COLUMNS,
]

STATUSES = {"Open", "Closed", "Unverified", "Stale"}
SOURCE_TYPES = {
    "Careers Page",
    "ATS: Greenhouse",
    "ATS: Lever",
    "ATS: Workable",
    "ATS: Ashby",
    "ATS: SmartRecruiters",
    "ATS: Zoho Recruit",
    "ATS: Workday",
    "LinkedIn (public index)",
    "Indeed (public index)",
    "Job Board (public index)",
    "Public Hiring Post",
}
# Boards read through an active reader spec (schema/readers/*.json) are labelled "ATS: <label>".
SPEC_SOURCE = re.compile(r"^ATS: [A-Za-z0-9][A-Za-z0-9 .&-]{1,39}$")


def is_source_type(s: str) -> bool:
    return s in SOURCE_TYPES or bool(SPEC_SOURCE.fullmatch(s or ""))


# Sources that list every open role, so a missing role means it closed.
COMPLETE_SOURCES = {s for s in SOURCE_TYPES if s == "Careers Page" or s.startswith("ATS:")}

# ---- Posted Date from a relative age ("3 weeks ago") -------------------------------------------
# Search snippets of public listings (LinkedIn via Exa) carry no publish date, only an age such as
# "3 weeks ago" or "30+ days ago". The scout records that text in "_posted_age"; this code, never the
# LLM, turns it into an approximate Posted Date and says so in "Posted Date Basis".
EXACT_BASIS = "exact (from page)"
UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}
# Extra days a Stale check adds for the precision of an estimate (weeks are +-7 days, months +-30).
UNIT_MARGIN = {"minute": 0, "hour": 0, "day": 0, "week": 7, "month": 30}
_UNIT = {"min": "minute", "mins": "minute", "minute": "minute", "minutes": "minute",
         "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
         "day": "day", "days": "day", "wk": "week", "wks": "week", "week": "week", "weeks": "week",
         "mo": "month", "mos": "month", "month": "month", "months": "month"}
_AGE_CORE = (r"(?:(?P<repost>re-?posted)\s+)?(?:"
             r"(?P<now>just now|today)|(?P<yday>yesterday)|"
             r"(?P<n>\d{1,3}|an?|one)(?P<plus>\+)?\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?|wks?|weeks?|months?|mos?)"
             r"(?P<ago>\s+ago)?)")
# The whole field is the age ("1 day", "today"): "ago" is optional.
_AGE_FULL = re.compile(r"^(?:posted\s+)?" + _AGE_CORE + r"$", re.I)
# An age inside longer snippet text: only "... ago" or "(re)posted today/yesterday" count, so
# "5 days a week" or "apply today" never become a date.
_AGE_IN_TEXT = re.compile(r"\b" + _AGE_CORE + r"(?=\W|$)", re.I)
APPROX_BASIS = re.compile(r'^approx: "(?P<phrase>[^"]{1,40})" seen (?P<seen>\d{4}-\d{2}-\d{2})(?: \((?:month precision|upper bound: posted on or before)\))?$')


def approx_posted(text: str | None, seen: str | None = None) -> dict | None:
    """Relative age text plus the date it was seen -> {"date": "YYYY-MM-DD", "basis": ..., "margin": days}.
    None when the text holds no age it can read. Weeks are 7 days, months 30 (month precision);
    "N+" (e.g. "30+ days ago") gives an upper bound (posted on or before the date)."""
    t = clean_text(text)
    if not t or len(t) > 400:
        return None
    try:
        base = dt.date.fromisoformat((seen or today())[:10])
    except ValueError:
        return None
    m = _AGE_FULL.match(t)
    if not m:
        m = next((x for x in _AGE_IN_TEXT.finditer(t) if x["ago"] or (x["repost"] and (x["now"] or x["yday"]))
                  or (x["now"] and re.search(r"posted\s+$", t[: x.start()], re.I))
                  or (x["yday"] and re.search(r"posted\s+$", t[: x.start()], re.I))), None)
    if not m:
        return None
    if m["now"]:
        days, unit = 0, "day"
    elif m["yday"]:
        days, unit = 1, "day"
    else:
        n = m["n"].lower()
        n = 1 if n in ("a", "an", "one") else int(n)
        unit = _UNIT[m["unit"].lower()]
        days = n * UNIT_DAYS[unit] if unit not in ("minute", "hour") else (n // 24 if unit == "hour" else 0)
    phrase = re.sub(r"\s+", " ", m.group(0)).strip()
    if phrase.lower().startswith("posted "):
        phrase = phrase[7:]
    tag = ""
    margin = UNIT_MARGIN[unit]
    if m["plus"]:
        tag, margin = " (upper bound: posted on or before)", 0
    elif unit == "month":
        tag = " (month precision)"
    date = (base - dt.timedelta(days=days)).isoformat()
    return {"date": date, "basis": f'approx: "{phrase}" seen {base.isoformat()}{tag}', "margin": margin}


def basis_margin(basis: str | None) -> int:
    """Days a Stale check adds for an approximate Posted Date (0 for an exact date or no basis)."""
    m = APPROX_BASIS.match(basis or "")
    hit = approx_posted(m["phrase"], m["seen"]) if m else None
    return hit["margin"] if hit else 0


def fill_posted_date(row: dict, seen: str | None = None) -> dict:
    """Set Posted Date and Posted Date Basis on a row in place: a real date is 'exact (from page)';
    a blank date with a readable "_posted_age" gets the estimate; otherwise both stay blank."""
    pd = str(row.get("Posted Date") or "").strip()
    if pd:
        if not str(row.get("Posted Date Basis") or "").strip():
            row["Posted Date Basis"] = EXACT_BASIS
        return row
    hit = approx_posted(row.get("_posted_age"), seen or row.get("Last Checked") or row.get("Date Discovered") or today())
    row["Posted Date"] = hit["date"] if hit else ""
    row["Posted Date Basis"] = hit["basis"] if hit else ""
    return row


TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gh_src|gh_jid_src|lever-source|lever-origin|source|ref|refid|trk|trackingid|src|fbclid|gclid)$",
    re.I,
)


def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def bare_domain(value: str) -> str:
    """'https://www.Razorpay.com/careers' -> 'razorpay.com'."""
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    host = (urlsplit(value).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    """Canonical form used as a dedupe key: lower-case host without www, no fragment,
    no tracking parameters, sorted query, no trailing slash."""
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    # One spelling per path: "café" and "caf%C3%A9" are the same URL (first Nordstrom run stored both).
    path = quote(unquote(re.sub(r"/{2,}", "/", parts.path or "/")), safe="/:@!$&'()*+,;=~")
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k))
    return urlunsplit(("https", host, path, urlencode(query), ""))


# Public job indexes whose search snippets often point at a listing page, not at one job
# (Razorpay scan, 2026-10-15: 29 LinkedIn rows shared https://in.linkedin.com/jobs/razorpay-jobs).
# A per-job URL carries an id: LinkedIn /jobs/view/<id> or ?currentJobId=, Indeed ?jk= / ?vjk=.
AGGREGATE_HOSTS = re.compile(r"(?:^|\.)(?:linkedin\.com|indeed\.(?:com|co\.[a-z]{2}|[a-z]{2,3}))$", re.I)
PER_JOB_PATH = re.compile(r"/jobs/view/(?:[^/]*-)?\d+/?$", re.I)
PER_JOB_PARAMS = {"currentjobid", "jk", "vjk"}


def is_aggregate_url(url: str) -> bool:
    """True for a LinkedIn or Indeed listing URL (a search, a company jobs page) that names no single
    job. Such a URL cannot tell two jobs apart, so job_key keys those rows by company, title and
    location instead. Every other URL is treated as naming one job."""
    if not url:
        return False
    parts = urlsplit(url.strip() if "://" in url else "https://" + url.strip())
    if not AGGREGATE_HOSTS.search(parts.hostname or ""):
        return False
    if PER_JOB_PATH.search(parts.path or ""):
        return False
    return not any(k.lower() in PER_JOB_PARAMS and v.strip() for k, v in parse_qsl(parts.query))


def _key_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_key(row: dict) -> str:
    """The dedupe key of a row in memory.db and in the workbook: the normalised URL of a per-job link;
    for an aggregate listing URL (is_aggregate_url) "company|title|location", normalised, so distinct
    jobs behind one listing page stay distinct and the same title and location is still one job."""
    url = row.get("Job / Post URL") or ""
    if url and is_aggregate_url(url):
        return "|".join(_key_text(row.get(c, "")) for c in ("Company Name", "Job Title", "Location / Work Mode"))
    if url:
        return normalize_url(url)
    parts = [row.get("Company Name", ""), row.get("Job Title", ""), row.get("Location / Work Mode", "")]
    return "|".join(re.sub(r"\s+", " ", p).strip().lower() for p in parts)


# Navigation, pagination and listing links that look like job links but are not jobs
# (first Nordstrom run, 2026-10-15: "Open Jobs", "Go to last page", "Job Location", page numbers).
NAV_TITLE = re.compile(
    r"^(?:\d+|page \d+|(?:go to )?(?:first|last|next|previous|prev) page|next|previous|prev|first|last|more|"
    r"(?:open|all|current|search|view all|see all|browse|find) (?:jobs?|roles?|positions?|openings?|careers?)|"
    r"jobs?|careers?|home|apply|apply now|job (?:location|locations|category|categories|type|search|alerts?)|"
    r"locations?|categories|teams?|departments?|search|filters?|sign in|log ?in|back|menu)$",
    re.I,
)
# A path that is a listing, not a job: /jobs/page/3, /careers/page/12, /jobs?page=2, a bare /jobs.
LISTING_PATH = re.compile(r"/(?:page|p)/\d+/?$|^/(?:careers?|jobs?|openings?|positions?)/?$", re.I)


def looks_like_job(title: str, url: str) -> bool:
    """False for rows that are navigation or listing links rather than jobs: a numeric or
    navigation title, a title of fewer than 4 letters, or a listing/pagination URL."""
    t = clean_text(title)
    if not t or NAV_TITLE.match(t) or len(re.findall(r"[A-Za-z]", t)) < 4:
        return False
    p = urlsplit(url or "")
    if LISTING_PATH.search(p.path or "/") or re.search(r"(?:^|&)(?:page|pg|p)=\d+", p.query or ""):
        return False
    return True


_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def safe_sheet_name(name: str, taken: set[str] | None = None) -> str:
    """Excel sheet names: 1-31 chars, none of []:*?/\\, no leading/trailing apostrophe,
    unique case-insensitively among `taken`."""
    base = _BAD_SHEET_CHARS.sub(" ", name or "Company")
    base = re.sub(r"\s+", " ", base).strip().strip("'").strip() or "Company"
    if base.lower() == "history":  # reserved by Excel
        base = "History (company)"
    base = base[:31].rstrip()
    taken_lower = {t.lower() for t in (taken or set())}
    candidate, n = base, 2
    while candidate.lower() in taken_lower:
        suffix = f" ({n})"
        candidate = base[: 31 - len(suffix)].rstrip() + suffix
        n += 1
    return candidate


def clean_text(text: str | None, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


SENTENCE_END = re.compile(r"[.!?:]$")


def shared_prefix(texts: list[str], share: float = 0.6, min_texts: int = 3, min_words: int = 8) -> list[str]:
    """The longest run of leading words that at least `share` of the texts start with (the company
    blurb every posting opens with), cut back to a sentence end. [] when there is none."""
    words = [t.split() for t in texts if t and t.strip()]
    if len(words) < min_texts:
        return []
    need = max(min_texts, int(share * len(words) + 0.999))
    prefix: list[str] = []
    group = words
    while True:
        i = len(prefix)
        counts: dict[str, int] = {}
        for w in group:
            if len(w) > i:
                counts[w[i]] = counts.get(w[i], 0) + 1
        if not counts:
            break
        word, n = max(counts.items(), key=lambda x: x[1])
        if n < need:
            break
        prefix.append(word)
        group = [w for w in group if len(w) > i and w[i] == word]
    ends = [i for i, w in enumerate(prefix) if SENTENCE_END.search(w)]
    prefix = prefix[: ends[-1] + 1] if ends else []
    return prefix if len(prefix) >= min_words else []


def strip_prefix(text: str, prefix: list[str]) -> str:
    """text without the shared prefix words; the original text when it does not start with them
    or nothing would be left."""
    w = (text or "").split()
    if prefix and w[: len(prefix)] == prefix and len(w) > len(prefix):
        return " ".join(w[len(prefix):])
    return text or ""


LEAD_LABEL = re.compile(r"^(?:(?:job|position|role) (?:description|summary|overview)|about (?:the|this) (?:job|role|position)|description|overview|summary)\s*[:\-–]?\s+", re.I)


def strip_labels(text: str) -> str:
    """'Job Description JOB SUMMARY The technician …' -> 'The technician …' (section labels a
    posting opens with say nothing about the role)."""
    prev = None
    while text and text != prev:
        prev, text = text, LEAD_LABEL.sub("", text, count=1)
    return text


def load_env() -> None:
    """Load ROOT/.env into os.environ without printing anything."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def user_agent() -> str:
    load_env()
    contact = os.environ.get("CRAWLER_CONTACT", "").strip()
    tail = f"; contact {contact}" if contact else ""
    return f"CompanyHiringIntelBot/0.1 (+polite; obeys robots.txt{tail})"
