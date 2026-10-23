"""Create the Excel file (Candidate_Research.xlsx) when it does not exist yet, with its Evidence sheet and headers.

Recruit Desk runs this when the assistant is installed and at the start of every run, so the file is always there before the first
search. Each job description gets its own sheet on its first search. An existing file is never touched.

CLI: uv run python scripts/init_workbook.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excel_upsert  # noqa: E402
from common import WORKBOOK_PATH  # noqa: E402


def ensure(path: Path = WORKBOOK_PATH) -> bool:
    """True when the file was created, False when it was already there."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    excel_upsert.write_evidence_sheet(wb, [])
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


if __name__ == "__main__":
    print(f"created {WORKBOOK_PATH}" if ensure() else f"already there: {WORKBOOK_PATH}")
