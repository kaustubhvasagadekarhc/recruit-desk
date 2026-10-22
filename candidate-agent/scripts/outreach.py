"""Outreach drafts for a JD's shortlist: facts in, checked drafts out. Nothing is ever sent (plan item 9, 2026-10-18).

uv run scripts/outreach.py plan  --jd <jd_id> --company "<hiring company>" [--statuses Shortlisted]
uv run scripts/outreach.py write --jd <jd_id> --company "<hiring company>" --input runs/outreach-<jd_id>.json

`plan` prints, per person with a matching Recruiter Status, the public facts the message may lean on (rows of the Evidence
sheet, each with an id, the fact, the exact quote and its source; "open to work" is left out, it is not a conversation
opener) and the sentence that must close the message. `write` takes drafts `{"drafts": [{"key", "fact_id", "message"}]}`,
checks them, and writes exports/<jd_id>-outreach-<date>.md. A draft is refused when:
- its key is not on the plan, or its fact_id is not one of that person's facts;
- the message does not repeat the cited fact's quote (or, for a fact with no quote, its text), or that quote is shorter than
  15 characters. This proves the quote is there, not that everything around it is true: read each draft before sending it;
- it holds an email address (also spelled out) or a phone number, repeats a key, or is longer than 900 characters.
The GDPR Art. 14 notice (README) is appended by this script to every draft, so it is never forgotten or reworded. The
script looks nobody up, finds no email address and contacts no one: a recruiter reads the file and sends it by hand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).parent))
import memory  # noqa: E402
import progress  # noqa: E402
from common import EVIDENCE_SHEET, HEADERS, ROOT, STATUSES, WORKBOOK_PATH, art14_notice, header_len, row_key  # noqa: E402

EXPORT_DIR = ROOT / "exports"
MAX_LEN = 900


def shown(path: Path) -> str:
    """The path as the user should see it: relative to this folder when it is inside it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_SPELLED = re.compile(r"\b[\w.-]+\s*[\[(]?\s*at\s*[\])]?\s*[\w-]+\s*[\[(]?\s*dot\s*[\])]?\s*\w{2,}\b", re.I)
_PHONE = re.compile(r"\+?\d[\d .()-]{7,}\d")
MIN_QUOTE = 15   # a shorter quote ("Python") is in almost any message, so it proves nothing


def has_contact(text: str) -> bool:
    """An email address (also spelled 'jane at example dot com') or a phone number (9 or more digits, so a span of years is not one)."""
    if _EMAIL.search(text) or _EMAIL_SPELLED.search(text):
        return True
    return any(len(re.sub(r"\D", "", m.group(0))) >= 9 for m in _PHONE.finditer(_EMAIL.sub(" ", text)))


def fact_id(key: str, fact: str, quote: str, url: str) -> str:
    return hashlib.sha1("|".join((key, fact, quote, url)).encode()).hexdigest()[:8]


def plan(wb_path: Path, sheet: str, jd_id: str, statuses: set[str], limit: int = 5) -> list[dict]:
    """People of the JD sheet with a matching status and the facts a message may cite."""
    wb = load_workbook(wb_path, read_only=True)
    if sheet not in wb.sheetnames:
        wb.close()
        return []
    ev: dict[str, list[dict]] = {}
    if EVIDENCE_SHEET in wb.sheetnames:
        for r in wb[EVIDENCE_SHEET].iter_rows(min_row=2, values_only=True):
            if not r or not r[0] or not r[1] or str(r[1]).lower().startswith("open to work"):
                continue
            if r[7] not in (None, jd_id):
                continue
            ev.setdefault(r[0], []).append({"fact": str(r[1]), "quote": str(r[2] or ""), "url": str(r[3] or ""), "source_type": str(r[4] or "")})
    it = wb[sheet].iter_rows(values_only=True)
    hdr = list(next(it, []) or [])
    n = header_len(hdr)
    out = []
    for vals in it if n is not None else []:
        c = {HEADERS[i]: vals[i] for i in range(min(n, len(vals)))}
        if not c.get("Candidate Name") or (c.get("Recruiter Status") or "New") not in statuses:
            continue
        key = row_key(c.get("Profile URL") or "", c["Candidate Name"], c.get("Current Role / Company") or "")
        facts = sorted(ev.get(key, []), key=lambda f: (not f["quote"], f["fact"]))[:limit]
        for f in facts:
            f["id"] = fact_id(key, f["fact"], f["quote"], f["url"])
            f["usable"] = len(f["quote"] or f["fact"]) >= MIN_QUOTE
        out.append({"key": key, "name": c["Candidate Name"], "role_company": c.get("Current Role / Company"), "profile_url": c.get("Profile URL"),
                    "source": c.get("Source") or "a public web page", "date_found": str(c.get("Date Found") or "")[:10] or date.today().isoformat(),
                    "status": c.get("Recruiter Status") or "New", "facts": facts})
    wb.close()
    return out


def check_draft(d: dict, person: dict | None) -> list[str]:
    errs = []
    if not isinstance(d, dict):
        return ["a draft must be an object with key, fact_id and message"]
    msg = str(d.get("message") or "")
    if person is None:
        return [f"key {d.get('key')!r} is not on the plan for this JD"]
    fact = next((f for f in person["facts"] if f["id"] == d.get("fact_id")), None)
    if fact is None:
        errs.append(f"{person['name']}: fact_id {d.get('fact_id')!r} is not one of this person's facts")
    elif not fact["usable"]:
        errs.append(f"{person['name']}: the cited {'quote' if fact['quote'] else 'fact'} is under {MIN_QUOTE} characters and proves nothing; cite another fact")
    else:
        needle = fact["quote"] or fact["fact"]
        if re.sub(r"\s+", " ", needle).strip().lower() not in re.sub(r"\s+", " ", msg).lower():
            errs.append(f"{person['name']}: the message must repeat the cited {'quote' if fact['quote'] else 'fact'} “{needle[:60]}”")
    if not msg.strip():
        errs.append(f"{person['name']}: empty message")
    if len(msg) > MAX_LEN:
        errs.append(f"{person['name']}: {len(msg)} characters, at most {MAX_LEN}")
    if has_contact(msg):
        errs.append(f"{person['name']}: the message holds an email address or phone number; none is looked up or guessed here")
    return errs


def render(jd_title: str, company: str, people: list[dict], drafts: list[dict]) -> str:
    by_key = {p["key"]: p for p in people}
    out = [f"# Outreach drafts: {jd_title} at {company}", "",
           f"{len(drafts)} draft(s), {date.today().isoformat()}. Nothing has been sent. Read each one, then send it yourself; "
           "each ends with the notice every first message to a person found through this agent must carry.", ""]
    for d in drafts:
        p = by_key[d["key"]]
        fact = next(f for f in p["facts"] if f["id"] == d["fact_id"])
        out += [f"## {p['name']}", "", f"{p['role_company'] or ''}".strip(), f"Profile: {p['profile_url']}", f"Cited: {fact['fact']}"
                + (f" (“{fact['quote']}”)" if fact["quote"] else "") + f" · {fact['url']}", "",
                str(d["message"]).strip(), "", "---", art14_notice(p["source"], p["date_found"], jd_title, company), ""]
    return "\n".join(l for l in out if l is not None).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "write"):
        p = sub.add_parser(name)
        p.add_argument("--jd", required=True)
        p.add_argument("--company", required=True, help="the hiring company named in the notice")
        p.add_argument("--statuses", default="Shortlisted")
    sub.choices["write"].add_argument("--input", required=True)
    a = ap.parse_args()
    statuses = {s.strip() for s in a.statuses.split(",") if s.strip()}
    if statuses - set(STATUSES):
        progress.fail_exit(f"unknown status; use {', '.join(STATUSES)}")
    jd = memory.get_jd(memory.connect(), a.jd)
    if not WORKBOOK_PATH.exists():
        progress.fail_exit(f"no workbook at {WORKBOOK_PATH}")
    people = plan(WORKBOOK_PATH, jd["sheet_name"], a.jd, statuses)
    if a.cmd == "plan":
        progress.result(f"{len(people)} people with status {', '.join(sorted(statuses))}; "
                        f"{sum(bool(p['facts']) for p in people)} have a fact to cite")
        print(json.dumps({"jd_id": a.jd, "jd_title": jd["title"], "company": a.company, "people": people}, indent=1, ensure_ascii=False))
        return
    payload = json.loads(Path(a.input).read_text(encoding="utf-8"))
    drafts = payload.get("drafts") or []
    by_key = {p["key"]: p for p in people}
    errs = [e for d in drafts for e in check_draft(d, by_key.get(d.get("key")) if isinstance(d, dict) else None)]
    keys = [d.get("key") for d in drafts if isinstance(d, dict)]
    errs += [f"more than one draft for {k}" for k in sorted({k for k in keys if keys.count(k) > 1})]
    if not drafts:
        errs.append("no drafts in the input file")
    if errs:
        print("DRAFTS REFUSED:\n- " + "\n- ".join(errs), file=sys.stderr)
        progress.fail_exit(f"{len(errs)} problem(s); nothing written", 2)
    EXPORT_DIR.mkdir(exist_ok=True)
    out = EXPORT_DIR / f"{a.jd}-outreach-{date.today().isoformat()}.md"
    n = 2
    while out.exists():   # never overwrite an earlier file, e.g. the same day with another --company
        out = EXPORT_DIR / f"{a.jd}-outreach-{date.today().isoformat()}-{n}.md"
        n += 1
    out.write_text(render(jd["title"], a.company, people, drafts), encoding="utf-8")
    progress.result(f"{len(drafts)} draft(s) → {shown(out)} (not sent)")


if __name__ == "__main__":
    main()
