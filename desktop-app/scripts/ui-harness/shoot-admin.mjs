// Author: kaustubh.s.v
// usage: node shoot-admin.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [out, port] = process.argv.slice(2);
const browser = await chromium.launch();
const errors = [], results = [];
const ok = (name, cond) => { results.push([name, !!cond]); console.log((cond ? "PASS " : "FAIL ") + name); };
async function page(q, dark) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce", permissions: ["clipboard-read", "clipboard-write"] });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${q}: ${e.message}`));
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}`);
  await p.waitForTimeout(700);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${name}.png` }); };
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const openPeople = async (p) => { await p.locator("#tab-admin").click(); await p.waitForTimeout(500); };
const confirmYes = async (p) => { await p.locator("#confirm-dialog button[value=ok]").click(); await p.waitForTimeout(600); };
const rows = (p) => p.locator("#users-box tbody tr");
const rowOf = (p, email) => p.locator("#users-box tbody tr", { hasText: email });

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  // 1. a plain user never sees the tab
  let p = await page("s=idle&view=candidate&auth=in", dark);
  ok(`[${t}] user: no People tab`, !(await vis(p, "#tab-admin")));
  await p.keyboard.press("Control+5");
  ok(`[${t}] user: Ctrl+5 does nothing`, !(await vis(p, "#view-admin")));
  await p.close();

  // 2. no sign-in at all: no People tab either
  p = await page("s=idle&view=candidate", dark);
  ok(`[${t}] sign-in off: no People tab`, !(await vis(p, "#tab-admin")));
  await p.close();

  // 3. an admin
  p = await page("s=idle&view=candidate&auth=admin", dark);
  ok(`[${t}] admin: People tab shown`, await vis(p, "#tab-admin"));
  await p.keyboard.press("Control+5");
  await p.waitForTimeout(500);
  ok(`[${t}] admin: Ctrl+5 opens People`, await vis(p, "#view-admin") && (await p.locator("#tab-admin").getAttribute("aria-current")) === "page");
  ok(`[${t}] admin: People says which company the invitations go to`, /^Invite people to Humancloud Pvt Ltd,/.test(await txt(p, "#admin-intro")) && /only see and manage people in your own company/.test(await txt(p, "#admin-intro")));
  ok(`[${t}] admin: 3 people and 2 waiting invitations`, (await rows(p).count()) === 3 && (await p.locator("#invites-box tbody tr").count()) === 2);
  const me = rowOf(p, "admin@humancloud.co.in");
  ok(`[${t}] admin: own row (the owner) says You and offers no actions`, (await me.locator(".you").textContent()) === "You" && (await me.locator("button").count()) === 0);
  const ann = rowOf(p, "ann@humancloud.co.in");
  ok(`[${t}] admin: a user row offers Make admin and Remove`, (await ann.locator("button").allTextContents()).join("|") === "Make admin|Remove");
  ok(`[${t}] admin: an admin row offers Make user and Remove`, (await rowOf(p, "ravi.k").locator("button").allTextContents()).join("|") === "Make user|Remove");
  ok(`[${t}] admin: the join date is shown`, (await ann.textContent()).includes("2026"));
  ok(`[${t}] admin: action buttons name the person for screen readers`, (await ann.locator("button").first().getAttribute("aria-label")) === "Make ann@humancloud.co.in an admin" && (await ann.locator("button").nth(1).getAttribute("aria-label")) === "Remove ann@humancloud.co.in from Humancloud Pvt Ltd");
  ok(`[${t}] admin: invitation expiry is in words`, (await txt(p, "#invites-box")).includes("in 40 hours") && (await txt(p, "#invites-box")).includes("in 20 hours"));
  ok(`[${t}] admin: waiting invitations offer Show code and Withdraw`, (await p.locator("#invites-box tbody tr").first().locator("button").allTextContents()).join("|") === "Show code|Withdraw");
  ok(`[${t}] admin: there is no reset link and no switch off anywhere`, !(await txt(p, "#view-admin")).includes("Reset link") && !(await txt(p, "#view-admin")).includes("Switch off"));
  await shot(p, `people-${t}`);

  // invite: bad input, existing member, success
  await p.fill("#invite-email", "not an email");
  await p.click("#invite-send");
  ok(`[${t}] invite: a bad address is explained`, (await txt(p, "#admin-error")) === "Enter a valid email address.");
  await p.fill("#invite-email", "ann@humancloud.co.in");
  await p.click("#invite-send"); await p.waitForTimeout(500);
  ok(`[${t}] invite: someone already in the company is explained`, (await txt(p, "#admin-error")) === "That person is already in your company." && !(await vis(p, "#link-box")));
  await p.fill("#invite-email", "new.recruiter@humancloud.co.in");
  await p.click("#invite-send"); await p.waitForTimeout(500);
  ok(`[${t}] invite: an address that is already invited is explained`, (await txt(p, "#admin-error")) === "That person was already invited. Withdraw the old invitation first.");
  await p.fill("#invite-email", "Fresh.Person@humancloud.co.in");
  await p.selectOption("#invite-role", "admin");
  await p.click("#invite-send"); await p.waitForTimeout(700);
  const code = await p.locator("#link-url").inputValue();
  ok(`[${t}] invite: the code is shown, with who it is for`, (await vis(p, "#link-box")) && (await txt(p, "#link-title")) === "Invitation code for fresh.person@humancloud.co.in" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(code));
  ok(`[${t}] invite: the text says the app writes the email and what it holds`, (await txt(p, "#link-text")).startsWith('Press "Email the invitation" to open a ready-written email to fresh.person@humancloud.co.in') && (await txt(p, "#link-text")).includes("invitation id and the download link") && !(await txt(p, "#link-text")).includes("No email is sent"));
  ok(`[${t}] invite: the Email and Copy message buttons are offered`, (await vis(p, "#link-mail")) && (await vis(p, "#link-copy-msg")));
  await p.click("#link-mail"); await p.waitForTimeout(400);
  const mail = await p.evaluate(() => window.__mock.lastMail);
  ok(`[${t}] invite: Email the invitation opens the mail program with the invitation id and the download link, no accept link`, mail && mail.to === "fresh.person@humancloud.co.in" && mail.subject === "You are invited to join Humancloud Pvt Ltd on Recruit Desk" && mail.body.includes(`Invitation id: ${code}`) && mail.body.includes("Download the app (Windows): https://github.com/kaustubhvasagadekarhc/recruit-desk/releases/latest/download/Recruit-Desk-Setup.exe") && !/accept/i.test(mail.body), JSON.stringify(mail).slice(0, 300));
  ok(`[${t}] invite: and says Press Send there`, (await p.locator("#toasts").textContent()).includes("Press Send there"));
  await p.click("#link-copy-msg"); await p.waitForTimeout(300);
  ok(`[${t}] invite: Copy the message puts the whole email on the clipboard`, (await p.evaluate(() => navigator.clipboard.readText())).startsWith("To: fresh.person@humancloud.co.in\nSubject: You are invited to join Humancloud Pvt Ltd on Recruit Desk\n\nHello,") && (await p.evaluate(() => navigator.clipboard.readText())).includes(code));
  ok(`[${t}] invite: the warning names the role and the company`, (await txt(p, "#link-warn")).includes("as admin") && (await txt(p, "#link-warn")).includes("Humancloud Pvt Ltd"));
  ok(`[${t}] invite: the form is cleared and the waiting list grew`, (await p.locator("#invite-email").inputValue()) === "" && (await p.locator("#invites-box tbody tr").count()) === 3);
  await shot(p, `people-link-${t}`);
  await p.click("#link-copy"); await p.waitForTimeout(300);
  ok(`[${t}] invite: Copy puts the code on the clipboard`, (await p.evaluate(() => navigator.clipboard.readText())) === code && (await p.locator("#toasts").textContent()).includes("Code copied."));
  // leaving the screen clears the secret, and the waiting list can show it again
  await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(300);
  await openPeople(p);
  ok(`[${t}] invite: the code box is empty after leaving and coming back`, !(await vis(p, "#link-box")) && (await p.locator("#link-url").inputValue()) === "");
  await p.locator("#invites-box tbody tr", { hasText: "fresh.person" }).locator("button", { hasText: "Show code" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] invite: Show code brings the same code back`, (await p.locator("#link-url").inputValue()) === code && (await txt(p, "#link-title")) === "Invitation code for fresh.person@humancloud.co.in");
  await p.click("#link-close");
  ok(`[${t}] invite: Done clears the code`, !(await vis(p, "#link-box")) && (await p.locator("#link-url").inputValue()) === "");

  // withdraw (cancel, then yes)
  await p.locator("#invites-box tbody tr", { hasText: "lead@humancloud.co.in" }).locator("button", { hasText: "Withdraw" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] withdraw: asks first and says the code stops working`, (await txt(p, "#confirm-text")).includes("The code given to lead@humancloud.co.in stops working at once."));
  await shot(p, `people-withdraw-confirm-${t}`);
  await p.locator("#confirm-dialog button[value=cancel]").click(); await p.waitForTimeout(300);
  ok(`[${t}] withdraw: Cancel keeps it`, (await p.locator("#invites-box tbody tr").count()) === 3);
  await p.locator("#invites-box tbody tr", { hasText: "lead@humancloud.co.in" }).locator("button", { hasText: "Withdraw" }).click(); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] withdraw: the invitation is gone`, (await p.locator("#invites-box tbody tr").count()) === 2 && !(await txt(p, "#invites-box")).includes("lead@humancloud.co.in"));

  // role change
  await rowOf(p, "ann@humancloud.co.in").locator("button", { hasText: "Make admin" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] role: making an admin asks first and says what it allows`, (await txt(p, "#confirm-title")) === "Make this person an admin?" && (await txt(p, "#confirm-text")).includes("invite people"));
  await shot(p, `people-role-confirm-${t}`);
  await confirmYes(p);
  ok(`[${t}] role: the person is now an admin`, (await rowOf(p, "ann@humancloud.co.in").textContent()).includes("Admin") && (await rowOf(p, "ann@humancloud.co.in").locator("button").first().textContent()) === "Make user");
  await rowOf(p, "ravi.k@humancloud.co.in").locator("button", { hasText: "Make user" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] role: removing admin rights asks first`, (await txt(p, "#confirm-title")) === "Remove admin rights?");
  await confirmYes(p);
  ok(`[${t}] role: ravi is a user again`, (await rowOf(p, "ravi.k@humancloud.co.in").locator("button").first().textContent()) === "Make admin");

  // remove
  await rowOf(p, "ann@humancloud.co.in").locator("button", { hasText: "Remove" }).click(); await p.waitForTimeout(300);
  ok(`[${t}] remove: asks and says what happens to them and to their files`, (await txt(p, "#confirm-title")) === "Remove this person?" && (await txt(p, "#confirm-text")).includes("loses access to Humancloud Pvt Ltd's data at once") && (await txt(p, "#confirm-text")).includes("not touched"));
  await shot(p, `people-remove-confirm-${t}`);
  await p.locator("#confirm-dialog button[value=cancel]").click(); await p.waitForTimeout(300);
  ok(`[${t}] remove: Cancel keeps them`, (await rows(p).count()) === 3);
  await rowOf(p, "ann@humancloud.co.in").locator("button", { hasText: "Remove" }).click(); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] remove: the person is gone from the list and the page says so`, (await rows(p).count()) === 2 && !(await txt(p, "#users-box")).includes("ann@humancloud.co.in") && (await p.locator("#toasts").textContent()).includes("ann@humancloud.co.in was removed from Humancloud Pvt Ltd."));

  // refresh
  await p.click("#admin-refresh"); await p.waitForTimeout(500);
  ok(`[${t}] refresh: says it refreshed`, (await p.locator("#toasts").textContent()).includes("List refreshed."));

  // signing out clears the screen and locks the app
  await p.click("#account-signout"); await p.waitForTimeout(300);
  await confirmYes(p);
  ok(`[${t}] sign out: locked, the tab is gone and the lists are emptied`, (await vis(p, "#login")) && !(await vis(p, "#tab-admin")) && (await p.locator("#users-box").innerHTML()) === "");
  await p.close();

  // 4. empty and failing service
  p = await page("s=idle&view=candidate&auth=admin&admin=empty", dark);
  await openPeople(p);
  ok(`[${t}] empty: says nobody is waiting`, (await txt(p, "#invites-box")) === "No invitation is waiting." && (await rows(p).count()) === 1);
  await shot(p, `people-empty-${t}`);
  await p.close();
  p = await page("s=idle&view=candidate&auth=admin&admin=fail", dark);
  await openPeople(p);
  ok(`[${t}] failing service: a plain message, no table`, (await txt(p, "#admin-error")) === "The service had a problem. Try again in a minute." && (await rows(p).count()) === 0);
  await shot(p, `people-error-${t}`);
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
