---
name: recruiter-feedback
description: Pull recruiter edits (Recruiter Status) from Candidate_Research.xlsx into memory.db, or record a score correction, so later runs learn from feedback. Use for "sync feedback", "I shortlisted...", "that score is wrong".
---

# Recruiter feedback

- Sync statuses from the workbook: `uv run scripts/memory.py sync-feedback --jd <jd_id>` (prints `{"synced": n}`;
  report n to the user as `✓ feedback synced · n status changes`)
- Record a correction: `uv run scripts/memory.py feedback --jd <jd_id> --key "<dedupe_key>" --score N --note "why"`
  (keys from `uv run scripts/memory.py seen --jd <jd_id>`)
- Before scoring a JD again, read feedback with
  `uv run scripts/memory.py feedback-list --jd <jd_id>` (the sqlite3 CLI is denied) and adjust: rejected profiles hint at
  over-weighted dimensions; shortlisted ones show what the recruiter values. Note the change in run notes.
