-- Candidate Agent memory (memory.db). Applied idempotently by scripts/memory.py init.
PRAGMA foreign_keys = ON;

-- One row per job description (JD) and its parsed criteria.
CREATE TABLE IF NOT EXISTS jds (
    id            TEXT PRIMARY KEY,           -- slug, e.g. senior-react-frontend-developer-pune
    title         TEXT NOT NULL,
    sheet_name    TEXT NOT NULL UNIQUE,       -- Excel worksheet that holds this JD's rows
    source        TEXT,                       -- pasted | file path | URL
    raw_text      TEXT,
    criteria_json TEXT NOT NULL,              -- mandatory/preferred skills, experience, seniority, location...
    weights_json  TEXT NOT NULL,              -- scoring weights for this JD (sum 100)
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Every candidate ever seen, keyed by the dedupe key.
CREATE TABLE IF NOT EXISTS candidates (
    dedupe_key   TEXT PRIMARY KEY,            -- url:<normalised profile URL> or nc:<name>|<company>
    name         TEXT NOT NULL,
    company      TEXT,
    profile_url  TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);

-- A candidate's score against one JD (latest wins).
CREATE TABLE IF NOT EXISTS candidate_scores (
    dedupe_key   TEXT NOT NULL REFERENCES candidates(dedupe_key),
    jd_id        TEXT NOT NULL REFERENCES jds(id),
    match_score  INTEGER NOT NULL CHECK (match_score BETWEEN 0 AND 100),
    breakdown    TEXT NOT NULL,
    confidence   TEXT NOT NULL CHECK (confidence IN ('High','Medium','Low')),
    run_id       INTEGER REFERENCES runs(id),
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (dedupe_key, jd_id)
);

-- Recruiter feedback on scores and statuses (pulled from the workbook or added by hand).
CREATE TABLE IF NOT EXISTS feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key       TEXT NOT NULL,
    jd_id            TEXT NOT NULL,
    recruiter_status TEXT,
    score_override   INTEGER CHECK (score_override IS NULL OR score_override BETWEEN 0 AND 100),
    note             TEXT,
    created_at       TEXT NOT NULL
);

-- Role synonyms used to widen discovery queries.
CREATE TABLE IF NOT EXISTS role_synonyms (
    role    TEXT NOT NULL,
    synonym TEXT NOT NULL,
    PRIMARY KEY (role, synonym)
);

-- Run history.
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    jd_id        TEXT NOT NULL REFERENCES jds(id),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    queries_json TEXT,
    found        INTEGER DEFAULT 0,
    inserted     INTEGER DEFAULT 0,
    updated      INTEGER DEFAULT 0,
    rejected     INTEGER DEFAULT 0,
    notes        TEXT
);

-- Audit trail (plan item 10, 2026-10-15): every source a candidate's facts came from.
-- One row per (candidate, url, snippet); written by excel_upsert.py, read by `memory.py where`.
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key   TEXT NOT NULL,
    url          TEXT NOT NULL,
    source_type  TEXT NOT NULL,               -- e.g. "Exa search (LinkedIn public profile)", "GitHub"
    query_id     TEXT,                        -- planned query that found it (e.g. "run12-q3"); NULL for a fetch
    fetched_at   TEXT NOT NULL,
    snippet_hash TEXT NOT NULL,               -- sha256 of the snippet or evidence text (the text itself is not kept here)
    jd_id        TEXT,
    UNIQUE (dedupe_key, url, snippet_hash)
);
CREATE INDEX IF NOT EXISTS sources_key ON sources(dedupe_key);

-- Evidence rows (2026-10-16, data-structure plan item 1): one row per fact or quote and the source it rests on,
-- written by the upsert from the scored batch and exported to the workbook's Evidence sheet. Quotes are short
-- phrases already shown in the sheet; `memory.py forget` deletes them with the rest of the person.
CREATE TABLE IF NOT EXISTS evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key   TEXT NOT NULL,
    jd_id        TEXT,
    fact         TEXT NOT NULL,
    quote        TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    UNIQUE (dedupe_key, jd_id, fact, quote, url)
);
CREATE INDEX IF NOT EXISTS evidence_key ON evidence(dedupe_key);

-- People who asked to be forgotten (`memory.py forget`). Only sha256 hashes of their dedupe keys
-- (normalised profile URL key and name|company key) are kept, so they are dropped before scoring.
CREATE TABLE IF NOT EXISTS suppressed (
    key_hash   TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('url','nc')),
    created_at TEXT NOT NULL,
    reason     TEXT
);

-- Weight changes made by `tune_weights.py --apply` (2026-10-18): what a JD's weights were, what they became, and the
-- evidence behind it. The human runs --apply; nothing here changes a stored score until the JD is scored again.
CREATE TABLE IF NOT EXISTS weight_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    jd_id       TEXT NOT NULL REFERENCES jds(id),
    changed_at  TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json  TEXT NOT NULL,
    n_positive  INTEGER NOT NULL,
    n_negative  INTEGER NOT NULL,
    basis_json  TEXT NOT NULL                 -- per dimension: mean share of its weight for each group, and the gap
);
