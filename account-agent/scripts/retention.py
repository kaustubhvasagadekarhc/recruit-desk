"""Retention, dry run only: list account rows whose Last Checked is older than N days (default 180).

  uv run python scripts/retention.py [--days 180]

Rows in Account_Research.xlsx hold personal data (leaders' names, titles and public bios). This script
only lists the rows due for review; it changes nothing and has no delete mode. A human removes a row
by hand in Excel (and may delete memory.db to clear the facts it remembers).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import COLUMNS, F_CHECKED, F_COMPANY, WORKBOOK_PATH  # noqa: E402


def due_rows(workbook: Path, days: int) -> list[tuple[str, int, str, str]]:
    from openpyxl import load_workbook

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    wb = load_workbook(workbook, read_only=True)
    out = []
    try:
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = [str(h or "") for h in next(it, [])]
            if header[: len(COLUMNS)] == COLUMNS:
                ci, cc = header.index(F_CHECKED), header.index(F_COMPANY)
            elif "Last Verified" in header and "Name" in header:  # Contacts sheet: one row per person
                ci, cc = header.index("Last Verified"), header.index("Name")
            else:
                continue
            for r, vals in enumerate(it, 2):
                seen = str(vals[ci] or "")[:10] if ci < len(vals) else ""
                if seen and seen < cutoff:
                    out.append((ws.title, r, str(vals[cc] or ""), seen))
    finally:
        wb.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--workbook", default=str(WORKBOOK_PATH))
    a = ap.parse_args()
    wb = Path(a.workbook)
    if not wb.exists():
        print(f"no workbook at {wb.name}; nothing to review")
        return
    hits = due_rows(wb, a.days)
    print(f"retention dry run: {len(hits)} row(s) last checked more than {a.days} days ago")
    for sheet, r, name, seen in hits:
        print(f"  {sheet} · row {r} · {name} · last checked {seen}")
    print("nothing changed; a human reviews these rows and deletes any no longer needed")


if __name__ == "__main__":
    main()
