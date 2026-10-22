// Author: kaustubh.s.v
// Screenshots for 0.7.0 (typed columns, filters, score breakdown, evidence, CRM export), light and dark.
// usage: node shoot-070.mjs <prefix> <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [prefix, out, port] = process.argv.slice(2);
const browser = await chromium.launch();
const errors = [];

async function page(q, dark) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${q}: ${e.message}`));
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}`);
  await p.waitForTimeout(600);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(300); await p.screenshot({ path: `${out}/${prefix}-${name}.png` }); console.log("shot", name); };
const openRow = async (p, view, n = 0) => { await p.locator(`#view-${view} table.sheet tbody tr.row`).nth(n).click(); await p.waitForTimeout(500); };

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  // Candidates: table, details with the score bar and evidence, a filter, the CRM dialog.
  let p = await page("s=idle&view=candidate&sheet=Sr. Software Engineer - Python", dark);
  const picks = await p.$$eval("#view-candidate .sheet-pick option", (o) => o.map((x) => x.value));
  console.log("candidate sheets:", picks.join(" | "));
  await shot(p, `c1-candidates-${t}`);
  await openRow(p, "candidate"); await shot(p, `c2-candidate-details-${t}`);
  await p.locator("#row-dialog").evaluate((d) => { d.scrollTop = d.scrollHeight; }); await shot(p, `c3-candidate-evidence-${t}`);
  await p.keyboard.press("Escape");
  await p.selectOption("#view-candidate select[aria-label=\"Filter by experience\"]", "5"); await shot(p, `c4-candidates-filtered-${t}`);
  await p.click("#view-candidate .crm-btn"); await shot(p, `c5-crm-dialog-${t}`);
  await p.click("#crm-dialog button[value=cancel]");
  await p.close();
  // Company jobs: Razorpay has the typed columns.
  p = await page("s=idle&view=company&sheet=Razorpay", dark);
  await shot(p, `j1-jobs-${t}`);
  await p.selectOption("#view-company .filters select >> nth=0", { index: 1 }); await shot(p, `j2-jobs-filtered-${t}`);
  await openRow(p, "company"); await shot(p, `j3-job-details-${t}`);
  await p.close();
  // Accounts.
  p = await page("s=idle&view=account", dark);
  console.log("account sheets:", (await p.$$eval("#view-account .sheet-pick option", (o) => o.map((x) => x.value))).join(" | "));
  await shot(p, `a1-accounts-${t}`);
  await openRow(p, "account"); await shot(p, `a2-account-details-${t}`);
  await p.locator("#row-dialog").evaluate((d) => { d.scrollTop = d.scrollHeight * 0.45; }); await shot(p, `a3-account-evidence-${t}`);
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
await browser.close();
