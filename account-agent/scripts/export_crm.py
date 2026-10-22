"""CRM export: companies and contacts from Account_Research.xlsx as CSV files in HubSpot or Salesforce import shape.

  uv run python scripts/export_crm.py --format hubspot|salesforce [--list "Accounts"] [--out exports]

Writes <out>/<format>-companies.csv (one row per company, keyed by domain) and <out>/<format>-contacts.csv (one row
per person, keyed by email). Reads the workbook only; never writes it.

Rules:
  - A contact is exported only with an email that was self-published (its source is a URL with a quote) or that
    Apollo marked verified ("Source: Apollo (verified)"). Everything else (Not published, Not stored, guessed) is
    left out. A phone goes with it only when it is the person's own, sourced the same way (never a company line).
  - Blanks stay blank: "Not found (checked: ...)" and "Not published (...)" are never written into a CRM field, and
    the inline [n] markers are stripped from prose.
  - Each row carries the source of its email (a URL or "Apollo (verified)") so the Art. 14 notice can name it.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CITE_RE, COLUMNS, F_BUILD, F_COMPANY, F_DOMAIN, ROOT, WORKBOOK_PATH, bare_domain, is_not_found, valid_domain,
)
from contacts import APOLLO_LABEL, CONTACT_COLUMNS, CONTACTS_SHEET, is_not_published  # noqa: E402

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}$")
HONORIFIC = re.compile(r"(?i)^(dr|mr|mrs|ms|prof)\.?$")
FORMATS = {
    "hubspot": {
        "companies": ["Company name", "Company Domain Name", "Website URL", "Description"],
        "contacts": ["First Name", "Last Name", "Email", "Job Title", "Phone Number", "Company name",
                     "Company Domain Name", "Email Source"],
    },
    "salesforce": {
        "companies": ["Account Name", "Website", "Description"],
        "contacts": ["First Name", "Last Name", "Email", "Title", "Phone", "Account Name", "Account Website",
                     "Email Source"],
    },
}


def clean(text) -> str:
    """Prose for a CRM field: no [n] markers, no 'Not found' / 'Not published' placeholders."""
    t = str(text or "").strip()
    if not t or is_not_found(t) or is_not_published(t) or t.startswith("Not stored"):
        return ""
    t = re.sub(r"\s+", " ", CITE_RE.sub("", t))
    return re.sub(r"\s+([.,;:])", r"\1", t).strip(" ;,")


def split_name(full: str) -> tuple[str, str]:
    parts = [p for p in re.sub(r"\([^)]*\)", " ", full or "").split() if not HONORIFIC.match(p)]
    if not parts:
        return "", ""
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (parts[0], "")


def email_source(value, source) -> str:
    """'' unless the email is self-published (URL source) or Apollo verified; else the source to cite."""
    v, s = str(value or "").strip(), str(source or "").strip()
    if not EMAIL.match(v):
        return ""
    if s.startswith(APOLLO_LABEL):
        return "Apollo (verified)"
    m = re.match(r"^(https?://\S+)\s+—\s+\".+\"", s)
    return m.group(1) if m else ""


def phone_ok(value, source) -> str:
    v, s = str(value or "").strip(), str(source or "").strip()
    if not v or "(company line)" in v or is_not_published(v) or v.startswith("Not stored"):
        return ""
    if s.startswith(APOLLO_LABEL) or s.startswith("Source: Apollo") or re.match(r"^https?://\S+\s+—\s+\".+\"", s):
        return v
    return ""


def read_workbook(path: Path, list_name: str | None = None) -> tuple[list[dict], list[dict]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    companies: dict[str, dict] = {}
    people: list[dict] = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = [str(h or "") for h in next(rows, [])]
        if header[:len(COLUMNS)] == COLUMNS:
            if list_name and ws.title.lower() != list_name.lower():
                continue
            for r in rows:
                rec = dict(zip(header, r))
                d = bare_domain(str(rec.get(F_DOMAIN) or ""))
                if d and valid_domain(d) and d not in companies:
                    companies[d] = rec
        elif ws.title == CONTACTS_SHEET and header[:len(CONTACT_COLUMNS)] == CONTACT_COLUMNS:
            people += [dict(zip(header, r)) for r in rows]
    wb.close()
    return list(companies.values()), people


def build(fmt: str, companies: list[dict], people: list[dict]) -> tuple[list[list[str]], list[list[str]], int]:
    """(company rows, contact rows, contacts skipped)."""
    names = {bare_domain(str(c.get(F_DOMAIN))): clean(c.get(F_COMPANY)) for c in companies}
    crow = []
    for c in companies:
        d = bare_domain(str(c.get(F_DOMAIN)))
        name, desc = clean(c.get(F_COMPANY)), clean(c.get(F_BUILD))
        crow.append([name, d, f"https://{d}", desc] if fmt == "hubspot" else [name, f"https://{d}", desc])
    seen: set[str] = set()
    prow, skipped = [], 0
    for p in people:
        d = bare_domain(str(p.get("Domain") or ""))
        src = email_source(p.get("Email"), p.get("Email Source"))
        email = str(p.get("Email") or "").strip().lower()
        if not src or not d or email in seen:
            skipped += 1
            continue
        seen.add(email)
        first, last = split_name(str(p.get("Name") or ""))
        title = clean(p.get("Title"))
        phone = phone_ok(p.get("Phone"), p.get("Phone Source"))
        company = names.get(d) or clean(p.get("Company"))
        if fmt == "hubspot":
            prow.append([first, last, email, title, phone, company, d, src])
        else:
            prow.append([first, last, email, title, phone, company, f"https://{d}", src])
    return crow, prow, skipped


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", required=True, choices=sorted(FORMATS))
    ap.add_argument("--list", help="only this Accounts sheet (default: every list)")
    ap.add_argument("--workbook", default=str(WORKBOOK_PATH))
    ap.add_argument("--out", default=str(ROOT / "exports"))
    a = ap.parse_args(argv)
    wbp = Path(a.workbook)
    if not wbp.exists():
        print(f"{wbp.name} does not exist; run a scan first")
        return 1
    companies, people = read_workbook(wbp, a.list)
    crow, prow, skipped = build(a.format, companies, people)
    out = Path(a.out)
    write_csv(out / f"{a.format}-companies.csv", FORMATS[a.format]["companies"], crow)
    write_csv(out / f"{a.format}-contacts.csv", FORMATS[a.format]["contacts"], prow)
    print(f"{a.format}: {len(crow)} companies -> {out / f'{a.format}-companies.csv'}; {len(prow)} contacts -> "
          f"{out / f'{a.format}-contacts.csv'} ({skipped} people left out: no self-published or Apollo-verified email)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
