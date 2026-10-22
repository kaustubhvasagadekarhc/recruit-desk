// Author: kaustubh.s.v
// Browser checks for the default theme: light, whatever the computer prefers. usage: node shoot-theme.mjs <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [out, port] = process.argv.slice(2);
const browser = await chromium.launch();
const errors = [], results = [];
const ok = (name, cond, extra = "") => { results.push([name, !!cond]); console.log((cond ? "PASS " : "FAIL ") + name + (cond ? "" : `   <- ${extra}`)); };
// os: what the computer prefers; q: the page's query (the mock's `theme` puts an OLD stored choice in place, under the old key)
async function page(os, q = "s=idle&view=candidate") {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: os, reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${q}: ${e.message}`));
  await p.goto(`http://127.0.0.1:${port}/index.html?${q}`);
  await p.waitForTimeout(700);
  return p;
}
const info = (p) => p.evaluate(() => ({ attr: document.documentElement.dataset.theme ?? null, bg: getComputedStyle(document.body).backgroundColor, checked: [...document.querySelectorAll("input[name=theme]")].find((r) => r.checked)?.value ?? null, saved: localStorage.getItem("theme-v2") }));
const lum = (rgb) => { const m = rgb.match(/\d+/g).map(Number); return (m[0] * 299 + m[1] * 587 + m[2] * 114) / 1000; };
const openSettings = async (p) => { await p.locator(".tab[data-view=settings]").click(); await p.waitForTimeout(300); };

const html = await (await fetch(`http://127.0.0.1:${port}/index.html`)).text();
ok("the page itself starts light, before any script runs", /<html[^>]*data-theme="light"/.test(html));

for (const os of ["dark", "light"]) {
  let p = await page(os);
  let i = await info(p);
  ok(`[computer prefers ${os}] first start: the app is light`, i.attr === "light" && lum(i.bg) > 200 && i.checked === "light", JSON.stringify(i));
  await openSettings(p);
  await p.screenshot({ path: `${out}/theme-default-${os}.png` });
  await p.close();
}

let p = await page("dark", "s=idle&view=candidate&theme=system");
let i = await info(p);
ok("an earlier 'match my computer' (which was only the default) becomes light", i.attr === "light" && lum(i.bg) > 200 && i.checked === "light", JSON.stringify(i));
await p.close();

p = await page("dark", "s=idle&view=candidate&theme=dark");
i = await info(p);
ok("an earlier 'dark' was chosen on purpose and is kept", i.attr === "dark" && lum(i.bg) < 100 && i.checked === "dark", JSON.stringify(i));
await p.close();

p = await page("light", "s=idle&view=candidate&theme=light");
i = await info(p);
ok("an earlier 'light' stays light", i.attr === "light" && lum(i.bg) > 200, JSON.stringify(i));
await p.close();

p = await page("dark");
await openSettings(p);
await p.locator("input[name=theme][value=dark]").check();
await p.waitForTimeout(300);
i = await info(p);
ok("choosing Dark switches at once and is remembered under the new key", i.attr === "dark" && lum(i.bg) < 100 && i.saved === "dark", JSON.stringify(i));
await p.locator("input[name=theme][value=system]").check();
await p.waitForTimeout(300);
i = await info(p);
ok("choosing 'Match my computer' on a dark computer is dark", i.attr === null && lum(i.bg) < 100 && i.saved === "system", JSON.stringify(i));
await p.locator("input[name=theme][value=light]").check();
await p.waitForTimeout(300);
i = await info(p);
ok("choosing Light is light even on a dark computer", i.attr === "light" && lum(i.bg) > 200 && i.saved === "light", JSON.stringify(i));
await p.close();

console.log(errors.length ? "PAGE ERRORS:\n" + errors.join("\n") : "no page errors");
console.log(`${results.filter(([, o]) => o).length}/${results.length} checks passed`);
await browser.close();
process.exit(errors.length || results.some(([, o]) => !o) ? 1 : 0);
