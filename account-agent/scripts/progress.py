"""Visible checkpoints for an /account-scan run (standard library only).

Copied from company-agent (no shared code between the agents) and adapted.
One shared helper: the /account-scan skill, the subagents and every pipeline script
print their progress through it, so the user always sees which step a run is on.

  uv run python scripts/progress.py plan --slug <company-slug> [--steps "Parse JD|Discover|..."]
  uv run python scripts/progress.py start <N>            # ▶ Step N/T · <label>
  uv run python scripts/progress.py sub "<text>"         #   … <text>
  uv run python scripts/progress.py done <N> "<result>"  # ✓ Step N/T · <result>
  uv run python scripts/progress.py fail <N> "<reason>"  # ✗ Step N/T · <reason>
  uv run python scripts/progress.py summary "<text>"     # closes the run
  uv run python scripts/progress.py status               # plan with ticks, and the log path

`plan` creates runs/<YYYY-MM-DD-HHMM>-<slug>/progress.log and points
runs/.current.json at it. Every line printed while a run is open is also
appended to that log with a timestamp. Scripts call result()/failed(), which
close whichever step is open with real counts; run outside a /account-scan run they
print the same lines without a step number and log nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
# Recruit Desk gives every run its own RD_RUN_ID, so two scans running at the
# same time keep separate state instead of writing into each other's log.
_RUN_ID = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("RD_RUN_ID", ""))[:64]
STATE = RUNS / (f".current-{_RUN_ID}.json" if _RUN_ID else ".current.json")
DEFAULT_STEPS = [
    "Verifying the company and its website",
    "Company facts (site, Wikidata, SEC EDGAR, GitHub, news)",
    "Open engineering roles (company-agent)",
    "Leadership, India Connection and contacts",
    "HumanCloud match (team.csv)",
    "Conversation starter",
    "Validating and writing Excel",
    "Reporting",
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def _load() -> dict | None:
    if not STATE.exists():
        return None
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    return None if st.get("closed") else st


def _save(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def emit(line: str, st: dict | None = None) -> None:
    """Print a checkpoint line and, while a run is open, append it to progress.log."""
    print(line, flush=True)
    st = st if st is not None else _load()
    if st:
        with open(ROOT / st["dir"] / "progress.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")


def _tag(st: dict, n: int) -> str:
    return f"Step {n}/{len(st['steps'])}"


def plan(slug: str, steps: list[str]) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    d = RUNS / f"{stamp}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    st = {"dir": str(d.relative_to(ROOT)), "slug": slug, "steps": steps,
          "state": ["todo"] * len(steps), "current": None, "closed": False}
    _save(st)
    emit(f"Plan · {slug} · {len(steps)} steps", st)
    for i, s in enumerate(steps, 1):
        emit(f"  {i}. {s}", st)
    emit(f"  log: {st['dir']}/progress.log", st)
    return st


def start(n: int, label: str | None = None) -> None:
    st = _load()
    if not st:
        emit(f"▶ {label or f'step {n}'}")
        return
    st["state"][n - 1], st["current"] = "doing", n
    _save(st)
    emit(f"▶ {_tag(st, n)} · {label or st['steps'][n - 1]}", st)


def sub(text: str) -> None:
    emit(f"  … {text}")


def every(i: int, n: int) -> bool:
    """True about five times across a loop of n items, and always on the last one."""
    return i == n or i % max(1, n // 5) == 0


def _close(mark: str, status: str, text: str, n: int | None) -> None:
    st = _load()
    n = n or (st or {}).get("current")
    if not st or not n:
        emit(f"{mark} {text}")
        return
    st["state"][n - 1] = status
    if st["current"] == n:
        st["current"] = None
    _save(st)
    emit(f"{mark} {_tag(st, n)} · {text}", st)


def result(text: str, n: int | None = None) -> None:
    """Close the open step (or step n) with a ✓ line carrying real counts."""
    _close("✓", "done", text, n)


def failed(text: str, n: int | None = None) -> None:
    """Close the open step (or step n) with a ✗ line."""
    _close("✗", "failed", text, n)


def fail_exit(text: str, code: int = 1) -> None:
    failed(text)
    sys.exit(code)


def summary(text: str) -> None:
    st = _load()
    for line in text.splitlines():
        emit(f"■ {line}" if line.strip() else "", st)
    if st:
        emit(f"■ progress log: {st['dir']}/progress.log", st)
        st["closed"] = True
        _save(st)


def status() -> None:
    st = _load()
    if not st:
        print("no open run")
        return
    marks = {"todo": " ", "doing": "▶", "done": "✓", "failed": "✗"}
    for i, (s, state) in enumerate(zip(st["steps"], st["state"]), 1):
        print(f"[{marks[state]}] {i}. {s}")
    print(f"log: {st['dir']}/progress.log")


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan"); p.add_argument("--slug", required=True); p.add_argument("--steps")
    p = sp.add_parser("start"); p.add_argument("n", type=int); p.add_argument("label", nargs="?")
    p = sp.add_parser("sub"); p.add_argument("text")
    p = sp.add_parser("done"); p.add_argument("n", type=int); p.add_argument("text")
    p = sp.add_parser("fail"); p.add_argument("n", type=int); p.add_argument("text")
    p = sp.add_parser("summary"); p.add_argument("text")
    sp.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "plan":
        plan(a.slug, [s.strip() for s in a.steps.split("|")] if a.steps else DEFAULT_STEPS)
    elif a.cmd == "start":
        start(a.n, a.label)
    elif a.cmd == "sub":
        sub(a.text)
    elif a.cmd == "done":
        result(a.text, a.n)
    elif a.cmd == "fail":
        failed(a.text, a.n)
    elif a.cmd == "summary":
        summary(a.text)
    else:
        status()


if __name__ == "__main__":
    main()
