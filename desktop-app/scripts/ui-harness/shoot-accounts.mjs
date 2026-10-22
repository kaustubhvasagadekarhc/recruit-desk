// Author: kaustubh.s.v
// Screenshots of the Accounts section (0.6.0). usage: node shoot-accounts.mjs <prefix> <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [prefix, out, port] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}/index.html`;
const browser = await chromium.launch();

// Real lines from account-agent/runs/2026-10-15-1732-razorpay/progress.log.
const RUN = [
  "Plan · razorpay · 8 steps", "▶ Step 1/8 · Verifying the company and its website",
  "✓ Step 1/8 · razorpay.com confirmed from site title + JSON-LD Organization",
  "▶ Step 2/8 · Company facts (site, Wikidata, SEC EDGAR, GitHub, news)",
  "✓ Step 2/8 · 12 facts from 9 sources, 3 Exa searches (1 rate-limited)",
  "▶ Step 3/8 · Open engineering roles (company-agent)", "… reusing company-agent scan from 2026-10-15 (newer than 7 days)",
  "✓ Step 3/8 · 4 open engineering roles of 32 open (sheet 'Razorpay' in company-agent, read-only)",
  "▶ Step 4/8 · Leadership and India Connection", "… leader-scout: 4 searches done; 5th (talks) hit Exa rate limit; reporting",
];
const REST = [
  "✓ Step 4/8 · 3 leaders (Mathur, Kumar, Brahmadesam), India Connection: No evidence found",
  "▶ Step 5/8 · HumanCloud match (team.csv)",
  "✓ Step 5/8 · team.csv has no consented rows: Warm Connection and Best Person left blank ('add team.csv')",
  "▶ Step 6/8 · Conversation starter", "✓ Step 6/8 · starter written, citing [1, 14]",
  "▶ Step 7/8 · Validating and writing Excel", "✓ Step 7/8 · sheet 'Accounts': 1 inserted, 0 updated (Razorpay); 1 companies in sheet",
  "▶ Step 8/8 · Reporting", "✓ Step 8/8 · 15 fields filled, 4 blank",
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
const lines = (p, arr) => p.evaluate((l) => l.forEach((t) => window.__mock.line("account", t)), arr);

let p = await page("s=idle&view=account");
await shot(p, "a01-accounts-idle");
await p.fill("#account-input", "Razorpay"); await p.fill("#account-domain-input", "razorpay.com");
await p.click("#view-account .run-btn"); await p.waitForTimeout(200);
console.log("input sent:", JSON.stringify(await p.evaluate(() => window.__mock.lastInput)));
await lines(p, RUN); await shot(p, "a02-accounts-running");
await lines(p, REST); await p.evaluate(() => window.__mock.end("account", "done", 102000)); await shot(p, "a03-accounts-finished");
const heights = await p.evaluate(() => [...document.querySelectorAll("#view-account .toolbar > *")].map((e) => Math.round(e.getBoundingClientRect().height)));
console.log("toolbar heights:", heights.join(","));
await p.close();

p = await page("s=idle&view=account");
await p.setInputFiles("#view-account input[type=file]", { name: "q4-targets.csv", mimeType: "text/csv", buffer: Buffer.from("company,domain,list\nRazorpay,razorpay.com,Q4 India\nFreshworks,freshworks.com,Q4 India\nPostman,postman.com,Q4 India\n") });
await p.waitForTimeout(200); await shot(p, "a04-accounts-csv-loaded");
await p.click("#view-account .run-btn"); await p.waitForTimeout(200);
const inp = await p.evaluate(() => window.__mock.lastInput);
console.log("csv input keys:", Object.keys(inp).join(","), "rows:", inp.csv.trim().split("\n").length - 1);
await p.close();

p = await page("s=setup-bad"); await shot(p, "a05-setup-trust-three"); await p.close();
p = await page("s=idle&view=account&sheet=Accounts", { dark: true }); await shot(p, "a06-accounts-dark"); await p.close();
p = await page("s=idle&view=account", { w: 1024, h: 640 }); await shot(p, "a07-accounts-narrow"); await p.close();
p = await page("s=idle&view=account"); await p.click("[data-view=settings]"); await p.waitForTimeout(150); await shot(p, "a08-settings-shortcuts"); await p.close();
await browser.close();
