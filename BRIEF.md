# Recruitment agents: brief (from the human's ChatGPT research, 2026-10-14)

Two fully independent Claude Code agents. Each has its own folder, instructions, skills, hooks,
SQLite memory and Excel workbook. Neither ever reads or writes the other's folder. They may each
carry their own copy of small utilities (URL normalisation, validation); no shared code folder.

## Agent 1: Candidate Research (`candidate-agent/`)
- Input: a job description (JD), pasted, as a file, or as a URL.
- Steps: parse the JD (mandatory and preferred skills, experience, seniority, location, industry,
  exclusions) → discover candidates from public, permitted sources → score each against the JD →
  deduplicate and validate → write to Excel.
- Workbook: `candidate-agent/Candidate_Research.xlsx`, one worksheet per JD, named after the role
  (for example "Frontend Developer"). Excel sheet names: 31 characters at most, no `[]:*?/\`.
- Columns: Candidate Name · Current Role / Company · Location · Experience · Key Skills ·
  Match Score (0–100) · Score Breakdown · Evidence Confidence (High/Medium/Low) · Profile URL ·
  Email / Mobile · Source · Evidence / Notes · Date Found · Recruiter Status
  (New, Reviewed, Shortlisted, Contacted, Rejected).
- Starting score weights (adjust per JD; mandatory skills act as gates): core technical skills 35,
  relevant experience 20, domain and project relevance 15, seniority and responsibilities 10,
  location and work requirements 10, preferred skills 10. Every score carries a breakdown, the
  supporting evidence and a confidence rating. A high score on thin evidence is never shown as verified.
- Memory (SQLite): JDs and their parsed criteria, candidates seen (dedupe key: normalised profile
  URL, else name plus company), recruiter feedback on scores, role synonyms, run history.

## Agent 2: Company Hiring Intelligence (`company-agent/`)
- Input: a company name, its domain, or both.
- Steps: verify the official site and careers page (and any ATS, applicant tracking system, such as
  Greenhouse, Lever, Workable, Zoho Recruit) → discover routes via robots.txt, sitemap and internal
  links, within limits, obeying robots.txt → find hiring activity (careers page, ATS, public
  indexed LinkedIn and Indeed listings, public hiring posts) → extract job details → validate and
  write to Excel.
- Workbook: `company-agent/Company_Hiring_Intelligence.xlsx`, one worksheet per company.
- Columns: Company Name · Official Website · Career Page · Job Title · Location / Work Mode ·
  Source Type · Job / Post URL · Posted By · Posted Date · Date Discovered · Last Checked ·
  Description Summary · Application URL · Status (Open, Closed, Unverified, Stale).
- Memory (SQLite): companies scanned, discovered routes, job and post URLs (dedupe key: normalised
  URL, else company plus title plus location), last-scan time, rejected sources.

## Rules for both
- The unofficial LinkedIn MCP is dropped (human decision, 2026-10-14, after the security review).
- No logging into LinkedIn or Indeed, no scraping behind a login, no bypassing access controls.
  Public search-engine results (Exa) and public pages only.
- Never fabricate data. Email and mobile only when publicly self-published by the person; else blank.
- Every row keeps its source URL and discovery date; verified facts are kept apart from estimates.
- Excel writes are idempotent: a rerun updates rows, never duplicates them.
- Excel online (Microsoft Graph) is phase 2; phase 1 is local `.xlsx` files.

## Visible checkpoints (human requirement, 2026-10-14)
Every run must show the user, at all times, which step of the process it is on.
- At the start, show the full numbered plan (for example 6 steps) and create one task per step
  in Claude Code's task list, so the checklist is visible in the UI and ticks off as steps finish.
- Before each step, print a banner line: `▶ Step 2/6 · Discovering candidates (Exa search)`.
- After each step, print a result line with real counts:
  `✓ Step 2/6 · 18 profiles found, 3 duplicates skipped`. On failure: `✗ Step 2/6 · <reason>`,
  then stop or ask.
- Long steps print sub-progress (for example `… scoring 7/15`, `… crawled 40/150 pages`).
- Python scripts print the same style of progress lines to stdout, so the user sees them in tool output.
- Subagents return their checkpoint lines to the main agent, which relays them to the user.
- Every checkpoint is also appended, with a timestamp, to `runs/<YYYY-MM-DD-HHMM>-<slug>/progress.log`.
- End with a summary: rows inserted or updated, the sheet name, the workbook path, and open gaps.
