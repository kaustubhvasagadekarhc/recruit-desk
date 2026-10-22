"""Re-scan every watched company, then write the digest (improvement plan item 8, 2026-10-18).

uv run python scripts/rescan_watchlist.py [--domain razorpay.com ...] [--since 7d] [--no-digest]

One command for a weekly job: it runs scripts/run_company.py for each company on the watchlist, one after the other
(the scan itself is polite: ATS feed first, robots.txt obeyed, one request a second per host), keeps going when one
company fails, then runs scripts/digest.py.
Exit status: 0 all scanned, 2 some failed (the others are saved), 1 none worked. No LLM and no Exa search are involved, so the public-posts step is skipped.
Schedule it with whatever scheduler you use; this script does not install one.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
import progress  # noqa: E402
from common import ROOT, bare_domain  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent


def run_one(name: str, domain: str) -> tuple[bool, str]:
    """Scan one company with run_company.py. Returns (ok, one line for the report)."""
    p = subprocess.run([sys.executable, str(SCRIPTS / "run_company.py"), "--company", name, "--domain", domain],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (p.stdout or "").splitlines()
    jobs = next((re.sub(r"\s+", " ", l).strip() for l in lines if l.strip().startswith("jobs:")), "")
    new = next((re.sub(r"\s+", " ", l).strip() for l in lines if l.strip().startswith("new (")), "")
    if p.returncode != 0:
        tail = [l.strip() for l in (lines + (p.stderr or "").splitlines()) if l.strip()][-1:] or ["no output"]
        return False, tail[0][:160]
    return True, "; ".join(x for x in (jobs, new[:100]) if x) or "scanned"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", action="append", default=[], help="only these watched domains")
    ap.add_argument("--since", default="7d", help="digest period (default 7d)")
    ap.add_argument("--no-digest", action="store_true")
    a = ap.parse_args()
    con = memory.connect()
    rows = memory.watch_list(con)
    if a.domain:
        want = {bare_domain(d) for d in a.domain}
        rows = [r for r in rows if r["domain"] in want]
    if not rows:
        sys.exit(f"nothing to scan: none of {', '.join(a.domain)} is on the watchlist" if a.domain
                 else "nothing to scan: the watchlist is empty (uv run python scripts/memory.py watch add <domain>)")
    t0 = time.time()
    failed = 0
    for i, r in enumerate(rows, 1):
        progress.sub(f"scanning {i}/{len(rows)} · {r['name']} ({r['domain']})")
        ok, line = run_one(r["name"], r["domain"])
        failed += not ok
        print(f"{'✓' if ok else '✗'} {r['domain']}: {line}", flush=True)
    progress.result(f"{len(rows) - failed}/{len(rows)} companies scanned in {round(time.time() - t0)}s"
                    + (f", {failed} failed (the others are saved)" if failed else ""))
    if not a.no_digest:
        sys.argv = ["digest.py", "--since", a.since]
        import digest
        digest.main()
    # 0 all scanned, 2 some failed (the rest are saved), 1 none worked: a scheduler can tell them apart
    sys.exit(0 if not failed else 1 if failed == len(rows) else 2)


if __name__ == "__main__":
    main()
