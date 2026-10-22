"""Mark the three agent folders as trusted in Claude Code.

This does the same thing as answering "Yes, proceed" to Claude Code's
"Do you trust the files in this folder?" question in each agent folder. It sets
projects[<folder>].hasTrustDialogAccepted = true in Claude Code's own config
file (~/.claude.json, or $CLAUDE_CONFIG_DIR/.claude.json).

That file belongs to Claude Code and its layout is not a public interface, so:
  * close every Claude Code window and the Recruit Desk app before running this
    (a running session can write the file back and undo the change);
  * a backup is written next to it first (.claude.json.bak-trust-<timestamp>);
  * nothing else in the file is changed.

Run from anywhere:
    uv run --no-project python trust-folders.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS = ("candidate-agent", "company-agent", "account-agent")


def config_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def config_key(folder: Path) -> str:
    # Claude Code keys projects by absolute path with forward slashes, on Windows too.
    return str(folder.resolve()).replace("\\", "/")


def main() -> int:
    root = Path(__file__).resolve().parent
    folders = [root / name for name in AGENTS]
    missing = [f for f in folders if not f.is_dir()]
    if missing:
        print("Agent folder not found: " + ", ".join(str(f) for f in missing))
        print("Keep this script inside the AI-AGENTS folder, next to candidate-agent, company-agent and account-agent.")
        return 1

    cfg = config_path()
    if not cfg.exists():
        print(f"{cfg} does not exist yet. Run `claude` once and sign in, then run this script again.")
        return 1
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read {cfg}: {e}. Nothing was changed.")
        return 1

    projects = data.setdefault("projects", {})
    changed = []
    for folder in folders:
        key = config_key(folder)
        # Reuse an existing entry for this folder even if it is spelled with other slashes or case.
        existing = next((k for k in projects if k.replace("\\", "/").lower() == key.lower()), None)
        entry = projects.setdefault(existing or key, {})
        if entry.get("hasTrustDialogAccepted") is True:
            print(f"Already trusted: {folder}")
            continue
        entry["hasTrustDialogAccepted"] = True
        changed.append(folder)

    if not changed:
        print("All three folders were already trusted. Nothing was changed.")
        return 0

    backup = cfg.with_name(f"{cfg.name}.bak-trust-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(cfg, backup)
    fd, tmp = tempfile.mkstemp(dir=cfg.parent, prefix=".claude.json.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, cfg)

    for folder in changed:
        print(f"Trusted: {folder}")
    print(f"Backup of the previous file: {backup}")
    print("Open Recruit Desk and press Re-check on the setup screen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
