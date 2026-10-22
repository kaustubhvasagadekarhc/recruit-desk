// Author: kaustubh.s.v
// Check what the built Windows installer really contains. Recompiles the
// installer.nsi that `cargo tauri build` generated with `makensis -V4` (into a
// throwaway exe), reads every "File:" line and FAILS if any excluded file
// (.env, *.db, *.xlsx, backups, runs/, .venv, __pycache__, an agent's data/ or any exports/) is packed.
// Writes the full list to src-tauri/windows/agents-bundle/INSTALLER-CONTENTS.txt.
//   node scripts/check-installer.mjs [path/to/installer.nsi]
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { excluded } from "./stage-agents.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const nsi = path.resolve(process.argv[2] || path.join(here, "../src-tauri/target/x86_64-pc-windows-msvc/release/nsis/x64/installer.nsi"));
if (!fs.existsSync(nsi)) {
  console.error(`check-installer: ${nsi} not found; run cargo tauri build for Windows first`);
  process.exit(1);
}
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rd-check-"));
let log;
try {
  log = execFileSync("makensis", ["-V4", nsi, `-XOutFile ${path.join(tmp, "probe.exe")}`], { cwd: path.dirname(nsi), encoding: "utf8", maxBuffer: 64 << 20 });
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}

// makensis stores identical file data once and logs the repeat as "0/<size> bytes".
// File /r prints basenames; the Descending/Returning lines carry the source folder.
const files = [];
let out = "", cur = null;
for (const line of log.split("\n")) {
  let m;
  if ((m = line.match(/^SetOutPath: "(.*)"$/))) { out = m[1]; cur = null; continue; }
  if ((m = line.match(/^File: (?:Descending|Returning) to: "(.*)"$/))) { cur = m[1]; continue; }
  if ((m = line.match(/^File: "([^"]+)"(?:->"([^"]+)")? (?:\d+\/)?(\d+) bytes/))) {
    let target = m[2];
    if (!target) {
      const i = cur ? cur.indexOf("/agents-bundle/") : -1;
      target = i >= 0
        ? ["$RD_AgentsDir", ...cur.slice(i + "/agents-bundle/".length).split("/").filter(Boolean), m[1]].join("\\")
        : `${out}\\${m[1]}`;
    }
    files.push({ target, bytes: Number(m[3]) });
  }
}
const bad = files.filter((f) => excluded(f.target.replace(/\\/g, "/")));
const agentFiles = files.filter((f) => f.target.startsWith("$RD_AgentsDir"));
const list = files.map((f) => `${f.target}\t${f.bytes}`).join("\n") + "\n";
fs.writeFileSync(path.join(here, "../src-tauri/windows/agents-bundle/INSTALLER-CONTENTS.txt"), list);
if (bad.length) {
  console.error(`check-installer: FAILED, excluded files are packed:\n  ${bad.map((f) => f.target).join("\n  ")}`);
  process.exit(1);
}
const staged = fs.readFileSync(path.join(here, "../src-tauri/windows/agents-bundle/MANIFEST.txt"), "utf8").trim().split("\n").length;
if (agentFiles.length !== staged) {
  console.error(`check-installer: FAILED, ${agentFiles.length} assistant files packed but ${staged} staged`);
  process.exit(1);
}
console.log(`check-installer: passed. ${files.length} files packed (${agentFiles.length} assistant files, matches MANIFEST); no .env, *.db, *.xlsx, backups, runs/, data/, exports/, last_record.json, new_rows.jsonl, erased_rows.jsonl, team.csv or parsed job descriptions (jds/).`);
