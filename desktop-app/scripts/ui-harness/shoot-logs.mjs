// Author: kaustubh.s.v
// Browser checks for the admin-only Logs screen. usage: node shoot-logs.mjs <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [out, port] = process.argv.slice(2);
const browser = await chromium.launch();
const errors = [], results = [];
const ok = (name, cond) => { results.push([name, !!cond]); console.log((cond ? "PASS " : "FAIL ") + name); };
async function page(q, dark) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${q}: ${e.message}`));
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}${dark ? "&theme=dark" : ""}`);
  await p.waitForTimeout(700);
  return p;
}
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const filter = (p) => p.evaluate(() => JSON.stringify(window.__mock.lastLogFilter));

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  let p = await page("s=idle&view=candidate&auth=in", dark);
  ok(`[${t}] a plain user has no Logs tab`, !(await vis(p, "#tab-logs")));
  await p.close();
  p = await page("s=idle&view=candidate&auth=out", dark);
  ok(`[${t}] signed out: no Logs tab`, !(await vis(p, "#tab-logs")));
  await p.close();

  p = await page("s=idle&view=candidate&auth=admin", dark);
  ok(`[${t}] an admin has the Logs tab`, await vis(p, "#tab-logs"));
  await p.locator("#tab-logs").click(); await p.waitForTimeout(500);
  ok(`[${t}] the screen says who can read it and that no search text or contact details are in it`, (await txt(p, "#view-logs")).includes("no search text, no candidates and no contact details") && (await txt(p, "#view-logs")).includes("Only admins can read them"));
  ok(`[${t}] every line is listed, newest first, with person, kind, level, assistant and message`, (await p.locator("#logs-box tbody tr").count()) === 5 && (await p.locator("#logs-box tbody tr").nth(0).textContent()).includes("bob@humancloud.co.in") && (await p.locator("#logs-box tbody tr").nth(0).textContent()).includes("Company jobs") && (await p.locator("#logs-box tbody tr").nth(0).textContent()).includes("✗ Step 2/6"));
  ok(`[${t}] kinds and levels are said in words`, (await txt(p, "#logs-box tbody tr:nth-child(3)")).includes("Sign-in") && (await txt(p, "#logs-box tbody tr:nth-child(4)")).includes("Warning") && (await txt(p, "#logs-box tbody tr:nth-child(1)")).includes("Error"));
  await p.screenshot({ path: `${out}/logs-${t}.png` });
  await p.selectOption("#logs-kind", "run"); await p.waitForTimeout(400);
  ok(`[${t}] the kind filter asks for runs only`, (await filter(p)) === JSON.stringify({ kind: "run", level: null, email: null }) && (await p.locator("#logs-box tbody tr").count()) === 2);
  await p.selectOption("#logs-level", "error"); await p.waitForTimeout(400);
  ok(`[${t}] the level filter narrows it further`, (await p.locator("#logs-box tbody tr").count()) === 1);
  await p.fill("#logs-person", "ann@humancloud.co.in"); await p.locator("#logs-person").blur(); await p.waitForTimeout(400);
  ok(`[${t}] a person with no matching lines gets a plain message`, (await filter(p)) === JSON.stringify({ kind: "run", level: "error", email: "ann@humancloud.co.in" }) && (await txt(p, "#logs-box")) === "No log lines match.");
  await p.selectOption("#logs-kind", ""); await p.selectOption("#logs-level", ""); await p.fill("#logs-person", ""); await p.locator("#logs-person").blur(); await p.waitForTimeout(400);
  const before = await p.evaluate(() => window.__mock.counts.logs_list);
  await p.click("#logs-refresh"); await p.waitForTimeout(400);
  ok(`[${t}] Refresh asks again`, (await p.evaluate(() => window.__mock.counts.logs_list)) > before);
  await p.close();

  p = await page("s=idle&view=candidate&auth=admin&logs=fail", dark);
  await p.locator("#tab-logs").click(); await p.waitForTimeout(500);
  ok(`[${t}] a failing service is said in plain words`, (await txt(p, "#logs-error")) === "The service had a problem. Try again in a minute.");
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
