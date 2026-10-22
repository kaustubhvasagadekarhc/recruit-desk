"""Deduplicate a candidate batch, and flag who memory already holds for this JD.

uv run scripts/dedupe.py --input runs/<file>.json [--out runs/<file>.dedup.json]

Key: normalised profile URL, else name plus company. Within the batch, later
duplicates are merged into the first (empty fields filled, evidence notes joined).
Each kept candidate gets "_key" and "_known" (true when already scored for the JD).
People in the suppressed table (`memory.py forget`) are dropped first (plan item 10).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import DB_PATH, drop_suppressed, row_key  # noqa: E402
import progress  # noqa: E402


def key_of(c: dict) -> str:
    return row_key(c.get("profile_url", ""), c.get("candidate_name", ""), c.get("current_role_company", ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    payload = json.loads(Path(a.input).read_text())
    known = set()
    if DB_PATH.exists():
        known = {r[0] for r in sqlite3.connect(DB_PATH).execute(
            "SELECT dedupe_key FROM candidate_scores WHERE jd_id=?", (payload.get("jd_id"),))}
    payload["candidates"], dropped = drop_suppressed(payload["candidates"], DB_PATH)
    if dropped:
        progress.sub(f"{dropped} suppressed people dropped (they asked to be forgotten)")
    kept: dict[str, dict] = {}
    merged = 0
    total = len(payload["candidates"])
    for i, c in enumerate(payload["candidates"], 1):
        if progress.every(i, total):
            progress.sub(f"checking {i}/{total}")
        k = key_of(c)
        if k in kept:
            first = kept[k]
            progress.sub(f"duplicate merged: {c.get('candidate_name')} ({c.get('profile_url')})")
            for f, v in c.items():
                if f == "evidence_notes" and v and v not in first.get(f, ""):
                    first[f] = (first.get(f, "") + " | " + v).strip(" |")
                elif v and not first.get(f):
                    first[f] = v
            merged += 1
            continue
        c["_key"], c["_known"] = k, k in known
        kept[k] = c
    payload["candidates"] = list(kept.values())
    out = Path(a.out or a.input)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    known_n = sum(c["_known"] for c in kept.values())
    progress.result(f"{total + dropped} in, {dropped} suppressed, {len(kept)} kept, {merged} merged as duplicates, "
                    f"{known_n} already in memory (refresh)")
    print(json.dumps({"kept": len(kept), "merged_in_batch": merged, "suppressed_dropped": dropped,
                      "already_in_memory": sum(c["_known"] for c in kept.values()), "out": str(out)}, indent=2))


if __name__ == "__main__":
    main()
