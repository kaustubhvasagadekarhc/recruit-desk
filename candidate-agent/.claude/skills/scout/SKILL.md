---
name: scout
description: Run the full candidate research pipeline for a job description (pasted text, file path or URL) - parse, discover via Exa, score, dedupe, validate and upsert into Candidate_Research.xlsx. Use for "/scout", "find candidates for this JD", "source candidates".
argument-hint: <JD text | path | URL> [--target N]
---

# /scout — candidate research for one JD

Input: `$ARGUMENTS` (JD text, a file path inside this folder, or a public URL). Default target: 10
scored candidates; honour `--target N`.

## Visible checkpoints (always)

The user must see at every moment which step the run is on. All checkpoint lines go through
`scripts/progress.py` (one shared helper), which prints them and appends them, timestamped, to
`runs/<YYYY-MM-DD-HHMM>-<jd_id>/progress.log`.

- **Before anything else**: pick the `jd_id` slug (role + location, see `/parse-jd`), run
  `uv run scripts/memory.py init`, then `uv run scripts/progress.py plan --slug <jd_id>`. It prints the
  numbered 6-step plan. Then create **one task per step** in Claude Code's task list (TaskCreate, or
  TodoWrite where that is the available tool), with the same six titles, so the checklist shows in the UI.
- **Before each step**: mark its task in progress and run `uv run scripts/progress.py start <N>` →
  `▶ Step N/6 · <label>`.
- **After each step**: the step's script prints `✓ Step N/6 · <real counts>` itself (add-jd, score.py,
  dedupe.py, excel_upsert.py, run-finish). Only steps without a closing script (step 2) are closed by
  hand: `uv run scripts/progress.py done 2 "<n> profiles found, <m> dropped (<why>)"`. Then mark the task
  completed. Echo the ▶ / ✓ / … lines to the user in your message text as you go — tool output alone can
  be collapsed in the UI.
- **Long steps** print sub-progress (`  … scoring 7/15 · <name> 88`). For your own loops (searches,
  subagent work) use `uv run scripts/progress.py sub "<text>"`.
- **On failure**: `uv run scripts/progress.py fail <N> "<reason>"` (scripts do this themselves when they
  exit with an error) → `✗ Step N/6 · <reason>`. Then fix honestly and rerun the step, or stop and ask.
  Never skip a step silently.
- `uv run scripts/progress.py status` reprints the plan with ticks.

## The six steps

1. **Parsing the JD** — `start 1`, follow `/parse-jd`. `memory.py add-jd` prints the ✓ line with the sheet
   name. Then `uv run scripts/memory.py seen --jd <jd_id>` and `synonyms list "<title>"` (known candidates
   are refreshed, not re-added). Then `uv run scripts/memory.py plan-queries --jd <jd_id> --open-to-work`: it prints each
   planned Exa query (`… planned query i/n · <query>`, built from the title, role synonyms, city and mandatory
   skills, plus one optional `"open to work" …` query marked `(optional: open to work)`, which the user may drop);
   show the list to the user, who may edit it. Then
   `uv run scripts/memory.py run-start --jd <jd_id> --queries '<the final json list>'`; keep the run id.
2. **Discovering candidates (Exa search)** — `start 2`, follow `/discover-candidates` (delegate to
   `candidate-scout`; pass it the progress commands). Relay the subagent's checkpoint lines to the user,
   then `done 2 "<found> profiles found, <dropped> dropped"`. Output: `runs/<jd_id>-<YYYY-MM-DD>.json`.
3. **Scoring candidates** — `start 3`, follow `/score-candidates` (delegate to `candidate-scorer`, which
   prints `… scoring i/n` lines and finishes with `uv run scripts/score.py --input runs/<file>.json`; that
   prints the ✓ line). Relay its lines. Then the evidence-depth pass (CLAUDE.md, step 3):
   `uv run scripts/evidence.py plan --input runs/<file>.json`, one `web_fetch_exa` per planned URL (top 10, never
   LinkedIn/Indeed, never a name search), write each `corroboration`, then
   `uv run scripts/dev_evidence.py fetch --input runs/<file>.json` (GitHub / Stack Exchange APIs, only for profiles
   the candidate already links; no emails), rerun score.py.
4. **Deduplicating** — `start 4`, `uv run scripts/dedupe.py --input runs/<file>.json` (prints ✓ with
   kept / merged / already-in-memory counts).
5. **Validating and writing Excel** — `start 5`, `uv run scripts/excel_upsert.py --input runs/<file>.json --run <id>`
   (append `--contacts` only if the user asked for contact details)
   (dedupe is already done in step 4). If the hook blocks: `fail 5 "<reason>"`, fix the named rows (blank the
   field, lower the score or drop the row) — never invent data — and run step 5 again.
6. **Closing the run and reporting** — `start 6`, then
   `uv run scripts/memory.py run-finish --run <id> --notes "<queries, counts, gaps>"`. It prints the ✓ line
   and the closing summary (rows inserted / updated, sheet, workbook path, gaps, progress log path).
   Its summary has a line `open to work: N Yes, M Likely of R rows in the sheet`; repeat it in your report.
   Finish with a table of name, score, confidence, Open to Work, Stack Match, Experience Match, Location Match,
   URL (top first) and the gaps (for example: mandatory skill rarely evidenced, location thin).
