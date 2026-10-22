# Account Research agent

A Claude Code agent that researches a company as a business-development account and writes one row per
company to `Account_Research.xlsx` (one sheet per list, default `Accounts`), from free public sources only.
It is the third Recruit Desk assistant, next to `candidate-agent` and `company-agent`.

## 2026-10-15 — v1, free and legal only

Design: `~/vault/90-claude/account-research-agent-options.md`, section 6 Option A and section 7 "v1: free and
legal only". Built on the `company-agent` pattern (named scripts, PreToolUse guard, visible checkpoints,
SQLite memory, idempotent Excel upsert).

### Run it

```bash
cd AI-AGENTS/account-agent
uv sync                              # once
claude                               # then type:
/account-scan Razorpay razorpay.com
/account-scan --csv companies.csv    # batch: columns company, domain (optional), list (optional sheet name)
```

Or press **Accounts** in Recruit Desk 0.6.0. The first `claude` run in this folder asks to trust it; the app's
"Trust the agent folders" button does the same for all three assistants.

### Columns

Company · Domain · Product Type · What They Build (brief) · Company Brief (founded, size, customers, milestones) ·
HQ / US Office · India Office · Product? · Funding · India Connection (US-based leader) · Development Requirement
(current) · Who to Contact · Leadership Education & Background · Warm Connection / Common Ground · Conversation
Starter (cold email / conference) · Best Person to Reach Out (HumanCloud side) · Sources · Confidence · Last Checked.

Every fact carries `[n]` markers that point at the numbered URLs in **Sources**.

**Search depth (0.6.2).** Each research field has a ladder of free sources (company site pages including
careers and investors, Wikidata, Wikipedia, SEC EDGAR with the 10-K headcount, GitHub, GDELT news, keyless Exa
with several targeted queries, conference speaker pages). A field is given up only after its ladder, and the
cell then says what was tried: `Not found (checked: site about/careers, Wikidata, Wikipedia, EDGAR, 4 Exa
searches)`. The validator refuses an empty research field, a bare `No evidence found`, a Company Brief with no
size, a funding round 2+ years old with no newer round searched (`No newer round found (checked: ...)`), and a
CTO conflict without a `(conflict: ...)` note.

### Decisions (taken by god on the design report's recommended defaults)

1. **India Connection replaces "Indian Decision Maker based in US".** Filled only from self-published, factual
   evidence with URLs about a US-based leader: Indian education, past work in India, building an India team,
   a public bio or interview that mentions India, India-focused events or communities in their own words. It is
   **never inferred from a name or photo**. Values: `Yes: <fact> [n]` or `No evidence found (checked: ...)`. The validator
   rejects a "Yes" without a cited URL and rejects inference wording (surname, name suggests, Indian-origin,
   ethnicity, …). The human has been told this.
2. **Free sources only:** company site pages (`lookup.py site|page`, robots-aware), keyless hosted Exa
   (`mcp__exa__*` via https://mcp.exa.ai/mcp, which works in `claude -p` without a key), Wikidata, SEC EDGAR,
   GitHub, press and news, conference and podcast pages. No paid APIs, no sign-ups.
3. **Development Requirement = option A1:** `scripts/roles_from_company_agent.py` runs company-agent's
   `uv run python scripts/run_company.py` with its working directory set to `company-agent/`, then reads
   `Company_Hiring_Intelligence.xlsx` **read-only** and summarises open engineering roles (count, areas,
   locations, India hiring), citing the careers page. A company-agent scan newer than 7 days is reused.
   company-agent is not modified, and the account agent's own tools may not touch that folder.
4. **team.csv:** `team.example.csv` shows the schema (name, role, email, seniority, regions, past_employers,
   colleges, cities_lived, communities, conferences, industries_known, linkedin_url, consent, plus the optional
   default_for for the region owner). `team.csv` ships with headers only and is git-ignored and never bundled.
   With no consented rows, Warm Connection and Best Person say `add team.csv`. Matching is deterministic in
   `scripts/match_team.py` (shared employer 3, college 3, community or same conference and year 2, conference
   another year 1, city 1, industry 1; aliases in `schema/aliases.json`). It states common ground only, never
   that people know each other. The agent never reads team.csv itself.
5. **Conversation Starter** is built only from cited facts in the same row; `starter.py` checks that every
   citation is in Sources, that every number also appears in another cell, and that nothing about ethnicity,
   religion, family, health or the India Connection field is used. Otherwise the cell says `Not found (checked: ...)` with the reason.
6. **Who to Contact:** names and titles from public company or press pages; emails or phones only if
   self-published (`self-published [n]`), otherwise blank. No LinkedIn page fetches or logins; Exa snippets only.
7. **Product Type** uses the fixed list in `schema/fields.json`: SaaS, Platform / API, Marketplace, Fintech,
   Healthtech, E-commerce / Retail, Consumer App, Enterprise Software, Hardware / IoT, AI / Deep Tech,
   IT Services / Consulting, Other.
8. **Batch mode:** `/account-scan <company> [domain]` or `/account-scan --csv <file>` (`batch.py`: one company at a
   time, resumable, skips companies checked in the last 14 days unless `--refresh`, at most 200 per CSV).

### Leaders and contacts (0.6.2)

- Up to 6 senior people per company (CEO, founders, CTO, VP or Head of Engineering, COO, CHRO or talent head, any
  US-based leader): name, title, location, source link, last-verified date, email, phone, LinkedIn profile URL
  (from search results only), X, GitHub, personal site or blog, podcast or newsletter, upcoming talks, booking
  link. Written to the `Contacts` sheet, one row per person, linked to the account by Domain. The company's own
  channels (press or media, sales, partnerships, investor relations, contact form, offices with address and phone)
  go to the `Company channels` sheet. A rescan replaces that company's rows in both sheets.
- **Hard rules** (`scripts/contacts.py`, enforced by the hook on every draft and rows Write and by
  `excel_upsert.py`): an email or phone is accepted only with its source URL and a quote of that page which
  contains it; anything else is `Not published (checked: ...)`. Guess wording (likely, pattern, firstname@, ...)
  is refused. Paid enrichment and data-broker hosts (Apollo web pages, Hunter, RocketReach, ZoomInfo, Lusha,
  ContactOut, HasData, Firecrawl, people-search sites) are refused as sources and as tools. With no personal number, the company's published main number is
  written and labelled `company line`. `contacts.py verify` re-reads each email and phone source page before
  the draft is assembled. No LinkedIn page is fetched and nothing behind a login is read.
- **Opt-in storage.** People's own emails and phones are kept only when the run has `--contacts`
  (`/account-scan Acme acme.com --contacts`, or the app's "Keep published personal emails and phones" box).
  Without it those cells read `Not stored (this run did not use --contacts)`. Company lines and company channels
  are business details and are always kept. `retention.py` also lists Contacts rows last verified more than
  180 days ago.
- **Apollo.io (optional, paid; the one approved exception).** With `APOLLO_API_KEY` in `account-agent/.env` (set
  it in the app's Settings or the installer), `lookup.py apollo <domain>` (`scripts/apollo.py`) runs first in the
  contacts step: organization enrichment, a free people search for senior titles and US-based leaders, and people
  match for at most `APOLLO_MAX_CREDITS` people (default 6). Only emails Apollo marks `verified` are kept; guessed,
  likely or unavailable ones are dropped; phones only when Apollo returns them directly (no phone-reveal webhook).
  Such values carry `source: apollo`, the Apollo person id and `email_status: verified`, and the Contacts sheet and
  the app show them as `Source: Apollo (verified)`, apart from self-published facts. Answers are cached in
  memory.db for 30 days (a rescan spends no credits; `apollo.py forget <domain>` clears a company). Every run
  prints `Apollo credits used: N`. Without a key it prints `Apollo not configured` and the scan stays free-only.
  The key is sent only as the X-Api-Key header; it is never printed, logged or put on a command line.
- `lookup.py contact <domain | URL>` reads the contact, press, newsroom, media, investors, partners, sales and
  offices pages and prints each published email, phone, contact form and address-like line with its quote.

### Evidence, freshness, Form D, GLEIF, DNS and CRM export (2026-10-16, Round 1)

Spec: `~/vault/90-claude/recruit-desk-data-structure-and-free-sources.md`, items 1, 3, 8 and 10. Existing headers and
sheets are unchanged; only new columns at the end and new sheets were added, and the inline `[n]` markers stay.

- **Evidence sheet** (`scripts/evidence.py`, written by every `excel_upsert.py` run): `Domain`, `Field`, `Fact`,
  `Source URL`, `Source Type`, `Checked On`, `Age (days)`, `Confidence`. One row per cited fact and source; a fact
  without a source URL is never written. Source Type is one of Filing, Registry, Company site, Careers / ATS, DNS
  (High); Wikidata, Wikipedia, News, Code host, Data provider (Medium); Web (Low). Age (days) is as of the write;
  recompute from Checked On for a live view.
- **New Accounts columns** after `Last Checked`: `Evidence Count`, `Freshness` (Fresh up to 30 days, Ageing up to 180,
  Stale after, from the oldest key-field check) and `Key Field Confidence` (the weakest of Company Brief, HQ / US
  Office, Funding and Who to Contact, each rated by its strongest source). The existing `Confidence` is unchanged.
- **Migration:** `uv run python scripts/excel_upsert.py --migrate-evidence` copies the workbook and memory.db to
  `backups/<YYYYMMDD-HHMMSS>/`, then builds Evidence and the new columns for every company in memory.db.
- **SEC Form D** (`lookup.py formd "<Company>" | <CIK>`): submissions JSON plus each filing's `primary_doc.xml`:
  amount sold and offered, first sale date, investors, issuer address and the filing URL. SEC hosts share one budget
  of at most 8 requests a second (SEC allows 10) with a User-Agent carrying a contact (`CRAWLER_CONTACT`, else the
  HumanCloud research address). Pooled funds and SPVs are flagged.
- **GLEIF LEI** (`lookup.py gleif "<Company>"`) and **DNS MX / TXT** (`lookup.py dns <domain>`, the local resolver,
  standard library only): legal entity and HQ, email provider and SaaS verification tokens. Wikidata also prints the
  LEI (P1278).
- **CRM export** (`uv run python scripts/export_crm.py --format hubspot|salesforce [--list X] [--out exports]`):
  `<format>-companies.csv` keyed by domain and `<format>-contacts.csv` keyed by email, HubSpot or Salesforce import
  headers. Only contacts whose email was self-published (URL and quote) or Apollo verified; company lines are never a
  contact phone; `[n]` markers and `Not found` / `Not published` text are never written. `exports/` is git-ignored.

### Typed company fields and the H-1B LCA signal (2026-10-16, Round 2)

Spec items 2 and 5. Fourteen columns are appended after `Key Field Confidence`; nothing existing moves.

- **Typed columns** (`scripts/typed.py`): `Founded Year`, `Employees`, `Employees As Of`, `Last Round`, `Last Round
  Amount USD`, `Last Round Date`, `Total Raised USD`, `HQ City`, `HQ Country`, `India Office City`, `Public/Private`,
  `Ticker`. Read in code from cited clauses of Company Brief, HQ / US Office, India Office and Funding: numbers are
  numbers (whole USD), dates are ISO (`2021-12`, `2026-07-31`, as precise as the source), Last Round is Pre-Seed, Seed,
  Series A to K or IPO, HQ Country is an ISO-2 code. Each value has its own Evidence row (Field = column name, Fact =
  the clause) and must re-read from it, or the write is refused. Blank means no cited clause states it.
- **H-1B columns**: `H-1B Filings (4Q)` (a number) and `H-1B Top Roles` (`Software Engineer (12); ...`), from the
  DOL OFLC LCA disclosure files. `uv run python scripts/h1b.py fetch` downloads the newest 4 files listed on the OFLC
  performance page to `data/` (git-ignored, ~500 MB) and streams them into `data/h1b.sqlite` (H-1B cases only, one
  row per case number). `lookup.py h1b "<Company>" [--alias "<legal name>"]` prints filings decided in the last 4
  fiscal quarters (ending with the newest file's quarter), top job titles, US worksites and wage ranges per pay
  unit, citing each quarter file. Names match after normalising legal suffixes, on the employer or DBA name, and
  through `schema/aliases.json` employers. `assemble.py` adds the signal to Development Requirement; the
  India Connection skill uses it as context only. An LCA is a company's filing for a role, never a claim about a
  person; the files carry no nationality.
- **Backfill:** `excel_upsert.py --migrate-evidence` (alias `--migrate-typed`) backs up first, then fills the typed
  and H-1B columns for every company in memory.db.

### Outreach notice (GDPR Art. 14)

The people in the Contacts sheet did not give us their details; we collected them from public pages. Put this in
the first message to anyone contacted through this agent:

> We found your name, role and public contact details on {source} on {date} while researching {company} as a
> possible business partner for HumanCloud. (For a contact whose details came from Apollo, write: "We obtained your
> name, role and business contact details from Apollo.io, a business-contact data provider, on {date} while
> researching {company} ...".) We keep only your name, title, location, the public links and any
> contact details you or your company published, for business development, under our legitimate interest, for up
> to 180 days. You can ask us to see this data, correct it or delete it at any time by replying to this message;
> if you ask us to delete it we will remove it and not contact you again.

On a delete request, remove the person's row from the `Contacts` sheet by hand (memory.db holds only leaders' names and titles; delete
memory.db to clear them) and run `uv run python scripts/apollo.py forget <domain>` to drop the cached Apollo answers. India's DPDP Rules (notified 2025-11-14) apply most duties from 2027-05-14 and expect the same.

### Security rules (inherited from company-agent)

- Only the named scripts run (`uv run python scripts/<name>.py`); no inline interpreters, no `sqlite3`.
- LinkedIn and Indeed pages are never fetched (Exa search snippets only); the hook blocks Exa fetch / crawl and
  WebFetch of them, and `lookup.py` refuses them.
- Exa queries use company and role, not people's names, where avoidable.
- `.env` and `team.csv` are never read by the agent; writes stay in this folder (`runs/` for drafts); code,
  config, schema and instructions cannot be changed during a run; the workbook is written only by
  `excel_upsert.py`, after validation. Settings allow list: the named scripts and `mcp__exa__*` only.
- Retention: `scripts/retention.py` lists rows last checked more than 180 days ago. Dry run only; there is no
  delete mode.

### Tests

```bash
python3 hooks/test_hook.py              # guard self-test: 108/108 (contacts: constructed / unsourced email or phone refused)
uv run python scripts/test_account.py   # validators, India Connection rule, starter check, team matching, CSV batch,
                                        # roles summary, assemble, idempotent upsert, depth and freshness,
                                        # contacts and the Contacts / Company channels sheets, Evidence, Form D,
                                        # GLEIF, DNS and CRM export: 189/189
```

### Live result (2026-10-15, Razorpay razorpay.com, `claude -p`, claude-sonnet-5-5)

110 s wall clock, $0.58, 15 of 19 fields filled with 14 sources. Blank: employee size (no source), India
Connection = "No evidence found" (no leader found based in the US), Warm Connection and Best Person = "add
team.csv". The agent flagged a CTO conflict (2022 press release vs a 2026 article) in the cell. Exa's keyless
tier rate-limited 2 of about 8 searches.

### Open items

- Provide `team.csv` (with consent) to fill Warm Connection and Best Person.
- Exa's keyless tier rate-limits bursts; an Exa key in `.env` (optional, free tier) avoids it.
- v2 paid sources (PDL, Crunchbase, Tracxn) only after the human opts in by name. Apollo was opted in on 2026-10-16
  (contacts only, verified emails only).
