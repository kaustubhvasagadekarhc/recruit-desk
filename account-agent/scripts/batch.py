"""Batch mode for /account-scan --csv <file>: one company at a time, resumable from memory.db.

CSV: UTF-8 with a header row; columns `company` (required), `domain` (optional) and `list`
(optional sheet name; default Accounts). Column names are case-insensitive.

  uv run python scripts/batch.py plan --csv companies.csv [--days 14] [--refresh] [--list "Q4 US SaaS"]
      reads and checks the CSV, skips companies checked in the last N days (unless --refresh),
      writes runs/batch.json and prints the queue.
  uv run python scripts/batch.py next                 # NEXT company=... domain=... list=... n=i/N, or BATCH_DONE
  uv run python scripts/batch.py mark --company "<Name>" --state done|failed|skipped [--note "..."]
  uv run python scripts/batch.py status
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402
from common import DEFAULT_LIST, RUNS_DIR, bare_domain, safe_sheet_name, valid_domain  # noqa: E402

STATE = RUNS_DIR / "batch.json"
MAX_COMPANIES = 200


def read_csv(path: Path, default_list: str = DEFAULT_LIST) -> tuple[list[dict], list[str]]:
    """Companies from the CSV and the problems found. Duplicates (same domain, else same name) are dropped."""
    problems: list[str] = []
    items: list[dict] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = {c.strip().lower(): c for c in (rd.fieldnames or []) if c}
        if "company" not in cols:
            return [], ["the CSV needs a header row with a `company` column (optional: domain, list)"]
        for i, raw in enumerate(rd, 2):
            name = (raw.get(cols["company"]) or "").strip()
            dom = bare_domain(raw.get(cols.get("domain", ""), "") or "") if "domain" in cols else ""
            lst = (raw.get(cols["list"]) or "").strip() if "list" in cols else ""
            if not name:
                if any((v or "").strip() for v in raw.values()):
                    problems.append(f"line {i}: no company name, skipped")
                continue
            if dom and not valid_domain(dom):
                problems.append(f"line {i}: {name}: domain {dom!r} is not a website domain; it will be verified instead")
                dom = ""
            key = dom or name.lower()
            if key in seen:
                problems.append(f"line {i}: {name} appears twice, skipped")
                continue
            seen.add(key)
            items.append({"company": name, "domain": dom, "list": safe_sheet_name(lst or default_list), "state": "todo", "note": ""})
            if len(items) > MAX_COMPANIES:
                return items[:MAX_COMPANIES], problems + [f"more than {MAX_COMPANIES} companies; only the first {MAX_COMPANIES} are queued"]
    return items, problems


def recent_domains(days: int) -> dict[str, str]:
    try:
        import memory
        con = memory.connect()
    except Exception:  # noqa: BLE001 - no memory yet means nothing is recent
        return {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return {r["domain"]: r["last_checked"] for r in con.execute("SELECT domain, last_checked FROM companies WHERE last_checked>=?", (cutoff,))}


def plan(csv_path: Path, days: int, refresh: bool, default_list: str) -> dict:
    items, problems = read_csv(csv_path, default_list)
    recent = {} if refresh else recent_domains(days)
    for it in items:
        if it["domain"] and it["domain"] in recent:
            it["state"], it["note"] = "skipped", f"checked {recent[it['domain']]} (within {days} days; --refresh to redo)"
    st = {"csv": str(csv_path), "created": date.today().isoformat(), "items": items, "problems": problems}
    RUNS_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    return st


def _load() -> dict:
    if not STATE.exists():
        raise SystemExit("no batch planned; run `batch.py plan --csv <file>` first")
    return json.loads(STATE.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan"); p.add_argument("--csv", required=True); p.add_argument("--days", type=int, default=14)
    p.add_argument("--refresh", action="store_true"); p.add_argument("--list", default=DEFAULT_LIST)
    sp.add_parser("next")
    p = sp.add_parser("mark"); p.add_argument("--company", required=True)
    p.add_argument("--state", required=True, choices=["done", "failed", "skipped"]); p.add_argument("--note", default="")
    sp.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "plan":
        path = Path(a.csv)
        if not path.exists():
            raise SystemExit(f"{path} not found")
        st = plan(path, a.days, a.refresh, a.list)
        todo = [i for i in st["items"] if i["state"] == "todo"]
        print(f"Batch · {len(st['items'])} companies · {len(todo)} to scan · {len(st['items']) - len(todo)} skipped")
        for n, it in enumerate(st["items"], 1):
            print(f"  {n}. {it['company']} ({it['domain'] or 'domain to verify'}) · sheet {it['list']}" + (f" · skipped: {it['note']}" if it["state"] == "skipped" else ""))
        for pr in st["problems"][:10]:
            progress.sub(pr)
    elif a.cmd == "next":
        st = _load()
        items = st["items"]
        for n, it in enumerate(items, 1):
            if it["state"] == "todo":
                print(f"NEXT company={json.dumps(it['company'])} domain={it['domain'] or '-'} list={json.dumps(it['list'])} n={n}/{len(items)}")
                progress.sub(f"company {n}/{len(items)} · {it['company']}")
                return
        print("BATCH_DONE " + ", ".join(f"{s} {sum(1 for i in items if i['state'] == s)}" for s in ("done", "failed", "skipped")))
    elif a.cmd == "mark":
        st = _load()
        hit = next((i for i in st["items"] if i["company"].lower() == a.company.lower() and i["state"] == "todo"), None) or \
            next((i for i in st["items"] if i["company"].lower() == a.company.lower()), None)
        if not hit:
            raise SystemExit(f"{a.company!r} is not in the batch")
        hit["state"], hit["note"] = a.state, a.note
        STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{hit['company']}: {a.state}")
    else:
        st = _load()
        for n, it in enumerate(st["items"], 1):
            print(f"[{ {'todo': ' ', 'done': '✓', 'failed': '✗', 'skipped': '-'}[it['state']] }] {n}. {it['company']} ({it['domain'] or '?'}) {it['note']}")


if __name__ == "__main__":
    main()
