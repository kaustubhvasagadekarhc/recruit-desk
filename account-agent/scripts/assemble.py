"""Step 5 of /account-scan: turn the agent's draft row into a validated rows file.

  uv run python scripts/assemble.py --draft runs/<slug>.draft.json [--roles runs/<slug>.roles.json]

The draft is the row the agent researched (cited cells, Sources, Confidence, and "_leaders" with each
leader's evidenced employers, colleges, cities, communities, conferences). This script adds what is
deterministic, never what needs judgment:
  - Development Requirement (current) from roles_from_company_agent.py's file, citing its source, plus the company's
    US H-1B LCA signal (filings in the last 4 quarters and top roles, citing the DOL quarter file) when the local
    LCA index (scripts/h1b.py) has any;
  - Warm Connection / Common Ground and Best Person to Reach Out from team.csv (match_team.py), or the
    note "add team.csv" when team.csv has no consented rows;
  - Last Checked (today).
It then validates the whole row (the same rules as the hook) and writes runs/<slug>.rows.json. The
Conversation Starter is added afterwards by starter.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h1b  # noqa: E402
import progress  # noqa: E402
from common import (  # noqa: E402
    COLUMNS, DEFAULT_LIST, F_BEST, F_CHECKED, F_COMPANY, F_DEV, F_DOMAIN, F_PTYPE, F_SOURCES, F_STARTER, F_WARM,
    RUNS_DIR, TEAM_NOTE, add_source, bare_domain, format_sources, is_not_found, parse_sources, slugify, today,
)
from match_team import load_team, warm_and_best  # noqa: E402
from validate_rows import load_rows, validate_payload  # noqa: E402


def brief(row: dict) -> list[str]:
    out = []
    for c in COLUMNS:
        v = str(row.get(c) or "").strip()
        if c == F_SOURCES:
            continue
        out.append(f"  {c}: {(v[:200] + ' …') if len(v) > 200 else v or '(blank)'}")
    return out


def h1b_signal(company: str, sources: dict[int, str]) -> str:
    """'US H-1B signal (...): N filing(s) in the last 4 quarters, top roles ... [n]' or '' (no index, no filings)."""
    got = h1b.signal(company) if company else None
    if not got or not got[0]["H-1B Filings (4Q)"]:
        return ""
    vals, ev = got
    n = add_source(sources, ev[0][2])
    top = f", top roles {vals['H-1B Top Roles']}" if vals["H-1B Top Roles"] else ""
    return (f"US H-1B signal (company-level, DOL LCA disclosure data): {vals['H-1B Filings (4Q)']} filing(s) in the "
            f"last 4 quarters{top} [{n}]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    ap.add_argument("--roles")
    a = ap.parse_args()
    draft_path = Path(a.draft)
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    rows = load_rows(payload)
    if len(rows) != 1:
        progress.fail_exit("a draft holds exactly one company row")
    row = dict(rows[0])
    meta = dict(payload.get("meta") or {}) if isinstance(payload, dict) else {}
    row[F_DOMAIN] = bare_domain(row.get(F_DOMAIN) or meta.get("domain") or "")
    slug = slugify(row.get(F_COMPANY) or meta.get("company") or "")
    sources, errs = parse_sources(row.get(F_SOURCES, ""))
    if errs:
        progress.fail_exit("draft Sources: " + "; ".join(errs[:5]))

    notes = []
    roles_path = Path(a.roles) if a.roles else RUNS_DIR / f"{slug}.roles.json"
    if roles_path.exists():
        roles = json.loads(roles_path.read_text(encoding="utf-8"))
        if roles.get("cell") and roles.get("source"):
            n = add_source(sources, roles["source"])
            row[F_DEV] = f"{roles['cell']} [{n}]"
        else:
            why = roles.get("reason") or "no roles summary"
            row[F_DEV] = f"Not found (checked: company-agent careers and ATS scan; {why})"
            notes.append(f"Development Requirement not found: {why}")
    else:
        row[F_DEV] = "Not found (checked: nothing; step 3, the company-agent roles scan, was not run)"
        notes.append("Development Requirement not found: no roles file (step 3 not run)")
    sig = h1b_signal(str(row.get(F_COMPANY) or meta.get("company") or ""), sources)
    if sig:
        row[F_DEV] = f"{sig}. Open roles: {row[F_DEV]}" if is_not_found(row[F_DEV]) else f"{row[F_DEV]}; {sig}"

    team, problems = load_team()
    if team:
        industries = [p.strip() for p in str(row.get(F_PTYPE) or "").split(";") if p.strip()]
        warm, best = warm_and_best(team, row.get("_leaders") or [], industries)
        row[F_WARM], row[F_BEST] = warm, best
        match_line = f"team.csv: {len(team)} member(s); " + ("common ground found" if warm else "no common ground") + \
            ("; best person: " + best.split(":")[0] if best else "; no default owner set")
    else:
        row[F_WARM] = row[F_BEST] = TEAM_NOTE
        match_line = f"team.csv has no consented rows: Warm Connection and Best Person left blank ({TEAM_NOTE!r})"
    if not row.get(F_STARTER):
        row.pop(F_STARTER, None)
    row[F_SOURCES] = format_sources(sources)
    row[F_CHECKED] = today()
    meta.update({"company": row.get(F_COMPANY), "domain": row[F_DOMAIN], "list": meta.get("list") or DEFAULT_LIST})
    out = {"meta": meta, "rows": [row]}
    errs = validate_payload(out)
    if errs:
        progress.fail_exit("row failed validation:\n" + "\n".join(errs[:20]))
    dest = RUNS_DIR / f"{slug}.rows.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    marks = [c for c in COLUMNS if is_not_found(str(row.get(c) or ""))]
    fresh = [c for c in COLUMNS if re.search(r"(?i)no newer round found|\(conflict:", str(row.get(c) or ""))]
    meta["not_found"] = marks
    meta["freshness_notes"] = fresh
    for line in brief(row):
        print(line)
    if marks:
        progress.sub(f"not found after searching: {', '.join(marks)}")
    if fresh:
        progress.sub(f"freshness notes kept in: {', '.join(fresh)}")
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for n in notes + problems[:5]:
        progress.sub(n)
    print(f"ROWS_FILE={dest.relative_to(dest.parent.parent).as_posix()}")
    progress.result(match_line)


if __name__ == "__main__":
    main()
