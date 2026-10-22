// Author: kaustubh.s.v
// Live mode for the screenshot harness: serves the harness folder and runs the app's real backend
// (the `headless` binary: same settings, cap logic, runner and Excel reader as the app) for Accounts runs.
// Open index.html?live=1 and mock.js forwards start_run / read_workbook for the account agent here.
//
// usage: node live-bridge.mjs <work-dir> <port> <headless-binary> <data-dir> <account-agent-dir>
import { createServer } from "node:http";
import { spawn, execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdtempSync, copyFileSync } from "node:fs";
import { join, extname } from "node:path";
import { tmpdir } from "node:os";

const [work, port, headless, data, accountDir] = process.argv.slice(2);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml" };
let events = [];
let child = null;

function start(company, domain, contacts) {
  events = [];
  const args = ["run", "account", company];
  if (domain) args.push(domain);
  if (contacts) args.push("--contacts");
  child = spawn(headless, args, { env: { ...process.env, RECRUIT_DESK_DATA: data }, stdio: ["ignore", "pipe", "pipe"] });
  let buf = "";
  child.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      const m = line.match(/^\[\s*\d+s\] (\S+)\s+(.*)$/);
      if (!m) continue;
      const [, kind, text] = m;
      if (kind === "usage") {
        const u = text.match(/counted (\d+) \/ cap (\d+) \(([\d.]+)%\)/);
        if (u) events.push({ type: "usage", counted: +u[1], cap: +u[2], percent: +u[3] });
      } else if (kind === "ENDED") {
        const e = text.match(/^"?(\w+)"? · (.*?) · run tokens (\d+)/);
        events.push({ type: "end", status: e ? e[1] : "error", text: e ? e[2] : text, counted: e ? +e[3] : 0 });
      } else events.push({ type: "line", kind, text });
    }
  });
  child.stderr.on("data", (d) => events.push({ type: "line", kind: "stderr", text: String(d).trim() }));
  child.on("exit", (code) => { if (!events.some((e) => e.type === "end")) events.push({ type: "end", status: "error", text: `headless exited ${code}`, counted: 0 }); child = null; });
}

function workbook() {
  const src = join(accountDir, "Account_Research.xlsx");
  if (!existsSync(src)) return null;
  const dir = mkdtempSync(join(tmpdir(), "rd-live-"));
  copyFileSync(src, join(dir, "Account_Research.xlsx"));
  execFileSync("uv", ["run", "--with", "openpyxl", "python", "-I", join(work, "export-fixtures.py"), dir, join(dir, "f.json")], { stdio: "ignore" });
  return JSON.parse(readFileSync(join(dir, "f.json"), "utf8")).account;
}

createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  const json = (v) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(v)); };
  if (u.pathname === "/api/start") {
    if (child) return json({ ok: false, error: "a run is already going" });
    start(u.searchParams.get("company") || "", u.searchParams.get("domain") || "", u.searchParams.get("contacts") === "1");
    return json({ ok: true });
  }
  if (u.pathname === "/api/events") { const from = +(u.searchParams.get("from") || 0); return json({ events: events.slice(from), next: events.length }); }
  if (u.pathname === "/api/stop") { if (child) child.kill("SIGTERM"); return json({ ok: true }); }
  if (u.pathname === "/api/workbook") return json(workbook());
  const f = join(work, u.pathname === "/" ? "index.html" : u.pathname.slice(1));
  if (!f.startsWith(work) || !existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" });
  res.end(readFileSync(f));
}).listen(+port, "127.0.0.1", () => console.log(`live bridge on http://127.0.0.1:${port}/index.html?live=1`));
