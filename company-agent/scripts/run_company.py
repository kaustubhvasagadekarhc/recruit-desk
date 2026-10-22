"""One command for steps 2-5 of a /company-scan: scan -> dedupe -> excel upsert (improvement plan item 5).

It runs scan.py, dedupe.py and excel_upsert.py one after the other and prints only what a person
(or the agent) needs: the checkpoint lines (▶ / ✓ / ✗ / …) and a summary of at most 20 lines.
Row files stay on disk; nothing here prints rows, so the agent never has to open a runs/*.rows.json.

The rows are validated twice before the workbook is touched: dedupe.py and excel_upsert.py both
refuse a file that fails validate_rows (the same rules the PreToolUse hook applies).

CLI:
  uv run python scripts/run_company.py --company "Nordstrom" --domain nordstrom.com
       [--careers URL] [--ats workday:tenant.wdN/site ...] [--max-pages 150] [--full-crawl]
       [--public-rows runs/<company>-public.rows.json]   # step 3 output, when it was run
       [--reuse-scan]                                     # skip step 2: use the newest scan rows file

Step 3 (public hiring posts, Exa) needs the agent. Run it before this command and pass its rows file
with --public-rows; without it, step 3 is closed as skipped (unless it was already closed).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402
from common import ROOT, RUNS_DIR  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SHOW = ("▶", "✓", "✗", "…", "■", "note:", "not used to close")
MAX_TITLES = 10


def run_step(script: str, args: list[str]) -> dict[str, str]:
    """Run one pipeline script, echo its checkpoint lines, return its KEY=value lines."""
    p = subprocess.Popen([sys.executable, str(SCRIPTS / script), *args], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    keys: dict[str, str] = {}
    tail: list[str] = []
    for line in p.stdout:
        line = line.rstrip("\n")
        m = re.match(r"^([A-Z_]+)=(\S+)$", line)
        if m:
            keys[m.group(1)] = m.group(2)
        elif line.lstrip().startswith(SHOW):
            print(line, flush=True)
        if line.strip():
            tail = (tail + [line])[-8:]
    if p.wait() != 0:
        print(f"{script} exited with {p.returncode}; last lines:", *tail[-8:], sep="\n  ")
        sys.exit(p.returncode)
    return keys


def step_state(n: int) -> str:
    st = progress._load()
    return st["state"][n - 1] if st and len(st.get("state", [])) >= n else ""


def titles(xs: list[str]) -> str:
    more = f" (+{len(xs) - MAX_TITLES} more)" if len(xs) > MAX_TITLES else ""
    return "; ".join(xs[:MAX_TITLES]) + more if xs else "none"


def newest_scan(company: str) -> Path | None:
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    files = [f for f in RUNS_DIR.glob(f"{slug}-*.rows.json")
             if re.fullmatch(rf"{re.escape(slug)}-\d{{8}}-\d{{6}}\.rows\.json", f.name)]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--careers")
    ap.add_argument("--ats", action="append", default=[])
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--full-crawl", action="store_true")
    ap.add_argument("--public-rows", help="rows file written by the public-hiring-posts step")
    ap.add_argument("--reuse-scan", action="store_true", help="skip the scan; use the newest runs/<company>-<stamp>.rows.json")
    a = ap.parse_args()
    t0 = time.time()

    # Step 2: scan
    progress.start(2)
    if a.reuse_scan:
        f = newest_scan(a.company)
        if not f:
            progress.fail_exit(f"no earlier scan rows file for {a.company}; run without --reuse-scan")
        rows_file = str(f.relative_to(ROOT))
        progress.result(f"reused the earlier scan {rows_file}")
    else:
        args = ["--company", a.company, "--domain", a.domain, "--max-pages", str(a.max_pages)]
        if a.careers:
            args += ["--careers", a.careers]
        for spec in a.ats:
            args += ["--ats", spec]
        if a.full_crawl:
            args.append("--full-crawl")
        rows_file = run_step("scan.py", args).get("ROWS_FILE")
        if not rows_file:
            progress.fail_exit("scan.py printed no ROWS_FILE")
    scan_payload = json.loads((ROOT / rows_file).read_text())
    scan_meta = scan_payload.get("meta", {})
    if not scan_payload.get("rows"):
        # Nothing to write: say why before step 4 stops, so the blockers are not lost.
        print(f"  learning: {scan_meta.get('learning') or 'n/a'}")
        for b in (scan_meta.get("blockers") or [])[:5]:
            print(f"  blocker: {b}")
        if scan_meta.get("blockers"):
            print("  next: blockers.py list --domain <d>; forge a reader spec only for a public job list (company-scan skill, step 2b)")

    # Step 3: public hiring posts (the agent's job; only closed here)
    files = [rows_file]
    if a.public_rows:
        pub = ROOT / a.public_rows
        if not pub.exists():
            progress.fail_exit(f"--public-rows {a.public_rows} does not exist")
        files.append(a.public_rows)
        if step_state(3) != "done":
            n = len(json.loads(pub.read_text()).get("rows", []))
            progress.result(f"{n} public listing(s) from {a.public_rows}", 3)
    elif step_state(3) not in ("done", "failed"):
        progress.result("skipped: no public rows file passed (--public-rows)", 3)

    # Step 4: dedupe + memory
    progress.start(4)
    final_file = run_step("dedupe.py", files).get("FINAL_FILE")
    if not final_file:
        progress.fail_exit("dedupe.py printed no FINAL_FILE")
    fmeta = json.loads((ROOT / final_file).read_text())
    meta, out_rows = fmeta.get("meta", {}), fmeta.get("rows", [])

    # Step 5: validate + write Excel (excel_upsert.py validates the rows itself)
    progress.start(5)
    run_step("excel_upsert.py", ["--rows", final_file])

    counts: dict[str, int] = {}
    for r in out_rows:
        counts[r["Status"]] = counts.get(r["Status"], 0) + 1
    print("")
    print(f"SUMMARY {meta.get('company') or a.company} ({meta.get('domain') or a.domain}), {round(time.time() - t0)}s total")
    print(f"  scan: {scan_meta.get('crawl_mode', '?')} crawl, {scan_meta.get('pages_fetched', '?')} pages, {scan_meta.get('seconds', '?')}s")
    print(f"  careers page: {scan_meta.get('careers_page') or 'not found'}")
    print(f"  complete sources: {', '.join(meta.get('complete_sources') or []) or 'none'}")
    if meta.get("incomplete_sources"):
        print(f"  read incompletely (no job closed from them): {', '.join(meta['incomplete_sources'])}")
    print(f"  jobs: {len(out_rows)} (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")")
    print(f"  new ({len(meta.get('new_titles') or [])}): {titles(meta.get('new_titles') or [])}")
    print(f"  closed ({len(meta.get('closed_titles') or [])}): {titles(meta.get('closed_titles') or [])}")
    print(f"  missing once ({len(meta.get('missing_once_titles') or [])}, Closed if missing again): {titles(meta.get('missing_once_titles') or [])}")
    for n in (scan_meta.get("notes") or [])[:3]:
        print(f"  note: {str(n)[:160]}")
    if scan_meta.get("learning"):
        print(f"  learning: {scan_meta['learning']}")
    bl = scan_meta.get("blockers") or []
    if bl:
        print(f"  open blockers ({len(bl)}): {'; '.join(bl[:4])}" + (" …" if len(bl) > 4 else ""))
        print("  next: blockers.py list --domain <d>; forge a spec only for a public job list (company-scan skill, step 2b)")
    print(f"  files (do not open; use memory.py jobs {meta.get('domain') or a.domain} --limit 20): {rows_file}, {final_file}")


if __name__ == "__main__":
    main()
