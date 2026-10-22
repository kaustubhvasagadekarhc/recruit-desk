"""Development Requirement (current), option A1: reuse company-agent for open roles.

Runs company-agent's own pipeline (`uv run python scripts/run_company.py --company <Name> --domain <d>`)
with its working directory set to ../company-agent, so that agent keeps its own memory, hook and
workbook. Then reads ../company-agent/Company_Hiring_Intelligence.xlsx READ-ONLY and summarises the
company's open engineering roles. Nothing in company-agent is modified by this script.

A scan newer than --max-age days (default 7) is reused without running company-agent again.

  uv run python scripts/roles_from_company_agent.py --company "Razorpay" --domain razorpay.com [--refresh] [--max-age 7]
  uv run python scripts/roles_from_company_agent.py --company "Razorpay" --domain razorpay.com --no-scan   # read only

Writes runs/<slug>.roles.json ({"cell", "source", "open", "engineering", ...}) for assemble.py and prints
a one-line result. A missing company-agent, a failed scan or no sheet leaves the cell blank with the reason.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402
from common import COMPANY_AGENT, RUNS_DIR, bare_domain, slugify  # noqa: E402

WORKBOOK = "Company_Hiring_Intelligence.xlsx"
# Where the company assistant keeps its Excel file: Recruit Desk's data folder when it says so, else the assistant's own folder.
COMPANY_WORKBOOK_DIR = (Path(os.environ["RECRUIT_DESK_DATA_ROOT"]) / "Company jobs") if os.environ.get("RECRUIT_DESK_DATA_ROOT") else COMPANY_AGENT
SCAN_TIMEOUT = 25 * 60
ENG = re.compile(
    r"(?i)\b(engineer\w*|developer|dev|sde|sdet|programmer|architect|devops|sre|site reliability|qa|quality assurance|"
    r"test(ing)?|automation|data scien\w*|data engineer\w*|machine learning|ml|ai|mlops|security|infosec|"
    r"cloud|platform|backend|back-end|frontend|front-end|full[- ]?stack|android|ios|mobile|firmware|embedded|"
    r"technical lead|tech lead|engineering manager|cto|database|dba|analytics engineer)\b"
)
AREAS = [
    ("backend", r"back[- ]?end|java\b|golang|\bgo\b|python|node|api|server"),
    ("frontend", r"front[- ]?end|react|angular|\bui\b|web developer"),
    ("full stack", r"full[- ]?stack"),
    ("mobile", r"android|ios|mobile|flutter|react native"),
    ("data / ML", r"data|machine learning|\bml\b|\bai\b|analytics|scientist"),
    ("DevOps / SRE / cloud", r"devops|sre|site reliability|cloud|platform|infra"),
    ("QA / test", r"\bqa\b|quality|test|sdet|automation"),
    ("security", r"security|infosec|appsec"),
    ("engineering leadership", r"manager|head|director|lead|principal|architect|vp"),
]
INDIA = re.compile(r"(?i)\b(india|bengaluru|bangalore|pune|mumbai|hyderabad|chennai|delhi|gurugram|gurgaon|noida|kolkata|ahmedabad|jaipur|kochi|coimbatore)\b")


def run_scan(company: str, domain: str) -> tuple[bool, str]:
    """Run company-agent's pipeline in its own folder; relay its ✓ / ✗ lines as sub-progress."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "CLAUDE_PROJECT_DIR")}
    if os.environ.get("RECRUIT_DESK_DATA_ROOT"):
        env["RECRUIT_DESK_WORKBOOK_DIR"] = str(COMPANY_WORKBOOK_DIR)   # the company assistant writes its file in its own folder, not the accounts folder
    cmd = ["uv", "run", "python", "scripts/run_company.py", "--company", company, "--domain", domain]
    try:
        p = subprocess.Popen(cmd, cwd=COMPANY_AGENT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"cannot start company-agent: {e}"
    tail: list[str] = []
    try:
        for line in p.stdout:
            line = line.rstrip()
            if line.lstrip().startswith(("✓", "✗")):
                progress.sub("company-agent " + line.strip())
            if line.strip():
                tail = (tail + [line.strip()])[-4:]
        code = p.wait(timeout=SCAN_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.kill()
        return False, "company-agent scan timed out"
    return code == 0, "" if code == 0 else f"company-agent exited {code}: " + " | ".join(tail)[-300:]


def read_sheet(company: str, domains: set[str]) -> tuple[list[dict], str]:
    """Rows of the company's sheet in company-agent's workbook (read-only), and the sheet name."""
    path = COMPANY_WORKBOOK_DIR / WORKBOOK
    if not path.exists():
        return [], ""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        fallback = None
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = [str(h or "") for h in next(it, [])]
            if "Job Title" not in header:
                continue
            rows = [dict(zip(header, r)) for r in it if any(r)]
            sites = {bare_domain(str(r.get("Official Website") or "")) for r in rows[:50]}
            if sites & domains:
                return rows, ws.title
            if ws.title.lower() == company.lower()[:31]:
                fallback = (rows, ws.title)
        return fallback or ([], "")
    finally:
        wb.close()


def summarise(rows: list[dict]) -> dict:
    open_rows = [r for r in rows if str(r.get("Status") or "") == "Open"]
    eng = [r for r in open_rows if ENG.search(str(r.get("Job Title") or ""))]
    areas: Counter = Counter()
    for r in eng:
        t = str(r.get("Job Title") or "").lower()
        hit = next((name for name, rx in AREAS if re.search(rx, t)), "other engineering")
        areas[hit] += 1
    locs: Counter = Counter()
    for r in eng:
        loc = str(r.get("Location / Work Mode") or "").strip()
        if loc:
            locs[re.split(r"[,;/|(]", loc)[0].strip() or loc] += 1
    india = any(INDIA.search(str(r.get("Location / Work Mode") or "")) for r in eng)
    checked = max((str(r.get("Last Checked") or "")[:10] for r in rows), default="")
    src = next((str(r.get("Career Page")) for r in rows if r.get("Career Page")), "") or \
        next((str(r.get("Job / Post URL")) for r in open_rows if r.get("Job / Post URL")), "")
    return {"open": len(open_rows), "engineering": len(eng), "areas": areas.most_common(4), "locations": locs.most_common(4),
            "india": india, "checked": checked, "source": src,
            "titles": [str(r.get("Job Title")) for r in eng[:10]]}


def cell_text(s: dict) -> str:
    if not s["open"]:
        return f"No open roles listed on the official careers page or ATS (company-agent scan {s['checked']})"
    parts = [f"{s['engineering']} open engineering roles of {s['open']} open roles (company-agent scan {s['checked']})"]
    if s["areas"]:
        parts.append("areas: " + ", ".join(f"{a} {n}" for a, n in s["areas"]))
    if s["locations"]:
        parts.append("locations: " + ", ".join(f"{loc} {n}" for loc, n in s["locations"]))
    if s["engineering"]:
        parts.append("hiring engineers in India: " + ("yes" if s["india"] else "no"))
    return " · ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--refresh", action="store_true", help="scan again even if a recent scan exists")
    ap.add_argument("--no-scan", action="store_true", help="only read company-agent's workbook")
    ap.add_argument("--max-age", type=int, default=7)
    a = ap.parse_args()
    domain = bare_domain(a.domain)
    out = RUNS_DIR / f"{slugify(a.company)}.roles.json"
    RUNS_DIR.mkdir(exist_ok=True)

    def finish(result: dict, line: str, ok: bool = True) -> None:
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"ROLES_FILE={out.relative_to(out.parent.parent).as_posix()}")
        (progress.result if ok else progress.failed)(line)

    if not (COMPANY_AGENT / "scripts" / "run_company.py").exists():
        finish({"cell": "", "source": "", "reason": "company-agent not found next to account-agent"},
               "skipped: company-agent folder not found; Development Requirement left blank", ok=False)
        return
    rows, sheet = read_sheet(a.company, {domain})
    fresh_cutoff = (date.today() - timedelta(days=a.max_age)).isoformat()
    recent = rows and max(str(r.get("Last Checked") or "")[:10] for r in rows) >= fresh_cutoff
    scanned = False
    if not a.no_scan and (a.refresh or not recent):
        progress.sub(f"running company-agent for {a.company} ({domain}); this can take a few minutes")
        ok, why = run_scan(a.company, domain)
        scanned = True
        if not ok:
            progress.sub(why)
        rows, sheet = read_sheet(a.company, {domain})
    elif recent:
        progress.sub(f"reusing company-agent scan from {max(str(r.get('Last Checked') or '')[:10] for r in rows)} (newer than {a.max_age} days)")
    if not rows:
        finish({"cell": "", "source": "", "reason": "no company-agent sheet for this company"},
               "no roles read: company-agent has no sheet for this company; Development Requirement left blank", ok=False)
        return
    s = summarise(rows)
    s.update({"cell": cell_text(s), "sheet": sheet, "scanned_now": scanned})
    if not s["source"]:
        s["cell"] = ""
        s["reason"] = "no careers page or job URL to cite"
    finish(s, f"{s['engineering']} open engineering roles of {s['open']} open (sheet '{sheet}' in company-agent, read-only)")


if __name__ == "__main__":
    main()
