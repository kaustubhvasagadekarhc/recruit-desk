"""Scan one company: read its ATS feed first, crawl only as much as needed, write a rows file.

Order (improvement plan item 3, 2026-10-15):
1. A board already known (memory, or --ats) is read first. If it gives jobs, the site gets only a
   light check: robots.txt, the home page and the careers pages (5 pages, no sitemap), to re-confirm
   the board link and to catch roles listed on the company's own site but not on the board.
2. Otherwise the site is crawled (robots, sitemap first, up to --max-pages). The crawl stops as soon
   as a careers page links a readable ATS board, and that board is read.
3. If that board turns out empty or unreadable, the full crawl runs as before.

A feed is "complete" only when it read every posting it reports (ats.fetch meta.complete); dedupe
closes jobs only from complete sources. It records the company, routes and the run in memory.db but
does NOT touch jobs or the workbook; dedupe.py and excel_upsert.py do that.

CLI:
  uv run python scripts/scan.py --company "Razorpay" --domain razorpay.com
       [--careers URL] [--ats greenhouse:slug ...] [--max-pages 150] [--full-crawl]
Prints the path of the rows file (runs/<company>-<timestamp>.rows.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats as atsmod  # noqa: E402
import blockers as blk  # noqa: E402
import memory  # noqa: E402
import progress  # noqa: E402
import spec_reader  # noqa: E402
from common import RUNS_DIR, bare_domain, normalize_url, now_iso, today  # noqa: E402
from crawl import crawl  # noqa: E402

LIGHT_PAGES = 5  # pages re-checked on the company's own site when a known feed answered
PROFILE_FRESH_DAYS = 30  # a site profile younger than this lets a run skip the crawl entirely
OPEN_KINDS = ("unknown_ats_host", "no_reader", "js_rendered", "empty_board", "parse_fail")


def tnorm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def readable(boards: list[dict], specs: dict[str, dict] | None = None) -> list[dict]:
    """Boards with a reader: a built-in one (ats.py) or an active reader spec, and a slug."""
    return [b for b in boards if b.get("slug") and (b["ats"] in atsmod.FETCHERS or b["ats"] in (specs or {}))]


def board_url(b: dict, specs: dict[str, dict]) -> str:
    if b["ats"] in atsmod.FETCHERS:
        return atsmod.board_url(b["ats"], b["slug"])
    return spec_reader.board_url(specs[b["ats"]], b["slug"])


def reader_name(ats: str, specs: dict[str, dict]) -> str:
    if ats in atsmod.FETCHERS:
        return f"builtin:{ats}"
    return f"spec:{specs[ats]['name']}" if ats in specs else ""


def skipped_crawl(domain: str, known, prof) -> dict:
    """The crawl result of a run that trusted its site profile and fetched no page of the site."""
    return {"domain": domain, "official_website": (known["official_website"] if known else "") or f"https://{domain}/",
            "careers_page": prof["careers_url"] or "", "sitemap_urls": 0, "pages_fetched": 0, "blocked_by_robots": 0,
            "career_urls_unvisited": 0, "stopped_early": False, "requests": 0, "ats_links": [], "careers_external": [],
            "jobs": [], "pages": []}


def profile_fresh(prof) -> bool:
    try:
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(prof["updated_at"])
    except (TypeError, ValueError):
        return False
    return age.days < PROFILE_FRESH_DAYS


class Feeds:
    """Reads each board once and remembers what it got."""

    def __init__(self, known_jobs: dict[str, dict[str, str]], open_by_source: dict[str, int],
                 specs: dict[str, dict] | None = None, site_domain: str = ""):
        self.specs = specs or {}
        self.site_domain = site_domain
        self.failures: list[dict] = []  # {board, kind, detail} for the blockers table
        self.got: dict[tuple[str, str], int] = {}  # (ats, slug) -> jobs read
        self.known_jobs = known_jobs  # source type -> {normalised url: stored summary}
        self.open_by_source = open_by_source
        self.read: list[dict] = []  # boards that answered
        self.rows: list[dict] = []
        self.complete: list[str] = []
        self.incomplete: list[str] = []
        self.notes: list[str] = []
        self._seen: set[tuple[str, str]] = set()

    def take(self, boards: list[dict]) -> int:
        """Read boards not read yet; return how many rows they added."""
        added = 0
        for b in readable(boards, self.specs):
            key = (b["ats"], b["slug"].lower())
            if key in self._seen:
                continue
            self._seen.add(key)
            spec = None if b["ats"] in atsmod.FETCHERS else self.specs[b["ats"]]
            src = atsmod.SOURCE[b["ats"]] if spec is None else f"ATS: {spec['label']}"
            progress.sub(f"reading ATS feed {b['ats']}:{b['slug']} ({b.get('evidence', b.get('url', ''))[:60]})"
                         + (f" with reader spec {spec['name']}" if spec else ""))
            try:
                if spec is None:
                    got, meta = atsmod.fetch(b["ats"], b["slug"], known=self.known_jobs.get(src, {}))
                else:
                    got, meta = spec_reader.fetch_rows(spec, b["slug"], site_domain=self.site_domain)
            except (requests.RequestException, ValueError) as e:
                self.notes.append(f"{b['ats']}:{b['slug']} feed failed: {e}")
                self.failures.append({"board": b, "kind": "parse_fail", "detail": str(e)[:300]})
                progress.sub(f"ATS feed {b['ats']}:{b['slug']} failed: {e}")
                continue
            self.got[key] = len(got)
            if not got:
                self.failures.append({"board": b, "kind": "empty_board", "detail": meta.get("note") or "the board listed 0 jobs"})
            if not got and meta.get("note"):
                self.notes.append(f"{b['ats']}:{b['slug']}: {meta['note']}")
                continue
            ok = bool(meta.get("complete"))
            # An empty answer for a board that had open jobs last time is more likely a feed
            # problem than every job closing at once: never treat it as a complete list.
            if not got and self.open_by_source.get(src, 0) > 0:
                ok = False
                self.notes.append(f"{b['ats']}:{b['slug']} returned 0 jobs but {self.open_by_source[src]} were open; not used to close jobs")
            (self.complete if ok else self.incomplete).append(src)
            self.read.append(b)
            self.rows.extend(got)
            added += len(got)
            extra = "" if ok else f" (incomplete: read {meta.get('read')} of {meta.get('fresh_total') or meta.get('total')})"
            self.notes.append(f"{b['ats']}:{b['slug']} -> {len(got)} jobs{extra} {meta.get('company_names', '')}".rstrip())
        return added


def learn(con, run_id: int, domain: str, cr: dict, det: dict, feeds: "Feeds", specs: dict, prof, mode: str,
          careers: str, own_site: int, n_rows: int, seconds: float) -> dict:
    """Write what this run learned: blockers met (and resolved), and the site profile."""
    notes: list[str] = []
    # Blockers met in this run
    for b in det["boards"]:
        if not reader_name(b["ats"], specs) or not b.get("slug"):
            blk.record(con, run_id, domain, "no_reader", b["host"], b["url"], f"{b['label']} board without a reader"
                       + ("" if b.get("slug") else " (no board id in the URL)"), b["ats"], b.get("slug"))
    for u in det["unknown"]:
        blk.record(con, run_id, domain, "unknown_ats_host", u["host"], u["url"], "job-board-looking host not in schema/ats_fingerprints.json")
    if not n_rows and cr.get("careers_page"):
        embeds = [x for x in cr.get("careers_external", []) if x.get("embed")]
        hosts = {b["host"] for b in det["boards"]} | {u["host"] for u in det["unknown"]}
        hit = next((x for x in embeds if (urlsplit(x["url"]).hostname or "") in hosts), None)
        if hit:
            blk.record(con, run_id, domain, "js_rendered", urlsplit(hit["url"]).hostname or "", hit["url"],
                       f"{hit['found_on']} fills its job list with a script from this host")
    for f in feeds.failures:
        b = f["board"]
        host = b.get("host") or urlsplit(board_url(b, specs)).hostname or ""
        if f["kind"] == "empty_board" and feeds.rows:
            continue  # another board answered
        blk.record(con, run_id, domain, f["kind"], host, board_url(b, specs), f["detail"], b["ats"], b["slug"])
    for p in cr.get("pages", []):
        if p.get("careers") and p.get("status") in (401, 403):
            blk.record(con, run_id, domain, "http_403", urlsplit(p["final_url"]).hostname or "", p["final_url"], f"HTTP {p['status']} on a careers page")
    if cr.get("blocked_by_robots") and not cr.get("careers_page"):
        blk.record(con, run_id, domain, "robots", domain, f"https://{domain}/robots.txt", f"{cr['blocked_by_robots']} URLs disallowed; no careers page reached")
    # Blockers cleared by boards that answered
    first_read = None
    for b in feeds.read:
        n = feeds.got.get((b["ats"], b["slug"].lower()), 0)
        if n:
            host = urlsplit(board_url(b, specs)).hostname or ""
            first_read = first_read or (b, n)
            k = blk.resolve(con, domain, f"{reader_name(b['ats'], specs)} feed {b['slug']} ({n} jobs, run {run_id})", host=host, kinds=OPEN_KINDS)
            if k:
                notes.append(f"{k} blocker(s) resolved by {reader_name(b['ats'], specs)}")
    # Site profile
    fields = {"last_crawl_mode": mode, "last_pages": cr.get("pages_fetched", 0), "last_seconds": round(seconds, 1),
              "last_jobs": n_rows, "last_run_id": run_id}
    if mode != "skipped (site profile)":
        fields.update(own_site_jobs=own_site, job_pages=[j.get("_found_on") for j in cr["jobs"] if j.get("_found_on")])
        fields["js_rendered"] = int(any(x.get("embed") for x in cr.get("careers_external", [])
                                        if (urlsplit(x["url"]).hostname or "") in {b["host"] for b in det["boards"]}))
    if careers:
        fields["careers_url"] = careers
    if first_read:
        b, n = first_read
        fields.update(ats_type=b["ats"], ats_slug=b["slug"], board_url=board_url(b, specs), reader=reader_name(b["ats"], specs),
                      what_worked=f"{reader_name(b['ats'], specs)} feed {b['slug']} ({n} jobs); crawl {mode}")
    elif det["boards"]:
        b = next((x for x in det["boards"] if x.get("slug")), det["boards"][0])
        fields.update(ats_type=b["ats"], ats_slug=b.get("slug") or "", reader=reader_name(b["ats"], specs),
                      board_url=b["url"], what_worked=f"nothing yet: {b['label']} board found, no reader")
    blk.save_profile(con, domain, **fields)
    open_ = [f"#{r['id']} {r['kind']} {r['host'] or '-'}" + (f" ({r['ats']}:{r['slug'] or '?'})" if r["ats"] else "")
             for r in blk.open_blockers(con, domain)]
    summary = ("site profile used: crawl skipped" if mode.startswith("skipped") else
               "site profile saved" + (f"; reader {fields.get('reader')}" if fields.get("reader") else ""))
    return {"notes": notes, "open": open_, "summary": summary}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--careers", help="careers page URL if already known")
    ap.add_argument("--ats", action="append", default=[], help="confirmed board as type:slug, e.g. greenhouse:acme")
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--full-crawl", action="store_true", help="crawl the whole site (up to --max-pages) even when a feed is known")
    a = ap.parse_args()

    con = memory.connect()
    t0 = time.time()
    known = memory.company_by_domain(con, a.domain)
    known_jobs: dict[str, dict[str, str]] = {}
    open_by_source: dict[str, int] = {}
    if known:
        for j in con.execute("SELECT url, source_type, status, description_summary FROM jobs WHERE company_id=?", (known["id"],)):
            known_jobs.setdefault(j["source_type"], {})[normalize_url(j["url"])] = j["description_summary"] or ""
            if j["status"] == "Open":
                open_by_source[j["source_type"]] = open_by_source.get(j["source_type"], 0) + 1
    specs = spec_reader.active_specs(con)
    fps = blk.load_fingerprints()
    prof = blk.get_profile(con, known["domain"] if known else a.domain)
    feeds = Feeds(known_jobs, open_by_source, specs, known["domain"] if known else bare_domain(a.domain))

    # 1. Boards already confirmed: --ats (by the agent) and memory (an earlier scan).
    first: list[dict] = []
    for spec in a.ats:
        t, _, slug = spec.partition(":")
        first.append({"ats": t.lower(), "slug": slug, "evidence": "confirmed by agent (--ats)"})
    if known and known["ats_type"] and known["ats_slug"]:
        first.append({"ats": known["ats_type"], "slug": known["ats_slug"], "evidence": "memory (earlier scan)"})
    if prof and prof["ats_type"] and prof["ats_slug"]:
        first.append({"ats": prof["ats_type"], "slug": prof["ats_slug"], "evidence": "site profile (learned)"})
    careers_hint = a.careers or (prof["careers_url"] if prof and prof["careers_url"] else None) or (known["career_page"] if known else None)
    feed_first = bool(feeds.take(first)) and not a.full_crawl
    mode = "full"
    if feed_first and prof and prof["careers_url"] and not prof["own_site_jobs"] and profile_fresh(prof):
        # The profile says every job came from the board last time: read it and skip the crawl.
        mode = "skipped (site profile)"
        progress.sub(f"site profile: jobs come from {prof['reader'] or prof['ats_type']} ({prof['ats_slug']}); the crawl is skipped")
        cr = skipped_crawl(known["domain"] if known else bare_domain(a.domain), known, prof)
    elif feed_first:
        mode = "light"
        progress.sub(f"feed answered first; light check of the site ({LIGHT_PAGES} pages, no sitemap)")
        cr = crawl(a.domain, min(LIGHT_PAGES, a.max_pages), careers_hint, sitemaps=False)
    else:
        # 2. Crawl, stopping once a careers page links a board we can read.
        site = known["domain"] if known else bare_domain(a.domain)
        stop = None if a.full_crawl else (lambda urls: bool(readable(blk.detect_boards(urls, site, specs, fps)["boards"], specs)))
        cr = crawl(a.domain, a.max_pages, careers_hint, stop_on=stop)
        if cr.get("stopped_early"):
            mode = "stopped at ATS link"
    if cr.get("error"):
        progress.fail_exit(f"crawl stopped: {cr['error']}")

    def detection(c: dict) -> dict:
        urls = [x["url"] for x in c["ats_links"]] + [x["url"] for x in c.get("careers_external", [])]
        return blk.detect_boards(urls, c["domain"], specs, fps)

    feeds.take(atsmod.detect([x["url"] for x in cr["ats_links"]]))
    det = detection(cr)
    feeds.take(det["boards"])
    if cr.get("stopped_early") and not feeds.rows:
        # 3. The linked board gave nothing: crawl the whole site after all.
        progress.sub("the linked board gave no jobs; crawling the whole site")
        cr = crawl(a.domain, a.max_pages, careers_hint)
        mode = "full (board empty)"
        if cr.get("error"):
            progress.fail_exit(f"crawl stopped: {cr['error']}")
        feeds.take(atsmod.detect([x["url"] for x in cr["ats_links"]]))
        det = detection(cr)
        feeds.take(det["boards"])

    # Companies are keyed on their final domain: a domain that redirects (humancloud.co.in ->
    # humancloud.dev) becomes an alias, and a company stored under it earlier is merged in.
    domain = cr["domain"]
    if domain != bare_domain(a.domain):
        cid = memory.upsert_company(con, a.company, domain)
        moved = memory.merge_company(con, a.domain, domain)
        print(f"note: {a.domain} redirects to {domain}; company stored under {domain} ({bare_domain(a.domain)} is an alias; merged {moved})")
    cid = memory.upsert_company(con, a.company, domain)
    run_id = memory.start_run(con, cid, f"scan.py ({mode} crawl)")
    rejected = memory.rejected_urls(con, cid)

    for p in cr["pages"]:
        kind = "careers" if p.get("careers") else "page"
        memory.add_route(con, cid, p["final_url"], kind, p["status"])
    for j in cr["jobs"]:
        memory.add_route(con, cid, j["Job / Post URL"], "job", 200)
    for b in feeds.read:
        memory.add_route(con, cid, board_url(b, specs), "ats", 200)
        memory.upsert_company(con, a.company, domain, ats_type=b["ats"], ats_slug=b["slug"])

    rows, complete, notes = list(feeds.rows), list(feeds.complete), list(feeds.notes)
    seen_boards = {(b["ats"], b["slug"]) for b in feeds.read}
    # Prefer the ATS feed over the crawl: when a feed was read, the careers site is a front end for
    # it, so crawled job pages are dropped (they would be duplicates under a second URL). Roles on the
    # company's own site whose title is not on any board are kept as Careers Page rows.
    own_site = len(cr["jobs"])
    if rows:
        feed_titles = {tnorm(r["Job Title"]) for r in rows}
        own = [j for j in cr["jobs"] if tnorm(j["Job Title"]) not in feed_titles]
        for j in own:
            j.setdefault("Source Type", "Careers Page")
            j.setdefault("Posted By", "")
            j.setdefault("Application URL", j["Job / Post URL"])
            rows.append(j)
        own_site = len(own)
        notes.append(f"ATS feed preferred: {len(cr['jobs']) - len(own)} crawled job page(s) not used, {len(own)} kept (not on the board)")
        # The feed is the full list, so careers-page rows missing now count as missing, but only
        # when every feed was read in full.
        if not feeds.incomplete:
            complete.append("Careers Page")
    else:
        for j in cr["jobs"]:
            j.setdefault("Source Type", "Careers Page")
            j.setdefault("Posted By", "")
            j.setdefault("Application URL", j["Job / Post URL"])
            rows.append(j)
        if mode.startswith("full") and cr["careers_page"] and cr["jobs"] and not cr.get("career_urls_unvisited"):
            complete.append("Careers Page")

    official = cr.get("official_website") or f"https://{domain}/"
    careers = cr["careers_page"]
    # A board-hosted careers page is better than none.
    if not careers and seen_boards:
        careers = board_url(feeds.read[0], specs)
    memory.upsert_company(con, a.company, domain, official_website=official, career_page=careers)

    d = today()
    final = []
    for r in rows:
        if normalize_url(r["Job / Post URL"]) in rejected:
            continue
        r.update({
            "Company Name": a.company,
            "Official Website": official,
            "Career Page": careers,
            "Date Discovered": d,
            "Last Checked": d,
        })
        for k in ("Application URL", "Posted Date", "Description Summary", "Location / Work Mode"):
            r.setdefault(k, "")
        final.append(r)

    learned = learn(con, run_id, domain, cr, det, feeds, specs, prof, mode, careers, own_site, len(final), time.time() - t0)
    notes.extend(learned["notes"])

    RUNS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", a.company.lower()).strip("-")
    out = RUNS_DIR / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.rows.json"
    meta = {
        "company": a.company,
        "domain": domain,
        "site_domain": domain,
        "official_website": official,
        "careers_page": careers,
        "complete_sources": sorted(set(complete)),
        "incomplete_sources": sorted(set(feeds.incomplete)),
        "crawl_mode": mode,
        "run_id": run_id,
        "pages_fetched": cr["pages_fetched"],
        "requests": cr["requests"],
        "blocked_by_robots": cr["blocked_by_robots"],
        "sitemap_urls": cr["sitemap_urls"],
        "ats_links": cr["ats_links"][:20],
        "notes": notes,
        "blockers": learned["open"],
        "learning": learned["summary"],
        "seconds": round(time.time() - t0, 1),
    }
    out.write_text(json.dumps({"meta": meta, "rows": final}, indent=1))
    memory.finish_run(con, run_id, pages=cr["pages_fetched"], jobs_found=len(final))
    con.commit()
    print(json.dumps({k: meta[k] for k in ("company", "official_website", "careers_page", "complete_sources", "incomplete_sources", "crawl_mode", "pages_fetched", "notes", "blockers", "learning", "seconds")}, indent=1))
    print(f"rows: {len(final)}")
    progress.result(
        f"{cr['pages_fetched']} pages crawled ({mode}) in {meta['seconds']}s, careers page {careers or 'not found'}, "
        f"{len(seen_boards)} ATS board(s), {len(final)} roles found, {cr['blocked_by_robots']} URLs blocked by robots"
    )
    print(f"ROWS_FILE={out.relative_to(RUNS_DIR.parent)}")


if __name__ == "__main__":
    main()
