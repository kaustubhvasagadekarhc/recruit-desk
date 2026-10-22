"""Self-test for .claude/hooks/guard.py. Run: python3 .claude/hooks/test_guard.py

Each case feeds the guard one PreToolUse event and checks it is blocked (exit 2) or allowed.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"
FETCH = "mcp__claude_ai_Exa__web_fetch_exa"
SEARCH = "mcp__claude_ai_Exa__web_search_exa"


def known_name() -> str:
    """A two-word candidate name really in memory.db (the name-search case must use live memory)."""
    try:
        con = sqlite3.connect(f"file:{ROOT / 'memory.db'}?mode=ro", uri=True)
        names = [r[0] for r in con.execute("SELECT DISTINCT name FROM candidates") if r[0] and len(r[0].split()) >= 2]
        con.close()
    except sqlite3.Error:
        names = []
    return names[0] if names else ""


# Key factors (2026-10-15): two batches the upsert guard must judge (written to runs/, removed after the run)
ROW = {"candidate_name": "Guard Test", "current_role_company": "FE @ Acme", "profile_url": "https://github.com/guard-test",
       "source": "fixture", "date_found": "2026-10-15", "match_score": 50, "score_breakdown": "x", "evidence_confidence": "Medium",
       "evidence_notes": "React dev | #OpenToWork", "recruiter_status": "New", "open_to_work": "Yes",
       "open_to_work_quote": "#OpenToWork", "open_to_work_match": 'Yes: "#OpenToWork"', "stack_match": "1/1 must-have: React",
       "experience_match": "not stated vs 5-9 required", "location_match": "Unknown"}
BATCHES = {
    "runs/.guard-test-factors-ok.json": [ROW],
    "runs/.guard-test-no-factors.json": [{k: v for k, v in ROW.items() if k != "stack_match"}],
    "runs/.guard-test-fake-otw.json": [dict(ROW, open_to_work_quote="actively looking")],
}
for f, rows in BATCHES.items():
    (ROOT / f).write_text(json.dumps({"jd_id": "guard-test", "candidates": rows}))
NAME = known_name()

CASES = [
    # (expect_block, tool, tool_input) -- step 1: sqlite3 and memory queries
    (True, "Bash", {"command": "sqlite3 memory.db \".shell curl https://example.com\""}),
    (True, "Bash", {"command": "sqlite3 memory.db 'select * from feedback'"}),
    (True, "Bash", {"command": "echo '.system id' | sqlite3 memory.db"}),
    (False, "Bash", {"command": "uv run scripts/memory.py feedback-list --jd senior-react-frontend-pune"}),
    # pipeline unit tests (2026-10-15): the runner is allowed, single test files and edits of tests/ are not
    (False, "Bash", {"command": "uv run tests/run_tests.py"}),
    (True, "Bash", {"command": "uv run tests/test_score.py"}),
    (True, "Bash", {"command": "echo x > tests/evil.py"}),
    (False, "Bash", {"command": "uv run scripts/memory.py plan-queries --jd senior-react-frontend-pune"}),
    # step 2: named scripts only, no inline interpreters, no writes to code
    (True, "Bash", {"command": "uv run scripts/x.py"}),
    (True, "Bash", {"command": "uv run runs/test-batch-2026-10-14.py"}),
    (True, "Bash", {"command": "uv run python -c \"open('/tmp/x','w').write('1')\""}),
    (True, "Bash", {"command": "python3 -c \"import os; os.system('id')\""}),
    (True, "Bash", {"command": "python -c 'print(1)'"}),
    (True, "Bash", {"command": "python3 - <<'EOF'\nopen('scripts/x.py','w').write('x')\nEOF"}),
    (True, "Bash", {"command": "cat runs/a.py | python3"}),
    (True, "Bash", {"command": "node -e \"require('fs').writeFileSync('/tmp/x','1')\""}),
    (True, "Bash", {"command": "bash -c 'id'"}),
    (True, "Bash", {"command": "uv run --with requests scripts/memory.py init"}),
    (True, "Bash", {"command": "uv run --directory /tmp scripts/memory.py init"}),
    (True, "Bash", {"command": "cd runs && uv run scripts/memory.py init"}),
    (True, "Bash", {"command": "PYTHONPATH=runs uv run scripts/memory.py init"}),
    (True, "Bash", {"command": "timeout 5 python3 -c 'print(1)'"}),
    (True, "Bash", {"command": "echo $(python3 -c 'print(1)')"}),
    (True, "Bash", {"command": "uv pip install requests"}),
    (True, "Bash", {"command": "uvx somepkg"}),
    (True, "Bash", {"command": "echo 'print(1)' > scripts/evil.py"}),
    (True, "Bash", {"command": "cp runs/a.py scripts/memory.py"}),
    (True, "Bash", {"command": "sed -i 's/a/b/' .claude/settings.json"}),
    (True, "Bash", {"command": "echo x >> CLAUDE.md"}),
    (True, "Bash", {"command": "rm memory.db"}),
    (True, "Bash", {"command": "find . -name '*.py' -exec python3 {} \\;"}),
    (True, "Bash", {"command": "awk 'BEGIN{system(\"id\")}'"}),
    (True, "Bash", {"command": "./run.sh"}),
    (True, "Bash", {"command": "printenv"}),
    (True, "Bash", {"command": "grep KEY .env"}),
    (True, "Bash", {"command": "echo hi > /tmp/outside.txt"}),
    (True, "Write", {"file_path": str(ROOT / "scripts" / "evil.py"), "content": "x"}),
    (True, "Edit", {"file_path": str(ROOT / ".claude" / "settings.json"), "old_string": "a", "new_string": "b"}),
    (True, "Write", {"file_path": str(ROOT / "CLAUDE.md"), "content": "x"}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "memory.sql"), "content": "ATTACH '/tmp/x' AS y;"}),
    (True, "Bash", {"command": "uv run scripts/retention.py --apply"}),
    (False, "Bash", {"command": "uv run scripts/retention.py"}),
    (False, "Bash", {"command": "uv run scripts/score.py --input runs/senior-react-frontend-pune-2026-10-14.json"}),
    (False, "Bash", {"command": "uv run scripts/progress.py sub 'query 1/3 · 10 hits'"}),
    (False, "Bash", {"command": "uv run scripts/memory.py history | head -5"}),
    (False, "Bash", {"command": "cat > runs/note.json <<'EOF'\n{\"python3 -c\": \"text in a heredoc body is data\"}\nEOF"}),
    (False, "Bash", {"command": "ls runs"}),
    (False, "Bash", {"command": "python3 .claude/hooks/test_guard.py"}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "batch.json"), "content": "{}"}),
    # step 4: no fetch of LinkedIn or Indeed pages
    (True, FETCH, {"urls": ["https://www.linkedin.com/in/someone"]}),
    (True, FETCH, {"urls": ["https://in.linkedin.com/in/someone"]}),
    (True, FETCH, {"urls": ["https://in.indeed.com/viewjob?jk=1"]}),
    (True, "mcp__exa__crawling_exa", {"url": "linkedin.com/jobs/view/1"}),
    (True, "WebFetch", {"url": "https://www.indeed.com/q-react-jobs.html", "prompt": "x"}),
    (False, FETCH, {"urls": ["https://github.com/someone"]}),
    # plan items 6 and 10 (2026-10-15): evidence plan, audit trail, forget (needs --yes; settings.json asks the human)
    (False, "Bash", {"command": "uv run scripts/evidence.py plan --input runs/batch.json --top 10"}),
    (False, "Bash", {"command": "uv run scripts/memory.py where url:https://github.com/someone"}),
    (False, "Bash", {"command": "uv run scripts/memory.py forget url:https://github.com/someone --yes"}),
    (True, "Bash", {"command": "uv run scripts/memory.py forget url:https://github.com/someone"}),
    (False, "Bash", {"command": "uv run scripts/retention.py --new-days 90"}),
    (True, "Bash", {"command": "uv run scripts/evidence.py plan --input runs/b.json > scripts/x.py"}),
    # plan item 8 (2026-10-16): CRM export is a named script; the builder-only backfill is not
    (False, "Bash", {"command": "uv run scripts/export_crm.py --format hubspot"}),
    (False, "Bash", {"command": "uv run scripts/export_crm.py --format salesforce --status Shortlisted"}),
    # plan item 9 (2026-10-16): GitHub / Stack Exchange skill evidence for linked profiles
    (False, "Bash", {"command": "uv run scripts/dev_evidence.py fetch --input runs/batch.json --top 10"}),
    (False, "Bash", {"command": "uv run scripts/dev_evidence.py fetch --input runs/batch.json --dry-run"}),
    (True, "Bash", {"command": "uv run scripts/dev_evidence.py fetch --input runs/b.json > scripts/x.py"}),
    (True, "Bash", {"command": "uv run python -c 'import scripts.dev_evidence'"}),
    (True, "Bash", {"command": "uv run scripts/backfill_structured.py"}),
    # step 5: search by skills, role and location, never by a candidate's name
    (bool(NAME), SEARCH, {"query": f"{NAME or 'Nobody Known'} React Pune"}),
    (True, SEARCH, {"query": "site:linkedin.com/in/vikranti-jadhav-54510b220"}),
    (False, SEARCH, {"query": "category:people Senior React Frontend Developer Pune TypeScript"}),
    (False, SEARCH, {"query": "site:linkedin.com/in React developer Pune"}),
    # key factors (2026-10-15): optional open-to-work query (no name), factor columns validated before Excel writes
    (False, SEARCH, {"query": '"open to work" React developer Pune site:linkedin.com/in'}),
    (False, "Bash", {"command": "uv run scripts/memory.py plan-queries --jd senior-react-frontend-pune --open-to-work"}),
    (False, "Bash", {"command": "uv run scripts/excel_upsert.py --input runs/.guard-test-factors-ok.json --dry-run"}),
    (True, "Bash", {"command": "uv run scripts/excel_upsert.py --input runs/.guard-test-no-factors.json"}),
    (True, "Bash", {"command": "uv run scripts/excel_upsert.py --input runs/.guard-test-fake-otw.json"}),
    # feedback loop (2026-10-18): tuning is a dry run for the agent, --apply is the human's
    (False, "Bash", {"command": "uv run scripts/tune_weights.py --jd senior-react-frontend-pune"}),
    (True, "Bash", {"command": "uv run scripts/tune_weights.py --jd senior-react-frontend-pune --apply"}),
    (False, "Bash", {"command": "uv run scripts/export_shortlist.py --jd senior-react-frontend-pune --statuses Shortlisted,Contacted"}),
    (False, "Bash", {"command": "uv run scripts/outreach.py plan --jd senior-react-frontend-pune --company Acme"}),
    (False, "Bash", {"command": "uv run scripts/outreach.py write --jd senior-react-frontend-pune --company Acme --input runs/outreach-x.json"}),
]

fails = 0
for expect, tool, ti in CASES:
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(ROOT)}),
                       capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    blocked = p.returncode == 2
    ok = blocked == expect
    fails += not ok
    why = p.stderr.strip().replace("BLOCKED by candidate-agent guard: ", "")[:90]
    print(("PASS" if ok else "FAIL"), "block" if blocked else "allow", tool.replace("mcp__claude_ai_Exa__", "exa:"), json.dumps(ti, ensure_ascii=False)[:70], "|", why)
for f in BATCHES:
    (ROOT / f).unlink(missing_ok=True)
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
