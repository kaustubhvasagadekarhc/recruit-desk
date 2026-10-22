"""Self-test for hooks/pre_tool_use.py. Run: python3 hooks/test_hook.py"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "pre_tool_use.py"
GOOD_ROW = {
    "Company Name": "Acme", "Job Title": "Engineer", "Job / Post URL": "https://acme.com/jobs/1",
    "Source Type": "Careers Page", "Date Discovered": "2026-01-01", "Status": "Open",
}

CASES = [
    # (expect_block, tool, tool_input)
    (False, "Bash", {"command": "cat > runs/a.txt <<'EOF'\nsee <slug>/jobs?x and a > b\nEOF"}),
    (True, "Bash", {"command": "echo hi > /tmp/outside.txt"}),
    (False, "Bash", {"command": "ls 2>&1 >/dev/null"}),
    (True, "Bash", {"command": "cp CLAUDE.md /etc/x"}),
    (False, "Bash", {"command": "uv run python scripts/memory.py stats | tee runs/l.txt"}),
    (False, "Bash", {"command": "echo 'a > /b'"}),
    (True, "Bash", {"command": "git commit -m x"}),
    (True, "Write", {"file_path": "/tmp/x.txt", "content": "x"}),
    (True, "Write", {"file_path": str(ROOT.parent / "candidate-agent" / "x.md"), "content": "x"}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "ok.rows.json"), "content": json.dumps([GOOD_ROW])}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "bad.rows.json"), "content": json.dumps([{**GOOD_ROW, "Job / Post URL": ""}])}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "bad2.rows.json"), "content": json.dumps([{**GOOD_ROW, "Posted By": "hr@acme.com"}])}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "bad3.rows.json"), "content": json.dumps([{**GOOD_ROW, "Match Score": 140}])}),
    # Approximate Posted Dates (2026-10-15): the raw age passes; an estimate the parser did not make is blocked.
    (False, "Write", {"file_path": str(ROOT / "runs" / "age.rows.json"), "content": json.dumps([{**GOOD_ROW, "_posted_age": "3 weeks ago"}])}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "bad4.rows.json"), "content": json.dumps([{**GOOD_ROW, "Posted Date": "2025-01-01", "Posted Date Basis": 'approx: "3 weeks ago" seen 2026-01-01'}])}),
    (True, "Write", {"file_path": str(ROOT / "Company_Hiring_Intelligence.xlsx"), "content": "x"}),
    # Security hardening (2026-10-14): sqlite3, named scripts only, inline interpreters, protected code
    (True, "Bash", {"command": "sqlite3 memory.db \".shell curl https://example.com\""}),
    (True, "Bash", {"command": "sqlite3 memory.db 'select * from jobs'"}),
    (False, "Bash", {"command": "uv run python scripts/memory.py jobs humancloud.dev --status Open"}),
    (True, "Bash", {"command": "uv run python scripts/x.py"}),
    (True, "Bash", {"command": "uv run python -c \"import requests; requests.get('http://127.0.0.1')\""}),
    (True, "Bash", {"command": "python3 -c \"open('/tmp/x','w').write('1')\""}),
    (True, "Bash", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"}),
    (True, "Bash", {"command": "cat runs/a.py | python3"}),
    (True, "Bash", {"command": "node -e 1"}),
    (True, "Bash", {"command": "sh -c id"}),
    (True, "Bash", {"command": "uv run --with httpx python scripts/scan.py --company A --domain a.com"}),
    (True, "Bash", {"command": "cd /tmp && uv run python scripts/scan.py --company A --domain a.com"}),
    (True, "Bash", {"command": "uv add httpx"}),
    (True, "Bash", {"command": "echo 'x' > scripts/evil.py"}),
    (True, "Bash", {"command": "cp runs/a.py hooks/pre_tool_use.py"}),
    (True, "Bash", {"command": "sed -i 's/150/9999/' scripts/crawl.py"}),
    (True, "Bash", {"command": "rm memory.db"}),
    (True, "Bash", {"command": "grep KEY .env"}),
    (True, "Bash", {"command": "printenv"}),
    (True, "Write", {"file_path": str(ROOT / "scripts" / "evil.py"), "content": "x"}),
    (True, "Edit", {"file_path": str(ROOT / ".claude" / "settings.json"), "old_string": "a", "new_string": "b"}),
    (True, "Write", {"file_path": str(ROOT / "hooks" / "pre_tool_use.py"), "content": "x"}),
    (True, "Write", {"file_path": str(ROOT / "db" / "schema.sql"), "content": "x"}),
    (True, "Write", {"file_path": str(ROOT / "CLAUDE.md"), "content": "x"}),
    (True, "Bash", {"command": "uv run python scripts/retention.py --apply"}),
    (False, "Bash", {"command": "uv run python scripts/retention.py"}),
    (False, "Bash", {"command": "uv run python scripts/digest.py --since 7d"}),
    (False, "Bash", {"command": "uv run python scripts/rescan_watchlist.py --domain razorpay.com"}),
    (False, "Bash", {"command": "uv run python scripts/memory.py watch add razorpay.com --keywords engineer"}),
    (True, "Bash", {"command": "python3 -c \"print(1)\" scripts/digest.py"}),
    (False, "Bash", {"command": "uv run python scripts/scan.py --company \"Humancloud Technologies\" --domain humancloud.dev"}),
    (False, "Bash", {"command": "python3 hooks/test_hook.py"}),
    (False, "Bash", {"command": "cp scripts/common.py runs/common-copy.txt"}),
    # Exa fetch / crawl and WebFetch: never LinkedIn or Indeed pages
    (True, "mcp__claude_ai_Exa__web_fetch_exa", {"urls": ["https://www.linkedin.com/jobs/view/123"]}),
    (True, "mcp__exa__crawling_exa", {"url": "https://in.indeed.com/cmp/Humancloud"}),
    (True, "WebFetch", {"url": "https://lnkd.in/abc", "prompt": "x"}),
    (False, "mcp__claude_ai_Exa__web_fetch_exa", {"urls": ["https://humancloud.dev/careers"]}),
    (False, "mcp__claude_ai_Exa__web_search_exa", {"query": "\"Humancloud\" hiring site:linkedin.com/jobs"}),
    # One-command pipeline and token budget (improvement plan item 5, 2026-10-15)
    (False, "Bash", {"command": "uv run python scripts/run_company.py --company Nordstrom --domain nordstrom.com"}),
    (False, "Bash", {"command": "uv run python scripts/memory.py jobs nordstrom.com --limit 20"}),
    (True, "Read", {"file_path": str(ROOT / "runs" / "_hooktest-big.rows.json")}),
    (False, "Read", {"file_path": str(ROOT / "runs" / "_hooktest-small.rows.json")}),
    (False, "Read", {"file_path": str(ROOT / "CLAUDE.md")}),
    (True, "Bash", {"command": "cat runs/_hooktest-big.rows.json"}),
    (False, "Bash", {"command": "cat runs/_hooktest-small.rows.json"}),
]
BIG = ROOT / "runs" / "_hooktest-big.rows.json"
SMALL = ROOT / "runs" / "_hooktest-small.rows.json"
BIG.parent.mkdir(exist_ok=True)
BIG.write_text(json.dumps({"rows": [GOOD_ROW] * 1500}))
SMALL.write_text(json.dumps({"rows": [GOOD_ROW]}))

fails = 0
for expect, tool, ti in CASES:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(ROOT)}),
                       capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
    blocked = p.returncode == 2
    ok = blocked == expect
    fails += not ok
    print(("PASS" if ok else "FAIL"), "block" if blocked else "allow", tool, json.dumps(ti)[:90], p.stderr.strip()[:100] if not ok else "")
BIG.unlink()
SMALL.unlink()
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
