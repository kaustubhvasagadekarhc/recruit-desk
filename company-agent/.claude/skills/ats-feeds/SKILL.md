---
name: ats-feeds
description: Detect applicant tracking system (ATS) boards and read their public JSON feeds - Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Zoho Recruit, Workday. Use when a company hosts jobs on an ATS.
---

# ATS feeds

`scan.py` reads a board known from memory or `--ats` first (then only a 5-page light check of the site) and handles boards linked from the official site automatically. Feeds that read fewer postings than they report are marked incomplete and never close jobs. Manual commands:

```bash
uv run python scripts/ats.py detect "<url or html snippet>"      # find type + slug
uv run python scripts/ats.py fetch greenhouse <slug>              # preview rows
uv run python scripts/ats.py guess "<Company>"                    # candidates to confirm
```

| ATS | Public feed |
| --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` |
| Lever | `api.lever.co/v0/postings/<slug>?mode=json` (EU: `api.eu.lever.co`) |
| Workable | `apply.workable.com/api/v1/widget/accounts/<slug>` (200 for ANY slug: trust only linked boards) |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<slug>` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<slug>/postings` |
| Zoho Recruit | no keyless JSON; the public `/jobs/Careers` page is parsed (robots-checked) |
| Workday | `tenant.wdN.myworkdayjobs.com/<site>` → slug `tenant.wdN/site`; `POST /wday/cxs/<tenant>/<site>/jobs`, 20 per page, 1 request a second, robots-checked; "N Locations" read from the posting's JSON. When a feed returns rows, scan.py uses it instead of crawled job pages. A read is complete only when it covers Workday's live total (asked again at the end if short); summaries come from the posting JSON for up to 40 jobs per run, new jobs first. |

Rows from a feed are `Open` (listed now), source `ATS: <name>`, Posted By blank. Greenhouse
`first_published` is the posting date; `updated_at` is not, so it is never used as one.
Darwinbox, Keka, Workday, SuccessFactors, Taleo and iCIMS links are reported in `ats_links` but
have no keyless feed here; read their public pages with Exa `web_fetch_exa` and mark rows
`Unverified` unless the page clearly lists the role as open.

## Checkpoints

Part of step 2 of /company-scan. `ats.py` prints `… ATS feed <type>:<slug>: <n> jobs` for each feed it reads, so the user sees each board as it is read.
