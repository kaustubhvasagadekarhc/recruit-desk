#!/usr/bin/env python3
"""PreToolUse guard for the Candidate Agent (stdlib only).

1. Write / Edit / NotebookEdit: block any path outside the project folder.
2. Bash: block obvious writes outside the project folder (redirects, cp/mv/tee/rm
   to absolute paths elsewhere), git, and anything touching the company agent.
3. Bash running scripts/excel_upsert.py: validate the --input JSON first
   (source URL, date, score 0-100, breakdown, confidence, no fabricated contacts).
4. Hardening (security review, 2026-10-14): only named scripts run (`uv run scripts/<name>.py`);
   no inline interpreters (python -c, heredoc to python, node -e, bash -c ...), no sqlite3, no
   reads of .env; code, config and instructions (scripts/, .claude/, schema/, CLAUDE.md, ...)
   cannot be written during a run; Exa fetch / WebFetch of linkedin.com or indeed.com is blocked;
   Exa searches never carry a known candidate's name or a specific LinkedIn profile.
Exit code 2 blocks the tool call and shows stderr to Claude.
"""
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from common import validate_payload  # noqa: E402

SAFE_OUTSIDE = ("/dev/null", "/dev/stdout", "/dev/stderr")
ALLOWED_SCRIPTS = ("memory", "progress", "score", "dedupe", "excel_upsert", "retention", "evidence", "export_crm", "dev_evidence",
                   "tune_weights", "export_shortlist", "outreach")
TEST_FILES = (".claude/hooks/test_guard.py", "tests/run_tests.py")
PROTECTED_DIRS = {"scripts", "tests", ".claude", "schema", ".venv"}
PROTECTED_FILES = {"CLAUDE.md", ".mcp.json", "pyproject.toml", "uv.lock", "run.sh", ".gitignore", ".env", ".env.example"}


def block(msg: str) -> None:
    print(f"BLOCKED by candidate-agent guard: {msg}", file=sys.stderr)
    sys.exit(2)


def inside(p: str, cwd: str) -> bool:
    path = Path(os.path.expanduser(os.path.expandvars(p.strip("'\""))))
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def strip_heredocs(cmd: str) -> str:
    """Drop heredoc bodies: they are file content, not commands."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        i += 1
        if m:
            while i < len(lines) and lines[i].strip() != m.group(1):
                i += 1
            i += 1
    return "\n".join(out)


# ---- Hardening checks (security review steps 1-4, 2026-10-14) -------------------------------
# A text-level guard, not a sandbox: it closes the known bypasses (sqlite3 .shell, writing then
# running new code, inline interpreters, protected files) and keeps runs in non-bypass mode so
# anything new still prompts the human.

INTERPRETERS = re.compile(
    r"^(python[0-9.]*|pypy[0-9.]*|node(js)?|deno|bun|perl[0-9.]*|ruby|irb|php[0-9.]*|lua(jit)?|"
    r"R|Rscript|tclsh|wish|osascript|bash|sh|zsh|dash|ksh|csh|tcsh|fish|busybox|jshell|java|"
    r"groovy|swift|expect|sqlite3|eval|source|\.)$"
)
WRAPPERS = {"env", "timeout", "nice", "nohup", "command", "time", "stdbuf", "xargs", "setsid",
            "flock", "watch", "parallel", "strace", "ionice", "unbuffer", "doas", "sudo", "builtin", "exec"}
WRAPPER_ARG_OPTS = {"-I", "-n", "-P", "-L", "-d", "-s", "-a", "-E", "-k", "-o", "-e", "-i"}
READ_ONLY_TOOLS = {"ls", "cat", "head", "tail", "grep", "wc", "file", "stat"}
SEG_OPS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
FETCH_BLOCKED = re.compile(r"(?i)(?<![\w-])((?:[\w-]+\.)*(?:linkedin\.[a-z]{2,3}(?:\.[a-z]{2})?|lnkd\.in|indeed\.[a-z]{2,3}(?:\.[a-z]{2})?))(?![\w-])")
ENV_FILE = re.compile(r"(?:^|[\s/'\"=<>(:])\.env(?!\.example)(?![\w-])")


def _segments(cmd: str) -> list[list[str]]:
    text = cmd.replace("\\\n", " ").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        block("cannot parse this shell command safely; simplify it (no unbalanced quotes)")
    segs, cur = [], []
    for t in toks:
        if t in SEG_OPS or set(t) <= set(";&|()"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t.lstrip("`").rstrip("`") if t.startswith("`") or t.endswith("`") else t)
    if cur:
        segs.append(cur)
    return [s for s in segs if s]


def _strip_wrappers(seg: list[str]) -> list[str]:
    while seg:
        head = os.path.basename(seg[0].lstrip("$"))
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
            block(f"inline variable assignment {seg[0].split('=')[0]}=… is not allowed in agent runs")
        if head not in WRAPPERS:
            return seg
        seg, i = seg[1:], 0
        while i < len(seg) and (seg[i].startswith("-") or re.match(r"^[A-Za-z_]\w*=", seg[i]) or (head == "timeout" and re.match(r"^\d+[smhd]?$", seg[i]))):
            i += 2 if seg[i] in WRAPPER_ARG_OPTS else 1
        seg = seg[i:]
    return seg


def _resolve(p: str, cwd: str) -> Path:
    path = Path(os.path.expanduser(p))
    return (path if path.is_absolute() else Path(cwd) / path).resolve()


def protected(p: str, cwd: str) -> bool:
    """Code, config and instructions the agent must never change during a run."""
    try:
        rel = _resolve(p, cwd).relative_to(ROOT)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return True
    return parts[0] in PROTECTED_DIRS or (len(parts) == 1 and parts[0] in PROTECTED_FILES) or rel.suffix in (".db", ".sqlite", ".sql")


def _script_ok(tok: str, cwd: str) -> bool:
    path = _resolve(tok, cwd)
    return path in {ROOT / "scripts" / f"{s}.py" for s in ALLOWED_SCRIPTS} or path in {ROOT / t for t in TEST_FILES}


def hardening_checks(cmd: str, cwd: str) -> None:
    """Bash rules shared by both recruitment agents (each keeps its own copy)."""
    if ENV_FILE.search(cmd) or "/proc/self/environ" in cmd:
        block(".env holds the keys; never read, print, copy or source it")
    if re.search(r"\.(shell|system)\b", cmd) and "sqlite" in cmd:
        block("sqlite3 .shell / .system run arbitrary commands")
    if re.search(r"tune_weights\.py\b.*--apply", cmd):
        block("tune_weights.py --apply changes a JD's scoring weights; only the human runs it, from a terminal")
    if re.search(r"retention\.py\b.*--apply", cmd):
        block("retention.py --apply deletes data; only the human runs it, from a terminal")
    if re.search(r"memory\.py\b.*\bforget\b", cmd) and not re.search(r"--yes\b", cmd):
        block("memory.py forget erases a person for good; it needs --yes and runs only when the human asks (settings ask first)")
    for seg in _segments(cmd):
        seg = _strip_wrappers(seg)
        if not seg:
            continue
        head = os.path.basename(seg[0])
        if head in ("printenv", "export", "declare", "compgen") or (seg[0] == "env" and len(seg) == 1):
            block("printing the environment can leak keys")
        if head in ("cd", "pushd") and (len(seg) == 1 or _resolve(seg[1], cwd) != ROOT):
            block(f"stay in {ROOT}; cd elsewhere changes what a script path means")
        if head == "uvx" or (head == "uv" and len(seg) > 1 and seg[1] not in ("run", "sync")):
            block("only `uv run <named script>` and `uv sync` are allowed (no uv pip/add/tool/uvx installs)")
        if head == "uv" and len(seg) > 1 and seg[1] == "sync" and any(a not in ("--frozen", "--locked", "-q", "--quiet") for a in seg[2:]):
            block("only plain `uv sync` (optionally --frozen/--locked) is allowed")
        if head == "uv" and len(seg) > 1 and seg[1] == "run":
            rest = seg[2:]
            if rest and rest[0] in ("python", "python3"):
                rest = rest[1:]
            if not rest or rest[0].startswith("-") or not _script_ok(rest[0], cwd):
                block("uv run may only run the named scripts: " + ", ".join(f"scripts/{s}.py" for s in ALLOWED_SCRIPTS) + " (no -c, no options, no new code)")
            continue
        if head in ("awk", "gawk", "mawk", "nawk") and re.search(r"system|getline|\||>", " ".join(seg[1:])):
            block("awk with system()/getline/pipes/redirects runs or writes code")
        if head == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls") for a in seg):
            block("find -exec / -delete is not allowed in agent runs")
        if INTERPRETERS.match(head) or ("/" in seg[0] and head not in READ_ONLY_TOOLS):
            if head.startswith("python") and len(seg) == 2 and seg[1] in TEST_FILES and _script_ok(seg[1], cwd):
                continue
            block(f"inline or ad-hoc code ({seg[0]} …) is not allowed; use `uv run` with a named script")


def check_fetch(tool: str, ti: dict) -> None:
    """Exa fetch / crawl and WebFetch: never linkedin.com or indeed.com pages (search snippets only)."""
    m = FETCH_BLOCKED.search(json.dumps(ti))
    if m:
        block(f"{tool} of {m.group(1)} is not allowed: LinkedIn and Indeed are read only through Exa search snippets")
# ---- end hardening checks -----------------------------------------------------------------------


def check_search(tool: str, ti: dict) -> None:
    """Exa's privacy policy lets it train on queries: search by skills, role and location only."""
    text = json.dumps(ti, ensure_ascii=False).lower()
    if re.search(r"linkedin\.com/in/[\w%-]+", text):
        block(f"{tool}: no lookups of a specific LinkedIn profile; search by skills, role and location")
    try:
        con = sqlite3.connect(f"file:{ROOT / 'memory.db'}?mode=ro", uri=True)
        names = [r[0] for r in con.execute("SELECT DISTINCT name FROM candidates")]
        con.close()
    except sqlite3.Error:
        names = []
    for n in names:
        n = re.sub(r"\s+", " ", (n or "").strip().lower())
        if len(n.split()) >= 2 and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text):
            block(f"{tool}: the query names a candidate ({n.title()}); search by skills, role and location, never by name")


def check_bash(cmd: str, cwd: str) -> None:
    cmd = strip_heredocs(cmd)
    hardening_checks(cmd, cwd)
    if re.search(r"company-agent(/|\b)", cmd):
        block("the company agent's folder is off limits")
    if re.search(r"(^|[;&|]\s*)git(\s|$)", cmd):
        block("no git in this project")
    targets = re.findall(r">>?\s*([^\s;&|]+)", cmd)
    for seg in re.split(r"&&|\|\||[;|]", cmd):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        args = [x for x in toks[1:] if not x.startswith("-")]
        if not toks or not args:
            continue
        if toks[0] in ("cp", "mv", "ln", "install", "rsync"):
            targets.append(args[-1])
        elif toks[0] in ("tee", "rm", "rmdir", "mkdir", "touch", "truncate", "chmod", "chown", "unlink", "shred"):
            targets += args if toks[0] not in ("chmod", "chown") else args[1:]
        elif toks[0] == "sed" and any(x.startswith("-i") or x == "--in-place" for x in toks):
            targets += args[1:]
        elif toks[0] == "dd":
            targets += [x[3:] for x in toks if x.startswith("of=")]
    for t in targets:
        if t.startswith("&") or t in SAFE_OUTSIDE:
            continue
        if protected(t, cwd):
            block(f"{t!r} is code, config or memory; it cannot be changed during a run")
        if not inside(t, cwd):
            block(f"write target {t!r} is outside {ROOT}")
    if "excel_upsert.py" in cmd:
        m = re.search(r"--input[ =]+(\S+)", cmd)
        if not m:
            block("excel_upsert.py needs --input <file>")
        f = Path(m.group(1).strip("'\""))
        f = f if f.is_absolute() else Path(cwd) / f
        try:
            payload = json.loads(f.read_text())
        except Exception as e:  # noqa: BLE001
            block(f"cannot read {f}: {e}")
        errs = validate_payload(payload)
        if errs:
            block("rows failed validation, fix them before writing Excel:\n- " + "\n- ".join(errs[:40]))


def main() -> None:
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        if p and not inside(p, cwd):
            block(f"{tool} to {p} is outside {ROOT}")
        if p and protected(p, cwd):
            block(f"{tool} to {p}: code, config and instructions cannot be changed during a run")
        if p.endswith(".xlsx"):
            block("write the workbook only through scripts/excel_upsert.py")
    elif tool == "Bash":
        check_bash(ti.get("command", ""), cwd)
    elif tool == "WebFetch" or re.search(r"(fetch|crawl)", tool, re.I):
        check_fetch(tool, ti)
    elif re.search(r"search", tool, re.I):
        check_search(tool, ti)
    sys.exit(0)


if __name__ == "__main__":
    main()
