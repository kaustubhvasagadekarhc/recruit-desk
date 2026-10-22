"""Offline tests for new_rows.jsonl (the person's search history queue) and the erasure tombstones (2026-10-20). Temp copies only.

Run: uv run python scripts/test_history_rows.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excel_upsert  # noqa: E402
import last_record  # noqa: E402
from common import SCHEMA_PATH, row_key  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name)


tmp = Path(tempfile.mkdtemp(prefix="hr-"))
nr = tmp / last_record.ROWS_FILE

# ---- the helper ----
n = last_record.append_rows(tmp, [("url:https://x.test/in/ann", {"name": "Ann Rao", "title": "Engineer", "match_score": 85, "email": "ann@x.test"}),
                                  ("k2", {"name": "Call +91 98765 43210"}), ("k3", {})])
lines = [json.loads(x) for x in nr.read_text(encoding="utf-8").splitlines()]
check("append_rows writes one line per usable row", n == 1 and len(lines) == 1)
check("only the listed fields; contact-looking values and unlisted fields are dropped", lines[0]["record"] == {"name": "Ann Rao", "title": "Engineer", "match_score": 85} and "ann@x.test" not in nr.read_text(encoding="utf-8"))
check("the key is only there as a hash", lines[0]["subject_hash"] == last_record.key_hash("url:https://x.test/in/ann") and "x.test/in/ann" not in nr.read_text(encoding="utf-8"))
last_record.append_rows(tmp, [("k4", {"name": "Bob Sen"})])
check("rows are appended, not replaced", len(nr.read_text(encoding="utf-8").splitlines()) == 2)
big = tmp / "big"
big.mkdir()
(big / last_record.ROWS_FILE).write_text("x" * (last_record.MAX_ROWS_FILE_BYTES + 1))
check("the file stops growing past its limit", last_record.append_rows(big, [("k", {"name": "A"})]) == 0)

# ---- erasure ----
(tmp / last_record.SENDING_FILE).write_text(json.dumps({"agent": "candidate", "subject_hash": last_record.key_hash("k4"), "record": {"name": "Bob Sen"}}) + "\n"
                                            + json.dumps({"agent": "candidate", "subject_hash": last_record.key_hash("k9"), "record": {"name": "Zed"}}) + "\n")
removed = last_record.forget_rows(tmp, {"k4", "url:https://x.test/in/ann"})
left = [json.loads(x)["record"]["name"] for x in nr.read_text(encoding="utf-8").splitlines()] if nr.exists() else []
check("forget removes the queued rows of that person from both files", removed == 3 and left == [] and [json.loads(x)["record"]["name"] for x in (tmp / last_record.SENDING_FILE).read_text(encoding="utf-8").splitlines()] == ["Zed"])
tomb = [json.loads(x) for x in (tmp / last_record.ERASED_FILE).read_text(encoding="utf-8").splitlines()]
check("and leaves one tombstone per key so the app removes any stored copy", {t["subject_hash"] for t in tomb} == {last_record.key_hash("k4"), last_record.key_hash("url:https://x.test/in/ann")} and all(t["agent"] == "candidate" for t in tomb))
check("a person with nothing queued still gets a tombstone", last_record.forget_rows(tmp, {"nobody"}) == 0 and len((tmp / last_record.ERASED_FILE).read_text(encoding="utf-8").splitlines()) == 3)

# ---- the real upsert writes the queue (temp workbook and memory, the real ones are never touched) ----
db = tmp / "memory.db"
excel_upsert.DB_PATH, excel_upsert.WORKBOOK_PATH = db, tmp / "Candidate_Research.xlsx"
con = sqlite3.connect(db)
con.executescript(SCHEMA_PATH.read_text())
con.execute("INSERT INTO jds (id, title, sheet_name, source, raw_text, criteria_json, weights_json, created_at, updated_at) VALUES ('react-dev','React Developer','React Dev','pasted','x','{}','{}','2026-10-20','2026-10-20')")
con.commit()
con.close()
import common  # noqa: E402


def cand(name, url):
    c = {f: "" for f in ("candidate_name",)}
    c.update({"candidate_name": name, "profile_url": url, "current_title": "Frontend Engineer", "current_company": "Acme", "current_role_company": "Frontend Engineer at Acme",
              "location": "Pune, India", "match_score": 80, "score_breakdown": "skills 40/50", "evidence_confidence": "Medium", "date_found": "2026-10-20",
              "email_mobile": "", "summary": "Builds React apps", "source": "LinkedIn public profile", "evidence_notes": "React 5 years per the public profile",
              "open_to_work": "Unknown", "open_to_work_match": "No signal", "stack_match": "React matches", "experience_match": "5 years", "location_match": "Same city", "contact_source_url": ""})
    return c


def upsert(cands):
    f = tmp / "in.json"
    f.write_text(json.dumps({"jd_id": "react-dev", "candidates": cands}))
    argv = sys.argv
    sys.argv = ["excel_upsert.py", "--input", str(f)]
    try:
        excel_upsert.main()
    except SystemExit as e:
        if e.code not in (0, None):
            raise
    finally:
        sys.argv = argv


nr.unlink(missing_ok=True)
try:
    upsert([cand("Ann Rao", "https://www.linkedin.com/in/ann-rao"), cand("Bob Sen", "https://www.linkedin.com/in/bob-sen")])
    got = [json.loads(x) for x in nr.read_text(encoding="utf-8").splitlines()] if nr.exists() else []
    check("an upsert queues every NEW candidate, not only the newest", {g["record"]["name"] for g in got} == {"Ann Rao", "Bob Sen"})
    check("the queue holds the jd title and no contact data", all(g["record"].get("jd") == "React Developer" for g in got) and "@" not in nr.read_text(encoding="utf-8"))
    upsert([cand("Ann Rao", "https://www.linkedin.com/in/ann-rao")])
    check("a rerun that only updates adds nothing", len(nr.read_text(encoding="utf-8").splitlines()) == 2)
except Exception as e:   # the upsert needs a fuller payload than this sketch: say so rather than pass quietly
    import traceback; traceback.print_exc()
    check(f"an upsert queues every NEW candidate ({type(e).__name__}: {str(e)[:120]})", False)

shutil.rmtree(tmp, ignore_errors=True)
bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
