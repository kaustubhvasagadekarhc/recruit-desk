"""Deduplicate rows, merge them into memory.db (jobs table) and set each job's status.

- Within the batch: same normalised URL, else same company + title + location, is one job.
  The better source wins (ATS feed > careers page > public index > public post).
- Against memory: a known job is updated (Last Checked, status, fields); Date Discovered is kept.
- Closing (safe, improvement plan item 4): only sources the scan read in full count
  (meta.complete_sources; a feed that read fewer postings than it reports is not complete). A job
  from such a source that is missing gets missing_count + 1; it becomes Closed only at 2, i.e. after
  two complete scans in a row without it. Misses count once per calendar day (jobs.last_miss_date):
  a second complete scan on the same day does not count again. A job seen again resets to 0. "Missing once" jobs are
  listed in the run report and the final file's meta, not in the sheet.
- Stale: off-site rows (public index / posts) not seen for 60 days, or posted 60+ days ago. An
  approximate Posted Date (from a relative age such as "3 weeks ago") counts only past the threshold
  plus its precision: +7 days for weeks, +30 for months (common.basis_margin).
- Posted Date Basis: common.fill_posted_date sets it on every batch row: 'exact (from page)' for a
  real date, the estimate from the scout's "_posted_age" text otherwise. An exact date is never
  replaced by an estimate.
- Junk: careers-page rows that are navigation or pagination links, not jobs (common.looks_like_job),
  are dropped from the batch and removed from memory; their keys go to the final file's
  meta.removed_keys so excel_upsert.py removes them from the sheet too.
- Same job, new URL: a job re-found from another source (careers page -> ATS feed) by title and
  location, or by a title that is unique on both sides, keeps its history and takes the new URL as
  its key; meta.renamed_keys tells excel_upsert.py to update that sheet row in place.
- Aggregate URLs (2026-10-15): public LinkedIn / Indeed snippets often point at a listing page
  (a search or company jobs page) shared by many jobs. common.job_key keys such a row by company +
  title + location instead of the URL (the company name is memory's, so the key is stable across
  runs and sources), so distinct jobs behind one URL stay distinct. A job stored under the old URL
  key is re-keyed in memory on the next run (meta.renamed_keys); per-job URLs keep their URL key.

CLI: uv run python scripts/dedupe.py runs/a.rows.json [runs/b.rows.json ...]
Writes runs/<company>-<date>.final.rows.json (every job of the company) and prints FINAL_FILE=...
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
import progress  # noqa: E402
from jobfields import TYPED_COLUMNS, fill_typed  # noqa: E402
from common import COLUMNS, EXACT_BASIS, RUNS_DIR, basis_margin, fill_posted_date, is_aggregate_url, job_key, looks_like_job, normalize_url, today  # noqa: E402
from validate_rows import validate_payload  # noqa: E402

RANK = {"ATS": 0, "Careers Page": 1, "LinkedIn (public index)": 2, "Indeed (public index)": 2, "Job Board (public index)": 2, "Public Hiring Post": 3}
STALE_DAYS = 60
CLOSE_AFTER_MISSES = 2  # complete scans in a row that must miss a job before it is Closed


def rank(source: str) -> int:
    return RANK["ATS"] if source.startswith("ATS:") else RANK.get(source, 4)


def is_stale(last_checked: str | None, posted_date: str | None, basis: str | None, today_: str | None = None) -> bool:
    """Not seen for STALE_DAYS, or posted more than STALE_DAYS ago (plus the precision margin of an
    approximate date: a "3 weeks ago" estimate is Stale only past STALE_DAYS + 7 days)."""
    t = dt.date.fromisoformat(today_ or today())
    cutoff = (t - dt.timedelta(days=STALE_DAYS)).isoformat()
    if (last_checked or "") and last_checked < cutoff:
        return True
    if not posted_date:
        return False
    return posted_date[:10] < (t - dt.timedelta(days=STALE_DAYS + basis_margin(basis))).isoformat()


def soft_key(row: dict) -> str:
    def n(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    loc = n(row.get("Location / Work Mode")).split(" ")[0] if row.get("Location / Work Mode") else ""
    return f"{n(row.get('Company Name'))}|{n(row.get('Job Title'))}|{loc}"


def typed(r: dict) -> list:
    """The row's typed cells in memory.TYPED_DB_COLUMNS order (blank -> NULL)."""
    return [r.get(c) if r.get(c) not in ("", None) else None for c in TYPED_COLUMNS]


def typed_cells(j, raw: dict) -> dict:
    """Typed sheet cells of a stored job. A job stored before 2026-10-16 has none yet: they are
    filled from its stored title, location and the script hints kept in raw_json."""
    if j["work_mode"] is None:
        return {c: v for c, v in fill_typed({**raw, "Job Title": j["title"], "Location / Work Mode": j["location"] or ""}).items() if c in TYPED_COLUMNS}
    return {c: (j[col] if j[col] is not None else "") for c, (col, _) in zip(TYPED_COLUMNS, memory.TYPED_DB_COLUMNS)}


def main(paths: list[str]) -> None:
    metas, rows = [], []
    for p in paths:
        payload = json.loads(Path(p).read_text())
        errs = validate_payload(payload)
        if errs:
            progress.fail_exit("refusing invalid rows:\n" + "\n".join(errs[:30]))
        metas.append(payload.get("meta", {}) if isinstance(payload, dict) else {})
        rows.extend(payload["rows"] if isinstance(payload, dict) else payload)
    if not rows and not any(m.get("complete_sources") for m in metas):
        progress.fail_exit("no rows")
    company = next((m.get("company") for m in metas if m.get("company")), rows[0]["Company Name"] if rows else "")
    domain = next((m.get("domain") for m in metas if m.get("domain")), "")
    con = memory.connect()
    c = memory.company_by_domain(con, domain) if domain else con.execute("SELECT * FROM companies WHERE name=?", (company,)).fetchone()
    if not c:
        progress.fail_exit(f"company {company!r} not in memory; run scan.py first")
    cid = c["id"]

    def key_of(r: dict) -> str:
        # memory's company name, so an aggregate-URL job keeps one key whatever the scout called the company
        return job_key({**r, "Company Name": c["name"]})

    rejected = memory.rejected_urls(con, cid)

    # 1. dedupe within the batch
    best: dict[str, dict] = {}
    soft: dict[str, tuple[str, str]] = {}
    dupes = 0
    progress.sub(f"deduplicating {len(rows)} rows from {len(paths)} file(s)")
    junk_in = 0
    for r in sorted(rows, key=lambda r: rank(r["Source Type"])):
        if normalize_url(r["Job / Post URL"]) in rejected:
            continue
        fill_posted_date(r)
        fill_typed(r)
        if r["Source Type"] == "Careers Page" and not looks_like_job(r["Job Title"], r["Job / Post URL"]):
            junk_in += 1
            continue
        k, s = key_of(r), soft_key(r)
        # The soft key only merges across sources: one board may list two real roles with the
        # same title and city under different URLs.
        if k in best or (s in soft and soft[s][1] != r["Source Type"]):
            dupes += 1
            continue
        best[k] = r
        soft.setdefault(s, (k, r["Source Type"]))

    # 2. remove junk rows an earlier run stored (navigation / pagination links, not jobs)
    #    and second copies of one job stored under two spellings of its URL (café / caf%C3%A9).
    #    A job stored under an aggregate listing URL gets its company + title + location key.
    removed, urls_seen = [], set()
    renamed: dict[str, str] = {}
    for row in con.execute("SELECT * FROM jobs WHERE company_id=? ORDER BY id", (cid,)).fetchall():
        junk = row["source_type"] == "Careers Page" and not looks_like_job(row["title"], row["url"])
        agg = is_aggregate_url(row["url"])
        ident = key_of({"Job / Post URL": row["url"], "Job Title": row["title"], "Location / Work Mode": row["location"]}) if agg else normalize_url(row["url"])
        dup = ident in urls_seen
        urls_seen.add(ident)
        if junk or dup:
            removed.append({"job_key": row["job_key"], "title": row["title"], "url": row["url"], "status": row["status"],
                            "reason": "navigation or pagination link, not a job" if junk else "second copy of the same job URL"})
            con.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
        elif agg and row["job_key"] != ident:
            con.execute("UPDATE jobs SET job_key=? WHERE id=?", (ident, row["id"]))
            renamed[row["job_key"]] = ident
    if removed:
        progress.sub(f"removed {len(removed)} junk row(s) (navigation / pagination links or duplicate URLs, not jobs) from memory")

    # 3. merge into memory
    existing = {row["job_key"]: row for row in con.execute("SELECT * FROM jobs WHERE company_id=?", (cid,))}
    existing_soft: dict[str, list[tuple[str, str]]] = {}
    for row in existing.values():
        existing_soft.setdefault(soft_key({"Company Name": company, "Job Title": row["title"], "Location / Work Mode": row["location"]}), []).append((row["job_key"], row["source_type"]))
    def tnorm(t):
        return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

    # Titles a stored job shares with the batch: a cross-source match when the batch lists that title
    # at least as often as memory does (a source that repeats a title pairs its copies in order).
    old_titles: dict[str, list[str]] = {}
    for row in existing.values():
        old_titles.setdefault(tnorm(row["title"]), []).append(row["job_key"])
    new_titles: dict[str, int] = {}
    for r in best.values():
        new_titles[tnorm(r["Job Title"])] = new_titles.get(tnorm(r["Job Title"]), 0) + 1
    d = today()
    new = updated = 0
    new_titles_out: list[str] = []
    seen_keys = set()
    for k, r in best.items():
        key = k
        if k not in existing:
            hit = next((h for h in existing_soft.get(soft_key(r), []) if h[1] != r["Source Type"] and h[0] not in seen_keys and h[0] not in best), None)
            if hit:
                key = hit[0]
            else:
                t = tnorm(r["Job Title"])
                cand = old_titles.get(t) or []
                free = [c_ for c_ in cand if c_ not in seen_keys and c_ not in best and existing[c_]["source_type"] != r["Source Type"]]
                if free and len(cand) <= new_titles.get(t, 0):
                    key = free[0]
        seen_keys.add(key)
        clean = {c_: r.get(c_, "") for c_ in COLUMNS}
        raw = json.dumps({**clean, **{x: y for x, y in r.items() if x.startswith("_")}})
        if key in existing:
            old = existing[key]
            if rank(r["Source Type"]) > rank(old["source_type"]):
                # a weaker source re-found a job we know from a better one: just touch it
                con.execute("UPDATE jobs SET last_checked=? WHERE job_key=?", (d, key))
                continue
            # The better source's URL becomes the job's key (history and Date Discovered stay).
            new_key = k if key != k and k not in existing else key
            # A new date replaces the stored one, except that an estimate never replaces an exact date.
            pd, basis = r.get("Posted Date", ""), r.get("Posted Date Basis", "")
            old_exact = bool(old["posted_date"]) and (old["posted_date_basis"] or EXACT_BASIS) == EXACT_BASIS
            if not pd or (basis != EXACT_BASIS and old_exact):
                pd, basis = old["posted_date"] or "", old["posted_date_basis"] or (EXACT_BASIS if old["posted_date"] else "")
            con.execute(
                """UPDATE jobs SET job_key=?, url=?, title=?, location=?, source_type=?, posted_by=?,
                   posted_date=?, posted_date_basis=?, last_checked=?,
                   description_summary=COALESCE(NULLIF(?, ''), description_summary),
                   application_url=?, status=?, raw_json=?, missing_count=0, last_miss_date=NULL,
                   work_mode=?, city=?, country=?, function=?, seniority=?, salary_min=?, salary_max=?, currency=? WHERE job_key=?""",
                (new_key, r["Job / Post URL"], r["Job Title"], r.get("Location / Work Mode", ""), r["Source Type"], r.get("Posted By", ""),
                 pd, basis, d, r.get("Description Summary", ""), r.get("Application URL", ""), r["Status"], raw, *typed(r), key),
            )
            if new_key != key:
                renamed[key] = new_key
            updated += 1
        else:
            con.execute(
                """INSERT INTO jobs (company_id, job_key, url, title, location, source_type, posted_by, posted_date,
                   posted_date_basis, date_discovered, last_checked, description_summary, application_url, status, raw_json,
                   work_mode, city, country, function, seniority, salary_min, salary_max, currency)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, key, r["Job / Post URL"], r["Job Title"], r.get("Location / Work Mode", ""), r["Source Type"], r.get("Posted By", ""),
                 r.get("Posted Date", ""), r.get("Posted Date Basis", ""), r.get("Date Discovered") or d, d, r.get("Description Summary", ""), r.get("Application URL", ""), r["Status"], raw,
                 *typed(r)),
            )
            new += 1
            new_titles_out.append(r["Job Title"])

    # 4. jobs missing from completely enumerated sources: Closed after complete misses on two different days
    complete = {s for m in metas for s in m.get("complete_sources", [])}
    incomplete = {s for m in metas for s in m.get("incomplete_sources", [])}
    complete -= incomplete  # one incomplete read of a source in the batch outweighs a complete one
    closed = 0
    closed_titles, missing_once = [], []
    for key, old in existing.items():
        if key in seen_keys or old["source_type"] not in complete or old["status"] not in ("Open", "Unverified"):
            continue
        if old["last_miss_date"] == d:
            # already counted a miss today: a same-day re-run is not a second miss
            missing_once.append(old["title"])
            continue
        n = (old["missing_count"] or 0) + 1
        if n >= CLOSE_AFTER_MISSES:
            con.execute("UPDATE jobs SET status='Closed', missing_count=?, last_miss_date=?, last_checked=? WHERE job_key=?", (n, d, d, key))
            closed += 1
            closed_titles.append(old["title"])
        else:
            con.execute("UPDATE jobs SET missing_count=?, last_miss_date=? WHERE job_key=?", (n, d, key))
            missing_once.append(old["title"])

    # 5. stale off-site rows (an approximate Posted Date gets a margin for its precision)
    stale = 0
    for j in con.execute(
        """SELECT id, last_checked, posted_date, posted_date_basis FROM jobs WHERE company_id=? AND status IN ('Open','Unverified')
           AND source_type NOT LIKE 'ATS:%' AND source_type <> 'Careers Page'""", (cid,)).fetchall():
        if is_stale(j["last_checked"], j["posted_date"], j["posted_date_basis"]):
            con.execute("UPDATE jobs SET status='Stale' WHERE id=?", (j["id"],))
            stale += 1
    con.commit()

    for m in metas:
        if m.get("run_id"):
            memory.finish_run(con, m["run_id"], jobs_found=len(best), jobs_new=new, jobs_closed=closed)

    # 6. final file: every job of the company, in sheet order
    company_row = dict(memory.company_by_domain(con, domain)) if domain else dict(c)
    out_rows = []
    order = {"Open": 0, "Unverified": 1, "Stale": 2, "Closed": 3}
    for j in con.execute("SELECT * FROM jobs WHERE company_id=?", (cid,)):
        out_rows.append({
            "Company Name": company_row["name"],
            "Official Website": company_row.get("official_website") or "",
            "Career Page": company_row.get("career_page") or "",
            "Job Title": j["title"],
            "Location / Work Mode": j["location"] or "",
            "Source Type": j["source_type"],
            "Job / Post URL": j["url"],
            "Posted By": j["posted_by"] or "",
            "Posted Date": j["posted_date"] or "",
            "Date Discovered": j["date_discovered"],
            "Last Checked": j["last_checked"],
            "Description Summary": j["description_summary"] or "",
            "Application URL": j["application_url"] or "",
            "Status": j["status"],
            "Posted Date Basis": j["posted_date_basis"] or (EXACT_BASIS if j["posted_date"] else ""),
            **typed_cells(j, raw := json.loads(j["raw_json"] or "{}")),
            **({"_work_mode": raw["_work_mode"]} if raw.get("_work_mode") else {}),
            **({"_confirmed_url": json.loads(j["raw_json"] or "{}").get("_confirmed_url")} if j["status"] == "Open" and j["source_type"].endswith("(public index)") else {}),
        })
    out_rows.sort(key=lambda r: (order.get(r["Status"], 9), r["Job Title"].lower()))
    slug = re.sub(r"[^a-z0-9]+", "-", company_row["name"].lower()).strip("-")
    out = RUNS_DIR / f"{slug}-{d}.final.rows.json"
    out.write_text(json.dumps({"meta": {
        "company": company_row["name"], "domain": company_row["domain"],
        "removed_keys": [x["job_key"] for x in removed], "removed": removed, "renamed_keys": renamed,
        "new_titles": new_titles_out, "closed_titles": closed_titles, "missing_once_titles": missing_once,
        "complete_sources": sorted(complete), "incomplete_sources": sorted(incomplete),
    }, "rows": out_rows}, indent=1))
    print(f"batch rows: {len(rows)}, batch duplicates dropped: {dupes}, junk dropped: {junk_in}, new: {new}, updated: {updated}, "
          f"closed: {closed}, missing once: {len(missing_once)}, stale: {stale}, junk removed from memory: {len(removed)}, re-keyed to a new URL: {len(renamed)}")
    if incomplete:
        print(f"not used to close jobs (read incompletely): {', '.join(sorted(incomplete))}")
    counts = ", ".join(f"{s}={sum(1 for r in out_rows if r['Status'] == s)}" for s in order)
    print(f"company total: {len(out_rows)} ({counts})")
    print(f"FINAL_FILE={out.relative_to(RUNS_DIR.parent)}")
    extra = f", {len(removed)} junk rows removed" if removed else ""
    if missing_once:
        extra += f", {len(missing_once)} missing once (Closed if missing again)"
    progress.result(f"{len(rows)} rows in, {dupes} duplicates dropped, {new} new, {updated} updated, {closed} closed, {stale} stale{extra}; company total {len(out_rows)} ({counts})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
