// Author: kaustubh.s.v
// Pure helpers for the typed columns the assistants write (round 1, 2026-10-16): which sheets are lists,
// the candidate score breakdown, the Evidence sheet rows behind a table row, and the table filters.
// No DOM here, so scripts/test-plain.mjs can test it in Node.

export type Agent = "candidate" | "company" | "account";

/** Sheets an assistant writes next to its list sheets. They feed the details dialog and are never offered as lists. */
export const SIDE_SHEETS = ["Evidence", "Contacts", "Company channels", "Candidates", "Matches", "Companies", "Jobs"];

/** "All lists together" views: the entity sheets the assistants export (one row per person, job or company). A view is
 * offered after the list sheets when the workbook has it, and shows its own short columns: [header, label on screen]. */
export interface ViewDef { sheet: string; label: string; columns: [string, string][] }
export const VIEWS: Record<Agent, ViewDef[]> = {
  candidate: [
    { sheet: "Candidates", label: "All candidates", columns: [["Candidate Name", "Name"], ["Title", "Title"], ["Company", "Company"], ["Location", "Location"],
      ["Experience Years", "Experience"], ["JDs Matched", "JDs"], ["Best Match", "Best match"], ["Best Match JD", "Best for"], ["Profile URL", "Profile"]] },
    { sheet: "Matches", label: "All matches", columns: [["Candidate Name", "Name"], ["JD Title", "Job description"], ["Match Score (0-100)", "Match"],
      ["Evidence Confidence", "Confidence"], ["Open to Work", "Open to work"], ["Recruiter Status", "Status"], ["Date Found", "Found"]] },
  ],
  company: [
    { sheet: "Jobs", label: "All jobs", columns: [["Job Title", "Job title"], ["Company Name", "Company"], ["Work Mode", "Work mode"], ["City", "City"],
      ["Country", "Country"], ["Function", "Function"], ["Seniority", "Seniority"], ["Status", "Status"], ["Posted Date", "Posted"], ["Job / Post URL", "Link"]] },
    { sheet: "Companies", label: "All companies", columns: [["Company Name", "Company"], ["ATS", "ATS"], ["Jobs", "Jobs"], ["Open", "Open"], ["Closed", "Closed"],
      ["Last Scanned", "Last scanned"], ["Career Page", "Careers page"]] },
  ],
  account: [
    { sheet: "Companies", label: "All companies (every list)", columns: [["Company", "Company"], ["Lists", "Lists"], ["Product Type", "Type"], ["Employees", "Employees"],
      ["Last Round", "Last round"], ["Total Raised USD", "Total raised"], ["HQ Country", "HQ"], ["People", "People"], ["Freshness", "Freshness"], ["Key Field Confidence", "Key fields"]] },
  ],
};

/** The views this workbook actually has, in the order above. */
export function viewsIn(agent: Agent, sheets: string[]): ViewDef[] {
  return VIEWS[agent].filter((v) => sheets.includes(v.sheet));
}

/** The sheets to offer in the picker, in workbook order. */
export function listSheets(sheets: string[]): string[] {
  return sheets.filter((s) => !SIDE_SHEETS.includes(s));
}

const norm = (s: string) => s.trim().toLowerCase();
export const col = (headers: string[], name: string) => headers.findIndex((h) => norm(h ?? "") === norm(name));
export const cell = (headers: string[], row: string[], name: string) => {
  const i = col(headers, name);
  return i < 0 ? "" : (row[i] ?? "").trim();
};

// ---------- candidate score breakdown ----------

/** The 8 score columns, in the order the score is built; together they add up to Match Score. */
export const SCORE_PARTS: [string, string][] = [
  ["Core Skills pts", "Core skills"], ["Experience pts", "Experience"], ["Domain pts", "Domain and projects"],
  ["Seniority pts", "Seniority"], ["Location pts", "Location"], ["Preferred pts", "Preferred skills"],
  ["OTW pts", "Open to work"], ["Cap Adjustment pts", "Cap adjustment"],
];

export interface ScorePart { header: string; label: string; pts: number }
export interface Breakdown { parts: ScorePart[]; positive: number; cap: number; total: number; score: number | null; adds: boolean }

/** The score breakdown of a candidate row, or null when the sheet has no pts columns or the row has none filled. */
export function scoreBreakdown(headers: string[], row: string[]): Breakdown | null {
  if (col(headers, SCORE_PARTS[0][0]) < 0) return null;
  const parts: ScorePart[] = [];
  for (const [h, label] of SCORE_PARTS) {
    const t = cell(headers, row, h);
    if (t === "") continue;
    const pts = Number(t);
    if (Number.isFinite(pts)) parts.push({ header: h, label, pts });
  }
  if (!parts.length) return null;
  const positive = parts.filter((p) => p.pts > 0).reduce((a, p) => a + p.pts, 0);
  const cap = parts.filter((p) => p.pts < 0).reduce((a, p) => a + p.pts, 0);
  const total = Math.round((positive + cap) * 10) / 10;
  const st = cell(headers, row, "Match Score (0-100)");
  const score = st !== "" && Number.isFinite(Number(st)) ? Number(st) : null;
  return { parts, positive, cap, total, score, adds: score === null || Math.abs(score - total) < 0.51 };
}

// ---------- evidence ----------

/** The candidate agent's normalise_url (scripts/common.py): https, lower-case host, no www, no query or trailing slash. */
export function normaliseUrl(url: string): string {
  let u = (url ?? "").trim();
  if (!u) return "";
  if (!u.includes("://")) u = "https://" + u;
  let p: URL;
  try { p = new URL(u); } catch { return ""; }
  let host = p.hostname.toLowerCase();
  let path = decodeURI(p.pathname);
  if (host.endsWith("linkedin.com")) { host = "www.linkedin.com"; path = path.toLowerCase(); }
  else if (host.startsWith("www.")) host = host.slice(4);
  path = path.replace(/\/+/g, "/").replace(/\/+$/, "");
  return `https://${host}${path}`;
}

const normText = (s: string) => (s ?? "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** The candidate agent's row key (common.row_key): url:<profile> or nc:<name>|<company>. */
export function candidateKey(profileUrl: string, name: string, roleCompany = ""): string {
  const u = normaliseUrl(profileUrl);
  if (u) return "url:" + u;
  const rc = roleCompany ?? "";
  const company = rc.includes("@") || rc.includes(" at ") ? rc.split("@").pop()!.split(" at ").pop()! : rc;
  return "nc:" + normText(name) + "|" + normText(company);
}

/** Whole days from a YYYY-MM-DD date to `now`; null when the text is not a date. */
export function ageDays(date: string, now: Date = new Date()): number | null {
  const m = (date ?? "").trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  const then = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.max(0, Math.floor((today - then) / 86400000));
}

export function ageText(days: number | null): string {
  if (days === null) return "date unknown";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 60) return `${days} days ago`;
  const months = Math.round(days / 30.4);
  return months < 24 ? `${months} months ago` : `${Math.round(days / 365)} years ago`;
}

export interface EvidenceItem {
  field: string; fact: string; quote: string; url: string; type: string; checked: string; age: number | null; confidence: string;
}

/**
 * Evidence for one table row. Accounts and candidates read the Evidence sheet; company jobs have no Evidence
 * sheet, so their one item is the listing itself (its URL, where it was found and when it was last checked).
 * The age is recomputed from Checked On (accounts) or Fetched On (candidates), never read from Age (days).
 */
export function evidenceFor(agent: Agent, headers: string[], row: string[], ev: { headers: string[]; rows: string[][] } | null, now: Date = new Date()): EvidenceItem[] {
  if (agent === "company") {
    const url = cell(headers, row, "Job / Post URL") || cell(headers, row, "Application URL");
    if (!url) return [];
    const checked = cell(headers, row, "Last Checked") || cell(headers, row, "Date Discovered");
    return [{ field: "Job listing", fact: `Listed as “${cell(headers, row, "Job Title")}”`, quote: "", url, type: cell(headers, row, "Source Type"), checked, age: ageDays(checked, now), confidence: "" }];
  }
  if (!ev || !ev.headers.length) return [];
  const H = ev.headers;
  let rows: string[][];
  if (agent === "account") {
    const dom = cell(headers, row, "Domain").toLowerCase();
    if (!dom) return [];
    rows = ev.rows.filter((r) => cell(H, r, "Domain").toLowerCase() === dom);
  } else {
    const key = cell(headers, row, "Candidate Key") || candidateKey(cell(headers, row, "Profile URL"), cell(headers, row, "Candidate Name"), cell(headers, row, "Current Role / Company"));
    rows = ev.rows.filter((r) => cell(H, r, "Candidate Key") === key);
    if (!rows.length) {
      // Older rows may carry a key built before the URL was known; fall back to the exact name.
      const name = norm(cell(headers, row, "Candidate Name"));
      rows = name ? ev.rows.filter((r) => norm(cell(H, r, "Candidate Name")) === name && cell(H, r, "Candidate Key").startsWith("nc:")) : [];
    }
  }
  const dateCol = agent === "account" ? "Checked On" : "Fetched On";
  return rows.map((r) => {
    const checked = cell(H, r, dateCol);
    return {
      field: cell(H, r, "Field"), fact: cell(H, r, "Fact"), quote: cell(H, r, "Quote"), url: cell(H, r, "Source URL"),
      type: cell(H, r, "Source Type"), checked, age: ageDays(checked, now), confidence: cell(H, r, "Confidence"),
    };
  });
}

/** "[1] https://…\n[2] https://…" (the account Sources cell) as a number → URL map. */
export function sourceMap(text: string): Map<string, string> {
  const m = new Map<string, string>();
  for (const x of (text ?? "").matchAll(/\[(\d+)\]\s*(https?:\/\/\S+)/g)) m.set(x[1], x[2]);
  return m;
}

// ---------- table filters ----------

/** "min" filters keep rows at or above a number; "enum" filters keep rows equal to one value. */
export interface FilterDef { header: string; label: string; kind: "enum" | "min"; steps?: number[] }

export const FILTERS: Record<Agent, FilterDef[]> = {
  candidate: [
    { header: "Work Mode", label: "Work mode", kind: "enum" },
    { header: "Experience Years", label: "Experience", kind: "min", steps: [1, 3, 5, 8, 10] },
    { header: "Match Score (0-100)", label: "Match", kind: "min", steps: [50, 70, 85] },
  ],
  company: [
    { header: "Work Mode", label: "Work mode", kind: "enum" },
    { header: "Country", label: "Country", kind: "enum" },
    { header: "City", label: "City", kind: "enum" },
    { header: "Function", label: "Function", kind: "enum" },
    { header: "Seniority", label: "Seniority", kind: "enum" },
  ],
  account: [
    { header: "Public/Private", label: "Listed", kind: "enum" },
    { header: "Last Round", label: "Last round", kind: "enum" },
    { header: "HQ Country", label: "HQ country", kind: "enum" },
    { header: "Employees", label: "Employees", kind: "min", steps: [50, 200, 1000, 5000, 10000] },
    { header: "Freshness", label: "Freshness", kind: "enum" },
    { header: "Key Field Confidence", label: "Key field confidence", kind: "enum" },
    { header: "Evidence Count", label: "Evidence", kind: "min", steps: [1, 5, 10, 20] },
  ],
};

/** Fixed orders for enums, so the picker reads in a sensible order instead of alphabetically. */
const ORDER: Record<string, string[]> = {
  "work mode": ["Onsite", "Hybrid", "Remote", "Unknown"],
  seniority: ["Intern", "Entry", "Mid", "Senior", "Staff", "Lead", "Manager", "Director", "VP", "Exec"],
  freshness: ["Fresh", "Ageing", "Stale"],
  "key field confidence": ["High", "Medium", "Low"],
  "public/private": ["Public", "Private"],
  "last round": ["Pre-Seed", "Seed", ..."ABCDEFGHIJK".split("").map((x) => `Series ${x}`), "IPO"],
};

/** Distinct values of an enum column. A cell holding "India; Malaysia" counts for both. */
export function enumValues(headers: string[], rows: string[][], header: string): string[] {
  const i = col(headers, header);
  if (i < 0) return [];
  const seen = new Set<string>();
  for (const r of rows) for (const v of (r[i] ?? "").split(";")) if (v.trim()) seen.add(v.trim());
  const fixed = ORDER[norm(header)];
  return [...seen].sort((a, b) => {
    const x = fixed ? fixed.indexOf(a) : -1, y = fixed ? fixed.indexOf(b) : -1;
    if (x >= 0 || y >= 0) return (x < 0 ? 99 : x) - (y < 0 ? 99 : y);
    return a.localeCompare(b);
  });
}

/** Whether a row passes every active filter (header → value; "" means any). */
export function passesFilters(headers: string[], row: string[], active: Record<string, string>, defs: FilterDef[]): boolean {
  for (const d of defs) {
    const want = active[d.header];
    if (!want) continue;
    const v = cell(headers, row, d.header);
    if (d.kind === "min") {
      const n = numberOf(v);
      if (v === "" || !Number.isFinite(n) || n < Number(want)) return false;
    } else if (!v.split(";").map((x) => x.trim()).includes(want)) return false;
  }
  return true;
}

/** Salary cell for the short table: "INR 1,200,000–1,800,000", "from USD 90,000", or "" when nothing is published. */
export function salaryText(headers: string[], row: string[]): string {
  const lo = cell(headers, row, "Salary Min"), hi = cell(headers, row, "Salary Max"), cur = cell(headers, row, "Currency");
  const f = (s: string) => (Number.isFinite(Number(s)) && s !== "" ? Number(s).toLocaleString("en-US") : s);
  if (!lo && !hi) return "";
  const pre = cur ? `${cur} ` : "";
  if (lo && hi && lo !== hi) return `${pre}${f(lo)}–${f(hi)}`;
  if (lo && hi) return `${pre}${f(lo)}`;
  return lo ? `from ${pre}${f(lo)}` : `up to ${pre}${f(hi)}`;
}

// ---------- numbers (round 2, 2026-10-16) ----------

/** A number from a cell: "375000000", "1,536", "$375M", "1.03B", "18k". NaN for anything else (blank, prose). */
export function numberOf(s: string): number {
  const m = (s ?? "").trim().match(/^\$?\s*(-?\d[\d,]*(?:\.\d+)?)\s*([kmb])?$/i);
  if (!m) return NaN;
  const n = Number(m[1].replace(/,/g, ""));
  const mult = { k: 1e3, m: 1e6, b: 1e9 }[(m[2] ?? "").toLowerCase() as "k" | "m" | "b"] ?? 1;
  return n * mult;
}

/** Up to `d` decimals, trailing zeros dropped: 1.50 → "1.5", 375.0 → "375". */
const trim = (n: number, d: number) => String(Number(n.toFixed(d)));

/** A USD amount for the table: 375000000 → "$375M", 1030000000 → "$1.03B", 2500000 → "$2.5M", 45000 → "$45K". Non-numbers pass through. */
export function usdShort(s: string): string {
  const t = (s ?? "").trim();
  const n = Number(t);
  if (t === "" || !Number.isFinite(n)) return t;
  const a = Math.abs(n), sign = n < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}$${trim(a / 1e9, 2)}B`;
  if (a >= 1e6) return `${sign}$${trim(a / 1e6, a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e3) return `${sign}$${trim(a / 1e3, 0)}K`;
  return `${sign}$${trim(a, 0)}`;
}

/** A whole count with thousands separators: "18600" → "18,600". Non-numbers pass through. */
export function intText(s: string): string {
  const t = (s ?? "").trim();
  const n = Number(t);
  return t !== "" && Number.isFinite(n) ? n.toLocaleString("en-US") : t;
}

/**
 * Sort order of two cells: numbers by value (also "1,536" and "$375M"), then text in natural order; blanks always
 * last, whichever way the column is sorted. A number and a text cell (a "Not found (checked: …)" note) put the
 * number first.
 */
export function compareCells(x: string, y: string, dir: 1 | -1): number {
  const a = (x ?? "").trim(), b = (y ?? "").trim();
  if (a === "" || b === "") return a === b ? 0 : a === "" ? 1 : -1;
  const na = numberOf(a), nb = numberOf(b);
  const fa = Number.isFinite(na), fb = Number.isFinite(nb);
  if (fa && fb) return (na - nb) * dir;
  if (fa !== fb) return fa ? -1 : 1;
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }) * dir;
}

// ---------- account key facts and H-1B ----------

/** One line of the Key facts card. `fields` are the typed columns behind it, in the order their Evidence is looked up. */
export interface Fact { label: string; value: string; fields: string[] }

/** The typed company columns as Key facts lines; a line is left out when its main column is blank. */
export function keyFacts(headers: string[], row: string[]): Fact[] {
  const c = (h: string) => cell(headers, row, h);
  const out: Fact[] = [];
  const add = (label: string, value: string, fields: string[]) => { if (value) out.push({ label, value, fields }); };
  add("Founded", c("Founded Year"), ["Founded Year"]);
  if (c("Employees")) add("Employees", intText(c("Employees")) + (c("Employees As Of") ? ` (as of ${c("Employees As Of")})` : ""), ["Employees", "Employees As Of"]);
  if (c("Last Round")) add("Last round", [c("Last Round"), usdShort(c("Last Round Amount USD")), c("Last Round Date")].filter(Boolean).join(" · "),
    ["Last Round", "Last Round Amount USD", "Last Round Date"]);
  add("Total raised", usdShort(c("Total Raised USD")), ["Total Raised USD"]);
  add("HQ", [c("HQ City"), c("HQ Country")].filter(Boolean).join(", "), ["HQ City", "HQ Country"]);
  add("India office", c("India Office City"), ["India Office City"]);
  add("Listed", c("Public/Private"), ["Public/Private"]);
  add("Ticker", c("Ticker"), ["Ticker"]);
  return out;
}

/** The columns the Key facts and H-1B cards show; the details dialog does not list them again. */
export const CARD_COLUMNS = ["Founded Year", "Employees", "Employees As Of", "Last Round", "Last Round Amount USD", "Last Round Date",
  "Total Raised USD", "HQ City", "HQ Country", "India Office City", "Public/Private", "Ticker", "H-1B Filings (4Q)", "H-1B Top Roles"];

export interface H1b { count: number | null; roles: { title: string; n: number | null }[]; window: string }

/** "Senior Engineer (9); Software Engineer (6)" → titles with counts. */
export function h1bRoles(text: string): { title: string; n: number | null }[] {
  return (text ?? "").split(";").map((x) => x.trim()).filter(Boolean).map((x) => {
    const m = x.match(/^(.*?)\s*\((\d+)\)$/);
    return m ? { title: m[1], n: Number(m[2]) } : { title: x, n: null };
  });
}

/** The H-1B card of an account row, or null when the sheet has no H-1B column. The window is read from the cited Evidence fact. */
export function h1bFor(headers: string[], row: string[], ev: EvidenceItem[] = []): H1b | null {
  if (col(headers, "H-1B Filings (4Q)") < 0) return null;
  const t = cell(headers, row, "H-1B Filings (4Q)");
  const n = t === "" ? NaN : Number(t);
  const fact = ev.find((e) => e.field === "H-1B Filings (4Q)")?.fact ?? "";
  const w = fact.match(/(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})/);
  return { count: Number.isFinite(n) ? n : null, roles: h1bRoles(cell(headers, row, "H-1B Top Roles")), window: w ? `${w[1]} to ${w[2]}` : "" };
}

/** A list cell split on commas or semicolons outside brackets: "reactjs (score 120, 45 answers), node.js (…)" → two items. */
export function splitList(text: string): string[] {
  const out: string[] = [];
  let depth = 0, cur = "";
  for (const ch of text ?? "") {
    if (ch === "(" || ch === "[") depth++;
    else if ((ch === ")" || ch === "]") && depth > 0) depth--;
    if ((ch === "," || ch === ";") && depth === 0) { if (cur.trim()) out.push(cur.trim()); cur = ""; }
    else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/** Candidate developer-profile columns, shown under Evidence instead of in the field list. */
export const DEV_COLUMNS: [string, string][] = [["GitHub Languages", "GitHub languages"], ["GitHub Last Active", "GitHub last active"], ["SO Top Tags", "Stack Overflow top tags"]];
