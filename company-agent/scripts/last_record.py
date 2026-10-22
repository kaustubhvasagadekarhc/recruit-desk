"""last_record.json: the last NEW record this assistant created, as a small plain file (2026-10-19). A copy of the candidate agent's module with its own fields: the assistants share no code.

The assistant does all its processing here and sends nothing anywhere. Recruit Desk reads this file and, while its person is signed
in, stores it as "the last record" in the sign-in server's database. So the file holds:
- a fixed list of fields only (no email, phone, evidence text or notes), each cut to a length the server accepts;
- the record's key as a sha256 hash, never the key itself (the server uses the hash to remove exactly this record again);
- when it was created.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

AGENT = "company"
FIELDS = ('job_title', 'company', 'location', 'status', 'source_type', 'posted_date', 'listing_url', 'date_discovered')
MAX_VALUE, MAX_URL = 300, 600
FILE_NAME = "last_record.json"


def folder(default) -> Path:
    """Where the hand-off files (last_record.json, new_rows.jsonl, erased_rows.jsonl) live: the assistant's own folder, which Recruit Desk
    reads them from, even when the Excel file is kept in its data folder (RECRUIT_DESK_HANDOFF_DIR). By hand, next to `default`."""
    return Path(os.environ.get("RECRUIT_DESK_HANDOFF_DIR") or default)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d .()-]{7,}\d")


def key_hash(key: str) -> str:
    return hashlib.sha256(f"{AGENT}:{key}".encode()).hexdigest()


def clean(record: dict) -> dict:
    """Only the listed fields, scalars only, cut to length; a value that looks like contact details is dropped."""
    out = {}
    for f in FIELDS:
        v = record.get(f)
        if isinstance(v, bool) or v is None or v == "":
            continue
        if isinstance(v, str):
            v = v.strip()
            limit = MAX_URL if f.endswith("_url") else MAX_VALUE
            if _EMAIL.search(v) or (not f.endswith("_url") and any(len(re.sub(r"\D", "", m.group(0))) >= 9 for m in _PHONE.finditer(v))):
                continue
            out[f] = v[:limit]
        elif isinstance(v, (int, float)):
            out[f] = v
    return out


def write(path: Path, key: str, record: dict, created_at: str | None = None) -> dict:
    """Replace the file with this record (atomically). Returns what was written."""
    doc = {"agent": AGENT, "subject_hash": key_hash(key), "record": clean(record),
           "created_at": created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    path = Path(path)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=path.parent)
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return doc


def read(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


ROWS_FILE = "new_rows.jsonl"
ERASED_FILE = "erased_rows.jsonl"
MAX_ROWS_FILE_BYTES = 4_000_000


def append_rows(folder: Path, rows: list, created_at: str | None = None) -> int:
    """Add every NEW row of this upsert, as one JSON line each, to new_rows.jsonl (2026-10-20). `rows` is a list of (key, record).

    Same rules as the last record: a fixed list of fields, no contact details, the key only as a hash, no network. Recruit Desk reads the
    file, stores the rows in the person's own search history and removes what it stored. The file stops growing past 4 MB.
    """
    path = Path(folder) / ROWS_FILE
    try:
        if path.stat().st_size > MAX_ROWS_FILE_BYTES:
            return 0
    except OSError:
        pass
    stamp = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = []
    for key, record in rows:
        rec = clean(record)
        if rec:
            lines.append(json.dumps({"agent": AGENT, "subject_hash": key_hash(key), "created_at": stamp, "record": rec}, ensure_ascii=False))
    if not lines:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)
