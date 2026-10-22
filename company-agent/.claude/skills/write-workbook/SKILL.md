---
name: write-workbook
description: Deduplicate a company's rows, merge them into memory.db with Open/Closed/Stale status, and upsert them into Company_Hiring_Intelligence.xlsx. Use after a scan.
---

# Write workbook

```bash
uv run python scripts/dedupe.py runs/<company>-<ts>.rows.json [runs/<company>-public.rows.json]
uv run python scripts/excel_upsert.py --rows runs/<company>-<date>.final.rows.json
```

- `dedupe.py` refuses invalid rows; dedupes by normalised URL (else company + title + location,
  across sources only); prefers ATS > careers page > public index > post; keeps the first Date
  Discovered; closes roles that vanished from a fully enumerated source; marks off-site rows
  Stale after 60 days; prints `FINAL_FILE=`.
- `excel_upsert.py` writes one sheet per company (safe name of at most 31 characters, stored in
  memory so it never changes), header row, frozen header, filters, column widths, clickable URLs,
  status colours. A rerun updates rows in place; it never duplicates.
- The PreToolUse hook re-validates `--rows` before the write and blocks it on any error. Fix the
  rows (or reject the source); never bypass the hook.
- If the workbook is open in Excel (`~$Company_Hiring_Intelligence.xlsx` exists) the write stops;
  ask the user to close it.

## Checkpoints

Steps 4 and 5 of /company-scan (`progress.py start 4`, then `start 5`). `dedupe.py` and `excel_upsert.py` print their own `…` lines and close their steps with `✓ Step 4/6 · ...` and `✓ Step 5/6 · ...`, or `✗` with the reason.
