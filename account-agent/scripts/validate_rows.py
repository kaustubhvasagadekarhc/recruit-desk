"""Row rules for Account_Research.xlsx, shared by the PreToolUse hook and the scripts (stdlib only).

A rows file is {"meta": {...}, "rows": [ {column: text, ..., "_leaders": [...]} ]} or a bare list.
Keys starting with "_" are working data for the scripts and are never written to the workbook.

Rules (each one a reason a row is refused):
  - Company, Domain (a bare host), Sources, Confidence and Last Checked are required (a draft may
    leave Last Checked to assemble.py).
  - Sources: one `[n] https://url` per line. Every [n] marker in any cell must be listed, and every
    URL written in any cell must also be in Sources.
  - The cited fields (What They Build, Company Brief, HQ, India Office, Funding, Who to Contact,
    Leadership, Conversation Starter) carry at least one [n] marker or URL when filled.
  - Product Type: items from schema/fields.json, separated by "; ". Product?: Yes / No / Both.
  - India Connection: blank, "No evidence found", or "Yes: <fact> [n]" with at least one cited URL.
    Words that infer origin from a name, photo or identity ("surname", "name suggests", "Indian origin",
    "ethnic", ...) are refused.
  - Conversation Starter: cites at least one source; every cited URL is in Sources; every number
    in it also appears in another cell of the same row; no ethnicity, religion, family or health
    and no mention of the India Connection field.
  - Leaders and contacts ("_contacts", "_channels"): contacts.validate_contacts. An email or phone needs its
    source URL and a quote from that page containing it; guessed or constructed values are refused.
  - Emails and phone numbers only in Who to Contact, and only marked "self-published" with a citation.
  - Warm Connection and Best Person: with no consented team.csv rows they are blank or "add team.csv";
    with team rows, Best Person names someone in team.csv. Warm Connection never claims the people know
    each other.
  - Search depth: the reason fields (schema/fields.json) are never empty. A field the free sources did
    not answer says what was tried: "Not found (checked: site, Wikidata, EDGAR, 4 Exa searches)"
    (India Connection: "No evidence found (checked: ...)"). A bare "No evidence found" is refused.
  - Company Brief states the employee size, or "size not found (checked: ...)".
  - Typed fields (schema/fields.json typed_columns; scripts/typed.py): usually left out, since excel_upsert.py sets
    them from the cited prose. A row that sets one gives a number as a number, a date as ISO (YYYY, YYYY-MM or
    YYYY-MM-DD), an allowed enum, and a value that a cited clause of the same row states. The H-1B columns are never
    set in a rows file; excel_upsert.py fills them from the DOL LCA index.
  - Freshness (stale_flags): a Funding cell whose newest year is 2 or more years old needs either a newer
    round or "No newer round found (checked: ...)"; a CTO named twice, or wording such as "confirm the
    current", needs a "(conflict: ...)" note naming both sources and which one is newer.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CITED_FIELDS, COLUMNS, CONFIDENCE_VALUES, F_BEST, F_CHECKED, F_COMPANY, F_CONF, F_CONTACT, F_DOMAIN,
    F_INDIA, F_PRODUCT, F_PTYPE, F_SOURCES, F_STARTER, F_WARM, INDIA_NONE, PRODUCT_TYPES, PRODUCT_VALUES,
    TEAM_NOTE, citations, norm_url, parse_sources, urls_in, valid_domain,
    F_BRIEF, F_FUNDING, F_EDU, REASON_FIELDS, is_not_found,
)
from contacts import validate_contacts  # noqa: E402
from typed import TYPED_COLUMNS, check_explicit  # noqa: E402
from common import _FIELDS  # noqa: E402

H1B_COLUMNS: list[str] = _FIELDS["h1b_columns"]
from datetime import date

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{8,}\d(?![\w/])")
INFER_WORDS = re.compile(
    r"(?i)\b(surname|last name|first name|family name|name suggests|name sounds|by (his|her|their) name|"
    r"photo|appears to be|looks|likely indian|probably indian|presumably|ethnic\w*|race|caste|religio\w*|"
    r"indian[- ]origin|of indian (origin|descent|heritage)|indian[- ]american|desi|nri|person of indian)\b"
)
STARTER_BANNED = re.compile(
    r"(?i)\b(india connection|ethnic\w*|race|caste|religio\w*|hindu|muslim|christian|sikh|jain|family|wife|"
    r"husband|children|kids|health|illness|pregnan\w*|indian[- ]origin|fellow indian|desi|heritage)\b"
)
ACQUAINT = re.compile(r"(?i)\b(knows|friends?|friendship|connected with|close to|met each other|personally)\b")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
SIZE_WORDS = re.compile(r"(?i)\b(employees|staff|headcount|workforce|team of|people|engineers|size not found \(checked: [^)]{6,}\))")
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
NEWER_CHECKED = re.compile(r"(?i)no newer (round|funding)[^()]*\(checked: [^)]{6,}\)")
CONFLICT_HINT = re.compile(r"(?i)\b(confirm the current|conflicting|unclear who|calls \w+(?: \w+)? (?:the )?CTO|disputed)\b")
CONFLICT_NOTE = re.compile(r"(?i)\(conflict: [^)]{10,}\)")
CTO_ENTRY = re.compile(r"([A-Z][\w.'-]+(?: [A-Z][\w.'-]+){1,3}),\s*(?:[^;\n\[]*?\b)?(?:CTO|Chief Technology Officer)\b")


def stale_flags(row: dict, today: date | None = None) -> list[str]:
    """Facts that may be out of date and were not re-checked. Empty when the row is fresh or annotated."""
    today = today or date.today()
    flags: list[str] = []
    fund = str(row.get(F_FUNDING) or "").strip()
    if fund and not is_not_found(fund) and not fund.lower().startswith("public"):
        years = [int(y) for y in YEAR_RE.findall(re.sub(r"https?://\S+|\[[\d,;\s]+\]", " ", fund))]
        if years and max(years) <= today.year - 2 and not NEWER_CHECKED.search(fund):
            flags.append(f"Funding: the newest round found is from {max(years)}, 2 or more years old; search for a newer "
                         f"round (Exa '<Company> raises', lookup.py news, lookup.py edgar Form D) and add it, or write "
                         f"'No newer round found (checked: ...)'")
    people = str(row.get(F_CONTACT) or "") + "\n" + str(row.get(F_EDU) or "")
    ctos = {m.group(1) for m in CTO_ENTRY.finditer(str(row.get(F_CONTACT) or ""))}
    if (len(ctos) > 1 or CONFLICT_HINT.search(people)) and not CONFLICT_NOTE.search(people):
        who = f" ({', '.join(sorted(ctos))})" if len(ctos) > 1 else ""
        flags.append(f"Who to Contact: conflicting CTO evidence{who}; check the newest dated source and keep that holder, "
                     f"or note '(conflict: <A> per [n] <date> vs <B> per [m] <date>)'")
    return flags


def load_rows(payload) -> list[dict]:
    if isinstance(payload, dict):
        return list(payload.get("rows") or [])
    return list(payload or [])


def _team_names() -> list[str] | None:
    """Names of consented team.csv rows, or None when there are none."""
    try:
        from match_team import load_team
    except ImportError:  # pragma: no cover
        return None
    team, _ = load_team()
    return [m["name"] for m in team] or None


def has_phone(text: str) -> bool:
    """A run of digits and separators holding at least 10 digits (dates like 2026-10-15 have 8)."""
    return any(sum(ch.isdigit() for ch in m.group(0)) >= 10 for m in PHONE_RE.finditer(text or ""))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUM_RE.findall(text or "")}


def validate_row(row: dict, i: int = 0, draft: bool = False, team: list[str] | None = None,
                 today: date | None = None) -> list[str]:
    errs: list[str] = []
    tag = f"row {i + 1} ({row.get(F_COMPANY) or '?'})"

    def e(msg: str) -> None:
        errs.append(f"{tag}: {msg}")

    if not isinstance(row, dict):
        return [f"row {i + 1}: not an object"]
    for k, v in row.items():
        if k.startswith("_"):
            continue
        if k not in COLUMNS and k not in TYPED_COLUMNS and k not in H1B_COLUMNS:
            e(f"unknown column {k!r}")
        elif v is not None and not isinstance(v, (str, int, float)):
            e(f"{k} must be text")
    cell = {c: str(row.get(c) or "").strip() for c in COLUMNS}

    for req in (F_COMPANY, F_DOMAIN, F_SOURCES, F_CONF) + (() if draft else (F_CHECKED,)):
        if not cell[req]:
            e(f"{req} is required")
    if cell[F_DOMAIN] and not valid_domain(cell[F_DOMAIN]):
        e(f"Domain must be a bare host like example.com (got {cell[F_DOMAIN]!r})")
    if cell[F_CHECKED] and not DATE_RE.match(cell[F_CHECKED]):
        e("Last Checked must be YYYY-MM-DD")
    if cell[F_CONF] and cell[F_CONF] not in CONFIDENCE_VALUES:
        e(f"Confidence must be one of {CONFIDENCE_VALUES}")
    if cell[F_PRODUCT] and cell[F_PRODUCT] not in PRODUCT_VALUES and not is_not_found(cell[F_PRODUCT]):
        e(f"Product? must be one of {PRODUCT_VALUES}")
    if cell[F_PTYPE] and not is_not_found(cell[F_PTYPE]):
        bad = [p for p in (x.strip() for x in cell[F_PTYPE].split(";")) if p not in PRODUCT_TYPES]
        if bad:
            e(f"Product Type {bad} not in the list {PRODUCT_TYPES}")

    sources, serrs = parse_sources(cell[F_SOURCES])
    errs += [f"{tag}: {x}" for x in serrs]
    if cell[F_SOURCES] and not sources:
        e("Sources has no `[n] https://url` line")
    known = {norm_url(u) for u in sources.values()}

    for c in COLUMNS:
        if c in (F_SOURCES, F_DOMAIN, F_CHECKED) or not cell[c]:
            continue
        for n in citations(cell[c]):
            if n not in sources:
                e(f"{c} cites [{n}], which is not in Sources")
        for u in urls_in(cell[c]):
            if norm_url(u) not in known:
                e(f"{c} names {u} but that URL is not in Sources")
        if c in CITED_FIELDS and not citations(cell[c]) and not urls_in(cell[c]) and not is_not_found(cell[c]):
            e(f"{c} has no citation; add [n] pointing at Sources, or leave it blank")
        if c != F_CONTACT and (EMAIL_RE.search(cell[c]) or has_phone(cell[c])):
            e(f"{c} contains an email or phone number; contact details go only in Who to Contact, self-published")

    for seg in re.split(r"[\n;]", cell[F_CONTACT]):
        if (EMAIL_RE.search(seg) or has_phone(seg)) and not ("self-published" in seg.lower() and citations(seg)):
            e("Who to Contact: an email or phone is allowed only when the person published it, marked "
              "'self-published [n]' in the same entry")

    for c in REASON_FIELDS:
        if not cell[c]:
            e(f"{c} is empty; fill it with a cited fact or, after trying the sources, 'Not found (checked: <sources tried>)'")
    b = cell[F_BRIEF]
    if b and not is_not_found(b) and not SIZE_WORDS.search(b):
        e("Company Brief must state the employee size ('approx. N employees (<basis>) [n]') or "
          "'size not found (checked: <sources tried>)'")
    for f in stale_flags(row, today):
        e(f)

    ic = cell[F_INDIA]
    if ic == INDIA_NONE:
        e(f"India Connection: say what was searched, e.g. '{INDIA_NONE} (checked: team page, 3 leader bios, 4 Exa searches)'")
    elif ic and not is_not_found(ic):
        if not ic.startswith("Yes"):
            e(f"India Connection must be blank, {INDIA_NONE!r}, or 'Yes: <evidence> [n]'")
        cited = [sources[n] for n in citations(ic) if n in sources] + urls_in(ic)
        if not cited:
            e("India Connection 'Yes' needs evidence: at least one cited source URL ([n] in Sources)")
        if INFER_WORDS.search(ic):
            e(f"India Connection must state self-published facts only; refused wording "
              f"{INFER_WORDS.search(ic).group(0)!r} (never infer from a name, photo or identity)")

    st = cell[F_STARTER]
    if st and not is_not_found(st):
        if not citations(st) and not urls_in(st):
            e("Conversation Starter must cite the facts it uses ([n] in Sources)")
        if STARTER_BANNED.search(st):
            e(f"Conversation Starter mentions {STARTER_BANNED.search(st).group(0)!r}; use business facts only")
        others = " ".join(cell[c] for c in COLUMNS if c not in (F_STARTER, F_SOURCES))
        stripped = re.sub(r"\[[\d,;\s]+\]", " ", URL_RE_SUB(st))
        missing = sorted(_numbers(stripped) - _numbers(URL_RE_SUB(others)))
        if missing:
            e(f"Conversation Starter uses {missing}, which no other cell of this row states; use only facts from the row")

    names = team if team is not None else _team_names()
    for c in (F_WARM, F_BEST):
        v = cell[c]
        if not names:
            if v and v != TEAM_NOTE:
                e(f"{c}: team.csv has no consented rows, so this stays blank with the note {TEAM_NOTE!r}")
        elif c == F_BEST and v and v != TEAM_NOTE and not any(v.startswith(n) for n in names):
            e(f"{c} must name a person from team.csv")
    errs += validate_contacts(row, today)
    for msg in check_explicit(row):
        e(msg)
    for c in H1B_COLUMNS:
        if row.get(c) not in (None, ""):
            e(f"{c} is written by excel_upsert.py from the DOL LCA index (scripts/h1b.py); leave it out of the rows file")
    if cell[F_WARM] and ACQUAINT.search(cell[F_WARM]):
        e("Warm Connection states common ground only; never claim the people know each other")
    return errs


def URL_RE_SUB(text: str) -> str:
    return re.sub(r"https?://\S+", " ", text or "")


def validate_payload(payload, draft: bool = False) -> list[str]:
    if not isinstance(payload, (dict, list)):
        return ["rows file must be an object with 'rows' or a list of rows"]
    rows = load_rows(payload)
    if not rows:
        return ["no rows"]
    team = _team_names()
    errs: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        errs += validate_row(r, i, draft=draft, team=team)
        d = str((r or {}).get(F_DOMAIN) or "").lower() if isinstance(r, dict) else ""
        if d and d in seen:
            errs.append(f"row {i + 1}: domain {d} appears twice in this file")
        seen.add(d)
    return errs


def validate_file(path: Path, draft: bool | None = None) -> list[str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"{path} does not exist"]
    except (OSError, ValueError) as e:
        return [f"{path} is not valid JSON: {e}"]
    return validate_payload(payload, draft=str(path).endswith(".draft.json") if draft is None else draft)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/validate_rows.py <file.rows.json | file.draft.json>")
        sys.exit(2)
    errs = validate_file(Path(sys.argv[1]))
    if errs:
        print("INVALID:\n" + "\n".join(errs[:40]))
        sys.exit(1)
    print("valid")


if __name__ == "__main__":
    main()
