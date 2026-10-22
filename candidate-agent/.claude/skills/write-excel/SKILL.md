---
name: write-excel
description: Dedupe, validate and idempotently upsert a scored candidate batch into Candidate_Research.xlsx and memory.db. Use as the final write step of /scout, or to re-apply a batch.
---

# Write Excel

Inside a /scout run this is step 5 (`uv run scripts/progress.py start 5` first; dedupe already ran as
step 4, so skip item 1). Standalone, run all three; the scripts print the same ✓ / … lines without a step
number.

1. `uv run scripts/dedupe.py --input runs/<file>.json`
2. Optional preview: `uv run scripts/excel_upsert.py --input runs/<file>.json --dry-run`
3. `uv run scripts/excel_upsert.py --input runs/<file>.json --run <run_id>` — prints `… validated n rows`,
   `… writing i/n` and `✓ … inserted, … updated · sheet '<name>' now N rows`.

The PreToolUse hook validates the batch before step 3 runs: http(s) Profile URL, Source, `date_found`
YYYY-MM-DD, integer score 0–100, a breakdown, evidence notes, confidence High/Medium/Low, no 70+ on Low,
Email / Mobile only with a `contact_source_url` and no placeholder values, and the four key-factor columns
(2026-10-15) filled: Open to Work (Yes / Likely only with a quote that is in the evidence and is an open-to-work
signal), Stack Match, Experience Match, Location Match. A batch scored before 2026-10-15 needs a score.py rerun first.
The four columns go at the end of the sheet; older sheets get them appended on their next upsert. If blocked, print
`uv run scripts/progress.py fail 5 "<reason>"`, fix the data honestly (blank the field, lower the score,
or drop the row) and rerun.

Never edit the .xlsx any other way. Reruns update matching rows (same normalised Profile URL, else name
plus company) and keep Recruiter Status and Date Found. Do not name the upsert script in any other shell
command (grep, cat): the hook treats any command mentioning it as a write and asks for `--input`.
