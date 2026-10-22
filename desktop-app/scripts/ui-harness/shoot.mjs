// Author: kaustubh.s.v
// usage: node shoot.mjs <prefix> <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [prefix, out, port] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}/index.html`;
const browser = await chromium.launch();
const perf = {};

const COMPANY = [
  "Plan · nordstrom · 6 steps", "1. Recalling and verifying the company", "2. Crawling the site and reading ATS feeds",
  "3. Public hiring posts (Exa search, optional)", "4. Deduplicating and updating memory", "5. Validating and writing Excel", "6. Reporting",
  "▶ Step 1/6 · Recalling and verifying the company",
  "✓ Step 1/6 · humancloud.co.in confirmed from site (Humancloud Technologies); memory has same name under humancloud.dev; careers page unknown, ATS none",
  "▶ Step 2/6 · Crawling the site and reading ATS feeds",
  "… crawled 20/150 pages, 4 job pages so far",
];
const COMPANY_REST = [
  "✓ Step 2/6 · 32 pages crawled, careers page https://humancloud.dev/careers, 0 ATS board(s), 6 roles found, 0 URLs blocked by robots",
  "▶ Step 3/6 · Public hiring posts (Exa search, optional)",
  "✓ Step 3/6 · skipped: official careers page gave 6 roles, public posts not requested",
  "▶ Step 4/6 · Deduplicating and updating memory",
  "✓ Step 4/6 · 6 rows in, 0 duplicates dropped, 0 new, 6 updated, 0 closed, 0 stale; company total 10 (Open=4, Unverified=5, Stale=1, Closed=0)",
  "▶ Step 5/6 · Validating and writing Excel",
  "✓ Step 5/6 · sheet 'Humancloud Technologies': 0 inserted, 10 updated, 10 rows in sheet",
  "▶ Step 6/6 · Reporting",
  "✓ Step 6/6 · 10 rows checked against memory listing (title, location, URL, status present), 0 mismatches; row-auditor not run",
];
const CAND = [
  "Plan · senior-react-frontend-pune · 6 steps", "1. Parsing the JD", "2. Discovering candidates (Exa search)", "3. Scoring candidates",
  "4. Deduplicating", "5. Validating and writing Excel", "6. Closing the run and reporting",
  "▶ Step 1/6 · Parsing the JD",
  "✓ Step 1/6 · JD 'senior-react-frontend-pune' registered · sheet 'Senior React Frontend Developer' · 3 mandatory, 6 preferred skills",
  "▶ Step 2/6 · Discovering candidates (Exa search)",
  "✓ Step 2/6 · reused the build-test batch (16 rows, no new Exa search)",
  "▶ Step 3/6 · Scoring candidates",
  "… scoring 9/15 · Tushar Jadhav 81",
];
const CAND_REST = [
  "✓ Step 3/6 · 15 scored, top 94, 0 capped by a mandatory-skill gate, 1 Low confidence",
  "▶ Step 4/6 · Deduplicating",
  "✓ Step 4/6 · 15 in, 15 kept, 0 merged as duplicates, 15 already in memory (refresh)",
  "▶ Step 5/6 · Validating and writing Excel",
  "✓ Step 5/6 · sheet 'Senior React Frontend Developer': 0 inserted, 15 updated, now 15 rows",
  "▶ Step 6/6 · Closing the run and reporting",
  "✓ Step 6/6 · run 4 closed, 15 candidates, top 94",
];

async function page(q, { w = 1280, h = 800, dark = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => console.log("PAGEERROR", q, e.message));
  await p.goto(`${base}?${q}`);
  await p.waitForTimeout(500);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${prefix}-${name}.png` }); console.log("shot", name); };
const lines = (p, agent, arr) => p.evaluate(([a, l]) => l.forEach((t) => window.__mock.line(a, t)), [agent, arr]);
async function run(p, agent) {
  if (agent === "company") { await p.fill("#company-input", "Humancloud Technologies"); await p.fill("#domain-input", "humancloud.co.in"); }
  else await p.fill("#jd-input", "Senior React Frontend Developer, Pune, 5+ years. Must have React, TypeScript, Redux.");
  await p.click(`#view-${agent} .run-btn`);
  await p.waitForTimeout(200);
}
async function nav(p, view) { await p.click(`[data-view=${view}]`); await p.waitForTimeout(150); }

let p;
p = await page("s=setup-bad"); await shot(p, "01-setup-some-failing"); await p.close();
p = await page("s=setup-ok"); await nav(p, "settings"); await p.click("#open-setup"); await p.waitForTimeout(300); await shot(p, "02-setup-all-ok"); await p.close();

for (const [agent, A, B, n] of [["candidate", CAND, CAND_REST, 3], ["company", COMPANY, COMPANY_REST, 7]]) {
  const v = agent === "candidate" ? "candidate" : "company";
  const tag = agent === "candidate" ? "find" : "company";
  p = await page(`s=idle&view=${v}`); await shot(p, `${String(n).padStart(2, "0")}-${tag}-idle`);
  await run(p, agent); await lines(p, agent, A); await shot(p, `${String(n + 1).padStart(2, "0")}-${tag}-running`);
  await lines(p, agent, B); await p.evaluate((a) => window.__mock.end(a, "done"), agent); await p.waitForTimeout(300);
  await shot(p, `${String(n + 2).padStart(2, "0")}-${tag}-finished`);
  await p.close();
  p = await page(`s=idle&view=${v}`); await run(p, agent); await lines(p, agent, A.slice(0, -2));
  await lines(p, agent, ["✗ Step 2/6 · Exa search failed: HTTP 401 invalid API key"]);
  await p.evaluate((a) => { window.__mock.line(a, "Permission denied for Bash(curl https://x)", "warn"); window.__mock.end(a, "error"); }, agent);
  await p.waitForTimeout(300); await shot(p, `${String(n + 3).padStart(2, "0")}-${tag}-error`); await p.close();
}
p = await page("s=near&view=company"); await shot(p, "11-cap-near-87pct"); await p.close();
p = await page("s=locked&view=company"); await shot(p, "12-lock-overlay-countdown"); await p.close();

p = await page("s=idle&view=candidate"); await shot(p, "13-excel-chips-candidates");
await p.click("#view-candidate .first-cell"); await p.waitForTimeout(200); await shot(p, "13b-row-details"); await p.keyboard.press("Escape"); await p.close();
p = await page("s=idle&view=company&sheet=Razorpay"); await p.fill("#view-company .filter", "bengaluru"); await shot(p, "14-excel-search"); await p.close();

p = await page("s=idle&view=company");
let t0 = Date.now();
await p.selectOption("#view-company .sheet-pick", "Nordstrom");
await p.waitForFunction(() => document.querySelectorAll("#view-company tbody tr").length > 20 || document.querySelector("#view-company .vt-body"), null, { timeout: 20000 });
perf.loadBig = Date.now() - t0;
perf.domRows = await p.evaluate(() => document.querySelectorAll("#view-company tbody tr").length);
perf.domNodes = await p.evaluate(() => document.getElementsByTagName("*").length);
await shot(p, "15-excel-big-1501-rows");
t0 = Date.now(); await p.fill("#view-company .filter", "seattle"); await p.waitForTimeout(50); perf.filter = Date.now() - t0;
t0 = Date.now(); await p.evaluate(() => { const b = document.querySelector("#view-company thead th button"); b && b.click(); }); await p.waitForTimeout(50); perf.sort = Date.now() - t0;
await p.close();
p = await page("s=empty&view=company"); await shot(p, "16-excel-empty-sheet"); await p.close();

p = await page("s=idle"); await nav(p, "settings"); await shot(p, "17-settings");
await p.evaluate(() => document.querySelector("#about")?.scrollIntoView()); await shot(p, "18-about"); await p.close();
p = await page("s=idle&view=candidate", { dark: true }); await run(p, "candidate"); await lines(p, "candidate", [...CAND, ...CAND_REST]);
await p.evaluate(() => window.__mock.end("candidate", "done")); await shot(p, "19-dark-find-finished"); await p.close();
p = await page("s=idle&view=company&sheet=Razorpay", { dark: true }); await shot(p, "20-dark-company-idle"); await nav(p, "settings"); await shot(p, "20b-dark-settings"); await p.close();
p = await page("s=locked&view=candidate", { dark: true }); await shot(p, "20c-dark-lock"); await p.close();
p = await page("s=setup-bad", { dark: true }); await shot(p, "20d-dark-setup"); await p.close();
p = await page("s=idle&view=company", { w: 1024, h: 640 }); await shot(p, "21-narrow-1024x640"); await p.close();
p = await page("s=idle&view=candidate", { w: 1024, h: 640 }); await run(p, "candidate"); await lines(p, "candidate", CAND); await shot(p, "21b-narrow-running"); await p.close();
// keyboard: tab a few times from start and show focus
p = await page("s=idle&view=company"); for (let i = 0; i < 6; i++) await p.keyboard.press("Tab"); await shot(p, "22-keyboard-focus"); await p.close();
console.log("PERF", JSON.stringify(perf));
await browser.close();
