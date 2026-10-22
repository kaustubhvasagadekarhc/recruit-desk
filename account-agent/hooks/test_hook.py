"""Self-test for hooks/pre_tool_use.py. Run: python3 hooks/test_hook.py"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "pre_tool_use.py"
COMPANY = ROOT.parent / "company-agent"
GOOD = {
    "Company": "Acme", "Domain": "acme.com", "What They Build (brief)": "Payments APIs for shops [1]",
    "Sources": "[1] https://acme.com/about", "Confidence": "Medium", "Last Checked": "2026-10-15",
}
NF = "Not found (checked: site, Wikidata, EDGAR, 3 Exa searches)"
for _c in ("Product Type", "HQ / US Office", "India Office", "Product?", "Funding", "Who to Contact",
           "Leadership Education & Background"):
    GOOD[_c] = NF
GOOD["Company Brief (founded, size, customers, milestones)"] = "Founded 2014 [1]; approx. 40 employees (About page) [1]"
GOOD["India Connection (US-based leader)"] = "No evidence found (checked: team page, 2 Exa searches)"
DRAFT = {k: v for k, v in GOOD.items() if k != "Last Checked"}


def rows(**kw):
    return json.dumps({"meta": {"list": "Accounts"}, "rows": [{**GOOD, **kw}]})


NP = "Not published (checked: team page, contact page, 2 Exa searches)"
PERSON = {"name": "Jane Doe", "title": "CTO", "location": "San Francisco, CA", "source": "https://acme.com/about",
          "verified": "2026-10-15", "email": NP, "phone": NP, "channels_checked": "team page, 2 Exa searches"}


def people(**kw):
    return rows(_contacts=[{**PERSON, **kw}])


CASES = [
    # (expect_block, tool, tool_input)
    (False, "Bash", {"command": "uv run python scripts/lookup.py wikidata Razorpay --domain razorpay.com"}),
    (False, "Bash", {"command": "uv run python scripts/roles_from_company_agent.py --company Razorpay --domain razorpay.com"}),
    (False, "Bash", {"command": "uv run python scripts/assemble.py --draft runs/razorpay.draft.json"}),
    (False, "Bash", {"command": "uv run python scripts/starter.py facts --rows runs/razorpay.rows.json"}),
    (False, "Bash", {"command": "uv run python scripts/batch.py plan --csv runs/companies.csv"}),
    (False, "Bash", {"command": "uv run python scripts/match_team.py check"}),
    (False, "Bash", {"command": "uv run python scripts/retention.py"}),
    (False, "Bash", {"command": "python3 hooks/test_hook.py"}),
    (False, "Bash", {"command": "uv run python scripts/progress.py start 2"}),
    (False, "Bash", {"command": "uv run python scripts/lookup.py formd Freshworks"}),
    (False, "Bash", {"command": "uv run python scripts/lookup.py gleif Freshworks"}),
    (False, "Bash", {"command": "uv run python scripts/lookup.py dns freshworks.com"}),
    (False, "Bash", {"command": "uv run python scripts/export_crm.py --format hubspot"}),
    (True, "Bash", {"command": "uv run python scripts/evidence.py"}),
    (True, "Write", {"file_path": str(ROOT / "scripts" / "export_crm.py"), "content": "x"}),
    (True, "Bash", {"command": "uv run python scripts/retention.py --apply"}),
    (True, "Bash", {"command": "uv run python scripts/run_company.py --company A --domain a.com"}),
    (True, "Bash", {"command": "cd ../company-agent && uv run python scripts/run_company.py --company A --domain a.com"}),
    (True, "Bash", {"command": "cat ../company-agent/CLAUDE.md"}),
    (True, "Bash", {"command": "ls ../company-agent/runs"}),
    (True, "Read", {"file_path": str(COMPANY / "Company_Hiring_Intelligence.xlsx")}),
    (True, "Read", {"file_path": str(ROOT.parent / "candidate-agent" / "CLAUDE.md")}),
    (True, "Grep", {"pattern": "x", "path": str(COMPANY)}),
    (True, "Write", {"file_path": str(COMPANY / "x.md"), "content": "x"}),
    (True, "Read", {"file_path": str(ROOT / "team.csv")}),
    (True, "Bash", {"command": "cat team.csv"}),
    (True, "Bash", {"command": "grep Priya ./team.csv"}),
    (True, "Bash", {"command": "head team*.csv"}),
    (True, "Bash", {"command": "wc -l < team.csv"}),
    (False, "Bash", {"command": "uv run python scripts/progress.py summary \"gaps: Warm Connection needs team.csv\""}),
    (False, "Bash", {"command": "cat team.example.csv"}),
    (True, "Write", {"file_path": str(ROOT / "team.csv"), "content": "name\n"}),
    (False, "Read", {"file_path": str(ROOT / "team.example.csv")}),
    (True, "Read", {"file_path": str(ROOT / ".env")}),
    (True, "Bash", {"command": "grep KEY .env"}),
    (True, "Bash", {"command": "printenv"}),
    (True, "Bash", {"command": "sqlite3 memory.db 'select 1'"}),
    (True, "Bash", {"command": "uv run python -c \"print(1)\""}),
    (True, "Bash", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"}),
    (True, "Bash", {"command": "node -e 1"}),
    (True, "Bash", {"command": "uv run python scripts/evil.py"}),
    (True, "Bash", {"command": "uv add httpx"}),
    (True, "Bash", {"command": "echo hi > /tmp/outside.txt"}),
    (True, "Bash", {"command": "echo x > scripts/evil.py"}),
    (True, "Bash", {"command": "sed -i 's/a/b/' scripts/validate_rows.py"}),
    (True, "Bash", {"command": "rm memory.db"}),
    (True, "Bash", {"command": "git commit -m x"}),
    (True, "Write", {"file_path": str(ROOT / "scripts" / "x.py"), "content": "x"}),
    (True, "Edit", {"file_path": str(ROOT / ".claude" / "settings.json"), "old_string": "a", "new_string": "b"}),
    (True, "Write", {"file_path": str(ROOT / "schema" / "fields.json"), "content": "{}"}),
    (True, "Write", {"file_path": str(ROOT / "CLAUDE.md"), "content": "x"}),
    (True, "Write", {"file_path": str(ROOT / "Account_Research.xlsx"), "content": "x"}),
    (True, "Bash", {"command": "cp runs/x.xlsx Account_Research.xlsx"}),
    (True, "mcp__exa__web_fetch_exa", {"urls": ["https://www.linkedin.com/in/someone"]}),
    (True, "mcp__exa__crawling_exa", {"url": "https://in.indeed.com/cmp/Acme"}),
    (True, "WebFetch", {"url": "https://lnkd.in/abc", "prompt": "x"}),
    (False, "mcp__exa__web_fetch_exa", {"urls": ["https://razorpay.com/about/"]}),
    (False, "mcp__exa__web_search_exa", {"query": "Razorpay CTO site:linkedin.com/in"}),
    # Rows and drafts are validated on Write
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows()}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": json.dumps({"rows": [DRAFT]})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Last Checked": ""})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"What They Build (brief)": "Payments APIs"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"India Connection (US-based leader)": "Yes: CTO studied in India"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"India Connection (US-based leader)": "Yes: surname suggests Indian origin [1]"})}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"India Connection (US-based leader)": "Yes: CTO Jane Doe (San Francisco) studied at IIT Delhi [1]"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Conversation Starter (cold email / conference)": "Congrats on the launch [7]"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Who to Contact": "Jane Doe, CTO, jane@acme.com [1]"})}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Who to Contact": "Jane Doe, CTO, jane@acme.com (self-published [1])"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Best Person to Reach Out (HumanCloud side)": "Priya"})}),
    # 0.6.2 search depth and freshness
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": rows(**{"India Office": ""})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"India Connection (US-based leader)": "No evidence found"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Funding": "Seed round, March 2019 [1]"})}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Funding": "Seed round, March 2019 [1]; No newer round found (checked: 3 Exa searches, GDELT news, EDGAR Form D)"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Who to Contact": "Jane Doe, CTO [1]; Sam Roe, CTO [1]"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Company Brief (founded, size, customers, milestones)": "Founded 2014 [1]"})}),
    (True, "Edit", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "old_string": "a", "new_string": "b"}),
    # Leaders and contacts: never a constructed or unsourced email or phone
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": people()}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": people(email={"value": "jane@acme.com", "quote": "email me: jane@acme.com", "source": "https://janedoe.dev"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": people(email={"value": "jane.doe@acme.com", "quote": "Jane Doe, CTO", "source": "https://acme.com/about"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": people(email="jane.doe@acme.com")}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": people(phone="+1 415 555 0199")}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": people(phone={"value": "+1 415 555 0199", "kind": "personal", "quote": "call us", "source": "https://acme.com"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": people(source="https://www.zoominfo.com/p/jane")}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(_channels={"press_email": "press@acme.com", "checked": "press page"})}),
    (False, "Bash", {"command": "uv run python scripts/contacts.py verify --draft runs/acme.draft.json"}),
    (False, "Bash", {"command": "uv run python scripts/lookup.py contact acme.com"}),
    (True, "Bash", {"command": "uv run python scripts/excel_upsert.py --rows runs/_hooktest-badcontact.rows.json --contacts"}),
    (False, "Bash", {"command": "uv run python scripts/excel_upsert.py --rows runs/_hooktest-good.rows.json --contacts"}),
    (True, "Bash", {"command": "uv run python scripts/excel_upsert.py"}),
    (True, "Bash", {"command": "uv run python scripts/excel_upsert.py --rows runs/_hooktest-bad.rows.json"}),
    (False, "Bash", {"command": "uv run python scripts/excel_upsert.py --rows runs/_hooktest-good.rows.json"}),
    # Apollo (0.6.3): only through scripts/apollo.py; other paid tools refused; Apollo rows need the person id.
    (False, "Bash", {"command": "uv run python scripts/lookup.py apollo acme.com --json"}),
    (False, "Bash", {"command": "uv run python scripts/apollo.py enrich acme.com"}),
    (True, "Bash", {"command": "curl -H 'X-Api-Key: x' https://api.apollo.io/api/v1/people/match"}),
    (True, "WebFetch", {"url": "https://app.apollo.io/#/people", "prompt": "emails"}),
    (True, "mcp__apollo__people_match", {"id": "1"}),
    (True, "mcp__hunter__email_finder", {"domain": "acme.com"}),
    (True, "mcp__firecrawl__scrape", {"url": "https://acme.com"}),
    (True, "WebFetch", {"url": "https://api.hunter.io/v2/domain-search?domain=acme.com", "prompt": "x"}),
    (True, "mcp__exa__crawling_exa", {"url": "https://rocketreach.co/acme"}),
    (True, "WebFetch", {"url": "https://www.zoominfo.com/c/acme", "prompt": "x"}),
    (True, "Bash", {"command": "curl https://api.hasdata.com/scrape?url=x"}),
    (False, "mcp__exa__web_search_exa", {"query": "Acme CTO"}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": json.dumps({"rows": [{**DRAFT, "_contacts": [{**PERSON, "source": "apollo", "email": {"value": "jane@acme.com", "source": "apollo", "email_status": "verified"}}]}]})}),
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": json.dumps({"rows": [{**DRAFT, "_contacts": [{**PERSON, "source": "apollo", "apollo_id": "64a1b2c3d4e5f60718293a4b", "email": {"value": "jane@acme.com", "source": "apollo", "apollo_id": "64a1b2c3d4e5f60718293a4b", "email_status": "verified"}}]}]})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.draft.json"), "content": json.dumps({"rows": [{**DRAFT, "_contacts": [{**PERSON, "email": {"value": "jane@acme.com", "source": "apollo", "apollo_id": "64a1b2c3d4e5f60718293a4b", "email_status": "guessed"}}]}]})}),
    # Typed fields and H-1B (round 2): typed values are optional, typed right and stated by a cited clause.
    (False, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Founded Year": 2014, "Employees": 40})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Founded Year": "2014"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Founded Year": 2015})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Employees As Of": "March 2026"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"Last Round": "Series Z"})}),
    (True, "Write", {"file_path": str(ROOT / "runs" / "acme.rows.json"), "content": rows(**{"H-1B Filings (4Q)": 12})}),
    (False, "Bash", {"command": "uv run python scripts/lookup.py h1b \"Acme\" --alias \"Acme Labs Inc\""}),
    (False, "Bash", {"command": "uv run python scripts/h1b.py status"}),
    (True, "Write", {"file_path": str(ROOT / "scripts" / "h1b.py"), "content": "x"}),
    (True, "Read", {"file_path": str(ROOT / "runs" / "_hooktest-big.rows.json")}),
    (False, "Read", {"file_path": str(ROOT / "runs" / "_hooktest-good.rows.json")}),
]
FILES = {
    ROOT / "runs" / "_hooktest-bad.rows.json": rows(**{"Sources": ""}),
    ROOT / "runs" / "_hooktest-good.rows.json": rows(),
    ROOT / "runs" / "_hooktest-badcontact.rows.json": people(email={"value": "jane@acme.com", "quote": "Jane", "source": "https://acme.com"}),
    ROOT / "runs" / "_hooktest-big.rows.json": json.dumps({"rows": [GOOD] * 1200}),
}
(ROOT / "runs").mkdir(exist_ok=True)
for f, text in FILES.items():
    f.write_text(text)

fails = 0
try:
    for expect, tool, ti in CASES:
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(ROOT)}),
                           capture_output=True, text=True, env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"})
        blocked = p.returncode == 2
        ok = blocked == expect
        fails += not ok
        print(("PASS" if ok else "FAIL"), "block" if blocked else "allow", tool, json.dumps(ti)[:90], p.stderr.strip()[:160] if not ok else "")
finally:
    for f in FILES:
        f.unlink(missing_ok=True)
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
