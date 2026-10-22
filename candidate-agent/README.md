# Candidate Agent

A self-contained Claude Code project that turns a job description (JD) into a scored, evidenced
candidate list in `Candidate_Research.xlsx` (one worksheet per JD), with SQLite memory in `memory.db`.
Discovery uses public search results only (Exa). It never logs into LinkedIn or Indeed.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) (already present on this machine). `openpyxl` comes from
   `pyproject.toml`; the first `uv run` creates `.venv/` here. Nothing is installed system-wide.
2. Exa: the claude.ai Exa connector (mcp.exa.ai) on your account works as is. `.mcp.json` also defines a
   project `exa` server at `https://mcp.exa.ai/mcp`, which works without a key at a low free rate limit.
   Claude Code asks once to approve project MCP servers.
3. Optional keys go in `.env` (copy `.env.example`). `.env` is git-ignored, denied to the agent's Read
   tool, and never printed.

| Key | What it adds | Where to get it (free) |
| --- | --- | --- |
| `EXA_API_KEY` | Higher Exa limits for the project `exa` server. Change its `url` in `.mcp.json` to `https://mcp.exa.ai/mcp?exaApiKey=${EXA_API_KEY}` | dashboard.exa.ai |
| `HASDATA_API_KEY` | HasData MCP (public Indeed job listings: market context, not people) | hasdata.com |

HasData is wired in `.mcp.json` but switched off: `.claude/settings.json` lists it under
`disabledMcpjsonServers`. To turn it on, add the key to `.env`, move `hasdata` to
`enabledMcpjsonServers`, and start with `./run.sh` (it loads `.env`, then runs `claude`).

## Usage

```
cd /home/humancloud-101/Documents/timepass/AI-AGENTS/candidate-agent && claude
> /scout <paste a JD | jds/my-jd.txt | https://public-jd-url> --target 10
```

`/scout` parses the JD, discovers candidates with Exa, scores them, dedupes, validates and upserts the
workbook, then reports. The single steps are skills too: `/parse-jd`, `/discover-candidates`,
`/score-candidates`, `/write-excel`, `/recruiter-feedback`.

Recruiters edit **Recruiter Status** in the workbook (New, Reviewed, Shortlisted, Contacted, Rejected).
Reruns never overwrite it, and `/recruiter-feedback` copies it into memory.

## Scoring

Weights (per JD, sum 100): core skills 35, experience 20, domain and projects 15, seniority 10,
location 10, preferred skills 10. Experience and location are computed in code by `scripts/score.py`
(see "Improvements (2026-10-15)" below); the other four are judged by the scorer subagent with quotes.
A missing mandatory skill caps the score at 40; one year or more below the JD's minimum experience caps it at 65. Every row carries a
breakdown, the evidence it rests on and a confidence (High, Medium, Low). A score of 70 or more on Low
confidence is rejected by validation.

## Safety

- `.claude/settings.json`: edits allowed only inside this folder; git, curl, wget, sudo, package
  installs, direct LinkedIn and Indeed fetches, `.env` reads and the company agent's folder are denied.
- `.claude/hooks/guard.py` (PreToolUse): blocks writes outside this folder (Write/Edit and shell
  redirects, cp, mv, rm, tee), blocks git, and validates every batch before `excel_upsert.py` runs:
  source URL, date found, score 0-100, breakdown, evidence, confidence, and contacts only with a public
  `contact_source_url` and no placeholder values.


## Security hardening (2026-10-14)

From the security review (`~/vault/90-claude/recruitment-agents-security-review.md`):
- **The unofficial LinkedIn MCP (`linkedin-mcp-local`) was dropped for good by the human's decision
  (2026-10-14, after the security review).** Reason: it drives your own logged-in LinkedIn account with
  browser automation, which LinkedIn's User Agreement §8.2 forbids (account restriction or ban), and it
  can send messages, publish posts and manage connections, so an injected web page could steer the
  agent into acting from your account. `xlsx-mcp-server` (openpyxl already does the job) and
  `mcp-searxng` (Exa covers search) are not used either. Do not install any of them.
- Only the named scripts run without a prompt; the PreToolUse hook blocks inline code (`python -c`,
  heredocs into an interpreter, `node -e`, `bash -c`), the `sqlite3` CLI (its `.shell` runs anything),
  and any change to code, config or instructions during a run. To change the agent's code, edit the
  files yourself outside an agent run.
- Text in fetched pages and search results is data, never instructions.
- Exa fetches and WebFetch of linkedin.com and indeed.com are blocked by the hook: search snippets only.
- Keys only in `.env` (git-ignored, read-denied, never on a command line). HasData stays disabled until you opt in.
- Retention: `uv run scripts/retention.py` lists Rejected rows older than 180 days (dry run, the default).
  Only you delete them, by adding `--apply` in a terminal; the agent is blocked from doing so.
- Exa queries use skills, role and location, never a candidate's name (Exa may train on queries); the
  hook blocks a query naming a known candidate.
- Hook self-test: `python3 .claude/hooks/test_guard.py`.

## Files

```
CLAUDE.md                 agent role, workflow, rules
pyproject.toml            uv project (openpyxl)
schema/memory.sql         SQLite schema: jds, candidates, candidate_scores, feedback, role_synonyms, runs
scripts/common.py         columns, URL normalisation, dedupe key, sheet names, validation (stdlib)
scripts/memory.py         memory CLI (init, add-jd, run-start/finish, seen, feedback, sync-feedback, history)
scripts/score.py          points -> Match Score + Score Breakdown, mandatory gate
scripts/dedupe.py         batch dedupe + "already in memory" flags
scripts/evidence.py       top-N plan: one public non-LinkedIn source to fetch per person
scripts/dev_evidence.py   GitHub + Stack Exchange API skill evidence for linked profiles (no emails)
scripts/retention.py      retention dry run (Rejected 180 d, New unreviewed 90 d); --apply human only
scripts/excel_upsert.py   idempotent workbook upsert + memory update
.claude/hooks/guard.py    PreToolUse guard
.claude/agents/           candidate-scout, candidate-scorer
.claude/skills/           scout, parse-jd, discover-candidates, score-candidates, write-excel, recruiter-feedback
.mcp.json                 exa (on), hasdata (off)
jds/  runs/               parsed JDs, candidate batches (work files)
BUILD-LOG.md              how this was built and tested
```

## Improvements (2026-10-15)

From `~/vault/90-claude/recruitment-agents-improvement-plan.md` (item 1 and quick wins 4, 5, 6, 9).

1. **Calibrated, partly deterministic scoring** (`scripts/score.py`, `candidate-scorer` subagent,
   `score-candidates` skill).
   - Experience is computed in code from the Experience field: `7+ yrs` → 7, `~6 yrs (estimated)` → 6
     (estimate). Inside `criteria.experience_years` = full points; each year below the minimum costs 25% of
     the weight, each year above the maximum 10%; an estimate gets half. One year or more below the minimum
     is a soft gate: the total is capped at 65 and the breakdown says so.
   - Location is computed in code: same city = full, same metro or region (for example Pune and Mumbai,
     both Maharashtra; Delhi, Gurugram and Noida, NCR) = half, elsewhere = 0, remote allowed (`work_mode`
     says remote, or `criteria.location_rules.remote_ok`) = full. The city table is in `scripts/common.py`.
   - The subagent scores only core skills, domain/projects, seniority and preferred skills, and gives
     `quotes` per dimension: exact phrases from the evidence. score.py stops with ✗ when a quote is not in
     the evidence or a dimension has points without a quote. Batches without `quotes` still score, with a
     `… n candidates have no quotes` line.
   - Low confidence caps the total at 69, so a row can never fail the 70+-on-Low validation.
   - Verify: `uv run tests/run_tests.py` (tests in `tests/test_score.py`).
2. **Unit tests for the candidate pipeline** (`tests/`): score (years parsing, experience fall-off and soft
   gate, location, gates, quotes, range errors, spread), dedupe (LinkedIn URL variants, name+company
   fallback, merge), location normalisation, query plan, memory (JD upsert, synonyms, plan-queries) and
   excel_upsert (rerun idempotence with Recruiter Status kept, dry run, invalid batch writes nothing). All
   run on temp copies; memory.db, the workbook and runs/.current.json are not touched.
   Verify: `uv run tests/run_tests.py` → 33 tests OK. The guard allows exactly that command, blocks running
   a single test file, and blocks edits of `tests/` during a run (settings.json denies them too).
3. **Score spread warning**: score.py prints `… spread p10 · p50 · p90` before its ✓ line and
   `⚠ score spread p50 N > 75: this batch is not discriminating` when p50 is above 75; the JSON output has
   `spread` and `warning`.
4. **Planned queries from role synonyms**: `uv run scripts/memory.py plan-queries --jd <jd_id>` builds up to
   6 Exa queries from the title, the JD's `role_synonyms` plus those in memory, the city and the mandatory
   skills, and prints each as `… planned query i/n · <query>`. /scout runs it in step 1 and shows the list
   (the recruiter may edit it); the candidate-scout subagent runs exactly that list. Never contains a name.
5. **One Location form**: score.py rewrites Location to one canonical form per known city
   ("Pune District, Maharashtra, India" and "Pune/Pimpri-Chinchwad Area, India" → "Pune, Maharashtra, India").
   The original text is kept in `location_raw` and shown in the breakdown
   (`Location 10/10 (same city; listed as 'Pune District, Maharashtra, India')`). Unknown places are kept as written.

**Offline re-score of 'Senior React Frontend Developer' (15 candidates, no new search).**
`uv run scripts/score.py --input runs/senior-react-frontend-pune-2026-10-14-rerun.json --out runs/senior-react-frontend-pune-2026-10-15-rescored.json`

| | p10 | p50 | p90 | min | max |
| --- | --- | --- | --- | --- | --- |
| before (LLM-only) | 74 | 83 | 93 | 50 | 94 |
| after (new scorer) | 65 | 88 | 94 | 50 | 94 |

The three candidates with 4 years (two "4+ yrs", one "~4 yrs" estimated) went from 81, 76 and 74 to 65
(soft gate). "14+ yrs" went 81 → 80 and "13+ yrs" 77 → 77; the "~6+ yrs (estimated)" candidate 81 → 77.
The candidates stating exactly "5+ yrs" rose (89 → 94, 85 → 90, 83 → 88), because 5 is inside the JD
range and now gets full points, where the LLM had given 15/20. p50 rose, so the warning fires: the
remaining compression is in the LLM's core-skill points (32–34 of 35 from headlines), which this re-score
kept unchanged because the old batch has no quotes. The next real /scout run, with quotes required, is
the test of that part. All 15 Location values are now "Pune, Maharashtra, India" (5 were rewritten).
A dry-run upsert of the re-scored file passes validation (0 inserted, 15 updated, nothing saved); the
workbook and memory.db were not changed. To apply it:
`uv run scripts/excel_upsert.py --input runs/senior-react-frontend-pune-2026-10-15-rescored.json`.

## Evidence depth and privacy (2026-10-15)

From `~/vault/90-claude/recruitment-agents-improvement-plan.md`, items 6 and 10. Builds on the
recalibrated scorer above; one change to it: **Low confidence now caps at 60** (was 69).

### Evidence and honest confidence (item 6)

- `uv run scripts/evidence.py plan --input runs/<file>.json [--top 10]` picks the top N by Match Score and,
  per person, at most one non-LinkedIn, non-Indeed public URL already in the batch (`public_urls`, a
  non-LinkedIn `profile_url`, or a URL in the evidence notes), GitHub first, then personal site, talk page,
  blog. It never searches for a person: no URL in hand means no fetch.
- The agent fetches each planned URL once with `web_fetch_exa` and writes a `corroboration`
  (url, source_type, fetched_at, snippet, confirms = [{fact, quote}]), then reruns `score.py`.
- `score.py` checks every quote is in the snippet and that the page is not LinkedIn/Indeed or the
  profile page itself, and stops with ✗ otherwise. Each fact is marked `claimed (<source>)` or
  `corroborated (<type>: <url>)` in Evidence / Notes. **High needs 2+ independent sources**: a corroborated
  Medium becomes High, a one-source High becomes Medium. **Medium caps the total at 85, Low at 60.** The
  scorer's own value is kept in `confidence_claimed`. Re-scoring is idempotent.
- The breakdown gets `Evidence Sources n (<sources>)`, and the sheet a new **Evidence Sources** column
  (the 15th, after Recruiter Status). Older sheets get the header added in place on their next upsert;
  existing rows stay blank there until rescored.

### Privacy and audit trail (item 10)

- `memory.db` gets two new tables (created automatically, `CREATE TABLE IF NOT EXISTS`):
  `sources` (dedupe_key, url, source_type, query_id, fetched_at, snippet_hash, jd_id; one row per
  candidate, url and snippet, written by the upsert for the profile and the corroboration page) and
  `suppressed` (sha256 of the person's URL key and name|company key, kind, date, reason; no names).
- `uv run scripts/memory.py where <dedupe key | profile URL>` prints everything held about a person:
  candidate row, scores, feedback, source trail, workbook rows and suppression state (subject-access request).
- `uv run scripts/memory.py forget <dedupe key | profile URL> --yes` deletes the person from every sheet
  and from candidates, candidate_scores, feedback and sources, then suppresses them. Without `--yes` it
  refuses; the guard blocks it without `--yes` and settings.json asks the human before it runs.
  score.py, dedupe.py and the upsert drop suppressed people, by URL variant or by name and company, so
  they are never scored or written again.
- Workbook row deletes now re-point hyperlinks (openpyxl's `delete_rows` left a ghost row with the moved
  link); `retention.py --apply` uses the same fix.
- `retention.py` (dry run) also lists rows still **New** whose Date Found is older than 90 days
  (`--new-days N`); they are listed for review only and `--apply` never deletes them.
- **Email / Mobile stays blank unless the upsert runs with `--contacts`** (data minimisation); the count
  left blank is printed.
- Company agent: unchanged. Its rows have no "New" status (Open / Closed / Unverified / Stale), so the
  90-day "not reviewed" listing does not apply there. The 'Posted By' column is unchanged (question closed).

### Feedback loop, shortlist export and outreach drafts (2026-10-18)

```bash
uv run scripts/tune_weights.py --jd <jd_id>                    # suggestion only; the human adds --apply from a terminal
uv run scripts/export_shortlist.py --jd <jd_id> [--statuses Shortlisted,Contacted] [--format xlsx|csv]
uv run scripts/outreach.py plan  --jd <jd_id> --company "<hiring company>"
uv run scripts/outreach.py write --jd <jd_id> --company "<hiring company>" --input runs/outreach-<jd_id>.json
```

- `tune_weights.py` needs 5 Shortlisted/Contacted and 5 Rejected people with a full score breakdown. For each dimension it compares the share
  of the weight each group earned and moves at most 5 points, one at a time, from the dimension that tells the groups apart least to the one
  that tells them apart most (never below 5 points, total stays 100). `--apply` updates `memory.db` and `jds/<jd_id>.json`, and logs the change in
  `weight_changes`; existing scores change only when the JD is scored again. With a small sample it is a hint, and it says so.
- `export_shortlist.py` writes `exports/<jd_id>-shortlist-<date>.xlsx`: name, title, company, location, score, confidence, open to work, profile
  link, up to 3 cited facts, status. No email or phone; contacts leave only through `export_crm.py`.
- `outreach.py` prints the people and the Evidence facts a message may cite, and checks the drafts: the fact must be that person's, the message must
  repeat its quote, no email address or phone number, at most 900 characters. It appends the notice below to every draft. It never sends anything
  and never looks up an address. Written by the `draft-outreach` skill.
- Not built: `/import-linkedin-csv` (the legal Recruiter CSV route); it needs a sample export to build against.

### Outreach notice (GDPR Art. 14)

Put this in the first message to anyone found through this agent (`common.art14_notice()` fills it in):

> We found your public profile on {source} on {date} while looking for candidates for a {role} role at
> {company}. We keep only your name, current role, location, public profile link and the skills shown
> there, for recruitment, under our legitimate interest, for up to 180 days. You can ask us to see this
> data, correct it or delete it at any time by replying to this message; if you ask us to delete it we
> will not contact you again.

On a delete request run `memory.py forget`; on an access request send the `memory.py where` output.

### Verify

- `uv run tests/run_tests.py` → 49 tests OK (33 earlier, one expectation changed from 69 to 60, plus 16 in
  `tests/test_evidence_privacy.py`).
- `python3 .claude/hooks/test_guard.py` → 65/65 (59 earlier + 6 new: evidence plan, where, forget with and
  without `--yes`, retention `--new-days`, no redirect into scripts/).
- On the existing Pune batch, `evidence.py plan` finds 0 fetchable URLs in the top 10: every row holds only
  a LinkedIn URL. Corroboration starts working once the scout records `public_urls` (now in its instructions).

## Key factors (2026-10-15)

Four recruiter-facing factors, each its own sheet column, appended at the **end** of every JD sheet in this
order: **Open to Work, Stack Match, Experience Match, Location Match**. `scripts/score.py` fills all four on
every run; three of them reuse scores that already existed (no new scoring logic).

| Column | Example | Built from |
| --- | --- | --- |
| Open to Work | `Yes: "#OpenToWork"`, `Likely: "Notice period: 30 days"`, `Unknown` | new: self-published phrases in the public snippet |
| Stack Match | `3/3 must-have: React, TypeScript, JavaScript; missing: none · preferred 2/6: Next.js, Redux · core 30/35, preferred 6/10` | the scorer's `mandatory_missing`, preferred skills named in the evidence, the core / preferred points |
| Experience Match | `6 yrs vs 5-9 required: in range`, `3 yrs (estimate) vs 5+ required: 2 below`, `14 yrs vs 5-9 required: 5 above` | the code experience score (same year parsing) |
| Location Match | `Same city · Pune, Maharashtra, India` (or Same region, Other, Remote OK, Unknown) | the code location score and the canonical location |

**Open to work rules.**
- Signals, from search snippets only: `#OpenToWork`, `open to work`, `actively looking`,
  `looking for new opportunities`, `open to (new) opportunities`, `open to <up to 5 words> roles / positions`, `immediate joiner` → **Yes**;
  `serving notice period`, `notice period: N days`, `N days notice` → **Likely**; nothing → **Unknown**.
  A negation just before the phrase (`not open to work`, `no longer looking …`) cancels it.
  Detector: `common.detect_open_to_work`.
- Never inferred from tenure, job changes or gaps; never fabricated. LinkedIn pages are never opened to check
  (the guard still blocks LinkedIn / Indeed fetches).
- The quote is exact. A quote the batch supplies (`open_to_work_quote`, set by the scout) must be in the evidence
  and must itself be a signal, or score.py stops with ✗; the validator (and so the guard before every Excel
  write) checks the same.
- Scoring: Yes +5, Likely +2, **Unknown never costs points**. The total stays capped at 100 and the mandatory
  gate, the experience soft gate and the Medium 85 / Low 60 confidence caps still apply after the bonus.
- Discovery: `uv run scripts/memory.py plan-queries --jd <id> --open-to-work` appends one optional query,
  `"open to work" <first mandatory skill> developer <city> site:linkedin.com/in`, marked `(optional: open to work)`.
  It never carries a name. /scout uses it by default; the recruiter may drop it.

**Reporting.** score.py's ✓ line ends with `open to work N Yes / M Likely`; `memory.py run-finish` prints
`open to work: N Yes, M Likely of R rows in the sheet` in the closing summary, and /scout repeats it.

**Validation and migration.** `common.validate_factors` requires the four columns and checks the open-to-work
value and quote; `excel_upsert.py` refuses a batch without them (score a pre-2026-10-15 batch again first).
Sheets with 14 or 15 columns get the missing headers appended in place on their next upsert; their old rows
stay blank in the new columns until rescored and upserted. Any other header is still refused.

**Verify.** `uv run tests/run_tests.py` → 66 tests OK (49 earlier + 17 in `tests/test_key_factors.py`: every
phrase, negations, Yes beats Likely, +5 / +2 with the 100 cap, confidence caps, supplied-quote checks, validator,
the optional query, each factor string, 14- and 15-column migration, unknown header refused, batch without
factors refused, run-finish counts). `python3 .claude/hooks/test_guard.py` → 70/70 (65 earlier + 5: the
open-to-work query and `--open-to-work` allowed, a valid factor batch allowed, a batch missing a factor and one
with a quote not in the evidence blocked). The name-search case now takes a real name from memory.db, so it
no longer breaks when memory is wiped.

Desktop app: the packaged desktop-app bundles this folder, so it needs a rebuild to ship the change.

**Live check (2026-10-15, run 6, 20 rows).** Open to work: 7 Yes, 0 Likely. Example rows:

| Candidate | Score | Open to Work | Stack Match | Experience Match | Location Match |
| --- | --- | --- | --- | --- | --- |
| Rohit Kulkarni | 85 | Yes: "Open to Work" | 3/3 must-have: React, TypeScript, JavaScript; missing: none · preferred 2/6: Next.js, micro-frontends / design systems · core 27/35, preferred 8/10 | 7 yrs vs 5-9 required: in range | Same city · Pune, Maharashtra, India |
| Pranali Ghalme | 58 | Yes: "Open to Opportunities" | 3/3 must-have …; missing: none · preferred 4/6: Next.js, Redux, Jest / React Testing Library, performance · core 22/35, preferred 5/10 | 7 yrs (estimate) vs 5-9 required: in range | Same city · Pune, Maharashtra, India |
| Gourav Jadhav | 54 | Unknown | 3/3 must-have …; missing: none · preferred 2/6: Next.js, Redux · core 22/35, preferred 3/10 | 3 yrs (estimate) vs 5-9 required: 2 below | Same city · Pune, Maharashtra, India |

## Structured fields, Evidence sheet and CRM export (2026-10-16)

From `~/vault/90-claude/recruit-desk-data-structure-and-free-sources.md`, items 4, 1 and 8. Every existing header,
column and sheet is unchanged; new columns are appended at the end and one new sheet is added.

**16 new columns on every JD sheet** (columns 20 to 35, after Location Match), filled by `common.structured_fields()`
from the row's own cells on every upsert. A value the batch already sets (`first_name`, `last_name`, `current_title`,
`current_company`, `work_mode`) wins. Blank means the row does not say it.

| Column | Type | Built from |
| --- | --- | --- |
| First Name, Last Name | text | Candidate Name, split at the first space (parentheses, honorifics and anything after a comma dropped); one word = First Name only |
| Title, Company | text | Current Role / Company split at `@` or ` at `; a note such as "(company not shown in snippet)" leaves Company blank |
| Experience Years | number | the years in Experience (same parser as the scorer) |
| Experience Basis | `self-stated`, `computed`, `estimate` | `computed` when Experience says it came from dates or a timeline, `estimate` for `~` / estimated, else `self-stated`; blank with no years |
| Work Mode | `Onsite`, `Hybrid`, `Remote`, `Unknown` | only when exactly one is written in Location; never inferred from silence |
| Core Skills pts, Experience pts, Domain pts, Seniority pts, Location pts, Preferred pts, OTW pts | number | the Score Breakdown parts |
| Cap Adjustment pts | number (0 or negative) | Match Score minus the parts: the gates and confidence caps. The eight pts columns add up to Match Score |
| Evidence Count | number | rows in the Evidence sheet for that candidate (all JDs) |

**Evidence sheet** (`Evidence`, rebuilt on every upsert): Candidate Key, Fact, Quote, Source URL, Source Type,
Fetched On, Candidate Name, JD Id. One row per claimed fact, per scoring quote (Fact = the dimension, e.g. "Core
skills"), per open-to-work quote and per corroboration `confirms` entry, each against the page it came from; a source
with nothing cited yet gets one row with Fact and Quote blank. The rows live in the new memory.db `evidence` table
(replaced per candidate and JD on each upsert); `memory.py where` lists them and `memory.py forget` deletes them with
the person. `Evidence` is a reserved name: a JD titled "Evidence" gets another sheet name.

**CRM export**: `uv run scripts/export_crm.py --format hubspot|salesforce [--status Shortlisted ...] [--out file.csv]`
writes `exports/candidates-<format>-<date>.csv`, one contact per person merged across JD sheets (best Match Score,
all JDs listed). Record Key = the email when Email / Mobile holds one (only ever written with `--contacts` from a
self-published page), else the profile URL; `Record Key Type` says which. No email is looked up, guessed or taken
from notes. HubSpot columns: First Name, Last Name, Email, Phone Number, Job Title, Company Name, City, Country/Region,
Profile URL, Lead Status, Match Score, Matched JDs. Salesforce (Data Import Wizard, Contacts): First Name, Last Name
(a one-word name goes here, as Salesforce requires it), Email, Phone, Title, Account Name, Mailing City, Mailing
Country, Profile URL, Lead Source, Description, Match Score, Matched JDs. `exports/` is git-ignored.

**Migration**: sheets with 14, 15 or 19 columns get the new headers appended in place on their next upsert.
`uv run scripts/backfill_structured.py [--dry-run]` (builder/human only) fills existing rows at once; run on
2026-10-16 after backing up to `*.backup-2026-10-16-before-structured-fields.*`: 53 rows on 3 sheets, Evidence sheet
53 rows (from the sources table; facts and quotes arrive with the next scored upsert). For all 53 rows the eight pts
columns add up to Match Score (a breakdown from before the open-to-work bonus counts OTW as 0).

**Verify**: `uv run tests/run_tests.py` → 86 tests OK (66 earlier, one migration assertion moved from "last four
columns" to columns 16-19, plus 20 in `tests/test_structured_crm.py`); `python3 .claude/hooks/test_guard.py` → 73/73
(3 new: export_crm allowed, backfill blocked).

## GitHub and Stack Exchange skill evidence (2026-10-16, plan item 9)

- `uv run scripts/dev_evidence.py fetch --input runs/<file>.json [--top 10] [--max-repos 5] [--max-wait 90] [--dry-run]`
  runs after score.py and before the rescore. For the top N by Match Score it reads, only when the candidate's own
  entry links the profile (profile_url, public_urls or a URL in the notes; never a name search):
  GitHub `/users/<login>`, `/users/<login>/repos`, `/repos/<login>/<repo>/languages` (the most recently pushed own repos),
  and Stack Exchange `/users/<id>` and `/users/<id>/top-answer-tags` on the linked site.
- Output per candidate: `dev_signals.github` (languages and bytes, top 3 own repos with stars, last push date, public repo
  count) and `dev_signals.stackexchange` (reputation, top 5 answer tags with score and count), each with `facts`
  ({fact, quote}, the quote being the API value). Only whitelisted fields are copied: never an email, name or company.
  Validation rejects any `dev_signals` holding an email. GitHub's Acceptable Use Policy bars recruiter outreach with its
  user data, so this is skill evidence only.
- score.py counts each API as one more independent source (not when it is the primary source's site or the
  corroboration's), so a Medium row with GitHub or Stack Exchange data reaches High.
- Three columns appended at the end: **GitHub Languages**, **GitHub Last Active**, **SO Top Tags**. 35-column sheets
  migrate in place; a later batch without the data keeps the cells. The facts go to the Evidence sheet
  (Source Type "GitHub API" / "Stack Exchange API") and the API pages to the `sources` table.
- Rate limits: GitHub 60 requests an hour without a token (`GITHUB_TOKEN` in the environment or `.env`, read by the
  script only), Stack Exchange 300 a day without a key (`STACKEXCHANGE_KEY` optional). 403/429 wait for the reset when it
  is within `--max-wait`, 5xx back off 2 s, 4 s, 8 s, a Stack Exchange `backoff` is honoured; otherwise the run stops
  cleanly and keeps the candidates already done. About 7 GitHub calls and 2 Stack Exchange calls per person.
- Tests: `tests/test_dev_evidence.py` with `tests/fixtures/dev_api.json` (no live calls).
