// Author: kaustubh.s.v
// usage: node shoot-history.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
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
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${name}.png` }); };
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const confirmYes = async (p) => { await p.locator("#confirm-dialog button[value=ok]").click(); await p.waitForTimeout(600); };
const rows = (p) => p.locator("#history-box tbody tr:not(.history-detail)");
const rowOf = (p, text) => p.locator("#history-box tbody tr:not(.history-detail)", { hasText: text });
const openHistory = async (p) => { await p.keyboard.press("Control+6"); await p.waitForTimeout(700); };

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";

  // sign-in off or signed out: no History tab
  let p = await page("s=idle&view=candidate", dark);
  ok(`[${t}] sign-in off: no History tab`, !(await vis(p, "#tab-history")));
  await p.keyboard.press("Control+6");
  ok(`[${t}] sign-in off: Ctrl+6 does nothing`, !(await vis(p, "#view-history")));
  await p.close();

  // a plain user: own searches only
  p = await page("s=idle&view=candidate&auth=in", dark);
  ok(`[${t}] user: History tab shown, People tab not`, (await vis(p, "#tab-history")) && !(await vis(p, "#tab-admin")));
  await openHistory(p);
  ok(`[${t}] user: Ctrl+6 opens History`, (await vis(p, "#view-history")) && (await p.locator("#tab-history").getAttribute("aria-current")) === "page");
  ok(`[${t}] user: the intro says only they and their admins can see it`, (await txt(p, "#history-intro")) === "Your searches and their results, as stored in Humancloud Pvt Ltd's database. Only you and your company's admins can see them.");
  ok(`[${t}] user: only their own two searches are listed`, (await rows(p).count()) === 2 && !(await txt(p, "#history-box")).includes("Razorpay") && !(await txt(p, "#history-box")).includes("ravi.k"));
  ok(`[${t}] user: no Person column`, !(await p.locator("#history-box th").allTextContents()).includes("Person"));
  ok(`[${t}] user: what was searched is one short line`, (await txt(p, "#history-box")).includes("Senior React Frontend Developer") && !(await txt(p, "#history-box")).includes("Pune, 5+ years"));
  const jd = rowOf(p, "Senior React");
  ok(`[${t}] user: assistant, status and results count are in words`, (await jd.textContent()).includes("Find candidates") && (await jd.textContent()).includes("Finished") && (await jd.textContent()).includes("3 results"));
  const run = rowOf(p, "Humancloud");
  ok(`[${t}] user: a running search says Running and has no results to show`, (await run.textContent()).includes("Running") && (await run.textContent()).includes("No results") && (await run.locator("button", { hasText: "No results" }).isDisabled()));
  await shot(p, `history-user-${t}`);

  await jd.locator("button", { hasText: "Show results" }).click(); await p.waitForTimeout(500);
  ok(`[${t}] user: Show results lists each result in one line`, (await p.locator(".history-rows li").count()) === 3 && (await txt(p, ".history-rows")).includes("Ann Rao · Senior Frontend Engineer · Acme · 85/100"));
  ok(`[${t}] user: the button now says Hide results`, (await jd.locator("button").first().textContent()) === "Hide results" && (await jd.locator("button").first().getAttribute("aria-expanded")) === "true");
  await shot(p, `history-user-open-${t}`);
  await jd.locator("button").first().click();
  ok(`[${t}] user: Hide results collapses it`, (await p.locator(".history-rows").count()) === 0 || !(await vis(p, ".history-rows")));

  await jd.locator("button", { hasText: "Remove" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] user: removing asks first and says what it does to the company database and the computer`, (await txt(p, "#confirm-title")) === "Remove this search?" && (await txt(p, "#confirm-text")).startsWith("This search and its 3 results are removed from the company database.") && (await txt(p, "#confirm-text")).includes("not touched"));
  await shot(p, `history-remove-confirm-${t}`);
  await p.locator("#confirm-dialog button[value=cancel]").click(); await p.waitForTimeout(300);
  ok(`[${t}] user: Keep it leaves the list alone`, (await rows(p).count()) === 2);
  await jd.locator("button", { hasText: "Remove" }).click(); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] user: the search is gone and the page says so`, (await rows(p).count()) === 1 && !(await txt(p, "#history-box")).includes("Senior React") && (await p.locator("#toasts").textContent()).includes("The search was removed."));
  await p.click("#history-refresh"); await p.waitForTimeout(500);
  ok(`[${t}] user: Refresh says it refreshed and syncs first`, (await p.locator("#toasts").textContent()).includes("List refreshed.") && (await p.evaluate(() => window.__mock.counts.history_sync || 0)) >= 2);

  // Settings: one line about the history, and no switch for it anywhere
  await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(400);
  ok(`[${t}] settings: the sign-in card says the history is stored`, (await txt(p, "#history-line")) === "Search history: your searches and results are stored in the company database.");
  ok(`[${t}] settings: there is no switch or checkbox for the history`, (await p.locator("#view-settings input[type=checkbox], #view-settings [role=switch]").filter({ hasText: /history/i }).count()) === 0 && !(await txt(p, "#view-settings")).toLowerCase().includes("turn off the history") && !(await txt(p, "#view-settings")).toLowerCase().includes("share my"));

  await p.click("#account-signout"); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] sign out: locked, the tab is gone and the list is emptied`, (await vis(p, "#login")) && !(await vis(p, "#tab-history")) && (await p.locator("#history-box").innerHTML()) === "");
  await p.close();

  // an admin: everyone's
  p = await page("s=idle&view=candidate&auth=admin", dark);
  ok(`[${t}] admin: both History and People tabs`, (await vis(p, "#tab-history")) && (await vis(p, "#tab-admin")));
  await openHistory(p);
  ok(`[${t}] admin: the intro says they see everyone's`, (await txt(p, "#history-intro")).startsWith("Every search in Humancloud Pvt Ltd, with its results.") && (await txt(p, "#history-intro")).includes("as an admin you see everyone's"));
  ok(`[${t}] admin: all four searches are listed with a Person column`, (await rows(p).count()) === 4 && (await p.locator("#history-box th").allTextContents()).includes("Person"));
  ok(`[${t}] admin: the people are named and their own row says You`, (await txt(p, "#history-box")).includes("ann@humancloud.co.in") && (await txt(p, "#history-box")).includes("ravi.k@humancloud.co.in") && (await rowOf(p, "Razorpay").locator(".you").textContent()) === "You");
  ok(`[${t}] admin: a company search says its domain and public posts, a list says how many companies`, (await txt(p, "#history-box")).includes("Razorpay (razorpay.com), with public posts") && (await txt(p, "#history-box")).includes("A list of 5 companies"));
  ok(`[${t}] admin: a stopped search says Stopped`, (await rowOf(p, "A list of 5").textContent()).includes("Stopped"));
  await shot(p, `history-admin-${t}`);
  await rowOf(p, "Razorpay").locator("button", { hasText: "Show results" }).click(); await p.waitForTimeout(500);
  ok(`[${t}] admin: opened results show who stored them`, (await p.locator(".history-rows li").count()) === 2 && (await txt(p, ".history-rows")).includes("admin@humancloud.co.in") && (await txt(p, ".history-rows")).includes("SRE · Razorpay · Bengaluru · Open"));
  await rowOf(p, "A list of 5").locator("button", { hasText: "Remove" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] admin: removing someone else's search names them`, (await txt(p, "#confirm-text")).startsWith("ravi.k@humancloud.co.in's search and its 1 result are removed"));
  await confirmYes(p);
  ok(`[${t}] admin: it is removed for everyone`, (await rows(p).count()) === 3 && !(await txt(p, "#history-box")).includes("A list of 5"));
  await p.close();

  // empty and failing service
  p = await page("s=idle&view=candidate&auth=in&hist=empty", dark);
  await openHistory(p);
  ok(`[${t}] empty: says nothing is stored yet`, (await txt(p, "#history-box")).startsWith("No search has been stored yet."));
  await shot(p, `history-empty-${t}`);
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&hist=fail", dark);
  await openHistory(p);
  ok(`[${t}] failing service: a plain message and no table`, (await txt(p, "#history-error")) === "The service had a problem. Try again in a minute." && (await rows(p).count()) === 0);
  await shot(p, `history-error-${t}`);
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&hist=waiting", dark);
  await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(500);
  ok(`[${t}] offline: Settings says the history is stored when back online`, (await txt(p, "#history-line")) === "Search history: You are offline. Your history is stored when you are back online.");
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
