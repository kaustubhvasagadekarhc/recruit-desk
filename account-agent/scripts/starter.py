"""Step 6 of /account-scan: the Conversation Starter, built only from cited facts in the same row.

  uv run python scripts/starter.py facts --rows runs/<slug>.rows.json
      prints the only facts a starter may use: each cited cell of the row with its [n] markers,
      and the Sources list.
  uv run python scripts/starter.py set --rows runs/<slug>.rows.json --text "<starter>"
      checks the text with the row rules (cites at least one [n] in Sources, every number also stated
      in another cell, no ethnicity, religion, family, health or India Connection wording). If it
      passes it is written into the row; otherwise the cell stays blank and the reason is printed.
  uv run python scripts/starter.py blank --rows runs/<slug>.rows.json --reason "<why>"

Format of a starter: "Email: <at most 2 sentences> Conference: <1 sentence> Facts used: [n], [m]".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress  # noqa: E402
from common import CITED_FIELDS, F_DEV, F_SOURCES, F_STARTER, citations  # noqa: E402
from validate_rows import load_rows, validate_payload  # noqa: E402


def _load(path: str) -> tuple[dict, dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = load_rows(payload)
    if len(rows) != 1:
        raise SystemExit("a rows file for the starter holds exactly one company row")
    return payload, rows[0]


def _save(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("facts"); p.add_argument("--rows", required=True)
    p = sp.add_parser("set"); p.add_argument("--rows", required=True); p.add_argument("--text", required=True)
    p = sp.add_parser("blank"); p.add_argument("--rows", required=True); p.add_argument("--reason", required=True)
    a = ap.parse_args()
    payload, row = _load(a.rows)
    if a.cmd == "facts":
        for c in CITED_FIELDS + [F_DEV]:
            v = str(row.get(c) or "").strip()
            if c != F_STARTER and v and citations(v):
                print(f"{c}: {v}")
        print("Sources:")
        print(row.get(F_SOURCES, ""))
        return
    if a.cmd == "blank":
        row[F_STARTER] = f"Not found (checked: {' '.join(a.reason.split())[:200]})"
        _save(a.rows, payload)
        progress.result(f"starter left blank: {a.reason}")
        return
    row[F_STARTER] = " ".join(a.text.split())
    errs = validate_payload(payload)
    mine = [e for e in errs if "Conversation Starter" in e]
    if mine or errs:
        row[F_STARTER] = "Not found (checked: the cited facts in this row; the drafted starter failed the citation check)"
        _save(a.rows, payload)
        for e in (mine or errs)[:6]:
            progress.sub(e)
        progress.result("starter left blank: it failed the citation check (see the lines above)")
        return
    _save(a.rows, payload)
    progress.result(f"starter written, citing {sorted(set(citations(row[F_STARTER])))}")


if __name__ == "__main__":
    main()
