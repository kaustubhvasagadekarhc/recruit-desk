"""Validate job rows before they reach the workbook. Standard library only (used by the hook).

A rows file is either a list of row dicts or {"meta": {...}, "rows": [...]}.
Usage: python scripts/validate_rows.py runs/x.rows.json   (exit 1 and print errors if invalid)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobfields import validate_typed  # noqa: E402
from common import APPROX_BASIS, COLUMNS, EXACT_BASIS, SOURCE_TYPES, STATUSES, approx_posted, is_source_type  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I)
REQUIRED = ["Company Name", "Job Title", "Job / Post URL", "Source Type", "Date Discovered", "Status"]
DATE_FIELDS = ["Posted Date", "Date Discovered", "Last Checked"]
URL_FIELDS = ["Official Website", "Career Page", "Job / Post URL", "Application URL"]
# Fields where an email or phone number could only be a contact detail.
CONTACT_FIELDS = ["Posted By", "Company Name", "Job Title", "Location / Work Mode"]


def _is_date(value: str) -> bool:
    try:
        dt.date.fromisoformat(str(value)[:10])
        return len(str(value)) >= 10
    except ValueError:
        return False


def validate_row(row: dict, i: int) -> list[str]:
    errs: list[str] = []
    where = f"row {i} ({str(row.get('Job Title', '?'))[:40]})"
    if not isinstance(row, dict):
        return [f"row {i}: not an object"]
    unknown = [k for k in row if k not in COLUMNS and not k.startswith("_")]
    if unknown:
        errs.append(f"{where}: unknown columns {unknown}")
    for key in REQUIRED:
        if not str(row.get(key) or "").strip():
            errs.append(f"{where}: missing required '{key}'")
    for key in URL_FIELDS:
        v = str(row.get(key) or "").strip()
        if v and not URL.match(v):
            errs.append(f"{where}: '{key}' is not an http(s) URL: {v[:80]}")
    for key in DATE_FIELDS:
        v = str(row.get(key) or "").strip()
        if v and not _is_date(v):
            errs.append(f"{where}: '{key}' must be an ISO date (YYYY-MM-DD), got {v[:30]}")
    # Posted Date Basis: blank, 'exact (from page)', or the approx text common.approx_posted wrote. An
    # approximate date must be exactly what the parser computes from the quoted age and seen date, so
    # an estimate can only come from the script, never be invented.
    basis = str(row.get("Posted Date Basis") or "").strip()
    posted = str(row.get("Posted Date") or "").strip()
    if basis:
        m = APPROX_BASIS.match(basis)
        if basis != EXACT_BASIS and not m:
            errs.append(f"{where}: 'Posted Date Basis' must be blank, '{EXACT_BASIS}' or 'approx: \"<age>\" seen <date>', got {basis[:60]}")
        elif not posted:
            errs.append(f"{where}: 'Posted Date Basis' is set but 'Posted Date' is blank")
        elif m:
            hit = approx_posted(m["phrase"], m["seen"])
            if not hit or hit["basis"] != basis or hit["date"] != posted[:10]:
                errs.append(f"{where}: approximate 'Posted Date' {posted[:10]} does not match its basis (expected "
                            f"{hit['date'] if hit else 'an age the parser can read'}); leave both blank and put the age text in '_posted_age'")
    errs.extend(f"{where}: {e}" for e in validate_typed(row))
    if row.get("_posted_age") is not None and not isinstance(row.get("_posted_age"), str):
        errs.append(f"{where}: '_posted_age' must be the relative-age text from the snippet")
    if row.get("Date Discovered") and str(row["Date Discovered"])[:10] > dt.date.today().isoformat():
        errs.append(f"{where}: 'Date Discovered' is in the future")
    if row.get("Status") and row["Status"] not in STATUSES:
        errs.append(f"{where}: Status must be one of {sorted(STATUSES)}")
    if row.get("Source Type") and not is_source_type(row["Source Type"]):
        errs.append(f"{where}: Source Type must be one of {sorted(SOURCE_TYPES)} or 'ATS: <label>' of a reader spec")
    # No fabricated contact data: the company sheet has no contact columns, so an email or
    # phone number in a short field is a contact detail that does not belong there.
    for key in CONTACT_FIELDS:
        v = str(row.get(key) or "")
        if EMAIL.search(v) or PHONE.search(v):
            errs.append(f"{where}: '{key}' contains an email/phone; contact details are not stored")
    # Posted By only makes sense for a public post or listing that names its poster.
    if str(row.get("Posted By") or "").strip() and str(row.get("Source Type", "")).startswith(("ATS:", "Careers Page")):
        errs.append(f"{where}: 'Posted By' must stay blank for {row.get('Source Type')} rows (the source names no poster)")
    # Off-site sources cannot be marked Open until confirmed on an official source.
    if row.get("Status") == "Open" and str(row.get("Source Type", "")).endswith("(public index)"):
        if not row.get("_confirmed_url"):
            errs.append(f"{where}: public-index rows are 'Unverified' unless '_confirmed_url' points to an official/ATS page")
    for key, v in row.items():
        if "score" in key.lower() and v not in (None, ""):
            try:
                n = float(v)
            except (TypeError, ValueError):
                errs.append(f"{where}: '{key}' must be a number 0-100")
                continue
            if not 0 <= n <= 100:
                errs.append(f"{where}: '{key}' must be within 0-100")
    return errs


def load_rows(payload) -> list[dict]:
    if isinstance(payload, dict):
        return payload.get("rows", [])
    if isinstance(payload, list):
        return payload
    raise ValueError("rows file must be a list or an object with 'rows'")


def validate_payload(payload) -> list[str]:
    try:
        rows = load_rows(payload)
    except ValueError as e:
        return [str(e)]
    errs: list[str] = []
    for i, row in enumerate(rows, 1):
        errs.extend(validate_row(row, i))
    return errs


def validate_file(path: str | Path) -> list[str]:
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        return [f"{path}: cannot read rows JSON ({e})"]
    return validate_payload(payload)


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        errs = validate_file(p)
        for e in errs:
            print(f"{p}: {e}")
        bad += len(errs)
        if not errs:
            print(f"{p}: OK")
    sys.exit(1 if bad else 0)
