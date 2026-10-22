// Author: kaustubh.s.v
// Browser checks for the last-record sync status (Settings > Sign-in) and the admin card "Last records in the company database".
// usage: node shoot-records.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
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
  await p.waitForTimeout(900);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${name}.png` }); };
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const calls = (p) => p.evaluate(() => window.__mock.counts.records_sync || 0);
const openSettings = async (p) => { await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(300); };
const lines = (p) => p.locator("#records-list li").allTextContents();

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  let p = await page("s=idle&view=candidate&auth=in", dark);
  ok(`[${t}] signed in: the records are synced at start-up`, (await calls(p)) >= 1);
  await openSettings(p);
  ok(`[${t}] settings: the three assistants are listed as stored`, JSON.stringify(await lines(p)) === JSON.stringify([
    "Find candidates: the latest record is stored in the company database.", "Company jobs: the latest record is stored in the company database.", "Accounts: the latest record is stored in the company database."]));
  ok(`[${t}] settings: the box says what is kept, contact details included`, (await txt(p, "#records-box .hint")).includes("no email, phone or evidence text") && (await txt(p, "#records-box .hint")).includes("Your Excel files are never sent anywhere"));
  await p.locator("#records-box").scrollIntoViewIfNeeded();
  await shot(p, `records-settings-${t}`);
  const before = await calls(p);
  await p.evaluate(() => window.__mock.end("candidate", "done"));
  await p.waitForTimeout(500);
  ok(`[${t}] a finished run triggers a sync`, (await calls(p)) > before);
  await p.close();

  p = await page("s=idle&view=candidate&auth=in&rec=waiting", dark);
  await openSettings(p);
  ok(`[${t}] offline: the lines say it is sent later`, (await lines(p)).every((l) => l.includes("You are offline. It is sent when you are back online.")) && (await p.locator("#records-list li.waiting").count()) === 3);
  await shot(p, `records-waiting-${t}`);
  await p.close();

  p = await page("s=idle&view=candidate&auth=in&rec=error", dark);
  await openSettings(p);
  ok(`[${t}] a refused record is said plainly`, (await lines(p)).some((l) => l === "Company jobs: The database did not accept this record.") && (await p.locator("#records-list li.error").count()) === 1);
  await p.close();

  p = await page("s=idle&view=candidate&auth=in&rec=older", dark);
  await openSettings(p);
  ok(`[${t}] a newer record from another computer is explained`, (await lines(p)).some((l) => l === "Accounts: a newer record from another computer is already stored."));
  await p.close();

  p = await page("s=idle&view=candidate", dark);
  await openSettings(p);
  ok(`[${t}] sign-in off: nothing is synced and no box is shown`, (await calls(p)) === 0 && !(await vis(p, "#records-box")));
  await p.close();

  p = await page("s=idle&view=candidate&auth=out", dark);
  ok(`[${t}] signed out: nothing is synced`, (await calls(p)) === 0);
  await p.fill("#login-email", "ann@humancloud.co.in");
  await p.fill("#login-password", "correct horse battery");
  await p.press("#login-password", "Enter");
  await p.waitForTimeout(1500);
  ok(`[${t}] signing in triggers the first sync`, (await calls(p)) >= 1);
  await p.close();

  p = await page("s=idle&view=candidate&auth=admin", dark);
  await p.locator("#tab-admin").click();
  await p.waitForTimeout(700);
  const rows = await p.locator("#stored-box tbody tr").allTextContents();
  ok(`[${t}] admin: the stored records are listed, one per assistant`, rows.length === 3 && rows[0].includes("Accounts") && rows[1].includes("Ann Rao · Senior Frontend Engineer · Acme · 85/100 · Senior React Frontend Developer") && rows[2].includes("SRE · Razorpay · Bengaluru · Open"));
  ok(`[${t}] admin: who sent each one is shown, with Unknown when it is not known`, rows[0].includes("ann@humancloud.co.in") && rows[1].includes("ravi.k@humancloud.co.in") && rows[2].includes("Unknown"));
  ok(`[${t}] admin: the card says only listed fields are kept`, (await txt(p, "#view-admin")).includes("Only these fields are kept"));
  await shot(p, `records-admin-${t}`);
  await p.click("#account-signout"); await p.waitForTimeout(300);
  await p.locator("#confirm-dialog button[value=ok]").click(); await p.waitForTimeout(500);
  ok(`[${t}] admin: signing out empties the card`, (await p.locator("#stored-box").innerHTML()) === "");
  await p.close();

  // automatic install: a countdown at a quiet moment, cancelled by Later, blocked by typed work or by the switch in About
  p = await page("s=idle&view=candidate&auth=in&upd=new", dark);
  await p.waitForTimeout(5800);
  ok(`[${t}] auto update: at a quiet moment a countdown starts`, /^Installing in \d+ seconds?\.$/.test(await txt(p, "#update-count")) && (await txt(p, "#update-text")).startsWith("Version 0.2.0 is available."), await txt(p, "#update-count"));
  await p.waitForTimeout(16000);
  ok(`[${t}] auto update: when it runs out the update is installed`, (await p.evaluate(() => window.__mock.counts.update_install || 0)) === 1);
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&upd=new", dark);
  await p.waitForTimeout(6500);
  await p.click("#update-later");
  await p.waitForTimeout(16000);
  ok(`[${t}] auto update: "Later" cancels the countdown and nothing is installed`, (await p.evaluate(() => window.__mock.counts.update_install || 0)) === 0 && (await txt(p, "#update-count")) === "" && !(await vis(p, "#update-banner")));
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&upd=new", dark);
  await p.fill("#jd-input", "Senior React developer, Pune");
  await p.waitForTimeout(6500);
  ok(`[${t}] auto update: work typed into a run form holds the countdown back, and the banner still offers the button`, (await txt(p, "#update-count")) === "" && (await vis(p, "#update-banner")) && (await p.evaluate(() => window.__mock.counts.update_install || 0)) === 0);
  await p.fill("#jd-input", "");
  await p.waitForTimeout(62000);
  ok(`[${t}] auto update: once the form is empty it is tried again within a minute`, /^Installing in \d+ seconds?\.$/.test(await txt(p, "#update-count")), await txt(p, "#update-count"));
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&upd=new", dark);
  await openSettings(p);
  ok(`[${t}] auto update: the switch in About is on by default and says what it does`, await p.locator("#update-auto").isChecked() && (await txt(p, "#view-settings")).includes("15 second countdown"));
  await p.locator("#update-auto").uncheck();
  await p.waitForTimeout(500);
  ok(`[${t}] auto update: switched off, no countdown starts`, (await txt(p, "#update-count")) === "" && (await p.evaluate(() => localStorage.getItem("rd-auto-update"))) === "off");
  await p.waitForTimeout(16000);
  ok(`[${t}] auto update: switched off, nothing is installed by itself`, (await p.evaluate(() => window.__mock.counts.update_install || 0)) === 0 && (await vis(p, "#update-banner")));
  await p.close();

  // updates: offered, postponed, installed on a click, checked by hand
  p = await page("s=idle&view=candidate&auth=in&upd=new", dark);
  await p.waitForTimeout(5600);
  ok(`[${t}] update: a newer version is offered in a banner`, (await vis(p, "#update-banner")) && (await txt(p, "#update-text")).startsWith("Version 0.2.0 is available. Faster tables"));
  await p.click("#update-later");
  ok(`[${t}] update: "Later" hides it`, !(await vis(p, "#update-banner")));
  await openSettings(p);
  await p.click("#update-check");
  await p.waitForTimeout(400);
  ok(`[${t}] update: checking by hand says what is available and shows the banner again`, (await txt(p, "#update-line")) === "Version 0.2.0 is available." && (await vis(p, "#update-banner")));
  await p.click("#update-install");
  await p.waitForTimeout(400);
  ok(`[${t}] update: "Update and restart" installs`, (await p.evaluate(() => window.__mock.counts.update_install || 0)) === 1);
  await p.close();
  p = await page("s=idle&view=candidate&auth=in", dark);
  await openSettings(p);
  await p.click("#update-check");
  await p.waitForTimeout(400);
  ok(`[${t}] update: up to date says so and shows no banner`, (await txt(p, "#update-line")) === "Recruit Desk is up to date." && !(await vis(p, "#update-banner")));
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&upd=fail", dark);
  await openSettings(p);
  await p.click("#update-check");
  await p.waitForTimeout(400);
  ok(`[${t}] update: a failed check is said in plain words`, (await txt(p, "#update-line")).startsWith("Could not reach the update server."));
  await p.close();

  // live updates: quick on Settings and other change-sensitive screens, once a minute on an idle assistant screen
  p = await page("s=idle&view=candidate&auth=in", dark);
  await p.waitForTimeout(12000);   // the first look comes after about ten seconds
  const a0 = await p.evaluate(() => window.__mock.counts.allowance_refresh || 0);
  await p.waitForTimeout(25000);
  const a1 = await p.evaluate(() => window.__mock.counts.allowance_refresh || 0);
  ok(`[${t}] live: an idle assistant screen does not ask every ten seconds`, a1 === a0, `${a0} -> ${a1}`);
  await openSettings(p);
  const s0 = await p.evaluate(() => window.__mock.counts.allowance_refresh || 0);
  await p.waitForTimeout(10600);
  const s1 = await p.evaluate(() => window.__mock.counts.allowance_refresh || 0);
  ok(`[${t}] live: on Settings the allowance is asked for every ten seconds`, s1 > s0, `${s0} -> ${s1}`);
  ok(`[${t}] live: Settings says when it asks, and that an idle screen asks once a minute`, /^Live updates: this window asks the company database for changes \(your allowance, History, People\) every 10 seconds while an assistant runs or this screen is open, and once a minute otherwise \(last time .+\)\.$/.test(await txt(p, "#live-line")), await txt(p, "#live-line"));
  await p.locator(".tab[data-view=candidate]").click();
  await p.waitForTimeout(72000);
  const q1 = await p.evaluate(() => window.__mock.counts.allowance_refresh || 0);
  ok(`[${t}] live: back on the assistant screen it still asks once a minute`, q1 > s1, `${s1} -> ${q1}`);
  await openSettings(p);
  // the data folder: where the results are kept, and a button to open it
  ok(`[${t}] data folder: Settings names the folder and says the files are not sent anywhere`, (await txt(p, "#data-folder-line")) === "Folder: C:\\Users\\ann\\Documents\\RecruitDesk-Data" && (await txt(p, "#card-data")).includes("Find candidates, Company jobs and Accounts") && (await txt(p, "#card-data")).includes("not sent to the company database"));
  await p.click("#open-data-folder");
  await p.waitForTimeout(300);
  ok(`[${t}] data folder: the button opens it`, (await p.evaluate(() => window.__mock.counts.open_data_folder || 0)) === 1 && !(await vis(p, "#data-folder-error")));
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&data=fail", dark);
  await openSettings(p);
  await p.click("#open-data-folder");
  await p.waitForTimeout(300);
  ok(`[${t}] data folder: a failure is said in plain words`, (await txt(p, "#data-folder-error")) === "Could not open the folder.");
  await p.close();
  p = await page("s=idle&view=candidate&auth=in&data=none", dark);
  await openSettings(p);
  ok(`[${t}] data folder: before the folder exists the button is off and the text says why`, (await p.locator("#open-data-folder").isDisabled()) && (await txt(p, "#data-folder-line")).startsWith("The folder is created the first time"));
  await p.close();
  p = await page("s=idle&view=candidate&auth=admin&admin=fail", dark);
  await p.locator("#tab-admin").click();
  await p.waitForTimeout(700);
  ok(`[${t}] admin: a failing service shows the plain message`, (await txt(p, "#admin-error")) === "The service had a problem. Try again in a minute.");
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
