// Author: kaustubh.s.v
// Browser checks for the sign-in screen, the account box and the Settings > Sign-in card (0.8.0), light and dark.
// usage: node shoot-signin.mjs <outdir> <port>   (run from a folder with the built dist files, mock.js, an index.html that loads mock.js, and fixtures.json)
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [out, port] = process.argv.slice(2);
const browser = await chromium.launch();
const errors = [], results = [];
const ok = (name, cond) => { results.push([name, !!cond]); console.log((cond ? "PASS " : "FAIL ") + name); };
async function page(q, dark) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${q}: ${e.message}`));
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}${dark ? "&theme=dark" : ""}`);
  await p.waitForTimeout(700);
  return p;
}
const shot = async (p, name) => { await p.waitForTimeout(250); await p.screenshot({ path: `${out}/${name}.png` }); };
const vis = (p, sel) => p.locator(sel).isVisible();
const txt = async (p, sel) => ((await p.locator(sel).textContent()) ?? "").trim();
const openSettings = async (p) => { await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(300); };

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  let p = await page("s=idle&view=candidate", dark);
  ok(`[${t}] off: app opens with no login screen`, !(await vis(p, "#login")) && !(await vis(p, "#account-box")));
  ok(`[${t}] off: Settings says sign-in is off`, (await txt(p, "#signin-summary")).startsWith("Sign-in is off"));
  await p.close();

  p = await page("s=idle&view=candidate&auth=out", dark);
  ok(`[${t}] out: login screen shown`, await vis(p, "#login"));
  ok(`[${t}] out: the app behind is inert`, await p.locator("main").evaluate((m) => m.hasAttribute("inert")) && await p.locator(".nav").evaluate((m) => m.hasAttribute("inert")));
  ok(`[${t}] out: email field has focus`, await p.evaluate(() => document.activeElement?.id === "login-email"));
  await shot(p, `login-out-${t}`);
  await p.click("#login-submit");
  ok(`[${t}] out: empty form says what is missing`, (await txt(p, "#login-error")) === "Enter your email address.");
  await p.fill("#login-email", "ann@humancloud.co.in");
  await p.click("#login-submit");
  ok(`[${t}] out: password missing says so`, (await txt(p, "#login-error")) === "Enter your password.");
  await p.fill("#login-password", "not the password");
  await p.press("#login-password", "Enter");
  await p.waitForTimeout(700);
  ok(`[${t}] out: wrong password is explained and the screen stays`, (await txt(p, "#login-error")) === "That email and password do not match." && (await vis(p, "#login")));
  await shot(p, `login-error-${t}`);
  await p.click("#login-show");
  ok(`[${t}] out: Show reveals the password`, (await p.locator("#login-password").getAttribute("type")) === "text" && (await p.locator("#login-show").getAttribute("aria-pressed")) === "true");
  await p.click("#login-show");
  await p.fill("#login-password", "correct horse battery");
  await p.press("#login-password", "Enter");
  await p.waitForTimeout(1500);
  ok(`[${t}] out: correct password opens the app`, !(await vis(p, "#login")) && (await p.locator("main").evaluate((m) => !m.hasAttribute("inert"))));
  ok(`[${t}] out: account box shows who is signed in`, (await txt(p, "#account-email")) === "ann@humancloud.co.in" && (await txt(p, "#account-role")) === "User");
  ok(`[${t}] out: the password is not left in the page`, !(await p.evaluate(() => document.body.innerHTML.includes("correct horse battery"))) && (await p.locator("#login-password").inputValue()) === "");
  await shot(p, `signed-in-user-${t}`);
  await p.click("#account-signout");
  await p.waitForTimeout(300);
  await shot(p, `signout-confirm-${t}`);
  await p.locator("#confirm-dialog button[value=ok]").click();
  await p.waitForTimeout(500);
  ok(`[${t}] out: Sign out brings the login screen back`, (await vis(p, "#login")) && !(await vis(p, "#account-box")));
  ok(`[${t}] out: the last email is remembered, the password is not`, (await p.locator("#login-email").inputValue()) === "ann@humancloud.co.in" && (await p.locator("#login-password").inputValue()) === "");
  await p.close();

  p = await page("s=idle&view=candidate&auth=ended", dark);
  ok(`[${t}] ended: the reason is shown on the login screen`, (await txt(p, "#login-notice")) === "Your session ended. Sign in again.");
  await shot(p, `login-ended-${t}`);
  await p.close();

  p = await page("s=idle&view=candidate&auth=in", dark);
  await openSettings(p);
  ok(`[${t}] user: Settings says who is signed in`, (await txt(p, "#signin-summary")) === "Signed in as ann@humancloud.co.in (User) at Humancloud Pvt Ltd.");
  ok(`[${t}] user: the sidebar names the company`, (await txt(p, "#account-tenant")) === "Humancloud Pvt Ltd" && await p.locator("#account-tenant").isVisible());
  ok(`[${t}] user: Settings has no connection box to change`, (await p.locator("#set-server, #save-server").count()) === 0);
  await p.locator("#card-signin").scrollIntoViewIfNeeded();
  await shot(p, `settings-user-${t}`);
  await p.close();

  p = await page("s=idle&view=candidate&auth=admin", dark);
  await openSettings(p);
  ok(`[${t}] admin: Settings has no connection box either, and the role shows`, (await p.locator("#set-server, #save-server").count()) === 0 && (await txt(p, "#account-role")) === "Admin");
  await shot(p, `settings-admin-${t}`);
  await p.close();

  p = await page("s=idle&view=candidate&auth=out", dark);
  ok(`[${t}] out: the sign-in screen has no company connection box`, (await p.locator("#login-advanced, #login-server, #login-server-save").count()) === 0 && !(await p.locator("#login").innerText()).includes("Company connection") && !(await p.locator("#login").innerText()).includes("rd1."));
  await p.close();

  p = await page("s=idle&view=candidate&auth=offline", dark);
  ok(`[${t}] offline: the sidebar says how long work can continue`, (await txt(p, "#account-offline")) === "You are offline. You can keep working for about 3 days.");
  await shot(p, `signed-in-offline-${t}`);
  await p.close();

}
console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
