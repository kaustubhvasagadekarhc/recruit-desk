#!/usr/bin/env python3
"""PreToolUse hook. Standard library only, so it runs without the uv environment.

Blocks (exit 2, reason on stderr, which Claude sees):
  - Write / Edit / MultiEdit / NotebookEdit outside this project folder;
  - any tool touching the sibling candidate-agent folder;
  - Bash output redirection, tee, cp, mv, mkdir, touch or rm aimed outside this folder;
  - Write of a *.rows.json file whose rows fail validation;
  - Bash running excel_upsert.py when the --rows file fails validation, or when the
    workbook is written by anything other than excel_upsert.py;
  - hardening (security review, 2026-10-14): anything but the named scripts
    (`uv run python scripts/<name>.py`), inline interpreters (python -c, heredoc to python,
    node -e, bash -c ...), sqlite3, reads of .env, writes to code, config and instructions
    (scripts/, hooks/, .claude/, db/, CLAUDE.md, ...), and Exa fetch / WebFetch of
    linkedin.com or indeed.com pages;
  - token budget (improvement plan item 5, 2026-10-15): Read, cat, less or more of a
    runs/*.rows.json file over 100 KB (use run_company.py's summary or `memory.py jobs --limit 20`).
  - self-learning loop (2026-10-15): the agent may write only data under schema/: reader specs
    schema/readers/<name>.json (checked by spec_schema.validate_spec on Write) and the fingerprint
    registry schema/ats_fingerprints.json (checked on Write). Any new or changed code file (*.py and
    other code suffixes) anywhere is blocked, Bash may not write under schema/ at all, and
    `tools.py --builder` (the human builder's flag) is refused.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parent.parent).resolve()
sys.path.insert(0, str(ROOT / "scripts"))
from validate_rows import validate_file, validate_payload  # noqa: E402
from spec_schema import validate_fingerprints, validate_spec  # noqa: E402

SIBLING = (ROOT.parent / "candidate-agent").resolve()
ALLOWED_OUTSIDE = {"/dev/null", "/dev/stdout", "/dev/stderr"}
ALLOWED_SCRIPTS = ("scan", "crawl", "ats", "dedupe", "excel_upsert", "memory", "progress", "validate_rows", "retention", "test_netsafe",
                   "run_company", "tools", "blockers", "spec_reader", "spec_schema", "digest", "rescan_watchlist")
BIG_ROWS = 100_000  # bytes; a rows file this large is never read into the agent's context
TEST_FILES = ("hooks/test_hook.py",)
PROTECTED_DIRS = {"scripts", ".claude", "hooks", "db", ".venv", "schema", "backups"}
# Code the agent must never create or change, wherever it is (it may only write data and specs).
CODE_SUFFIXES = {".py", ".pyw", ".pyc", ".pyi", ".pth", ".so", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".pl", ".rb", ".php", ".lua"}
SPEC_WRITE = re.compile(r"^schema/readers/[a-z][a-z0-9_-]{1,39}\.json$")
FP_FILE = "schema/ats_fingerprints.json"
PROTECTED_FILES = {"CLAUDE.md", ".mcp.json", "pyproject.toml", "uv.lock", ".gitignore", ".env", ".env.example"}


def block(msg: str) -> None:
    print(f"BLOCKED by company-agent hook: {msg}", file=sys.stderr)
    sys.exit(2)


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


def learned_data(p: str, cwd: str) -> str:
    """'spec' or 'fingerprints' for the two schema/ files the agent may write (Write/Edit tools only), else ''."""
    try:
        rel = _resolve(p, cwd).relative_to(ROOT).as_posix()
    except ValueError:
        return ""
    return "spec" if SPEC_WRITE.fullmatch(rel) else "fingerprints" if rel == FP_FILE else ""


def protected(p: str, cwd: str, data_ok: bool = False) -> bool:
    """Code, config and instructions the agent must never change during a run. With data_ok, the
    reader specs and the fingerprint registry under schema/ are allowed (Write / Edit tools only)."""
    resolved = _resolve(p, cwd)
    if resolved.suffix.lower() in CODE_SUFFIXES:
        return True  # no new or changed code anywhere, inside or outside the project
    try:
        rel = resolved.relative_to(ROOT)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return True
    if data_ok and learned_data(p, cwd):
        return False
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
    if re.search(r"tools\.py\b.*--builder", cmd):
        block("tools.py --builder is the human builder's flag; the agent trials specs without it (the forge cap applies)")
    if re.search(r"retention\.py\b.*--apply", cmd):
        block("retention.py --apply deletes data; only the human runs it, from a terminal")
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


def big_rows_file(p: str, cwd: str) -> bool:
    """A runs/*.rows.json file over BIG_ROWS bytes."""
    if not p.endswith(".rows.json"):
        return False
    path = _resolve(p, cwd)
    try:
        return path.relative_to(ROOT).parts[0] == "runs" and path.stat().st_size > BIG_ROWS
    except (ValueError, IndexError, OSError):
        return False


BIG_ROWS_MSG = ("{} is over 100 KB; reading it costs a large share of the token budget. "
                "Use the summary printed by run_company.py / dedupe.py, or `uv run python scripts/memory.py jobs <domain> --limit 20`.")


def inside(path: str, cwd: Path) -> bool:
    p = Path(os.path.expanduser(path))
    p = (p if p.is_absolute() else cwd / p).resolve()
    return p == ROOT or ROOT in p.parents


def in_sibling(path: str, cwd: Path) -> bool:
    p = Path(os.path.expanduser(path))
    p = (p if p.is_absolute() else cwd / p).resolve()
    return p == SIBLING or SIBLING in p.parents


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or ROOT)

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if in_sibling(path, cwd):
            block("the sibling recruitment agent's folder is off limits; never read or write it")
        if not inside(path, cwd):
            block(f"writes are allowed only inside {ROOT} (got {path})")
        if protected(path, str(cwd), data_ok=True):
            block(f"{tool} to {path}: code, config and instructions cannot be changed during a run "
                  "(under schema/ only readers/<name>.json specs and ats_fingerprints.json may be written; never *.py)")
        kind = learned_data(path, str(cwd))
        if kind and tool == "Write":
            try:
                doc = json.loads(ti.get("content") or "")
            except json.JSONDecodeError as e:
                block(f"{path} is not valid JSON: {e}")
            errs = validate_spec(doc, Path(path).name) if kind == "spec" else validate_fingerprints(doc)
            if errs:
                block(f"{kind} failed the schema check:\n" + "\n".join(errs[:15]))
        if Path(path).name.endswith(".xlsx"):
            block("the workbook is written only by scripts/excel_upsert.py")
        if tool == "Write" and path.endswith(".rows.json"):
            try:
                payload = json.loads(ti.get("content") or "")
            except json.JSONDecodeError as e:
                block(f"{path} is not valid JSON: {e}")
            errs = validate_payload(payload)
            if errs:
                block("rows failed validation:\n" + "\n".join(errs[:20]))
        return

    if tool == "Read":
        path = ti.get("file_path") or ""
        if big_rows_file(path, str(cwd)):
            block(BIG_ROWS_MSG.format(path))
        return

    if tool == "WebFetch" or re.search(r"(fetch|crawl)", tool, re.I):
        check_fetch(tool, ti)
        return

    if tool == "Bash":
        cmd = ti.get("command") or ""
        # Heredoc bodies are data, not shell syntax.
        body_free = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?:\n|$)", "\n", cmd, flags=re.S)
        hardening_checks(body_free, str(cwd))
        for seg in _segments(body_free):
            if os.path.basename(seg[0]) in ("cat", "less", "more", "bat", "jq"):
                for arg in seg[1:]:
                    if big_rows_file(arg, str(cwd)):
                        block(BIG_ROWS_MSG.format(arg))
        try:
            lex = shlex.shlex(body_free, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            tokens = body_free.split()
        targets = []
        for i, t in enumerate(tokens[:-1]):
            if t in (">", ">>", ">|", "&>", "&>>"):
                targets.append(tokens[i + 1])
        writers = {"tee", "cp", "mv", "mkdir", "touch", "rm", "rmdir", "ln", "install", "rsync", "dd",
                   "chmod", "chown", "truncate", "unlink", "shred"}
        ops = {";", "&&", "||", "|", "&", "(", ")"}
        changed = list(targets)  # paths a command creates, changes or removes
        for i, t in enumerate(tokens):
            name = os.path.basename(t)
            if (name in writers or name == "sed") and (i == 0 or tokens[i - 1] in ops):
                args = []
                for arg in tokens[i + 1 :]:
                    if arg in ops:
                        break
                    if arg.startswith("of="):
                        arg = arg[3:]
                    if not arg.startswith("-"):
                        args.append(arg)
                if name == "sed":
                    if any(a.startswith("-i") or a == "--in-place" for a in tokens[i + 1 :]):
                        changed += args[1:]
                    continue
                targets += args
                if name in ("cp", "ln", "install", "rsync") and args:
                    changed.append(args[-1])
                elif name in ("chmod", "chown"):
                    changed += args[1:]
                else:
                    changed += args
        for t in changed:
            if not t.startswith("&") and t not in ALLOWED_OUTSIDE and protected(t, str(cwd)):
                block(f"{t!r} is code, config or memory; it cannot be changed during a run")
        for t in targets:
            if t in ALLOWED_OUTSIDE or t.startswith("&"):
                continue
            if not inside(t, cwd):
                block(f"shell write outside {ROOT}: {t}")
        if re.search(r"\bgit\s+(init|commit|push|add)\b", cmd):
            block("no git in this project")
        if "Company_Hiring_Intelligence.xlsx" in body_free and "excel_upsert.py" not in body_free and re.search(r"(>|\b(cp|mv|rm|tee)\b)", body_free):
            block("the workbook is written only by scripts/excel_upsert.py")
        if any(t.endswith("excel_upsert.py") for t in tokens):
            rows = ""
            for i, t in enumerate(tokens):
                if t == "--rows" and i + 1 < len(tokens):
                    rows = tokens[i + 1]
                elif t.startswith("--rows="):
                    rows = t.split("=", 1)[1]
            if not rows:
                block("excel_upsert.py needs --rows <file> so the rows can be validated first")
            rp = Path(rows) if Path(rows).is_absolute() else cwd / rows
            errs = validate_file(rp)
            if errs:
                block("rows failed validation:\n" + "\n".join(errs[:20]))


if __name__ == "__main__":
    main()
