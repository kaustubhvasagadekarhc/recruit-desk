// Author: kaustubh.s.v
// usage: node shoot-allowance.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
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
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}`);
  await p.waitForTimeout(700);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${name}.png` }); };
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const confirmYes = async (p) => { await p.locator("#confirm-dialog button[value=ok]").click(); await p.waitForTimeout(600); };
const openSettings = async (p) => { await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(500); };

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";

  // sign-in off: the old per-computer allowance, no card, no shared label
  let p = await page("s=idle&view=company", dark);
  ok(`[${t}] sign-in off: the meter is the plain allowance`, (await txt(p, "#view-company .usage:not(.reset-card) .usage-label")) === "Allowance used" && !(await vis(p, "#view-company .usage-shared")));
  ok(`[${t}] sign-in off: the reset card says 4 hours or the running time`, /h|min/.test(await txt(p, "#view-company .reset-in")));
  await openSettings(p);
  ok(`[${t}] sign-in off: there is no allowance card`, !(await vis(p, "#card-allowance")));
  await p.close();

  // a plain user: sees their own total over all assistants, cannot change the cap
  p = await page("s=idle&view=company&auth=in", dark);
  ok(`[${t}] user: the meter says it is their own allowance over all assistants`, (await txt(p, "#view-company .usage:not(.reset-card) .usage-label")) === "Your allowance used" && (await txt(p, "#view-company .usage-shared")) === "Your own total over all the assistants");
  const totals = await Promise.all(["candidate", "company", "account"].map((a) => txt(p, `#view-${a} .usage:not(.reset-card) .usage-note:not(.usage-shared)`)));
  ok(`[${t}] user: every assistant shows the same total`, totals[0] === totals[1] && totals[1] === totals[2] && /of 3M tokens/.test(totals[0]));
  await shot(p, `allowance-user-${t}`);
  await openSettings(p);
  ok(`[${t}] user: Settings has no allowance card to change it`, !(await vis(p, "#card-allowance")) && (await p.locator("#allowance-people input").count()) === 0 && (await p.evaluate(() => window.__mock.counts.allowance_people || 0)) === 0);
  await p.close();

  // an admin: each person's own cap and window, and everyone's usage
  p = await page("s=idle&view=company&auth=admin", dark);
  await openSettings(p);
  const prow = (email) => p.locator(`#allowance-people [data-email="${email}"]`);
  ok(`[${t}] admin: the card shows, says every person has their own cap and window, and who may change it`, (await vis(p, "#card-allowance")) && (await txt(p, "#allowance-intro")).startsWith("Every person in Humancloud Pvt Ltd has their own allowance, counted over all three assistants together, with their own cap and window.") && (await txt(p, "#allowance-intro")).includes("Only admins can change it."));
  ok(`[${t}] admin: there is one row per person, each with their own cap and window in hours`, (await p.locator("#allowance-people .allowance-row").count()) === 3 && (await prow("ann@humancloud.co.in").locator(".al-cap").inputValue()) === "3000000" && (await prow("ann@humancloud.co.in").locator(".al-hours").inputValue()) === "4");
  ok(`[${t}] admin: each row says how much that person used, and who is used up`, (await prow("admin@humancloud.co.in").locator(".al-used").textContent()).includes("210k of 3M used") && (await prow("bob@humancloud.co.in").locator(".al-used").textContent()).includes("3M of 3M used (used up)"));
  ok(`[${t}] admin: there is no company-wide cap box`, (await p.locator("#allowance-rows").count()) === 0 && (await p.locator("#allowance-people .al-cap").count()) === 3);
  await shot(p, `allowance-admin-${t}`);
  await prow("ann@humancloud.co.in").locator(".al-cap").fill("not a number");
  await prow("ann@humancloud.co.in").locator("button", { hasText: "Save" }).click();
  ok(`[${t}] admin: a bad cap is explained and nothing is sent`, (await txt(p, "#allowance-error")).startsWith("The cap must be a whole number of tokens") && !(await p.evaluate(() => window.__mock.allowance)));
  await prow("ann@humancloud.co.in").locator(".al-cap").fill("2,500,000");
  await prow("ann@humancloud.co.in").locator(".al-hours").fill("0.1");
  await prow("ann@humancloud.co.in").locator("button", { hasText: "Save" }).click();
  ok(`[${t}] admin: a window under 15 minutes is explained`, (await txt(p, "#allowance-error")).startsWith("The window must be between 0.25 hours"));
  await prow("ann@humancloud.co.in").locator(".al-hours").fill("1,5");
  await prow("ann@humancloud.co.in").locator("button", { hasText: "Save" }).click(); await p.waitForTimeout(800);
  ok(`[${t}] admin: a good change is saved for that person only and says so`, (await p.evaluate(() => JSON.stringify(window.__mock.allowance))) === JSON.stringify({ "ann@humancloud.co.in": { cap: 2500000, minutes: 90 } }) && (await p.locator("#toasts").textContent()).includes("The allowance of ann@humancloud.co.in was saved."));
  ok(`[${t}] admin: the saved numbers show on that row and the others keep theirs`, (await prow("ann@humancloud.co.in").locator(".al-cap").inputValue()) === "2500000" && (await prow("ann@humancloud.co.in").locator(".al-hours").inputValue()) === "1.5" && (await prow("bob@humancloud.co.in").locator(".al-cap").inputValue()) === "3000000" && (await txt(p, "#view-company .usage:not(.reset-card) .usage-note:not(.usage-shared)")).includes("of 3M tokens"));
  await prow("admin@humancloud.co.in").locator(".al-cap").fill("1,000,000");
  await prow("admin@humancloud.co.in").locator("button", { hasText: "Save" }).click(); await p.waitForTimeout(800);
  ok(`[${t}] admin: changing their own cap changes their own meter`, (await txt(p, "#view-company .usage:not(.reset-card) .usage-note:not(.usage-shared)")).includes("of 1M tokens"));
  await prow("bob@humancloud.co.in").locator("button", { hasText: "Reset count" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] admin: resetting one person asks first and names them`, (await txt(p, "#confirm-title")) === "Reset this allowance now?" && (await txt(p, "#confirm-text")).includes("bob@humancloud.co.in's count goes back to zero"));
  await p.locator("#confirm-dialog button[value=cancel]").click(); await p.waitForTimeout(300);
  ok(`[${t}] admin: Keep it changes nothing`, !(await p.evaluate(() => window.__mock.resets)));
  await prow("bob@humancloud.co.in").locator("button", { hasText: "Reset count" }).click(); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] admin: after the reset that person is at zero, with their cap kept, and the others are not`, (await p.evaluate(() => [window.__mock.resets, window.__mock.resetOf])).join() === "1,bob@humancloud.co.in" && (await prow("bob@humancloud.co.in").locator(".al-used").textContent()).startsWith("0 of 3M") && !/^0 of/.test(await prow("ann@humancloud.co.in").locator(".al-used").textContent()));
  await p.locator("#allowance-people button", { hasText: "Reset everyone's count" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] admin: resetting everyone asks first and says so`, (await txt(p, "#confirm-text")).includes("The count of everyone in Humancloud Pvt Ltd goes back to zero"));
  await confirmYes(p);
  ok(`[${t}] admin: after that everyone is at zero, including the admin's own meter`, (await p.evaluate(() => [window.__mock.resets, window.__mock.resetOf])).join() === "2," && (await p.locator("#allowance-people .al-used").allTextContents()).every((x) => x.startsWith("0 of")) && (await txt(p, "#view-company .usage:not(.reset-card) .usage-note:not(.usage-shared)")).startsWith("0 of"));
  await p.close();

  // a failing service: a plain message, the card stays
  p = await page("s=idle&view=company&auth=admin&pool=fail", dark);
  await openSettings(p);
  await p.locator("#allowance-people .al-cap").first().fill("2000000");
  await p.locator("#allowance-people button", { hasText: "Save" }).first().click(); await p.waitForTimeout(500);
  ok(`[${t}] failing service: a plain message`, (await txt(p, "#allowance-error")) === "The service had a problem. Try again in a minute.");
  await p.close();

  // locked pool: the lock text names the common time
  p = await page("s=locked&view=company&auth=in", dark);
  ok(`[${t}] a full allowance locks every assistant for that person`, (await vis(p, "#view-company .lock-overlay")) && (await txt(p, "#view-company .usage:not(.reset-card) .usage-label")) === "Your allowance used");
  await shot(p, `allowance-locked-${t}`);
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
