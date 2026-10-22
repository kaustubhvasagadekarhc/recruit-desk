"""Shared helpers for the Candidate Agent. Standard library only, so the
PreToolUse hook can import it with a bare python3."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "memory.db"
# Recruit Desk keeps the Excel files in its data folder (Documents/RecruitDesk-Data/<assistant>) and says where in this variable; run by hand
# without it, the file stays in this folder as before.
WORKBOOK_PATH = (Path(os.environ["RECRUIT_DESK_WORKBOOK_DIR"]) if os.environ.get("RECRUIT_DESK_WORKBOOK_DIR") else ROOT) / "Candidate_Research.xlsx"
SCHEMA_PATH = ROOT / "schema" / "memory.sql"

# (JSON field, Excel header, column width)
COLUMNS = [
    ("candidate_name", "Candidate Name", 24),
    ("current_role_company", "Current Role / Company", 34),
    ("location", "Location", 22),
    ("experience", "Experience", 12),
    ("key_skills", "Key Skills", 40),
    ("match_score", "Match Score (0-100)", 12),
    ("score_breakdown", "Score Breakdown", 52),
    ("evidence_confidence", "Evidence Confidence", 12),
    ("profile_url", "Profile URL", 44),
    ("email_mobile", "Email / Mobile", 24),
    ("source", "Source", 22),
    ("evidence_notes", "Evidence / Notes", 60),
    ("date_found", "Date Found", 12),
    ("recruiter_status", "Recruiter Status", 14),
    ("evidence_sources", "Evidence Sources", 10),   # added 2026-10-15 (plan item 6), appended so old sheets migrate in place
    # key factors (2026-10-15), appended at the end so older sheets migrate in place
    ("open_to_work_match", "Open to Work", 26),
    ("stack_match", "Stack Match", 44),
    ("experience_match", "Experience Match", 28),
    ("location_match", "Location Match", 30),
    # structured fields (2026-10-16, data-structure plan items 1 and 4), appended at the end so older sheets
    # migrate in place; filled by structured_fields() from the row itself, blank when the row does not say
    ("first_name", "First Name", 14),
    ("last_name", "Last Name", 16),
    ("current_title", "Title", 30),
    ("current_company", "Company", 22),
    ("experience_years", "Experience Years", 10),
    ("experience_basis", "Experience Basis", 12),
    ("work_mode", "Work Mode", 10),
    ("pts_core", "Core Skills pts", 8),
    ("pts_experience", "Experience pts", 8),
    ("pts_domain", "Domain pts", 8),
    ("pts_seniority", "Seniority pts", 8),
    ("pts_location", "Location pts", 8),
    ("pts_preferred", "Preferred pts", 8),
    ("pts_otw", "OTW pts", 8),
    ("pts_cap", "Cap Adjustment pts", 10),
    ("evidence_count", "Evidence Count", 9),
    # GitHub and Stack Exchange skill evidence (2026-10-16, plan item 9), appended at the end; filled by
    # dev_columns() from the batch's dev_signals (scripts/dev_evidence.py), blank when the person links no profile
    ("github_languages", "GitHub Languages", 34),
    ("github_last_active", "GitHub Last Active", 12),
    ("so_top_tags", "SO Top Tags", 34),
]
HEADERS = [c[1] for c in COLUMNS]
BASE_HEADERS = HEADERS[:14]   # header row of sheets written before 2026-10-15
# 14 = before Evidence Sources, 15 = before the key-factor columns, 19 = before the structured fields (2026-10-16),
# 35 = before the GitHub / Stack Exchange columns (2026-10-16)
LEGACY_HEADER_LENGTHS = (14, 15, 19, 35)
DEV_FIELDS = ("github_languages", "github_last_active", "so_top_tags")
FACTOR_FIELDS = ("open_to_work_match", "stack_match", "experience_match", "location_match")
OPEN_TO_WORK = ("Yes", "Likely", "Unknown")
LOCATION_MATCHES = ("Same city", "Same region", "Other", "Remote OK", "Unknown")
CONFIDENCE = {"High", "Medium", "Low"}
STATUSES = ["New", "Reviewed", "Shortlisted", "Contacted", "Rejected"]
DEFAULT_WEIGHTS = {
    "core_skills": 35,
    "experience": 20,
    "domain_projects": 15,
    "seniority": 10,
    "location": 10,
    "preferred_skills": 10,
}

_TRACKING = re.compile(r"^(utm_|trk|originalSubdomain|lipi|miniProfile)", re.I)


def normalise_url(url: str) -> str:
    """Lowercase scheme/host, drop query, fragment, tracking and trailing slash.
    LinkedIn country subdomains (in.linkedin.com) collapse to www.linkedin.com."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    host = p.netloc.lower().split("@")[-1].split(":")[0]
    if host.endswith("linkedin.com"):
        host = "www.linkedin.com"
        path = p.path.lower()
    else:
        path = p.path
        if host.startswith("www."):
            host = host[4:]
    path = re.sub(r"/+", "/", path).rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def dedupe_key(profile_url: str, name: str = "", company: str = "") -> str:
    """Normalised profile URL, else name plus company."""
    u = normalise_url(profile_url)
    if u:
        return "url:" + u
    return "nc:" + _norm_text(name) + "|" + _norm_text(company)


def safe_sheet_name(title: str, taken: set[str] | None = None) -> str:
    """Excel sheet name: no []:*?/\\ , not blank, at most 31 chars, unique."""
    name = re.sub(r"[\[\]:*?/\\]", " ", title or "").strip().strip("'")
    name = re.sub(r"\s+", " ", name) or "JD"
    if name.lower() == "history":
        name = "JD History"
    name = name[:31].rstrip()
    taken_l = {t.lower() for t in (taken or set())}
    base, n = name, 2
    while name.lower() in taken_l:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)].rstrip() + suffix
        n += 1
    return name


# ---------- validation (also used by the PreToolUse hook) ----------

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_PHONE = re.compile(r"^\+?[\d\s().-]{8,20}$")
_FAKE = re.compile(r"example\.(com|org)|test@|noreply|xxx|\b(\d)\1{7,}\b|1234567|n/?a@", re.I)


def _is_url(s: str) -> bool:
    p = urlsplit((s or "").strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def validate_row(row: dict) -> list[str]:
    """Return a list of problems; empty means the row may be written."""
    errs = []
    name = (row.get("candidate_name") or "").strip()
    if not name:
        errs.append("candidate_name is required")
    if not _is_url(row.get("profile_url", "")):
        errs.append("profile_url must be an http(s) source URL")
    if not (row.get("source") or "").strip():
        errs.append("source is required")
    d = str(row.get("date_found") or "")
    try:
        if date.fromisoformat(d) > date.today():
            errs.append("date_found is in the future")
    except ValueError:
        errs.append("date_found must be YYYY-MM-DD")
    score = row.get("match_score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        errs.append("match_score must be an integer 0-100")
    if not (row.get("score_breakdown") or "").strip():
        errs.append("score_breakdown is required")
    conf = row.get("evidence_confidence")
    if conf not in CONFIDENCE:
        errs.append("evidence_confidence must be High, Medium or Low")
    elif conf == "Low" and isinstance(score, int) and score >= 70:
        errs.append("a score of 70+ on Low-confidence evidence would look verified; lower it or find more evidence")
    if not (row.get("evidence_notes") or "").strip():
        errs.append("evidence_notes is required (what the score rests on)")
    status = row.get("recruiter_status") or "New"
    if status not in STATUSES:
        errs.append(f"recruiter_status must be one of {STATUSES}")
    errs += validate_factors(row)
    errs += validate_dev_signals(row)
    contact = (row.get("email_mobile") or "").strip()
    if contact:
        if not _is_url(row.get("contact_source_url", "")):
            errs.append("email_mobile needs contact_source_url: the public page where the person self-published it")
        for part in re.split(r"[;,|]", contact):
            part = part.strip()
            if not part:
                continue
            if _FAKE.search(part):
                errs.append(f"email_mobile looks fabricated or placeholder: {part!r}")
            elif not (_EMAIL.match(part) or _PHONE.match(part)):
                errs.append(f"email_mobile is not an email or phone number: {part!r}")
    return errs


def validate_factors(row: dict) -> list[str]:
    """The four key-factor columns (2026-10-15): all filled by score.py; open to work Yes/Likely only with a quote
    that is in the row's evidence and is itself an open-to-work signal."""
    errs = [f"{f} is required (run score.py)" for f in FACTOR_FIELDS if not (row.get(f) or "").strip()]
    otw = row.get("open_to_work")
    if otw not in OPEN_TO_WORK:
        errs.append(f"open_to_work must be one of {list(OPEN_TO_WORK)} (run score.py)")
    elif otw != "Unknown":
        quote = (row.get("open_to_work_quote") or "").strip()
        if not quote:
            errs.append(f"open_to_work {otw} needs open_to_work_quote, the exact phrase from the evidence")
        elif _squash(quote) not in evidence_text(row):
            errs.append(f"open_to_work quote not found in the evidence: {quote!r}")
        elif (detect_open_to_work(quote) or ("Unknown",))[0] != otw:
            errs.append(f"open_to_work {otw} does not match its quote {quote!r}")
    loc = (row.get("location_match") or "").split(" · ")[0]
    if (row.get("location_match") or "").strip() and loc not in LOCATION_MATCHES:
        errs.append(f"location_match must start with one of {list(LOCATION_MATCHES)}")
    return errs


def _strings(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield str(k)
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif v is not None:
        yield str(v)


_ANY_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def validate_dev_signals(row: dict) -> list[str]:
    """GitHub / Stack Exchange data is skill evidence only: it never carries an email (GitHub AUP, plan item 9)."""
    sig = row.get("dev_signals")
    if not sig:
        return []
    if not isinstance(sig, dict):
        return ["dev_signals must be an object (run scripts/dev_evidence.py)"]
    if any(k.lower() == "email" or _ANY_EMAIL.search(k) for k in _strings(sig)) or \
            any(_ANY_EMAIL.search(x) for x in _strings(sig)):
        return ["dev_signals must never hold an email: GitHub and Stack Exchange data is skill evidence, not contact data"]
    return []


def validate_payload(payload: dict) -> list[str]:
    errs = []
    if not (payload.get("jd_id") or "").strip():
        errs.append("payload.jd_id is required")
    rows = payload.get("candidates")
    if not isinstance(rows, list) or not rows:
        errs.append("payload.candidates must be a non-empty list")
        return errs
    for i, r in enumerate(rows):
        for e in validate_row(r):
            errs.append(f"candidates[{i}] ({r.get('candidate_name', '?')}): {e}")
    return errs


# ---------- open to work (key factor, 2026-10-15) ----------
# Self-published signals only, read from the public search snippet. Never inferred from tenure or job changes.
# (pattern, value): "Yes" = an explicit signal, "Likely" = an indirect one (a notice period).
OTW_PATTERNS = [
    (r"#\s?open\s?to\s?work\b", "Yes"),
    (r"\bopen\s+to\s+work\b", "Yes"),
    (r"\bactively\s+(?:looking|seeking)\b", "Yes"),
    (r"\blooking\s+for\s+(?:new\s+)?(?:job\s+)?opportunit(?:y|ies)\b", "Yes"),
    (r"\bopen\s+to\s+(?:new\s+)?(?:job\s+)?opportunit(?:y|ies)\b", "Yes"),
    # "Currently open to Senior Frontend Engineer roles", "Open to frontend leadership roles" (up to 5 words between)
    (r"\bopen\s+to\s+(?:[\w.+#/-]+\s+){0,5}?(?:roles?|positions?|openings?)\b", "Yes"),
    (r"\bimmediate(?:ly)?\s+(?:joiner|joining|available)\b", "Yes"),
    (r"\bserving\s+(?:my\s+|the\s+)?notice(?:\s+period)?\b", "Likely"),
    (r"\bnotice\s+period\s*(?:[:\-–]|of|is)?\s*\d{1,3}\s*days?\b", "Likely"),
    (r"\b\d{1,3}\s*days?\s+notice(?:\s+period)?\b", "Likely"),
]
_OTW = [(re.compile(p, re.I), v) for p, v in OTW_PATTERNS]
# "not open to work", "no longer looking", "isn't actively looking": a negation just before the phrase cancels it
_OTW_NEG = re.compile(r"(?:\bnot|\bno\s+longer|\bnever|n't|\bno)\s+(?:currently\s+|really\s+)?$", re.I)
OTW_POINTS = {"Yes": 5, "Likely": 2, "Unknown": 0}


def _squash(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def evidence_text(c: dict) -> str:
    """The text a row's quotes must come from: the snippet notes and the profile fields (lowercased, spaces squashed)."""
    parts = [c.get(f) or "" for f in ("evidence_notes_raw", "evidence_notes", "key_skills", "current_role_company",
                                      "experience", "location_raw", "location")]
    return _squash(" ".join(map(str, parts)))


def detect_open_to_work(text: str) -> tuple[str, str] | None:
    """("Yes" | "Likely", exact quote) for the strongest self-published signal in text, else None."""
    t = re.sub(r"\s+", " ", str(text or ""))
    best = None
    for rx, value in _OTW:
        for m in rx.finditer(t):
            if _OTW_NEG.search(t[max(0, m.start() - 24): m.start()]):
                continue
            if best is None or (value == "Yes" and best[0] == "Likely"):
                best = (value, m.group(0).strip())
            break
    return best


# ---------- location (one canonical form per city) ----------

# city key -> (canonical text written to the Location column, region, aliases matched as whole words).
# Cities in the same region (or NCR) count as "same metro or region" for scoring.
CITIES = {
    "pune": ("Pune, Maharashtra, India", "maharashtra",
             ["pune", "pimpri", "chinchwad", "pcmc", "hinjewadi", "hinjawadi", "kharadi", "wakad", "baner",
              "hadapsar", "magarpatta", "viman nagar", "aundh", "kothrud"]),
    "mumbai": ("Mumbai, Maharashtra, India", "maharashtra",
               ["mumbai", "bombay", "navi mumbai", "thane", "powai", "andheri", "vashi"]),
    "nagpur": ("Nagpur, Maharashtra, India", "maharashtra", ["nagpur"]),
    "nashik": ("Nashik, Maharashtra, India", "maharashtra", ["nashik", "nasik"]),
    "bengaluru": ("Bengaluru, Karnataka, India", "karnataka", ["bengaluru", "bangalore"]),
    "hyderabad": ("Hyderabad, Telangana, India", "telangana", ["hyderabad", "secunderabad"]),
    "chennai": ("Chennai, Tamil Nadu, India", "tamil nadu", ["chennai", "madras"]),
    "delhi": ("Delhi, India", "ncr", ["delhi", "new delhi"]),
    "gurugram": ("Gurugram, Haryana, India", "ncr", ["gurugram", "gurgaon"]),
    "noida": ("Noida, Uttar Pradesh, India", "ncr", ["noida", "greater noida"]),
    "kolkata": ("Kolkata, West Bengal, India", "west bengal", ["kolkata", "calcutta"]),
    "ahmedabad": ("Ahmedabad, Gujarat, India", "gujarat", ["ahmedabad"]),
    "indore": ("Indore, Madhya Pradesh, India", "madhya pradesh", ["indore"]),
}
_REGION_WORDS = {"maharashtra", "karnataka", "telangana", "tamil nadu", "ncr", "west bengal", "gujarat", "madhya pradesh"}
_ALIASES = sorted(((a, k) for k, (_, _, al) in CITIES.items() for a in al), key=lambda t: -len(t[0]))


def canonical_city(text: str) -> str | None:
    """City key for a free-text location ('Pune/Pimpri-Chinchwad Area' -> 'pune'); the earliest mention wins."""
    t = " " + _norm_text(text) + " "
    best = None
    for alias, key in _ALIASES:
        i = t.find(" " + alias + " ")
        if i >= 0 and (best is None or i < best[0]):
            best = (i, key)
    return best[1] if best else None


def region_of(text: str) -> str | None:
    city = canonical_city(text)
    if city:
        return CITIES[city][1]
    t = " " + _norm_text(text) + " "
    return next((r for r in _REGION_WORDS if " " + r + " " in t), None)


def normalise_location(text: str) -> str:
    """One canonical form for known cities ('Pune District, Maharashtra, India' -> 'Pune, Maharashtra, India');
    unknown places are returned trimmed and unchanged."""
    city = canonical_city(text)
    return CITIES[city][0] if city else re.sub(r"\s+", " ", (text or "")).strip()


# ---------- discovery query plan ----------

def open_to_work_query(criteria: dict) -> str:
    """The optional extra query for self-published open-to-work signals: a skill, the city, never a name."""
    city = (criteria.get("location") or "").split(",")[0].strip()
    s1 = next((s for s in criteria.get("mandatory_skills") or [] if s), "")
    return re.sub(r"\s+", " ", f'"open to work" {s1} developer {city} site:linkedin.com/in').strip()


def plan_queries(title: str, criteria: dict, synonyms: list[str] | None = None, limit: int = 6) -> list[str]:
    """Exa queries built from the JD: title, role synonyms, city and mandatory skills. Never a person's name."""
    city = (criteria.get("location") or "").split(",")[0].strip()
    skills = [s for s in criteria.get("mandatory_skills") or [] if s][:3]
    s1 = skills[0] if skills else ""
    syns = []
    for s in list(criteria.get("role_synonyms") or []) + list(synonyms or []):
        if s and s.lower() != (title or "").lower() and s.lower() not in {x.lower() for x in syns}:
            syns.append(s)
    qs = [f"category:people {title} {city} {' '.join(skills)}"]
    qs += [f"site:linkedin.com/in {s} {city} {s1}" for s in syns[:3]]
    qs += [f"site:github.com {s1} developer {city}", f"{title} {city} speaker OR blog {s1}"]
    out = []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in out:
            out.append(q)
    return out[:limit]


def row_key(profile_url: str, name: str, role_company: str = "") -> str:
    """Dedupe key for a workbook row: company is the part after '@' or ' at '."""
    rc = role_company or ""
    company = rc.split("@")[-1].split(" at ")[-1] if ("@" in rc or " at " in rc) else rc
    return dedupe_key(profile_url, name, company)


def header_len(row: list) -> int | None:
    """How many known columns a header row holds (len(HEADERS) when current, 14, 15, 19 or 35 for older sheets), else None."""
    row = list(row)
    if row[: len(HEADERS)] == HEADERS:
        return len(HEADERS)
    for n in sorted(LEGACY_HEADER_LENGTHS, reverse=True):
        if row[:n] == HEADERS[:n] and not any(row[n:]):
            return n
    return None


def header_ok(row: list) -> bool:
    """True for a current header row or an older one that the next upsert migrates in place."""
    return header_len(row) is not None


def delete_sheet_rows(ws, rows) -> None:
    """Delete rows (bottom up) and re-point hyperlinks: openpyxl's delete_rows moves cells but leaves each
    hyperlink's ref on its old coordinate, which shows up as a ghost row with a moved link."""
    for r in sorted(set(rows), reverse=True):
        ws.delete_rows(r)
    for row in ws.iter_rows():
        for c in row:
            if c.hyperlink is not None:
                c.hyperlink.ref = c.coordinate


# ---------- suppression (plan item 10: forgotten people are never re-added) ----------

def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def suppression_hashes(profile_url: str, name: str, role_company: str = "") -> dict[str, str]:
    """{kind: sha256} for the person's URL key (if any) and name|company key."""
    out = {"nc": key_hash(row_key("", name, role_company))}
    if normalise_url(profile_url):
        out["url"] = key_hash(row_key(profile_url, name, role_company))
    return out


def suppressed_hashes(db_path=None) -> set[str]:
    """All suppressed key hashes in memory.db (empty if the db or the table is missing)."""
    p = Path(db_path or DB_PATH)
    if not p.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return {r[0] for r in con.execute("SELECT key_hash FROM suppressed")}
        finally:
            con.close()
    except sqlite3.Error:
        return set()


def is_suppressed(c: dict, hashes: set[str]) -> bool:
    h = suppression_hashes(c.get("profile_url", ""), c.get("candidate_name", ""), c.get("current_role_company", ""))
    return bool(hashes & set(h.values()))


def drop_suppressed(cands: list[dict], db_path=None) -> tuple[list[dict], int]:
    """(kept candidates, number dropped because the person asked to be forgotten)."""
    hashes = suppressed_hashes(db_path)
    if not hashes:
        return cands, 0
    kept = [c for c in cands if not is_suppressed(c, hashes)]
    return kept, len(cands) - len(kept)


def snippet_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()


# ---------- GDPR Art. 14 notice (first contact with a person whose data was not collected from them) ----------

ART14_NOTICE = ("We found your public profile on {source} on {date} while looking for candidates for a "
                "{role} role at {company}. We keep only your name, current role, location, public profile "
                "link and the skills shown there, for recruitment, under our legitimate interest, for up to "
                "180 days. You can ask us to see this data, correct it or delete it at any time by replying "
                "to this message; if you ask us to delete it we will not contact you again.")


def art14_notice(source: str, found: str, role: str, company: str) -> str:
    return ART14_NOTICE.format(source=source, date=found, role=role, company=company)


# ---------- structured fields (2026-10-16, data-structure plan items 1 and 4) ----------
# One fact per cell, derived only from what the row already says: a typed field stays blank when the row has
# nothing for it (no inference, no lookup). The overloaded columns are kept unchanged next to them.

_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?|yoe)\b", re.I)
_LEAD_NUM = re.compile(r"^\D{0,3}(\d+(?:\.\d+)?)")


def parse_years(text: str) -> tuple[float | None, bool]:
    """'7+ yrs (self-stated)' -> (7, False); '~6+ yrs (estimated from timeline)' -> (6, True); '' -> (None, False)."""
    t = text or ""
    m = _YEARS.search(t) or _LEAD_NUM.search(t)
    if not m:
        return None, False
    estimated = "~" in t[: m.end()] or bool(re.search(r"estimat|approx|about", t, re.I))
    return float(m.group(1)), estimated


EXPERIENCE_BASES = ("self-stated", "computed", "estimate")
WORK_MODES = ("Onsite", "Hybrid", "Remote", "Unknown")
_PAREN = re.compile(r"\s*[(\[][^)\]]*[)\]]")
_HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|er|prof)\.?\s+", re.I)
_NO_COMPANY = re.compile(r"not (shown|stated|listed|given|public)|unknown|undisclosed|confidential|^n/?a$|^-+$", re.I)
_WORK_MODE_RX = {"Remote": re.compile(r"\bremote\b", re.I), "Hybrid": re.compile(r"\bhybrid\b", re.I),
                 "Onsite": re.compile(r"\bon-?\s?site\b|\bin[- ]office\b", re.I)}
# Score Breakdown label -> structured column (score.py LABELS plus the older LLM-only spellings)
_PART_FIELDS = (("core", "pts_core"), ("experience", "pts_experience"), ("domain", "pts_domain"),
                ("seniority", "pts_seniority"), ("location", "pts_location"), ("preferred", "pts_preferred"))
_PART_RX = re.compile(r"^\s*([A-Za-z][A-Za-z/ -]*?)\s+(\d+(?:\.\d+)?)\s*/\s*\d+")
_OTW_RX = re.compile(r"^\s*open to work\s+(?:\+(\d+)|unknown)", re.I)
SCORE_PART_FIELDS = tuple(f for _, f in _PART_FIELDS) + ("pts_otw",)


def _num(v: float):
    return int(v) if float(v).is_integer() else v


def split_name(name: str) -> tuple[str, str]:
    """'Rahul Sethi' -> ('Rahul', 'Sethi'); 'Dr. A. B. Rao, PMP (He/Him)' -> ('A.', 'B. Rao'); one word -> (word, '')."""
    t = _PAREN.sub("", name or "")
    t = re.split(r"[,|•·]", t)[0].strip()
    t = _HONORIFIC.sub("", t).strip()
    parts = t.split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def split_role_company(text: str) -> tuple[str, str]:
    """'Senior FE Engineer @ Acme' -> ('Senior FE Engineer', 'Acme'); a note in place of a company gives ''."""
    t = (text or "").strip()
    m = re.match(r"^(.*?)\s+(?:@|at)\s+(.+)$", t) if (" @ " in t or " at " in t or "@" in t) else None
    if m is None and "@" in t:
        m = re.match(r"^(.*?)\s*@\s*(.+)$", t)
    title, company = (m.group(1), m.group(2)) if m else (t, "")
    title = _PAREN.sub("", title).strip(" ,;-|")
    company = "" if _NO_COMPANY.search(company or "") else _PAREN.sub("", company).strip(" ,;-|")
    if _NO_COMPANY.search(title):
        title = ""
    return title, company


def experience_basis(text: str) -> str:
    """Basis of the Experience field: 'computed' (from dates or a timeline), 'estimate', 'self-stated', or '' (no years)."""
    years, est = parse_years(text)
    if years is None:
        return ""
    if re.search(r"computed|from (dates|timeline|the timeline)|timeline", text or "", re.I):
        return "computed"
    return "estimate" if est else "self-stated"


def work_mode_of(*texts: str) -> str:
    """Onsite / Hybrid / Remote when exactly one is stated in the given text, else Unknown (never inferred from silence)."""
    t = " ".join(x for x in texts if x)
    hits = [m for m, rx in _WORK_MODE_RX.items() if rx.search(t)]
    return hits[0] if len(hits) == 1 else "Unknown"


def score_parts(breakdown: str) -> dict:
    """Numbers from a Score Breakdown string: {'pts_core': 24, ..., 'pts_otw': 5}; parts it does not name are left out."""
    out = {}
    for seg in re.split(r"\s+·\s+", breakdown or ""):
        m = _OTW_RX.match(seg)
        if m:
            out["pts_otw"] = int(m.group(1) or 0)
            continue
        m = _PART_RX.match(seg)
        if not m:
            continue
        label = m.group(1).strip().lower()
        for prefix, field in _PART_FIELDS:
            if label.startswith(prefix) and field not in out:
                out[field] = _num(float(m.group(2)))
                break
    if "pts_otw" not in out and all(f in out for _, f in _PART_FIELDS):
        out["pts_otw"] = 0   # a full breakdown from before the open-to-work bonus (2026-10-15): no bonus in that score
    return out


def structured_fields(c: dict) -> dict:
    """The typed columns for one row, from its own cells. Values the batch already sets (first_name, current_title,
    work_mode, ...) win; everything else is derived, and is blank when the row does not say it."""
    out = {}
    first, last = split_name(c.get("candidate_name") or "")
    title, company = split_role_company(c.get("current_role_company") or "")
    years, _ = parse_years(c.get("experience") or "")
    derived = {
        "first_name": first, "last_name": last, "current_title": title, "current_company": company,
        "experience_years": _num(years) if years is not None else "",
        "experience_basis": experience_basis(c.get("experience") or ""),
        "work_mode": work_mode_of(c.get("location_raw") or "", c.get("location") or ""),
    }
    for k, v in derived.items():
        given = c.get(k)
        out[k] = given if given not in (None, "") else v
    if out["work_mode"] not in WORK_MODES:
        out["work_mode"] = work_mode_of(str(out["work_mode"]))
    parts = score_parts(c.get("score_breakdown") or "")
    for f in SCORE_PART_FIELDS:
        out[f] = parts.get(f, "")
    out.update({k: v for k, v in dev_columns(c).items() if c.get(k) in (None, "")})
    out["pts_cap"] = ""
    ms = c.get("match_score")
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and all(f in parts for f in SCORE_PART_FIELDS):
        out["pts_cap"] = _num(round(ms - sum(parts[f] for f in SCORE_PART_FIELDS), 2))
    return out


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"


def dev_columns(c: dict) -> dict:
    """GitHub Languages ('TypeScript 412 KB (62%), Go 120 KB (18%)'), GitHub Last Active (YYYY-MM-DD) and
    SO Top Tags ('reactjs (score 120, 45 answers)') from the batch's dev_signals; blank when it has none."""
    sig = c.get("dev_signals") if isinstance(c.get("dev_signals"), dict) else {}
    gh, se = sig.get("github") or {}, sig.get("stackexchange") or {}
    langs = {k: v for k, v in (gh.get("languages") or {}).items() if isinstance(v, int) and v > 0}
    total = sum(langs.values())
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:5]
    return {
        "github_languages": ", ".join(f"{k} {_kb(v)} ({v * 100 // total}%)" for k, v in top),
        "github_last_active": str(gh.get("last_active") or "")[:10],
        "so_top_tags": ", ".join(f"{t['tag']} (score {t.get('answer_score', 0)}, {t.get('answer_count', 0)} answers)"
                                 for t in (se.get("top_tags") or [])[:5] if t.get("tag")),
    }


DEV_SOURCE_TYPES = {"github": "GitHub API", "stackexchange": "Stack Exchange API"}


def dev_sources(c: dict) -> list[tuple[str, dict]]:
    """[(source type, signal)] for each GitHub / Stack Exchange signal that holds at least one fact."""
    sig = c.get("dev_signals") if isinstance(c.get("dev_signals"), dict) else {}
    return [(DEV_SOURCE_TYPES[k], sig[k]) for k in DEV_SOURCE_TYPES
            if isinstance(sig.get(k), dict) and sig[k].get("url") and sig[k].get("facts")]


# ---------- Evidence sheet (plan item 1): one row per fact and source ----------
EVIDENCE_SHEET = "Evidence"
ENTITY_SHEETS = ("Candidates", "Matches")   # entity exports (entities.py), rebuilt on every upsert; not JD sheets
EVIDENCE_COLUMNS = [("Candidate Key", 36), ("Fact", 34), ("Quote", 50), ("Source URL", 44), ("Source Type", 24),
                    ("Fetched On", 12), ("Candidate Name", 22), ("JD Id", 28)]
EVIDENCE_HEADERS = [h for h, _ in EVIDENCE_COLUMNS]
DIM_LABELS = {"core_skills": "Core skills", "domain_projects": "Domain/projects", "seniority": "Seniority",
              "preferred_skills": "Preferred skills", "experience": "Experience", "location": "Location"}


def evidence_rows(c: dict) -> list[dict]:
    """Evidence rows for one scored candidate: the claimed facts and per-dimension quotes against the primary source,
    the open-to-work quote, and the corroboration's confirmed facts against its page. Only what the batch cites."""
    def day(v):
        return str(v or "")[:10]
    found = day(c.get("fetched_at") or c.get("date_found"))
    primary = {"url": c.get("profile_url") or "", "source_type": c.get("source") or "unknown", "fetched_at": found}
    rows = []
    for f in c.get("facts") or []:
        fact = f.get("fact") if isinstance(f, dict) else f
        if fact and (not isinstance(f, dict) or f.get("status", "claimed") == "claimed"):
            rows.append(dict(primary, fact=str(fact), quote=""))
    for dim, qs in (c.get("quotes") or {}).items():
        for q in qs or []:
            if q:
                rows.append(dict(primary, fact=DIM_LABELS.get(dim, dim), quote=str(q)))
    if c.get("open_to_work_quote"):
        rows.append(dict(primary, fact=f"Open to work: {c.get('open_to_work') or 'Yes'}", quote=str(c["open_to_work_quote"])))
    cor = c.get("corroboration")
    if isinstance(cor, dict) and cor.get("url"):
        src = {"url": cor["url"], "source_type": cor.get("source_type") or "Web page", "fetched_at": day(cor.get("fetched_at")) or found}
        for f in cor.get("confirms") or []:
            if f.get("fact") or f.get("quote"):
                rows.append(dict(src, fact=str(f.get("fact") or ""), quote=str(f.get("quote") or "")))
    for kind, s in dev_sources(c):
        src = {"url": s["url"], "source_type": kind, "fetched_at": day(s.get("fetched_at")) or found}
        for f in s.get("facts") or []:
            if f.get("fact"):
                rows.append(dict(src, fact=str(f["fact"]), quote=str(f.get("quote") or "")))
    return [r for r in rows if r["url"]]
