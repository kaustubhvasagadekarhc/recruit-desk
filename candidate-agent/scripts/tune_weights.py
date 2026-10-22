"""Suggest (and, for the human, apply) a small shift in a JD's scoring weights from recruiter feedback (plan item 9).

uv run scripts/tune_weights.py --jd <jd_id> [--min-each 5] [--max-shift 5]          # dry run: prints the suggestion
uv run scripts/tune_weights.py --jd <jd_id> --apply                                # human only, from a terminal

Evidence: the latest recruiter status per person for the JD (synced from the workbook first). Shortlisted and Contacted
people are the positives, Rejected the negatives; Reviewed and New say nothing. For each weighted dimension it compares
the mean share of that dimension's weight earned by the positives and by the negatives. A dimension where positives earn
clearly more (gap >= 0.10) is one the recruiter rewards; one where the groups score alike (gap <= 0.02) is not telling
them apart. It moves one point at a time, at most --max-shift (never more than 5) points, from the least telling
dimension to the most telling one, keeps the total at 100 and never takes a weight below 5.

It does nothing until each group has --min-each people (default 5), and --apply is the only thing that writes: it
updates the JD's weights in memory.db (and in jds/<jd_id>.json when that file exists, so a later add-jd does not undo
it) and logs the change in weight_changes. Scores already in the workbook keep their old values until that JD is
scored again. A suggestion from a handful of people is a hint, not a finding; the script says so.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory  # noqa: E402
import progress  # noqa: E402
from common import ROOT, score_parts  # noqa: E402

POSITIVE, NEGATIVE = ("Shortlisted", "Contacted"), ("Rejected",)
FIELD = {"core_skills": "pts_core", "experience": "pts_experience", "domain_projects": "pts_domain",
         "seniority": "pts_seniority", "location": "pts_location", "preferred_skills": "pts_preferred"}
FLOOR, MAX_SHIFT_CAP = 5, 5
RECEIVE_GAP, DONATE_GAP = 0.10, 0.02


def collect(con, jd_id: str, weights: dict) -> tuple[list[dict], list[dict]]:
    """(positives, negatives): each a {dimension: share of its weight earned} for people whose breakdown names every dimension."""
    latest = {}
    for r in con.execute("SELECT dedupe_key, recruiter_status FROM feedback WHERE jd_id=? AND recruiter_status IS NOT NULL ORDER BY id", (jd_id,)):
        latest[r["dedupe_key"]] = r["recruiter_status"]
    pos, neg = [], []
    for key, status in latest.items():
        group = pos if status in POSITIVE else neg if status in NEGATIVE else None
        if group is None:
            continue
        row = con.execute("SELECT breakdown FROM candidate_scores WHERE dedupe_key=? AND jd_id=?", (key, jd_id)).fetchone()
        parts = score_parts(row["breakdown"]) if row else {}
        if not all(FIELD[d] in parts for d in weights if d in FIELD):
            continue
        group.append({d: parts[FIELD[d]] / weights[d] for d in weights if d in FIELD and weights[d] > 0})
    return pos, neg


def analyse(pos: list[dict], neg: list[dict], weights: dict, max_shift: int = MAX_SHIFT_CAP) -> tuple[dict, dict]:
    """(basis per dimension, new weights). New weights equal the old ones when nothing qualifies."""
    mean = lambda rows, d: sum(r[d] for r in rows) / len(rows)  # noqa: E731
    basis = {d: {"weight": weights[d], "positive": round(mean(pos, d), 3), "negative": round(mean(neg, d), 3),
                 "gap": round(mean(pos, d) - mean(neg, d), 3)} for d in weights if d in FIELD and weights[d] > 0}
    new = dict(weights)
    for _ in range(min(max_shift, MAX_SHIFT_CAP)):
        receivers = sorted((d for d, b in basis.items() if b["gap"] >= RECEIVE_GAP), key=lambda d: -basis[d]["gap"])
        donors = sorted((d for d, b in basis.items() if b["gap"] <= DONATE_GAP and new[d] > FLOOR), key=lambda d: basis[d]["gap"])
        if not receivers or not donors:
            break
        new[donors[0]] -= 1
        new[receivers[0]] += 1
    return basis, new


def apply(con, jd_id: str, before: dict, after: dict, n_pos: int, n_neg: int, basis: dict, jd_file: Path | None = None) -> None:
    if sum(after.values()) != 100:
        progress.fail_exit(f"new weights add up to {sum(after.values())}, not 100; nothing written")
    now = memory.now()
    con.execute("UPDATE jds SET weights_json=?, updated_at=? WHERE id=?", (json.dumps(after), now, jd_id))
    con.execute("INSERT INTO weight_changes (jd_id, changed_at, before_json, after_json, n_positive, n_negative, basis_json) VALUES (?,?,?,?,?,?,?)",
                (jd_id, now, json.dumps(before), json.dumps(after), n_pos, n_neg, json.dumps(basis)))
    con.commit()
    if jd_file and jd_file.exists():
        data = json.loads(jd_file.read_text())
        data["weights"] = after
        jd_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jd", required=True)
    ap.add_argument("--min-each", type=int, default=5)
    ap.add_argument("--max-shift", type=int, default=MAX_SHIFT_CAP)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.min_each < 1:
        ap.error("--min-each must be at least 1")
    con = memory.connect()
    jd = memory.get_jd(con, a.jd)
    synced = memory.sync_feedback(con, a.jd)
    weights = jd["weights"]
    pos, neg = collect(con, a.jd, weights)
    progress.sub(f"{synced} status changes synced · {len(pos)} Shortlisted/Contacted and {len(neg)} Rejected people with a full breakdown")
    if len(pos) < a.min_each or len(neg) < a.min_each:
        progress.result(f"not enough feedback yet: need {a.min_each} of each, have {len(pos)} Shortlisted/Contacted and {len(neg)} Rejected. Weights unchanged")
        print(json.dumps({"jd_id": a.jd, "changed": False, "positive": len(pos), "negative": len(neg), "needed_each": a.min_each}))
        return
    basis, new = analyse(pos, neg, weights, a.max_shift)
    for d, b in basis.items():
        print(f"  {d:<17} weight {weights[d]:>2} -> {new[d]:>2} · share earned: shortlisted {b['positive']:.2f}, rejected {b['negative']:.2f}, gap {b['gap']:+.2f}")
    changed = new != weights
    small = len(pos) < 10 or len(neg) < 10
    if not changed:
        progress.result("no change suggested: no dimension separates the two groups clearly enough, or none can give up a point")
    else:
        progress.result(f"suggestion: {', '.join(f'{d} {weights[d]}->{new[d]}' for d in weights if new[d] != weights[d])}"
                        + (" (small sample: treat as a hint)" if small else ""))
    if a.apply and changed:
        apply(con, a.jd, weights, new, len(pos), len(neg), basis, ROOT / "jds" / f"{a.jd}.json")
        progress.result("applied: weights saved. Scores in the workbook change when this JD is scored again")
    elif a.apply:
        progress.result("nothing to apply")
    elif changed:
        print("dry run: nothing saved. A human adds --apply, from a terminal, to save it.")
    print(json.dumps({"jd_id": a.jd, "changed": changed, "applied": bool(a.apply and changed), "before": weights, "after": new,
                      "positive": len(pos), "negative": len(neg)}))


if __name__ == "__main__":
    main()
