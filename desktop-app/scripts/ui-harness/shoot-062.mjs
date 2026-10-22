// Author: kaustubh.s.v
// Screenshots of every view in light and dark for 0.6.2, plus a defect scan (clipped text, side stripes,
// overflow). usage: node shoot-062.mjs <prefix> <outdir> <port>
import { createRequire } from "node:module";
const require = createRequire("/home/linuxbrew/.linuxbrew/lib/node_modules/@playwright/test/");
const { chromium } = require("playwright-core");
const [prefix, out, port] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}/index.html`;
const browser = await chromium.launch();
const defects = [];

// Lines in the form account-agent 0.6.2 prints (progress.py), so the step list and the result card are exercised.
const ACC = [
  "Plan · freshworks · 8 steps", "▶ Step 1/8 · Verifying the company and its website",
  "✓ Step 1/8 · freshworks.com confirmed from site title and JSON-LD Organization",
  "▶ Step 2/8 · Company facts (site, Wikidata, SEC EDGAR, GitHub, news)",
  "… size: 4,500 full-time employees per 10-K filed 2026-02-26",
];
const ACC_REST = [
  "✓ Step 2/8 · 14 facts from 9 sources, 6 Exa searches, not found: none",
  "▶ Step 3/8 · Open engineering roles (company-agent)", "✓ Step 3/8 · 21 open engineering roles of 64 open",
  "▶ Step 4/8 · Leadership and India Connection", "✓ Step 4/8 · 3 leaders, India Connection: Yes; contacts: 6 people, 0 emails, 1 phone, 9 channels",
  "▶ Step 5/8 · HumanCloud match (team.csv)",
  "✓ Step 5/8 · team.csv has no consented rows: Warm Connection and Best Person left blank ('add team.csv')",
  "▶ Step 6/8 · Conversation starter", "✓ Step 6/8 · starter written, citing [3, 9]",
  "▶ Step 7/8 · Validating and writing Excel", "✓ Step 7/8 · sheet 'Accounts': 1 inserted, 0 updated (Freshworks); 5 companies in sheet",
  "▶ Step 8/8 · Reporting", "✓ Step 8/8 · 17 fields filled, 2 not found",
];
const CAND = ["Plan · senior-react-frontend-pune · 6 steps", "▶ Step 1/6 · Parsing the JD",
  "✓ Step 1/6 · JD 'senior-react-frontend-pune' registered · sheet 'Senior React Frontend Developer' · 3 mandatory, 6 preferred skills",
  "▶ Step 2/6 · Discovering candidates (Exa search)"];
const COMP = ["Plan · nordstrom · 6 steps", "▶ Step 1/6 · Recalling and verifying the company",
  "✓ Step 1/6 · nordstrom.com confirmed from site", "▶ Step 2/6 · Crawling the site and reading ATS feeds", "… crawled 20/150 pages, 4 job pages so far"];

async function page(q, { w = 1280, h = 800, dark = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: dark ? "dark" : "light", reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => defects.push(`PAGEERROR ${q}: ${e.message}`));
  await p.goto(`${base}?${q}${dark ? "&theme=dark" : ""}`);
  await p.waitForTimeout(500);
  return p;
}

// Visible elements whose text is cut without an ellipsis, wide side borders (stripe accents) and page overflow.
async function scan(p, name) {
  const found = await p.evaluate(() => {
    const out = [];
    const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    for (const e of document.querySelectorAll("body *")) {
      if (!vis(e) || e.closest("table.sheet tbody") || e.closest(".raw")) continue;
      const s = getComputedStyle(e);
      for (const side of ["Left", "Right"]) {
        const w = parseFloat(s[`border${side}Width`]);
        const other = parseFloat(s.borderTopWidth);
        if (w >= 3 && w > other + 1 && s[`border${side}Style`] !== "none") out.push(`stripe: ${side.toLowerCase()} border ${w}px on ${e.tagName.toLowerCase()}.${e.className}`);
      }
      for (const pseudo of ["::before", "::after"]) {
        const ps = getComputedStyle(e, pseudo);
        if (ps.content && ps.content !== "none" && ps.position === "absolute" && parseFloat(ps.width) <= 4 && parseFloat(ps.height) >= 16 && ps.backgroundColor !== "rgba(0, 0, 0, 0)")
          out.push(`stripe: ${pseudo} bar on ${e.tagName.toLowerCase()}.${e.className}`);
      }
      const leaf = [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (leaf && s.overflow !== "visible" && s.textOverflow !== "ellipsis" && e.scrollWidth > e.clientWidth + 2 && !["INPUT", "TEXTAREA", "SELECT"].includes(e.tagName))
        out.push(`clipped: ${e.tagName.toLowerCase()}.${e.className} "${e.textContent.trim().slice(0, 50)}"`);
    }
    if (document.documentElement.scrollWidth > innerWidth + 1) out.push(`page scrolls sideways: ${document.documentElement.scrollWidth} > ${innerWidth}`);
    return out;
  });
  for (const f of new Set(found)) defects.push(`${name}: ${f}`);
}
const shot = async (p, name) => { await p.waitForTimeout(250); await scan(p, name); await p.screenshot({ path: `${out}/${prefix}-${name}.png` }); console.log("shot", name); };
const lines = (p, agent, arr) => p.evaluate(([a, l]) => l.forEach((t) => window.__mock.line(a, t)), [agent, arr]);
const nav = async (p, view) => { await p.click(`[data-view=${view}]`); await p.waitForTimeout(150); };

for (const dark of [false, true]) {
  const t = dark ? "dark" : "light";
  let p = await page("s=setup-bad", { dark }); await shot(p, `${t}-01-setup`); await p.close();

  p = await page("s=idle&view=candidate", { dark }); await shot(p, `${t}-02-find-idle`);
  await p.fill("#jd-input", "Senior React Frontend Developer, Pune, 5+ years. Must have React, TypeScript, Redux.");
  await p.click("#view-candidate .run-btn"); await p.waitForTimeout(150); await lines(p, "candidate", CAND);
  await shot(p, `${t}-03-find-running`); await p.close();

  p = await page("s=idle&view=company&sheet=Razorpay", { dark }); await shot(p, `${t}-04-company-idle`);
  await p.fill("#company-input", "Nordstrom"); await p.fill("#domain-input", "nordstrom.com");
  await p.click("#view-company .run-btn"); await p.waitForTimeout(150); await lines(p, "company", COMP);
  await shot(p, `${t}-05-company-running`); await p.close();

  p = await page("s=idle&view=account&sheet=Accounts", { dark }); await shot(p, `${t}-06-accounts-rows`);
  await p.click("#view-account tbody tr.row"); await p.waitForTimeout(400); await shot(p, `${t}-07-accounts-row-detail`);
  const nContacts = await p.evaluate(() => { const b = document.querySelector("#row-fields .contacts-block"); b?.scrollIntoView({ block: "start" }); return document.querySelectorAll("#row-fields .contact-card").length; });
  if (!nContacts) defects.push(`${t}: the account details show no contacts`);
  await p.waitForTimeout(150); await shot(p, `${t}-07b-accounts-contacts`);
  await p.keyboard.press("Escape"); await p.waitForTimeout(100);
  await p.fill("#account-input", "Freshworks"); await p.fill("#account-domain-input", "freshworks.com");
  await p.check("#account-contacts-input");
  await p.click("#view-account .run-btn"); await p.waitForTimeout(150);
  const sent = await p.evaluate(() => window.__mock.lastInput);
  if (!sent || sent.contacts !== true) defects.push(`${t}: the contacts opt-in was not sent (${JSON.stringify(sent)})`);
  if (!sent || sent.company !== "Freshworks") defects.push(`${t}: Accounts search did not send the typed company (${JSON.stringify(sent)})`);
  await lines(p, "account", ACC); await shot(p, `${t}-08-accounts-running`);
  await lines(p, "account", ACC_REST); await p.evaluate(() => window.__mock.end("account", "done", 98000)); await p.waitForTimeout(300);
  await shot(p, `${t}-09-accounts-finished`); await p.close();

  p = await page("s=idle&view=account", { dark });
  await p.fill("#account-input", "Acme"); await p.click("#view-account .run-btn"); await p.waitForTimeout(150);
  await lines(p, "account", ACC.slice(0, 3));
  await lines(p, "account", ["✗ Step 2/8 · Wikidata, Wikipedia and the company site could not be reached (network error)"]);
  await p.evaluate(() => { window.__mock.line("account", "Permission denied for Bash(curl https://x)", "warn"); window.__mock.end("account", "error"); });
  await p.waitForTimeout(300); await shot(p, `${t}-10-accounts-error`); await p.close();

  p = await page("s=locked&view=account", { dark }); await shot(p, `${t}-11-accounts-locked`); await p.close();
  p = await page("s=idle", { dark }); await nav(p, "settings"); await shot(p, `${t}-12-settings`); await p.close();
  p = await page("s=empty&view=account", { dark }); await shot(p, `${t}-13-accounts-empty`); await p.close();
  p = await page("s=idle&view=account&sheet=Accounts", { dark, w: 1024, h: 640 }); await shot(p, `${t}-14-accounts-narrow`); await p.close();
}
console.log(defects.length ? "DEFECTS\n" + [...new Set(defects)].join("\n") : "DEFECTS none");
await browser.close();
