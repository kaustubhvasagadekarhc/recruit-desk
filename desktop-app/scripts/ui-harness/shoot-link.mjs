// Author: kaustubh.s.v
// usage: node shoot-link.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
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
const GOOD = "11111111-2222-3333-4444-555555555555";
const WRONG = "22222222-2222-3333-4444-555555555555";
const RST = "https://recruitdesk.example/reset?token=abcdefghijklmnopqrstuvwxmockResetToken";
const LONGPW = "a long enough password";

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  let p = await page("s=idle&view=candidate&auth=out", dark);
  ok(`[${t}] options: an invitation code and a forgotten password are offered under the sign-in form`, (await vis(p, "#show-create")) && (await vis(p, "#show-forgot")) && !(await vis(p, "#link-form")));

  // ---- create an account ----
  await p.click("#show-create");
  ok(`[${t}] create: the panel replaces the sign-in form with email, code and password fields`, (await vis(p, "#link-form")) && !(await vis(p, "#login-form")) && (await txt(p, "#login-title")) === "Create your account" && !(await vis(p, "#login-alt"))
    && (await vis(p, "#link-email")) && (await vis(p, "#link-code")) && !(await vis(p, "#link-input")) && (await vis(p, "#newpw-1")) && (await vis(p, "#newpw-2")));
  ok(`[${t}] create: the email field has focus`, await p.evaluate(() => document.activeElement?.id === "link-email"));
  await shot(p, `link-create-${t}`);
  await p.click("#link-submit");
  ok(`[${t}] create: a missing email is explained`, (await txt(p, "#link-error")) === "Enter a valid email address.");
  await p.fill("#link-email", "new.person@humancloud.co.in");
  await p.click("#link-submit");
  ok(`[${t}] create: a missing code is explained`, (await txt(p, "#link-error")) === "Enter the invitation code you were given.");
  await p.fill("#link-code", "not-a-code");
  await p.click("#link-submit");
  ok(`[${t}] create: a malformed code is explained before anything is sent`, (await txt(p, "#link-error")).startsWith("That does not look like an invitation code."));
  await p.fill("#link-code", GOOD);
  await p.fill("#newpw-1", "short");
  await p.fill("#newpw-2", "short");
  await p.click("#link-submit");
  ok(`[${t}] create: a short password is explained`, (await txt(p, "#link-error")) === "Choose a password of at least 10 characters.");
  await p.fill("#newpw-1", LONGPW);
  await p.fill("#newpw-2", LONGPW + "X");
  await p.click("#link-submit");
  ok(`[${t}] create: two different passwords are explained`, (await txt(p, "#link-error")) === "The two passwords are different.");
  await p.click("#newpw-show");
  ok(`[${t}] create: Show reveals both password fields`, (await p.locator("#newpw-1").getAttribute("type")) === "text" && (await p.locator("#newpw-2").getAttribute("type")) === "text");
  await p.click("#newpw-show");
  await p.fill("#link-email", "ann@humancloud.co.in");
  await p.fill("#newpw-2", LONGPW);
  await p.click("#link-submit"); await p.waitForTimeout(600);
  ok(`[${t}] create: an address that already has an account says so and keeps the form`, (await txt(p, "#link-error")) === "That email already has an account. Sign in instead." && (await vis(p, "#link-form")));
  await shot(p, `link-create-error-${t}`);
  await p.click("#link-back");
  ok(`[${t}] create: Back returns to the sign-in form with the title restored`, (await vis(p, "#login-form")) && !(await vis(p, "#link-form")) && (await txt(p, "#login-title")) === "Sign in" && (await vis(p, "#login-alt")));
  await p.click("#show-create");
  ok(`[${t}] create: reopening the panel starts empty (code and passwords)`, (await p.locator("#link-code").inputValue()) === "" && (await p.locator("#newpw-1").inputValue()) === "");
  await p.fill("#link-email", "new.person@humancloud.co.in");
  await p.fill("#link-code", GOOD.toUpperCase());
  await p.fill("#newpw-1", LONGPW);
  await p.fill("#newpw-2", LONGPW);
  await p.press("#newpw-2", "Enter"); await p.waitForTimeout(1200);
  ok(`[${t}] create: a good code and password create the account and open the app`, !(await vis(p, "#login")) && (await txt(p, "#account-email")) === "new.person@humancloud.co.in" && (await txt(p, "#account-role")) === "User" && (await txt(p, "#account-tenant")) === "Humancloud Pvt Ltd");
  ok(`[${t}] create: no password or code is left in the page`, !(await p.evaluate((g) => document.body.innerHTML.includes("a long enough password") || document.body.innerHTML.includes(g), GOOD)) && (await p.locator("#newpw-1").inputValue()) === "" && (await p.locator("#link-code").inputValue()) === "");
  await p.click("#account-signout"); await p.waitForTimeout(300);
  await p.locator("#confirm-dialog button[value=ok]").click(); await p.waitForTimeout(500);
  ok(`[${t}] create: signing out shows the plain sign-in form again`, (await vis(p, "#login-form")) && !(await vis(p, "#link-form")));
  await p.close();

  // ---- a code that does not work: the account exists, the person is asked for a code ----
  p = await page("s=idle&view=candidate&auth=out", dark);
  await p.click("#show-create");
  await p.fill("#link-email", "new.person@humancloud.co.in");
  await p.fill("#link-code", WRONG);
  await p.fill("#newpw-1", LONGPW);
  await p.fill("#newpw-2", LONGPW);
  await p.click("#link-submit"); await p.waitForTimeout(900);
  ok(`[${t}] join: a wrong code keeps the person signed in and asks for a code`, (await vis(p, "#login")) && (await txt(p, "#login-title")) === "Join your company" && (await vis(p, "#link-code")) && !(await vis(p, "#link-email")) && !(await vis(p, "#newpw-1")));
  ok(`[${t}] join: the reason is shown`, (await txt(p, "#login-notice")).startsWith("That invitation code did not work."));
  ok(`[${t}] join: there is Sign out instead of Back`, (await vis(p, "#link-signout")) && !(await vis(p, "#link-back")) && !(await vis(p, "#link-have")));
  await shot(p, `link-join-${t}`);
  await p.fill("#link-code", WRONG);
  await p.click("#link-submit"); await p.waitForTimeout(600);
  ok(`[${t}] join: another wrong code is explained`, (await txt(p, "#link-error")).startsWith("That invitation code did not work."));
  await p.fill("#link-code", GOOD);
  await p.press("#link-code", "Enter"); await p.waitForTimeout(900);
  ok(`[${t}] join: the right code opens the app in the company`, !(await vis(p, "#login")) && (await txt(p, "#account-tenant")) === "Humancloud Pvt Ltd");
  await p.close();

  p = await page("s=idle&view=candidate&auth=nocompany", dark);
  ok(`[${t}] join: opening the app signed in without a company shows the code panel at once`, (await vis(p, "#login")) && (await txt(p, "#login-title")) === "Join your company" && (await txt(p, "#login-notice")).startsWith("You are not part of a company yet."));
  await p.click("#link-signout"); await p.waitForTimeout(500);
  ok(`[${t}] join: Sign out returns to the plain sign-in form`, (await vis(p, "#login-form")) && !(await vis(p, "#link-form")) && (await txt(p, "#login-title")) === "Sign in");
  await p.close();

  // ---- forgotten password, then the reset link ----
  p = await page("s=idle&view=candidate&auth=out", dark);
  await p.click("#show-forgot");
  ok(`[${t}] forgot: the panel asks only for the email address`, (await txt(p, "#login-title")) === "Forgot your password?" && (await vis(p, "#link-email")) && !(await vis(p, "#link-code")) && !(await vis(p, "#link-input")) && !(await vis(p, "#newpw-1")) && (await txt(p, "#link-submit")) === "Send reset email");
  ok(`[${t}] forgot: Back and "I have the reset link" are offered`, (await vis(p, "#link-back")) && (await vis(p, "#link-have")) && !(await vis(p, "#link-signout")));
  await shot(p, `link-forgot-${t}`);
  await p.fill("#link-email", "nonsense");
  await p.click("#link-submit");
  ok(`[${t}] forgot: a bad address is explained`, (await txt(p, "#link-error")) === "Enter a valid email address.");
  await p.fill("#link-email", "ann@humancloud.co.in");
  await p.click("#link-submit"); await p.waitForTimeout(700);
  ok(`[${t}] forgot: after sending, the page moves on to the reset form with the answer on top`, (await txt(p, "#login-title")) === "Choose a new password" && (await txt(p, "#link-intro")).startsWith("If that address has an account, an email with a reset link is on its way.") && (await vis(p, "#link-input")) && (await vis(p, "#newpw-1")) && !(await vis(p, "#link-email")));
  await shot(p, `link-reset-${t}`);
  await p.fill("#link-input", "https://recruitdesk.example/reset?token=staleabcdefghijklmnopqrstu");
  await p.fill("#newpw-1", "a brand new password");
  await p.fill("#newpw-2", "a brand new password");
  await p.click("#link-submit"); await p.waitForTimeout(600);
  ok(`[${t}] reset: an old link says so`, (await txt(p, "#link-error")) === "This reset link is no longer valid. Ask for a new one.");
  await p.fill("#link-input", RST);
  await p.click("#link-submit"); await p.waitForTimeout(800);
  ok(`[${t}] reset: success returns to sign-in with a notice, and does not sign in`, (await vis(p, "#login-form")) && (await vis(p, "#login")) && (await txt(p, "#login-notice")) === "Password changed. Sign in with your new password.");
  await shot(p, `link-reset-done-${t}`);
  await p.click("#show-forgot");
  await p.click("#link-have");
  ok(`[${t}] reset: "I have the reset link" opens the reset form directly`, (await txt(p, "#login-title")) === "Choose a new password" && (await vis(p, "#link-input")));
  await p.close();
}
// the notice from an earlier session is hidden while an account or reset panel is open, and comes back with "Back"
for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  const p = await page("s=idle&view=candidate&auth=ended", dark);
  ok(`[${t}] notice: shown on the plain sign-in form`, (await txt(p, "#login-notice")) === "Your session ended. Sign in again.");
  await p.click("#show-create");
  ok(`[${t}] notice: hidden while the account panel is open`, !(await vis(p, "#login-notice")));
  await p.click("#link-back");
  ok(`[${t}] notice: back after Back to sign in`, (await txt(p, "#login-notice")) === "Your session ended. Sign in again.");
  await p.click("#show-create");
  await p.fill("#link-email", "new.person@humancloud.co.in");
  await p.fill("#link-code", GOOD);
  await p.fill("#newpw-1", LONGPW);
  await p.fill("#newpw-2", LONGPW);
  await p.click("#link-submit"); await p.waitForTimeout(1200);
  ok(`[${t}] create: the new person's email is remembered for the next sign-in`, (await p.evaluate(() => localStorage.getItem("rd.lastEmail"))) === "new.person@humancloud.co.in");
  await p.close();
}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
