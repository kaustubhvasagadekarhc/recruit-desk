// Author: kaustubh.s.v
// Stage clean copies of candidate-agent, company-agent and account-agent for the
// Windows installer, in src-tauri/windows/agents-bundle/. Only code and config are
// copied; user data (.env, *.db, *.xlsx, backups, runs/, team.csv) never is. After the
// copy the staged tree is checked again and the build FAILS if anything
// excluded slipped in. MANIFEST.txt lists every staged file.
//
//   node scripts/stage-agents.mjs              stage from ../ (AI-AGENTS)
//   node scripts/stage-agents.mjs --self-test  run the copy rules on a fixture
// RD_AGENTS_SRC overrides the source folder.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const AGENTS = ["candidate-agent", "company-agent", "account-agent"];

// Top-level entries that are copied. Folders are copied recursively, minus EXCLUDED.
const TOP_FILES = new Set([
  "CLAUDE.md", "README.md", ".mcp.json", "pyproject.toml", "uv.lock",
  ".env.example", ".gitignore", "run.sh", "team.example.csv",
]);
const TOP_DIRS = new Set([".claude", "hooks", "scripts", "schema", "tests", "jds"]);
// db/ holds the user's databases; only its schema ships.
const DB_FILES = new Set(["schema.sql"]);

/** True for anything that must never be in the installer (checked at every depth). */
export function excluded(rel) {
  const parts = rel.split(/[\\/]/);
  const name = parts[parts.length - 1];
  if (parts.some((p) => ["__pycache__", ".venv", "runs", ".pytest_cache", "node_modules", "exports"].includes(p))) return true;
  // An agent's top-level data/ holds downloads and indexes (account-agent: ~580 MB of H-1B files and h1b.sqlite).
  // rel is relative to an agent folder, or to the bundle / installer root (agent name or $RD_AgentsDir first).
  const di = parts.indexOf("data");
  if (di === 0 || (di > 0 && AGENTS.includes(parts[di - 1]))) return true;
  if (name === ".env" || (name.startsWith(".env.") && name !== ".env.example")) return true;
  if (/\.(db|db-wal|db-shm|db-journal|sqlite3?|xlsx|xlsm|xls|pyc|log)$/i.test(name)) return true;
  if (/\.backup-/i.test(name) || /^~\$/.test(name)) return true;
  if (name === "settings.local.json") return true; // personal Claude Code permissions
  if (name.toLowerCase() === "team.csv") return true; // account-agent: colleagues' details, the human's file
  // parsed job descriptions are a company's own hiring data (jds/<id>.json, made by a run): only a sample or example file may ship
  const ji = parts.indexOf("jds");
  if (ji >= 0 && ji < parts.length - 1 && !/^(sample|example)/i.test(name)) return true;
  if (name === "last_record.json") return true; // the last record an assistant created: a person's own data, never shipped
  if (name === "new_rows.jsonl" || name === "new_rows.sending.jsonl" || name === "erased_rows.jsonl") return true; // search-history queues: a person's own data, never shipped
  return false;
}

/** Whether a path relative to an agent folder belongs in the bundle. */
export function included(rel, isDir) {
  if (excluded(rel)) return false;
  const parts = rel.split(/[\\/]/);
  if (parts.length === 1) {
    if (isDir) return TOP_DIRS.has(parts[0]) || parts[0] === "db";
    return TOP_FILES.has(parts[0]) || /\.py$/.test(parts[0]);
  }
  if (parts[0] === "db") return isDir ? false : parts.length === 2 && DB_FILES.has(parts[1]);
  if (parts[0] === "jds" && !isDir) return /\.(json|md|txt)$/i.test(parts[parts.length - 1]);
  return true;
}

function walk(root, rel, out) {
  for (const e of fs.readdirSync(path.join(root, rel), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isSymbolicLink()) continue;
    if (!included(r, e.isDirectory())) continue;
    if (e.isDirectory()) walk(root, r, out);
    else out.push(r);
  }
  return out;
}

function listAll(root, rel = "", out = []) {
  for (const e of fs.readdirSync(path.join(root, rel), { withFileTypes: true })) {
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) listAll(root, r, out);
    else out.push(r);
  }
  return out;
}

/** Copy every agent from src into dest (wiped first). Returns the manifest lines. */
export function stage(src, dest) {
  fs.rmSync(dest, { recursive: true, force: true });
  const manifest = [];
  for (const a of AGENTS) {
    const from = path.join(src, a);
    if (!fs.existsSync(path.join(from, "CLAUDE.md"))) throw new Error(`${from} is not an agent folder (no CLAUDE.md)`);
    for (const rel of walk(from, "", [])) {
      const to = path.join(dest, a, rel);
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(path.join(from, rel), to);
      manifest.push(`${a}/${rel}\t${fs.statSync(to).size}`);
    }
  }
  return manifest;
}

/** Every staged file that breaks the rules; empty means the bundle is clean. */
export function verify(dest) {
  return listAll(dest).filter((r) => r !== "MANIFEST.txt" && excluded(r));
}

function selfTest() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rd-stage-"));
  const src = path.join(tmp, "src");
  const files = {
    "CLAUDE.md": "x", "README.md": "x", ".env": "SECRET", ".env.example": "EXA_API_KEY=", "uv.lock": "x",
    "memory.db": "x", "memory.backup-2026.db": "x", "Book.xlsx": "x", "Book.backup-1.xlsx": "x",
    "BUILD-LOG.md": "x", "scripts/a.py": "x", "scripts/__pycache__/a.pyc": "x", "scripts/data.xlsx": "x",
    "db/schema.sql": "x", "db/company.db": "x", "db/notes.txt": "x", "runs/r1/out.json": "x",
    ".venv/lib/x.py": "x", "jds/sample.json": "{}", ".claude/settings.json": "{}",
    ".claude/settings.local.json": "{}", ".claude/hooks/guard.py": "x", "tests/t.py": "x", "random.bin": "x",
    "team.csv": "name,email", "team.example.csv": "name", "schema/fields.json": "{}",
    "data/h1b.sqlite": "x", "data/LCA_Disclosure_Data_FY2026_Q3.xlsx": "x", "data/LCA.csv": "x",
    "exports/hubspot-companies.csv": "x", "scripts/exports/x.csv": "x", "jds/senior-engineer-pune.json": "{}",
  };
  for (const a of AGENTS) for (const [f, c] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(src, a, f)), { recursive: true });
    fs.writeFileSync(path.join(src, a, f), c);
  }
  const dest = path.join(tmp, "out");
  stage(src, dest);
  const got = listAll(path.join(dest, "candidate-agent")).sort();
  const want = [".claude/hooks/guard.py", ".claude/settings.json", ".env.example", "CLAUDE.md", "README.md",
    "db/schema.sql", "jds/sample.json", "schema/fields.json", "scripts/a.py", "team.example.csv", "tests/t.py", "uv.lock"].sort();
  const ok1 = JSON.stringify(got) === JSON.stringify(want);
  const ok2 = verify(dest).length === 0;
  // A planted .env, database or workbook in the staged tree must be caught.
  for (const bad of [".env", "x.db", "scripts/y.xlsx", "runs/z.txt", "team.csv", "data/LCA.csv", "exports/a.csv", "jds/my-real-jd.json", "last_record.json", "new_rows.jsonl", "new_rows.sending.jsonl", "erased_rows.jsonl"]) {
    fs.mkdirSync(path.dirname(path.join(dest, "account-agent", bad)), { recursive: true });
    fs.writeFileSync(path.join(dest, "account-agent", bad), "x");
  }
  const ok3 = verify(dest).length === 11;
  // The installer listing names files as $RD_AgentsDir\<agent>\...; data/ under an agent is caught there too,
  // while a data/ folder deeper in the code (scripts/data) is not.
  const ok4 = excluded("$RD_AgentsDir/account-agent/data/h1b.sqlite") && excluded("$RD_AgentsDir/account-agent/data/LCA.csv")
    && excluded("$RD_AgentsDir/candidate-agent/exports/a.csv") && !excluded("$RD_AgentsDir/account-agent/scripts/data/x.json");
  fs.rmSync(tmp, { recursive: true, force: true });
  if (!(ok1 && ok2 && ok3 && ok4)) {
    console.error("stage-agents self-test FAILED", { got, want, ok1, ok2, ok3, ok4 });
    process.exit(1);
  }
  console.log("stage-agents self-test passed (copy rules, clean check, planted-file check, data/ and exports/ rule)");
}

function main() {
  if (process.argv.includes("--self-test")) return selfTest();
  const src = process.env.RD_AGENTS_SRC || path.resolve(here, "../..");
  const dest = path.resolve(here, "../src-tauri/windows/agents-bundle");
  const manifest = stage(src, dest);
  const bad = verify(dest);
  if (bad.length) {
    console.error(`stage-agents: excluded files in the bundle, refusing to build:\n  ${bad.join("\n  ")}`);
    process.exit(1);
  }
  fs.writeFileSync(path.join(dest, "MANIFEST.txt"), manifest.join("\n") + "\n");
  const bytes = manifest.reduce((n, l) => n + Number(l.split("\t")[1]), 0);
  console.log(`stage-agents: ${manifest.length} files, ${bytes} bytes staged in ${path.relative(process.cwd(), dest)}; excluded-files check passed`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
