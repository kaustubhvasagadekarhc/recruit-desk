"""Blocker detection and learned site memory (self-learning loop, 2026-10-15).

- Fingerprints: schema/ats_fingerprints.json maps job-board hosts to ATS types (data only).
- detect_boards(urls, site_domain): scans the third-party URLs a careers page links, embeds or names
  in its scripts. A host in the registry becomes a board {ats, slug, reader}; reader is
  builtin:<ats> (scripts/ats.py), spec:<name> (an active reader spec) or '' (none yet). A job-board-
  looking host that is not in the registry becomes an unknown_ats_host blocker.
- site_profile: per domain, what worked last time (careers URL, board, reader, crawl stats), so the
  next run goes straight to the source.
- blockers: why a run could not read jobs (unknown_ats_host, no_reader, js_rendered, http_403,
  robots, empty_board, parse_fail); one open row per (domain, kind, host), resolved_by filled once a
  run or a trialled spec reads jobs from that source.

CLI (read-only except where noted):
  uv run python scripts/blockers.py list [--domain d] [--all]     # open blockers (or all)
  uv run python scripts/blockers.py profile <domain>              # the learned site profile
  uv run python scripts/blockers.py fingerprints                  # registry check + entries
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, bare_domain, now_iso  # noqa: E402
from spec_schema import validate_fingerprints  # noqa: E402

FP_PATH = ROOT / "schema" / "ats_fingerprints.json"
KINDS = ("unknown_ats_host", "no_reader", "js_rendered", "http_403", "robots", "empty_board", "parse_fail")

# Words that make a third-party URL look like a job board or job widget.
JOBBY = re.compile(r"(career|jobs?\b|jobs?[/._-]|recruit|hiring|talent|vacanc|opening|embedjobs|applicant|\bats\b|requisition)", re.I)
# Hosts that are never job boards (CDNs, analytics, social, fonts, consent, video).
IGNORE_HOST = re.compile(
    r"(^|\.)(google(apis|tagmanager|-analytics|usercontent|syndication)?\.[a-z.]+|gstatic\.com|doubleclick\.net|"
    r"facebook\.(com|net)|fbcdn\.net|twitter\.com|x\.com|t\.co|linkedin\.com|licdn\.com|lnkd\.in|youtube(-nocookie)?\.com|"
    r"youtu\.be|ytimg\.com|instagram\.com|cdninstagram\.com|tiktok\.com|pinterest\.com|vimeo\.com|cloudflare\.com|"
    r"cdnjs\.cloudflare\.com|jsdelivr\.net|unpkg\.com|jquery\.com|bootstrapcdn\.com|fontawesome\.com|typekit\.net|"
    r"gravatar\.com|wp\.com|wordpress\.(org|com)|w\.org|hotjar\.com|hubspot\.com|hs-scripts\.com|hs-analytics\.net|"
    r"hsforms\.(com|net)|hscollectedforms\.net|clarity\.ms|bing\.com|onetrust\.com|cookielaw\.org|cookiebot\.com|"
    r"recaptcha\.net|adobe\.com|adobedtm\.com|nitrocdn\.com|getnitropack\.com|cloudfront\.net|amazonaws\.com|"
    r"glassdoor\.[a-z.]+|indeed\.[a-z.]+|naukri\.com|wa\.me|whatsapp\.com|apple\.com|microsoft\.com|github\.com|"
    r"schema\.org|w3\.org|gmpg\.org|ogp\.me|zendesk\.com|intercom\.io|crisp\.chat|tawk\.to|calendly\.com)$",
    re.I,
)
URL_IN_TEXT = re.compile(r"""https?://[A-Za-z0-9.-]+(?::\d+)?(?:/[^\s"'<>\\)]*)?""")
MAX_URLS_PER_PAGE = 400


def load_fingerprints(path: Path = FP_PATH) -> list[dict]:
    """Entries of the registry; an invalid registry is reported and ignored, never half-used."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"note: fingerprint registry unreadable ({e}); only built-in ATS detection is used", file=sys.stderr)
        return []
    errs = validate_fingerprints(doc)
    if errs:
        print(f"note: fingerprint registry invalid ({errs[0]}); only built-in ATS detection is used", file=sys.stderr)
        return []
    out = []
    for fp in doc["fingerprints"]:
        out.append({**fp, "_host": re.compile(fp["host"]), "_slug": re.compile(fp["slug"]) if fp.get("slug") else None})
    return out


def urls_in_html(body: bytes | str, page_url: str) -> list[str]:
    """Absolute URLs named anywhere in a page, inline scripts included (khConfig = {domain: ...})."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    text = text.replace("\\/", "/")
    out, seen = [], set()
    for m in URL_IN_TEXT.finditer(text):
        u = m.group(0).rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= MAX_URLS_PER_PAGE:
            break
    return out


def same_site(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_boards(urls: list[str], site_domain: str, specs: dict[str, dict] | None = None,
                  fingerprints: list[dict] | None = None) -> dict:
    """Classify third-party URLs found on careers pages.

    Returns {"boards": [{ats, slug, host, url, reader, label}], "unknown": [{host, url}]}.
    `specs` maps ats name -> active spec (spec_reader.active_specs)."""
    import ats as atsmod  # built-in patterns and slugs

    fps = load_fingerprints() if fingerprints is None else fingerprints
    specs = specs or {}
    site = bare_domain(site_domain)
    boards: dict[tuple[str, str], dict] = {}
    unknown: dict[str, dict] = {}
    for u in urls:
        try:
            p = urlsplit(u)
        except ValueError:
            continue
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or not host or same_site(host, site) or IGNORE_HOST.search(host):
            continue
        fp = next((f for f in fps if f["_host"].fullmatch(host)), None)
        builtin_hits = atsmod.detect([u])
        if builtin_hits:
            for b in builtin_hits:
                boards.setdefault((b["ats"], b["slug"].lower()), {
                    "ats": b["ats"], "slug": b["slug"], "host": host, "url": u, "reader": f"builtin:{b['ats']}",
                    "label": atsmod.SOURCE[b["ats"]].removeprefix("ATS: ")})
            continue
        if fp:
            if fp.get("builtin"):
                continue  # a built-in vendor URL without a board slug (assets, API host)
            m = fp["_slug"].match(u) if fp["_slug"] else None
            if fp["_slug"] and not m:
                continue  # a vendor URL that is not a board (CDN asset, marketing site)
            slug = m.group("slug") if m else None
            spec = specs.get(fp["ats"])
            if spec and slug and not re.fullmatch(spec["slug_pattern"], slug):
                slug = None
            key = (fp["ats"], (slug or "").lower())
            if key not in boards or (slug and not boards[key]["slug"]):
                boards[key] = {"ats": fp["ats"], "slug": slug, "host": host, "url": u,
                               "reader": f"spec:{spec['name']}" if spec and slug else "",
                               "label": fp.get("label") or fp["ats"]}
            continue
        if JOBBY.search(host) or JOBBY.search(p.path or ""):
            unknown.setdefault(host, {"host": host, "url": u})
    # A slug-less entry is noise when the same ATS also gave a board with a slug.
    with_slug = {b["ats"] for b in boards.values() if b["slug"]}
    out = [b for b in boards.values() if b["slug"] or b["ats"] not in with_slug]
    return {"boards": out, "unknown": list(unknown.values())}


# ---- site_profile ---------------------------------------------------------------------------------
def get_profile(con, domain: str):
    try:
        return con.execute("SELECT * FROM site_profile WHERE domain=?", (bare_domain(domain),)).fetchone()
    except Exception:
        return None


def save_profile(con, domain: str, **fields) -> None:
    d = bare_domain(domain)
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = now_iso()
    if "job_pages" in fields and not isinstance(fields["job_pages"], str):
        fields["job_pages"] = json.dumps(list(dict.fromkeys(fields["job_pages"]))[:20])
    if con.execute("SELECT 1 FROM site_profile WHERE domain=?", (d,)).fetchone():
        con.execute(f"UPDATE site_profile SET {', '.join(f'{k}=?' for k in fields)} WHERE domain=?", (*fields.values(), d))
    else:
        cols = {"domain": d, **fields}
        con.execute(f"INSERT INTO site_profile ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    con.commit()


# ---- blockers -------------------------------------------------------------------------------------
def record(con, run_id: int | None, domain: str, kind: str, host: str = "", evidence_url: str = "",
           detail: str = "", ats: str | None = None, slug: str | None = None) -> int:
    """Insert an open blocker, or refresh the open one with the same (domain, kind, host)."""
    if kind not in KINDS:
        raise ValueError(f"unknown blocker kind {kind}")
    d, ts = bare_domain(domain), now_iso()
    row = con.execute("SELECT id FROM blockers WHERE domain=? AND kind=? AND host=? AND resolved_by IS NULL", (d, kind, host)).fetchone()
    if row:
        con.execute("""UPDATE blockers SET run_id=?, last_seen=?, seen_count=seen_count+1,
                       evidence_url=COALESCE(NULLIF(?, ''), evidence_url), detail=COALESCE(NULLIF(?, ''), detail),
                       ats=COALESCE(?, ats), slug=COALESCE(?, slug) WHERE id=?""",
                    (run_id, ts, evidence_url, detail[:500], ats, slug, row["id"]))
        bid = row["id"]
    else:
        bid = con.execute("""INSERT INTO blockers (run_id, domain, kind, host, ats, slug, evidence_url, detail, first_seen, last_seen)
                             VALUES (?,?,?,?,?,?,?,?,?,?)""", (run_id, d, kind, host, ats, slug, evidence_url, detail[:500], ts, ts)).lastrowid
    con.commit()
    return bid


def resolve(con, domain: str, resolved_by: str, host: str | None = None, kinds: tuple[str, ...] | None = None,
            blocker_id: int | None = None) -> int:
    """Mark open blockers resolved; returns how many."""
    q = "UPDATE blockers SET resolved_by=?, resolved_at=? WHERE resolved_by IS NULL AND domain=?"
    args: list = [resolved_by, now_iso(), bare_domain(domain)]
    if blocker_id is not None:
        q += " AND id=?"
        args.append(blocker_id)
    if host is not None:
        q += " AND (host=? OR host='')"
        args.append(host)
    if kinds:
        q += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += list(kinds)
    n = con.execute(q, args).rowcount
    con.commit()
    return n


def open_blockers(con, domain: str | None = None, include_resolved: bool = False) -> list[dict]:
    q = "SELECT * FROM blockers WHERE 1=1"
    args: list = []
    if not include_resolved:
        q += " AND resolved_by IS NULL"
    if domain:
        q += " AND domain=?"
        args.append(bare_domain(domain))
    return [dict(r) for r in con.execute(q + " ORDER BY id DESC LIMIT 50", args)]


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--domain")
    ls.add_argument("--all", action="store_true")
    pr = sub.add_parser("profile")
    pr.add_argument("domain")
    sub.add_parser("fingerprints")
    a = ap.parse_args()
    if a.cmd == "fingerprints":
        doc = json.loads(FP_PATH.read_text())
        errs = validate_fingerprints(doc)
        print("registry ok" if not errs else "registry INVALID:\n  " + "\n  ".join(errs))
        for fp in doc.get("fingerprints", []):
            print(f"  {fp['ats']:16} {'builtin' if fp.get('builtin') else 'spec   '} {fp['host']}")
        return
    import memory

    con = memory.connect()
    if a.cmd == "list":
        rows = open_blockers(con, a.domain, a.all)
        for r in rows:
            print(f"#{r['id']} {r['domain']} {r['kind']} host={r['host'] or '-'} ats={r['ats'] or '-'} slug={r['slug'] or '-'} "
                  f"seen={r['seen_count']} {'RESOLVED by ' + r['resolved_by'] if r['resolved_by'] else 'open'}\n    evidence: {r['evidence_url'] or '-'}"
                  + (f"\n    {r['detail']}" if r["detail"] else ""))
        print(f"{len(rows)} blocker(s)")
    elif a.cmd == "profile":
        p = get_profile(con, a.domain)
        print(json.dumps(dict(p), indent=1) if p else f"no site profile for {a.domain}")


if __name__ == "__main__":
    _main()
