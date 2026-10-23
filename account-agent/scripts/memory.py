"""SQLite memory for the account agent (memory.db). The agent uses these subcommands; never the sqlite3 CLI.

  uv run python scripts/memory.py init
  uv run python scripts/memory.py stats
  uv run python scripts/memory.py show <domain>        # last row's facts, people and checks
  uv run python scripts/memory.py recent [--days 14]   # companies checked in the last N days
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    COLUMNS, DB_PATH, DEFAULT_LIST, F_COMPANY, F_CONF, F_CONTACT, F_DOMAIN, F_SOURCES, ROOT, citations,
    parse_sources, today, urls_in,
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    return con


def last_checked(con: sqlite3.Connection, domain: str) -> str | None:
    r = con.execute("SELECT last_checked FROM companies WHERE domain=?", (domain,)).fetchone()
    return r["last_checked"] if r else None


def record_row(con: sqlite3.Connection, row: dict, sheet: str) -> None:
    """Store a written row: company, every cited field, and the leaders' names and titles."""
    d, t = row[F_DOMAIN].lower(), row.get("Last Checked") or today()
    con.execute(
        "INSERT INTO companies(domain,name,list,first_checked,last_checked,confidence) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(domain) DO UPDATE SET name=excluded.name, list=excluded.list, last_checked=excluded.last_checked, "
        "confidence=excluded.confidence",
        (d, row[F_COMPANY], sheet or DEFAULT_LIST, t, t, row.get(F_CONF)))
    sources, _ = parse_sources(row.get(F_SOURCES, ""))
    for c in COLUMNS:
        v = str(row.get(c) or "").strip()
        if c in (F_COMPANY, F_DOMAIN, F_SOURCES) or not v:
            continue
        cited = [sources[n] for n in citations(v) if n in sources] + urls_in(v)
        con.execute("INSERT OR REPLACE INTO facts(domain,field,value,sources,checked) VALUES(?,?,?,?,?)",
                    (d, c, v, json.dumps(cited), t))
    for ld in row.get("_leaders") or []:
        if ld.get("name"):
            src = ""
            n = citations(ld.get("cite", ""))
            if n and n[0] in sources:
                src = sources[n[0]]
            con.execute("INSERT OR REPLACE INTO people(domain,name,title,source,checked) VALUES(?,?,?,?,?)",
                        (d, ld["name"], ld.get("title", ""), src, t))
    filled = sum(1 for c in COLUMNS if str(row.get(c) or "").strip())
    con.execute("INSERT INTO runs(domain,started,filled,blank,note) VALUES(?,?,?,?,?)",
                (d, t, filled, len(COLUMNS) - filled, ""))
    con.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    sp.add_parser("stats")
    p = sp.add_parser("show"); p.add_argument("domain")
    p = sp.add_parser("recent"); p.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    con = connect()
    if a.cmd == "init":
        try:   # the Excel file exists before the first search; an existing file is never touched
            import init_workbook
            init_workbook.ensure()
        except Exception as e:  # a missing file is made again by the upsert, so this must never stop a run
            print(f"note: could not prepare the Excel file yet ({e})", file=sys.stderr)
        print(f"memory ready: {DB_PATH.name}")
    elif a.cmd == "stats":
        n = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        f = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        print(f"{n} companies, {f} facts")
    elif a.cmd == "show":
        c = con.execute("SELECT * FROM companies WHERE domain=?", (a.domain.lower(),)).fetchone()
        if not c:
            print(f"{a.domain}: not in memory")
            return
        print(f"{c['name']} · {c['domain']} · list {c['list']} · last checked {c['last_checked']} · confidence {c['confidence']}")
        for r in con.execute("SELECT field, value FROM facts WHERE domain=? ORDER BY field", (c["domain"],)):
            if r["field"] != F_CONTACT:
                print(f"  {r['field']}: {r['value'][:160]}")
        for r in con.execute("SELECT name, title FROM people WHERE domain=?", (c["domain"],)):
            print(f"  leader: {r['name']} · {r['title']}")
    else:
        cutoff = (date.today() - timedelta(days=a.days)).isoformat()
        for r in con.execute("SELECT name, domain, last_checked FROM companies WHERE last_checked>=? ORDER BY last_checked DESC", (cutoff,)):
            print(f"{r['last_checked']}  {r['name']} ({r['domain']})")


if __name__ == "__main__":
    main()
