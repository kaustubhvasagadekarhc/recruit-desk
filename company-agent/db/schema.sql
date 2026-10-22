-- SQLite memory for the Company Hiring Intelligence agent.
-- Applied idempotently by scripts/memory.py (CREATE ... IF NOT EXISTS).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    domain           TEXT NOT NULL UNIQUE,      -- bare host, no www (e.g. razorpay.com)
    official_website TEXT,
    career_page      TEXT,
    ats_type         TEXT,                      -- greenhouse | lever | workable | ashby | smartrecruiters | zohorecruit | workday
    ats_slug         TEXT,
    sheet_name       TEXT,                      -- worksheet in Company_Hiring_Intelligence.xlsx
    first_scanned    TEXT,
    last_scanned     TEXT
);

-- Other domains of a company (an old domain that redirects to the final one, e.g.
-- humancloud.co.in -> humancloud.dev). Companies are keyed on their final domain.
CREATE TABLE IF NOT EXISTS company_aliases (
    domain      TEXT PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS routes (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    url           TEXT NOT NULL,
    url_norm      TEXT NOT NULL,
    kind          TEXT NOT NULL,                -- robots | sitemap | careers | ats | job | page
    status_code   INTEGER,
    discovered_at TEXT NOT NULL,
    last_checked  TEXT NOT NULL,
    UNIQUE (company_id, url_norm)
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    job_key             TEXT NOT NULL UNIQUE,   -- normalised URL, else company|title|location
    url                 TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT,
    source_type         TEXT NOT NULL,
    posted_by           TEXT,
    posted_date         TEXT,
    date_discovered     TEXT NOT NULL,
    last_checked        TEXT NOT NULL,
    description_summary TEXT,
    application_url     TEXT,
    status              TEXT NOT NULL CHECK (status IN ('Open','Closed','Unverified','Stale')),
    raw_json            TEXT,
    missing_count       INTEGER NOT NULL DEFAULT 0,  -- complete scans in a row that did not list it; Closed at 2
    posted_date_basis   TEXT, -- 'exact (from page)', 'approx: "3 weeks ago" seen <date>', or blank
    -- Typed fields (2026-10-16), filled by scripts/jobfields.py from source fields only.
    work_mode           TEXT, -- Remote | Hybrid | Onsite | Unknown
    city                TEXT,
    country             TEXT,
    function            TEXT, -- from the ATS department / team field
    seniority           TEXT, -- level word in the title
    salary_min          REAL, -- yearly, only when the posting states it
    salary_max          REAL,
    currency            TEXT
);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_id);

CREATE TABLE IF NOT EXISTS rejected_sources (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER REFERENCES companies(id),
    url         TEXT NOT NULL,
    url_norm    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    rejected_at TEXT NOT NULL,
    UNIQUE (company_id, url_norm)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    pages       INTEGER DEFAULT 0,
    jobs_found  INTEGER DEFAULT 0,
    jobs_new    INTEGER DEFAULT 0,
    jobs_closed INTEGER DEFAULT 0,
    notes       TEXT
);

-- ---- Learned memory (self-learning loop, 2026-10-15). Data only: no code is ever stored here. ----

-- What worked last time for a site, so the next run goes straight to the right source.
CREATE TABLE IF NOT EXISTS site_profile (
    domain          TEXT PRIMARY KEY,          -- final bare domain (companies.domain)
    careers_url     TEXT,
    ats_type        TEXT,                      -- builtin ATS name or an active reader spec's ats name
    ats_slug        TEXT,
    board_url       TEXT,
    reader          TEXT,                      -- builtin:<ats> | spec:<name> | '' (none yet)
    js_rendered     INTEGER NOT NULL DEFAULT 0, -- careers page fills its job list with a third-party script
    job_pages       TEXT,                      -- JSON list (<= 20) of own-site pages that held jobs
    own_site_jobs   INTEGER NOT NULL DEFAULT 0, -- roles found on the company's own pages last time
    last_crawl_mode TEXT,
    last_pages      INTEGER,
    last_seconds    REAL,
    last_jobs       INTEGER,
    last_run_id     INTEGER,
    what_worked     TEXT,                      -- one line, e.g. "spec:keka feed perennial (2 jobs); crawl skipped"
    updated_at      TEXT NOT NULL
);

-- Why a run could not read jobs. One open row per (domain, kind, host); a later run that reads
-- jobs from that source fills resolved_by.
CREATE TABLE IF NOT EXISTS blockers (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER,                       -- runs.id of the run that last saw it
    domain       TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('unknown_ats_host','no_reader','js_rendered','http_403','robots','empty_board','parse_fail')),
    host         TEXT NOT NULL DEFAULT '',
    ats          TEXT,
    slug         TEXT,
    evidence_url TEXT,
    detail       TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    seen_count   INTEGER NOT NULL DEFAULT 1,
    resolved_by  TEXT,                          -- e.g. "spec:keka (trial 2 jobs)" or "builtin:greenhouse"
    resolved_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS blockers_open ON blockers(domain, kind, host) WHERE resolved_by IS NULL;

-- Declarative reader specs (schema/readers/<name>.json) and their provenance. A spec is used only
-- while its row is 'active' AND the file's sha256 still equals the one that passed the trial.
CREATE TABLE IF NOT EXISTS tools_registry (
    name            TEXT PRIMARY KEY,
    ats             TEXT NOT NULL,
    path            TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_by      TEXT NOT NULL,              -- 'agent' (forge flow) or 'builder' (hand-written)
    created_at      TEXT NOT NULL,
    blocker_id      INTEGER,
    trial_company   TEXT,
    trial_file      TEXT,
    trial_jobs      INTEGER,
    trial_plausible INTEGER,
    trial_at        TEXT,
    disabled_at     TEXT,
    disabled_reason TEXT
);

-- Companies to re-scan every week and report on in the digest (2026-10-18, improvement plan item 8).
-- keywords: comma-separated words; when set, the digest lists only new roles whose title contains one of them.
CREATE TABLE IF NOT EXISTS watchlist (
    domain    TEXT PRIMARY KEY,                 -- final bare domain (companies.domain)
    name      TEXT NOT NULL,
    keywords  TEXT NOT NULL DEFAULT '',
    added_at  TEXT NOT NULL
);
