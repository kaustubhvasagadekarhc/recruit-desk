-- SQLite memory for the account agent. Applied idempotently by scripts/memory.py.

CREATE TABLE IF NOT EXISTS companies (
    domain         TEXT PRIMARY KEY,          -- bare host, no www (e.g. razorpay.com)
    name           TEXT NOT NULL,
    list           TEXT NOT NULL DEFAULT 'Accounts',   -- worksheet in Account_Research.xlsx
    first_checked  TEXT,
    last_checked   TEXT,
    confidence     TEXT
);

-- Every cited fact of the latest row, one line per field (so reruns can refresh stale fields).
CREATE TABLE IF NOT EXISTS facts (
    domain   TEXT NOT NULL REFERENCES companies(domain),
    field    TEXT NOT NULL,
    value    TEXT NOT NULL,
    sources  TEXT NOT NULL,                  -- JSON list of the URLs the value cites
    checked  TEXT NOT NULL,
    PRIMARY KEY (domain, field)
);

-- Leaders seen per company (names and titles from public pages only; no contact details).
CREATE TABLE IF NOT EXISTS people (
    domain   TEXT NOT NULL REFERENCES companies(domain),
    name     TEXT NOT NULL,
    title    TEXT,
    source   TEXT,
    checked  TEXT NOT NULL,
    PRIMARY KEY (domain, name)
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY,
    domain    TEXT NOT NULL,
    started   TEXT NOT NULL,
    filled    INTEGER,
    blank     INTEGER,
    note      TEXT
);

-- Apollo.io answers (optional paid enrichment), kept 30 days so a rescan spends no credits. Never the API key.
CREATE TABLE IF NOT EXISTS apollo_cache (
    key      TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    domain   TEXT NOT NULL,
    body     TEXT NOT NULL,
    fetched  TEXT NOT NULL
);
