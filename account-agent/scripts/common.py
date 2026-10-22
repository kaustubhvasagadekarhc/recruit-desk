"""Shared constants and helpers for the account agent (standard library only).

The column list and the closed value lists live in schema/fields.json, so the hook, the scripts
and the tests read one definition.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
DB_PATH = ROOT / "memory.db"
# Recruit Desk keeps the Excel files in its data folder (Documents/RecruitDesk-Data/<assistant>) and says where in this variable; run by hand
# without it, the file stays in this folder as before.
WORKBOOK_PATH = (Path(os.environ["RECRUIT_DESK_WORKBOOK_DIR"]) if os.environ.get("RECRUIT_DESK_WORKBOOK_DIR") else ROOT) / "Account_Research.xlsx"
TEAM_CSV = ROOT / "team.csv"
COMPANY_AGENT = ROOT.parent / "company-agent"
DEFAULT_LIST = "Accounts"

_FIELDS = json.loads((ROOT / "schema" / "fields.json").read_text(encoding="utf-8"))
COLUMNS: list[str] = _FIELDS["columns"]
PRODUCT_TYPES: list[str] = _FIELDS["product_types"]
PRODUCT_VALUES: list[str] = _FIELDS["product_values"]
CONFIDENCE_VALUES: list[str] = _FIELDS["confidence_values"]
CITED_FIELDS: list[str] = _FIELDS["cited_fields"]
INDIA_NONE: str = _FIELDS["india_connection_none"]
TEAM_NOTE: str = _FIELDS["team_note"]
REASON_FIELDS: list[str] = _FIELDS.get("reason_fields", [])
# A cell the agent could not fill after trying the free sources says which ones it tried.
NOT_FOUND_RE = re.compile(r"^(?:Not found|No evidence found) \(checked: [^)]{6,}\)\.?$")


def is_not_found(text: str) -> bool:
    return bool(NOT_FOUND_RE.match((text or "").strip()))

F_COMPANY, F_DOMAIN, F_PTYPE, F_BUILD, F_BRIEF, F_HQ, F_INDIA_OFFICE, F_PRODUCT, F_FUNDING, \
    F_INDIA, F_DEV, F_CONTACT, F_EDU, F_WARM, F_STARTER, F_BEST, F_SOURCES, F_CONF, F_CHECKED = COLUMNS

URL_RE = re.compile(r"https?://[^\s\]\[<>\"')]+")
CITE_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
SOURCE_LINE = re.compile(r"^\s*\[(\d+)\]\s+(https?://\S+)\s*$")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def today() -> str:
    return date.today().isoformat()


def bare_domain(value: str) -> str:
    """'https://www.Razorpay.com/careers' -> 'razorpay.com'."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = (urlsplit(value).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def valid_domain(d: str) -> bool:
    return bool(re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", d or ""))


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "company"


def safe_sheet_name(name: str, taken: set[str] | None = None) -> str:
    """Excel sheet names: 1-31 chars, none of []:*?/\\, unique case-insensitively among `taken`."""
    base = _BAD_SHEET_CHARS.sub(" ", name or DEFAULT_LIST)
    base = re.sub(r"\s+", " ", base).strip().strip("'").strip() or DEFAULT_LIST
    if base.lower() in ("history", "contacts", "company channels", "evidence", "companies"):
        base = base + " (list)"
    return base[:31].rstrip()


def parse_sources(cell: str) -> tuple[dict[int, str], list[str]]:
    """Sources cell -> ({n: url}, errors). One source per line: `[n] https://...`."""
    out: dict[int, str] = {}
    errs: list[str] = []
    for line in (cell or "").splitlines():
        if not line.strip():
            continue
        m = SOURCE_LINE.match(line)
        if not m:
            errs.append(f"Sources line is not `[n] https://url`: {line.strip()[:80]!r}")
            continue
        n = int(m.group(1))
        if n in out:
            errs.append(f"Sources number [{n}] is used twice")
        out[n] = m.group(2)
    return out, errs


def format_sources(sources: dict[int, str]) -> str:
    return "\n".join(f"[{n}] {u}" for n, u in sorted(sources.items()))


def add_source(sources: dict[int, str], url: str) -> int:
    """Number of url in sources, adding it with the next number when new."""
    for n, u in sources.items():
        if u == url:
            return n
    n = max(sources, default=0) + 1
    sources[n] = url
    return n


def citations(text: str) -> list[int]:
    """Every [n] / [n, m] marker in a cell."""
    nums: list[int] = []
    for m in CITE_RE.finditer(text or ""):
        nums += [int(x) for x in re.split(r"\s*[,;]\s*", m.group(1))]
    return nums


def urls_in(text: str) -> list[str]:
    return [u.rstrip(".,;:") for u in URL_RE.findall(text or "")]


def norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/").lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u
