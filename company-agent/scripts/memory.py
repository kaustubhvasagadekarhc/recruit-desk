"""SQLite memory (memory.db): companies, routes, jobs, rejected sources, runs.

CLI:
  uv run python scripts/memory.py init
  uv run python scripts/memory.py show <domain>
  uv run python scripts/memory.py stats
  uv run python scripts/memory.py jobs <domain> [--status Open] [--limit 20]   # read-only job list (use instead of sqlite3)
  uv run python scripts/memory.py reject --domain <d> --url <u> --reason "<why>"
  uv run python scripts/memory.py merge <from-domain> <into-domain>   # fold a redirect domain's duplicate company in
  uv run python scripts/memory.py watch add <domain> [--name "Razorpay"] [--keywords "engineer,designer"]
  uv run python scripts/memory.py watch remove <domain>
  uv run python scripts/memory.py watch list
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DB_PATH, SCHEMA_PATH, bare_domain, normalize_url, now_iso  # noqa: E402


# jobs columns behind the typed sheet columns (jobfields.TYPED_COLUMNS, same order).
TYPED_DB_COLUMNS = [("work_mode", "TEXT"), ("city", "TEXT"), ("country", "TEXT"), ("function", "TEXT"),
                    ("seniority", "TEXT"), ("salary_min", "REAL"), ("salary_max", "REAL"), ("currency", "TEXT")]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_PATH.read_text())
    # Columns added after a database was created (CREATE TABLE IF NOT EXISTS does not add them).
    cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}
    if "missing_count" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN missing_count INTEGER NOT NULL DEFAULT 0")
        con.commit()
    if "last_miss_date" not in cols:  # day of the last counted miss: one miss per calendar day
        con.execute("ALTER TABLE jobs ADD COLUMN last_miss_date TEXT")
        con.commit()
    if "posted_date_basis" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN posted_date_basis TEXT")
        con.commit()
    added = [(c, t) for c, t in TYPED_DB_COLUMNS if c not in cols]
    for c, t in added:
        con.execute(f"ALTER TABLE jobs ADD COLUMN {c} {t}")
    if added:
        con.commit()
    return con


def upsert_company(con, name: str, domain: str, **fields) -> int:
    domain = bare_domain(domain)
    ts = now_iso()
    row = company_by_domain(con, domain)
    if row:
        cid = row["id"]
        sets = {k: v for k, v in fields.items() if v}
        sets["name"] = name
        sets["last_scanned"] = ts
        con.execute(
            f"UPDATE companies SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
            (*sets.values(), cid),
        )
    else:
        cols = {"name": name, "domain": domain, "first_scanned": ts, "last_scanned": ts}
        cols.update({k: v for k, v in fields.items() if v})
        cur = con.execute(
            f"INSERT INTO companies ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(cols.values()),
        )
        cid = cur.lastrowid
    con.commit()
    return cid


def company_by_domain(con, domain: str):
    """The company stored under this domain, or under the final domain this one redirects to."""
    d = bare_domain(domain)
    row = con.execute("SELECT * FROM companies WHERE domain=?", (d,)).fetchone()
    if row:
        return row
    return con.execute(
        "SELECT c.* FROM companies c JOIN company_aliases a ON a.company_id=c.id WHERE a.domain=?", (d,)
    ).fetchone()


def merge_company(con, from_domain: str, into_domain: str) -> dict:
    """Fold the company stored under from_domain into the one under into_domain and remember
    from_domain as an alias. Jobs, routes, runs and rejected sources move over; nothing is lost
    (a route both companies know keeps the into-company's copy)."""
    src = con.execute("SELECT * FROM companies WHERE domain=?", (bare_domain(from_domain),)).fetchone()
    dst = company_by_domain(con, into_domain)
    moved = {"jobs": 0, "routes": 0, "runs": 0, "rejected_sources": 0}
    if dst is None:
        raise ValueError(f"no company with domain {into_domain}")
    if src is not None and src["id"] != dst["id"]:
        s_id, d_id = src["id"], dst["id"]
        moved["jobs"] = con.execute("UPDATE jobs SET company_id=? WHERE company_id=?", (d_id, s_id)).rowcount
        moved["runs"] = con.execute("UPDATE runs SET company_id=? WHERE company_id=?", (d_id, s_id)).rowcount
        moved["routes"] = con.execute("UPDATE OR IGNORE routes SET company_id=? WHERE company_id=?", (d_id, s_id)).rowcount
        con.execute("DELETE FROM routes WHERE company_id=?", (s_id,))
        moved["rejected_sources"] = con.execute("UPDATE OR IGNORE rejected_sources SET company_id=? WHERE company_id=?", (d_id, s_id)).rowcount
        con.execute("DELETE FROM rejected_sources WHERE company_id=?", (s_id,))
        con.execute("UPDATE company_aliases SET company_id=? WHERE company_id=?", (d_id, s_id))
        for col in ("official_website", "career_page", "ats_type", "ats_slug", "sheet_name"):
            if not dst[col] and src[col]:
                con.execute(f"UPDATE companies SET {col}=? WHERE id=?", (src[col], d_id))
        if src["first_scanned"] and (not dst["first_scanned"] or src["first_scanned"] < dst["first_scanned"]):
            con.execute("UPDATE companies SET first_scanned=? WHERE id=?", (src["first_scanned"], d_id))
        con.execute("DELETE FROM companies WHERE id=?", (s_id,))
        # a watch on the folded-in domain follows the company to its final domain
        con.execute("UPDATE OR IGNORE watchlist SET domain=? WHERE domain=?", (dst["domain"], src["domain"]))
        con.execute("DELETE FROM watchlist WHERE domain=?", (src["domain"],))
    if bare_domain(from_domain) != dst["domain"]:
        con.execute("INSERT OR REPLACE INTO company_aliases (domain, company_id) VALUES (?,?)", (bare_domain(from_domain), dst["id"]))
    con.commit()
    return moved


def watch_add(con, domain: str, name: str = "", keywords: str | None = None) -> dict:
    """Put a company on the weekly watchlist (or update its name and keywords). The name comes from the stored
    company when there is one; a company never scanned needs --name."""
    d = bare_domain(domain)
    if not d or "." not in d:
        raise ValueError(f"{domain!r} is not a domain")
    c = company_by_domain(con, d)
    if c:
        d = c["domain"]
    name = (name or (c["name"] if c else "")).strip()
    if not name:
        raise ValueError(f"{d} has not been scanned yet; give --name")
    old = con.execute("SELECT keywords FROM watchlist WHERE domain=?", (d,)).fetchone()
    # Adding again without --keywords keeps the keywords already set; --keywords "" clears them.
    kw = old["keywords"] if keywords is None and old else ",".join(k.strip() for k in (keywords or "").split(",") if k.strip())
    con.execute("INSERT INTO watchlist (domain, name, keywords, added_at) VALUES (?,?,?,?) "
                "ON CONFLICT(domain) DO UPDATE SET name=excluded.name, keywords=excluded.keywords", (d, name, kw, now_iso()))
    con.commit()
    return {"domain": d, "name": name, "keywords": kw, "scanned_before": c is not None}


def watch_remove(con, domain: str) -> bool:
    d = bare_domain(domain)
    c = company_by_domain(con, d)
    n = con.execute("DELETE FROM watchlist WHERE domain IN (?,?)", (d, c["domain"] if c else d)).rowcount
    con.commit()
    return n > 0


def watch_list(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT w.domain, w.name, w.keywords, w.added_at, c.last_scanned, "
        "CASE WHEN c.id IS NULL THEN NULL ELSE (SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.status='Open') END AS open_jobs "
        "FROM watchlist w LEFT JOIN companies c ON c.domain=w.domain ORDER BY w.name COLLATE NOCASE")]


def add_route(con, company_id: int, url: str, kind: str, status_code: int | None = None) -> None:
    ts = now_iso()
    con.execute(
        """INSERT INTO routes (company_id, url, url_norm, kind, status_code, discovered_at, last_checked)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(company_id, url_norm) DO UPDATE SET
             last_checked=excluded.last_checked,
             status_code=COALESCE(excluded.status_code, routes.status_code),
             kind=CASE WHEN routes.kind='page' THEN excluded.kind ELSE routes.kind END""",
        (company_id, url, normalize_url(url), kind, status_code, ts, ts),
    )


def reject_source(con, company_id: int | None, url: str, reason: str) -> None:
    con.execute(
        """INSERT INTO rejected_sources (company_id, url, url_norm, reason, rejected_at) VALUES (?,?,?,?,?)
           ON CONFLICT(company_id, url_norm) DO UPDATE SET reason=excluded.reason, rejected_at=excluded.rejected_at""",
        (company_id, url, normalize_url(url), reason, now_iso()),
    )
    con.commit()


def rejected_urls(con, company_id: int) -> set[str]:
    return {r["url_norm"] for r in con.execute("SELECT url_norm FROM rejected_sources WHERE company_id=?", (company_id,))}


def start_run(con, company_id: int, notes: str = "") -> int:
    cur = con.execute("INSERT INTO runs (company_id, started_at, notes) VALUES (?,?,?)", (company_id, now_iso(), notes))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, **counts) -> None:
    counts["finished_at"] = now_iso()
    con.execute(f"UPDATE runs SET {', '.join(f'{k}=?' for k in counts)} WHERE id=?", (*counts.values(), run_id))
    con.commit()


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
    s = sub.add_parser("show")
    s.add_argument("domain")
    j = sub.add_parser("jobs")
    j.add_argument("domain")
    j.add_argument("--status", choices=["Open", "Closed", "Unverified", "Stale"])
    j.add_argument("--limit", type=int, default=20, help="at most this many jobs (default 20; 0 = all)")
    r = sub.add_parser("reject")
    r.add_argument("--domain", required=True)
    r.add_argument("--url", required=True)
    r.add_argument("--reason", required=True)
    m = sub.add_parser("merge")
    m.add_argument("from_domain")
    m.add_argument("into_domain")
    w = sub.add_parser("watch")
    wsub = w.add_subparsers(dest="watch_cmd", required=True)
    wa = wsub.add_parser("add")
    wa.add_argument("domain")
    wa.add_argument("--name", default="")
    wa.add_argument("--keywords", default=None, help="comma-separated; the digest then lists only new roles whose title has one of them (omit to keep the current ones, '' to clear)")
    wsub.add_parser("remove").add_argument("domain")
    wsub.add_parser("list")
    a = ap.parse_args()
    con = connect()
    if a.cmd == "watch":
        if a.watch_cmd == "add":
            try:
                print(json.dumps(watch_add(con, a.domain, a.name, a.keywords)))
            except ValueError as e:
                sys.exit(str(e))
        elif a.watch_cmd == "remove":
            print("removed" if watch_remove(con, a.domain) else f"{a.domain} was not on the watchlist")
        else:
            rows = watch_list(con)
            for r_ in rows:
                print(f"{r_['domain']:<26} {r_['name']:<26} open {r_['open_jobs'] if r_['open_jobs'] is not None else '-':>4}  "
                      f"last scanned {(r_['last_scanned'] or 'never')[:10]}  keywords: {r_['keywords'] or '-'}")
            print(f"{len(rows)} on the watchlist")
        return
    if a.cmd == "init":
        try:   # the Excel file exists before the first search; an existing file is never touched
            import init_workbook
            init_workbook.ensure()
        except Exception as e:  # a missing file is made again by the upsert, so this must never stop a run
            print(f"note: could not prepare the Excel file yet ({e})", file=sys.stderr)
        print(f"memory ready: {DB_PATH}")
    elif a.cmd == "stats":
        for t in ("companies", "company_aliases", "routes", "jobs", "rejected_sources", "runs"):
            print(f"{t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
        for r_ in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
            print(f"  jobs {r_['status']}: {r_['n']}")
    elif a.cmd == "show":
        c = company_by_domain(con, a.domain)
        if not c:
            sys.exit(f"no company with domain {a.domain}")
        out = dict(c)
        out["jobs_by_status"] = {
            r_["status"]: r_["n"]
            for r_ in con.execute("SELECT status, COUNT(*) n FROM jobs WHERE company_id=? GROUP BY status", (c["id"],))
        }
        out["routes"] = con.execute("SELECT COUNT(*) FROM routes WHERE company_id=?", (c["id"],)).fetchone()[0]
        out["last_runs"] = [
            dict(r_) for r_ in con.execute("SELECT * FROM runs WHERE company_id=? ORDER BY id DESC LIMIT 3", (c["id"],))
        ]
        print(json.dumps(out, indent=1))
    elif a.cmd == "jobs":
        c = company_by_domain(con, a.domain)
        if not c:
            sys.exit(f"no company with domain {a.domain}")
        q = "SELECT title, location, source_type, status, url, date_discovered, last_checked FROM jobs WHERE company_id=?"
        args = [c["id"]]
        if a.status:
            q += " AND status=?"
            args.append(a.status)
        n = con.execute(q.replace("SELECT title, location, source_type, status, url, date_discovered, last_checked", "SELECT COUNT(*)"), args).fetchone()[0]
        if a.limit:
            q += " ORDER BY status, title LIMIT ?"
            args.append(a.limit)
        else:
            q += " ORDER BY status, title"
        rows = [dict(r_) for r_ in con.execute(q, args)]
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"{len(rows)} of {n} jobs shown" + (" (use --limit 0 for all)" if len(rows) < n else ""))
    elif a.cmd == "merge":
        print(json.dumps(merge_company(con, a.from_domain, a.into_domain)))
    elif a.cmd == "reject":
        c = company_by_domain(con, a.domain)
        reject_source(con, c["id"] if c else None, a.url, a.reason)
        print("rejected")


if __name__ == "__main__":
    _main()
