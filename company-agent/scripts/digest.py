"""Weekly digest of new and closed roles at the watched companies (improvement plan item 8, 2026-10-18).

uv run python scripts/digest.py [--since 7d|YYYY-MM-DD] [--all] [--out runs/digest-<date>.md]

Reads memory.db only (no network). Default scope is the watchlist (`memory.py watch add ...`); --all covers every
company. For each company it lists:
- new roles: discovered on or after the start date and after the company's first scan day (a company's first scan is
  its baseline, so it is counted but not listed as new), still Open or Unverified. A watch with keywords lists only
  the roles whose title contains one of them and counts the rest;
- closed roles: set to Closed on or after the start date (the day of the second complete miss), and known before it;
  a role found and closed inside the same period was never reported as new, so it is not reported as closed either;
- a warning when the company was not scanned since the start date, because its digest may be missing roles.
Every new role carries a ready line `/scout <job URL>` for the candidate agent. The only link between the two agents is
that URL; the line is left out for LinkedIn and Indeed listings, which the candidate agent may not fetch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
import progress  # noqa: E402
from common import ROOT, is_aggregate_url  # noqa: E402

NO_SCOUT_HOSTS = re.compile(r"(^|\.)(linkedin|indeed)\.", re.I)


def local_day(stamp: str | None) -> str:
    """The local calendar day of a stored time. memory.db keeps first_scanned and last_scanned in UTC but job dates in local
    days, so both are compared as local days (a scan at 02:00 IST is still the previous day in UTC)."""
    if not stamp:
        return ""
    try:
        t = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp[:10]
    return (t.astimezone() if t.tzinfo else t).date().isoformat()


def parse_since(text: str, today: dt.date) -> dt.date:
    """Start of the period. '7d' is the 7 days ending today (today and the 6 before), so two weekly runs a week apart do
    not share a day; an ISO date is the first day itself."""
    m = re.fullmatch(r"(\d+)\s*([dw])", text.strip().lower())
    if m:
        days = int(m.group(1)) * (7 if m.group(2) == "w" else 1)
        if not 1 <= days <= 3650:
            raise ValueError("--since must be between 1 day and 10 years")
        return today - dt.timedelta(days=days - 1)
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"--since must look like 7d, 2w or 2026-10-11, not {text!r}")


def scout_line(url: str) -> str:
    """`/scout <url>` for a per-job URL the candidate agent may fetch, else ''."""
    if not url or is_aggregate_url(url) or NO_SCOUT_HOSTS.search(urlsplit(url).hostname or ""):
        return ""
    return f"/scout {url}"


def company_section(con, domain: str, name: str, keywords: str, since: dt.date) -> tuple[list[str], dict]:
    c = memory.company_by_domain(con, domain)
    stats = {"new": 0, "hidden": 0, "closed": 0, "baseline": 0, "unscanned": c is None, "stale": False}
    out = [f"## {name} ({domain})", ""]
    if c is None:
        out += ["Not scanned yet. Run `rescan_watchlist.py` or `/company-scan` once to set the baseline.", ""]
        return out, stats
    s = since.isoformat()
    first = local_day(c["first_scanned"])
    last = local_day(c["last_scanned"])
    if last < s:
        stats["stale"] = True
        out += [f"> Last scanned {last or 'never'}, before {s}. This digest may be missing roles.", ""]
    if first >= s:
        n = con.execute("SELECT COUNT(*) FROM jobs WHERE company_id=? AND status IN ('Open','Unverified')", (c["id"],)).fetchone()[0]
        stats["baseline"] = n
        out += [f"First scan on {first}: {n} open roles are the baseline. New roles are listed from the next digest.", ""]
    rows = [dict(r) for r in con.execute(
        "SELECT title, location, work_mode, posted_date, url, source_type, status FROM jobs "
        "WHERE company_id=? AND substr(date_discovered,1,10)>=? AND substr(date_discovered,1,10)>? "
        "AND status IN ('Open','Unverified') ORDER BY title COLLATE NOCASE", (c["id"], s, first))]
    words = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    shown = [r for r in rows if not words or any(w in (r["title"] or "").lower() for w in words)]
    stats["new"], stats["hidden"] = len(shown), len(rows) - len(shown)
    if shown:
        out += [f"### New roles ({len(shown)})", ""]
        for r in shown:
            meta = " · ".join(x for x in (r["location"], r["work_mode"] if r["work_mode"] not in (None, "", "Unknown") else "",
                                          f"posted {r['posted_date']}" if r["posted_date"] else "",
                                          "Unverified" if r["status"] == "Unverified" else "") if x)
            out.append(f"- **{r['title']}**" + (f" · {meta}" if meta else ""))
            if r["url"]:
                out.append(f"  - Listing: {r['url']}")
            sl = scout_line(r["url"])
            out.append(f"  - Find candidates: `{sl}`" if sl else "  - Find candidates: copy the description into `/scout` (this listing cannot be fetched).")
        out.append("")
    elif first < s:
        out += ["No new roles.", ""]
    if stats["hidden"]:
        out += [f"{stats['hidden']} other new role(s) did not match the keywords ({', '.join(words)}).", ""]
    closed = [dict(r) for r in con.execute(
        "SELECT title, location, url FROM jobs WHERE company_id=? AND status='Closed' AND substr(last_checked,1,10)>=? "
        "AND substr(date_discovered,1,10)<? ORDER BY title COLLATE NOCASE", (c["id"], s, s))]
    stats["closed"] = len(closed)
    if closed:
        out += [f"### Closed roles ({len(closed)})", ""]
        out += [f"- {r['title']}" + (f" · {r['location']}" if r["location"] else "") for r in closed] + [""]
    return out, stats


def build(con, since: dt.date, all_companies: bool = False, today: dt.date | None = None) -> tuple[str, dict]:
    today = today or dt.date.today()
    if all_companies:
        scope = [(r["domain"], r["name"], "") for r in con.execute("SELECT domain, name FROM companies ORDER BY name COLLATE NOCASE")]
    else:
        scope = [(r["domain"], r["name"], r["keywords"]) for r in con.execute("SELECT domain, name, keywords FROM watchlist ORDER BY name COLLATE NOCASE")]
    body: list[str] = []
    total = {"companies": len(scope), "new": 0, "closed": 0, "baseline": 0, "stale": 0, "unscanned": 0}
    for domain, name, kw in scope:
        sec, st = company_section(con, domain, name, kw, since)
        body += sec
        for k in ("new", "closed", "baseline"):
            total[k] += st[k]
        total["stale"] += st["stale"]
        total["unscanned"] += st["unscanned"]
    head = [f"# Hiring digest: {since.isoformat()} to {today.isoformat()}", "",
            f"{total['companies']} companies · {total['new']} new roles · {total['closed']} closed roles"
            + (f" · {total['stale']} not scanned in this period" if total["stale"] else "")
            + (f" · {total['unscanned']} never scanned" if total["unscanned"] else ""), ""]
    if not scope:
        head += ["The watchlist is empty. Add companies with `uv run python scripts/memory.py watch add <domain>`.", ""]
    return "\n".join(head + body).rstrip() + "\n", total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="7d")
    ap.add_argument("--all", action="store_true", help="every company in memory, not only the watchlist")
    ap.add_argument("--out")
    a = ap.parse_args()
    today = dt.date.today()
    try:
        since = parse_since(a.since, today)
    except ValueError as e:
        sys.exit(str(e))
    out = Path(a.out) if a.out else ROOT / "runs" / f"digest-{today.isoformat()}.md"
    out = out if out.is_absolute() else ROOT / out
    try:
        out.resolve().relative_to((ROOT / "runs").resolve())
    except ValueError:
        sys.exit("the digest is written under runs/ only")
    text, total = build(memory.connect(), since, a.all, today)
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    progress.result(f"{total['companies']} companies · {total['new']} new roles · {total['closed']} closed · "
                    f"{total['stale']} not scanned in the period → {out.relative_to(ROOT)}")
    print(f"DIGEST_FILE={out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
