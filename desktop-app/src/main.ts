// Author: kaustubh.s.v
// Recruit Desk front end. Plain TypeScript, no framework; talks to the Rust side
// through window.__TAURI__ (withGlobalTauri is on in tauri.conf.json).

import { cleanLine, expiresText, formatUtc, inviteResultText, isSignInRequired, LINK_TEXT, looksLikeCode, loginNotice, recordsLine, recordSummary, AGENT_LABEL, allowanceInput, allowanceIntro, assistantName, historyIntro, historyLine, offlineText, removeSearchText, resultsText, searchSummary, sharedNote, statusLabel, windowText, passwordProblem, plainResult, roleLabel, signInSummary, userActions, validEmail, type AdminInvite, type AdminUser, type AgentKey, type AuthStatusLike, type InviteResult, type HistoryResult, type HistoryRow, type HistorySearch, type LinkField, type LinkMode, type RecordsResult, type StoredRecord, type UserAction } from "./plain.js";
import { CARD_COLUMNS, DEV_COLUMNS, FILTERS, SIDE_SHEETS, VIEWS, ageText, cell, compareCells, enumValues, evidenceFor, h1bFor, intText, keyFacts, listSheets, passesFilters, viewsIn, salaryText, scoreBreakdown, sourceMap, splitList, usdShort, type EvidenceItem } from "./data.js";

interface Status {
  agent: AgentKey; counted: number; cache_read: number; cap: number; percent: number;
  locked: boolean; resets_at: number | null; locked_at: number | null; unlock_at: number | null;
  clock_backwards: boolean; running: boolean; shared: boolean; window_minutes: number | null;
}
interface SheetData { sheets: string[]; sheet: string | null; headers: string[]; rows: string[][] }
interface Workbook { path: string; exists: boolean; local?: boolean; source?: string; modified_ms: number | null; data: SheetData }
interface Check { name: string; ok: boolean; detail: string; fix: string }
interface Settings { agents_root: string; data_root?: string; claude_path: string; caps: Record<AgentKey, number> }
interface SettingsInfo {
  settings: Settings; version: string; detected_root: string | null; detected_claude: string | null;
  data_dir: string; platform: string;
}

declare global {
  interface Window {
    __TAURI__: {
      core: { invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> };
      event: { listen<T>(name: string, cb: (e: { payload: T }) => void): Promise<() => void> };
    };
  }
}

const invoke = <T,>(cmd: string, args?: Record<string, unknown>) => window.__TAURI__.core.invoke<T>(cmd, args);
const listen = <T,>(name: string, cb: (p: T) => void) => window.__TAURI__.event.listen<T>(name, (e) => cb(e.payload));
const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector(sel) as T;
const fmt = (n: number) => n.toLocaleString("en-US");
let platform = "windows";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}
function button(text: string, cls = "", onClick?: () => void): HTMLButtonElement {
  const b = el("button", { type: "button" }, text);
  if (cls) b.className = cls;
  if (onClick) b.onclick = onClick;
  return b;
}
const PUBLIC_POSTS_KEY = "rd.company.publicPosts";
const CONTACTS_KEY = "rd.account.contacts";
/** Sheets the account agent writes next to its list sheets; shown inside a company's details, not as lists. */
const CONTACT_SHEETS = ["Contacts", "Company channels"];
const EVIDENCE_SHEET = SIDE_SHEETS[0];
const store = {
  get(k: string): string | null { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

// ---------- time and wording helpers ----------

const timeOf = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
function dayTime(ms: number): string {
  const d = new Date(ms), now = new Date();
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  if (d.toDateString() === now.toDateString()) return timeOf(ms);
  if (d.toDateString() === tomorrow.toDateString()) return `${timeOf(ms)} tomorrow`;
  return d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
function lastUpdated(ms: number): string {
  const d = new Date(ms), now = new Date();
  if (d.toDateString() === now.toDateString()) return `Updated ${timeOf(ms)}`;
  return `Updated ${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${timeOf(ms)}`;
}
function inHM(ms: number): string {
  const m = Math.max(0, Math.ceil(ms / 60000));
  if (m >= 60) return `${Math.floor(m / 60)} h ${m % 60} min`;
  return m <= 1 ? "less than a minute" : `${m} min`;
}
function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
const clock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
function pctText(s: Status): string {
  if (s.counted > 0 && s.percent < 1) return "Under 1%";
  return `${Math.min(100, Math.floor(s.percent))}%`;
}
/** 2,610,000 → "2.6M", 210,000 → "210k". */
function short(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}
const plural = (n: number, one: string, many = one + "s") => `${fmt(n)} ${n === 1 ? one : many}`;

// ---------- toasts ----------

/** Success and info toasts hide after a few seconds (paused while hovered); errors stay until closed. */
function toast(text: string, kind: "info" | "ok" | "error" = "info") {
  const box = $("#toasts");
  const t = el("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" });
  const close = button("Close", "", () => t.remove());
  close.setAttribute("aria-label", "Close message");
  t.append(el("p", {}, text), close);
  box.append(t);
  while (box.children.length > 3) box.firstElementChild!.remove();
  if (kind === "error") return;
  let timer = window.setTimeout(() => t.remove(), 4500);
  t.addEventListener("mouseenter", () => clearTimeout(timer));
  t.addEventListener("mouseleave", () => { timer = window.setTimeout(() => t.remove(), 2500); });
}
const toastError = (e: unknown) => toast(plainError(e), "error");

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall back below */ }
  const ta = el("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.append(ta); ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok;
}

// Turn backend errors into plain words with a next step.
function plainError(e: unknown): string {
  const s = String(e).replace(/^Error:\s*/, "");
  if (isSignInRequired(e)) { void recheckAuth(); return "Your sign-in ended. Sign in again to continue."; }
  if (/claude was not found/i.test(s)) return "Claude Code was not found on this computer. Open Settings and run the setup check.";
  if (/not an agent folder/i.test(s)) return "Recruit Desk cannot find the assistant's folder. Open Settings and check the assistants folder.";
  if (/already in progress/i.test(s)) return "A search is already running. Wait for it to finish, or press Stop.";
  if (/token cap and is locked/i.test(s)) return "This assistant has used its allowance for now. Try again when the break is over.";
  if (/No terminal program/i.test(s)) return "No terminal window could be opened. Use the copy button and the steps shown instead.";
  return s;
}

/** Ask before doing something that cannot be undone. Resolves true when the person confirms. */
function confirmAction(title: string, text: string, ok: string, cancel: string): Promise<boolean> {
  const d = $<HTMLDialogElement>("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = ok;
  $("#confirm-cancel").textContent = cancel;
  d.returnValue = "";
  d.showModal();
  $("#confirm-cancel").focus();
  return new Promise((res) => d.addEventListener("close", () => res(d.returnValue === "ok"), { once: true }));
}

// ---------- agent sections ----------

type RunIn = Record<string, string | boolean>;

interface Step { label: string; state: "pending" | "active" | "done" | "failed" | "skipped"; result: string; raw: string; sub: string }

interface View {
  key: AgentKey; root: HTMLElement; status?: Status; running: boolean;
  book?: Workbook; sheetName?: string; sortCol: number; sortDir: 1 | -1; filter: string; allCols: boolean; colFilters: Record<string, string>;
  evidence?: { headers: string[]; rows: string[][] } | null;
  rows: string[][]; index: string[]; cols: number[]; active: number; range: [number, number]; fitW: number;
  steps: Step[]; warns: number; trustWarn: boolean; lastInput: RunIn; started: boolean; startedAt: number;
  lockDismissed: boolean; lockShown: boolean;
  getInput(): RunIn;
  validate(): string | null;
  focusInput(): void;
}

interface AgentText {
  title: string; lede: string; nav: string; run: string; noun: string; nouns: string; pick: string; task: string;
  search: string; steps: string[]; empty: string[]; emptyTable: string;
}

const TEXT: Record<AgentKey, AgentText> = {
  candidate: {
    title: "Find candidates for a job",
    lede: "Paste a job description. The assistant searches public profiles, scores each person against the job and saves them to your Excel file.",
    nav: "Find candidates", run: "Find candidates", noun: "candidate", nouns: "candidates", pick: "Job", task: "search",
    search: "Search candidates, e.g. React or Pune",
    steps: ["Read the job description", "Search for candidates", "Score each candidate", "Remove duplicates", "Save to the Excel file", "Wrap up"],
    empty: ["Paste the job description above, or load it from a file.", "Press Find candidates. A search usually takes a few minutes.", "Watch the progress here. The candidates appear in the table on the right."],
    emptyTable: "Paste a job description on the left and press Find candidates. Your results appear here, best matches first.",
  },
  company: {
    title: "Check a company's open jobs",
    lede: "Enter a company. The assistant reads its careers page and job boards, then saves every job it finds to your Excel file.",
    nav: "Company jobs", run: "Check jobs", noun: "job", nouns: "jobs", pick: "Company", task: "check",
    search: "Search jobs, e.g. engineer or Pune",
    steps: ["Look up the company", "Read its careers page and job boards", "Look for public hiring posts", "Remove duplicates and update records", "Save to the Excel file", "Double-check a few jobs"],
    empty: ["Type the company name. Add its website if you know it; that makes the search faster and surer.", "Press Check jobs. A check usually takes one to three minutes.", "Watch the progress here. The jobs appear in the table on the right."],
    emptyTable: "Type a company name on the left and press Check jobs. Its jobs appear here.",
  },
  account: {
    title: "Research a company as an account",
    lede: "Enter a company, or load a list. The assistant gathers public facts (what it builds, offices, funding, leaders, open engineering roles), each with its link, and saves one row per company to your Excel file.",
    nav: "Accounts", run: "Research company", noun: "company", nouns: "companies", pick: "List", task: "research",
    search: "Search companies",
    steps: ["Confirm the company and its website", "Gather company facts", "Check open engineering roles", "Find leaders and India connections",
      "Match with your team", "Write a conversation starter", "Save to the Excel file", "Wrap up"],
    empty: ["Type the company name and, if you know it, its website. Or load a CSV list of companies.", "Press Research company. One company usually takes two to five minutes.", "Watch the progress here. Each company appears as one row in the table on the right."],
    emptyTable: "Type a company name on the left and press Research company. Each company appears here as one row.",
  },
};

// Columns shown by default, in the order a recruiter reads them: [header in the file, label on screen, header to use
// instead on sheets written before the typed columns existed].
const COLUMNS: Record<AgentKey, [string, string, string?][]> = {
  company: [["Job Title", "Job title"], ["Work Mode", "Work mode"], ["City", "City", "Location / Work Mode"], ["Country", "Country"],
    ["Function", "Function"], ["Seniority", "Seniority"], ["Salary Min", "Salary"], ["Status", "Status"], ["Posted Date", "Posted"],
    ["Job / Post URL", "Link"]],
  candidate: [["Candidate Name", "Name"], ["Match Score (0-100)", "Match"], ["Title", "Title", "Current Role / Company"], ["Company", "Company"],
    ["Experience Years", "Experience"], ["Work Mode", "Work mode"], ["Location", "Location"], ["Recruiter Status", "Status"], ["Profile URL", "Profile"]],
  account: [["Company", "Company"], ["Product Type", "Type"], ["Employees", "Employees"], ["Last Round", "Last round", "Funding"],
    ["Last Round Amount USD", "Last amount"], ["Total Raised USD", "Total raised"], ["HQ Country", "HQ", "HQ / US Office"],
    ["H-1B Filings (4Q)", "H-1B (4Q)"], ["Freshness", "Freshness"], ["Key Field Confidence", "Key fields"], ["Evidence Count", "Evidence"]],
};
const LABELS: Record<string, string> = Object.fromEntries([...COLUMNS.company, ...COLUMNS.candidate, ...COLUMNS.account].map(([h, l]) => [h, l])
  .concat([["Location / Work Mode", "Location"], ["Current Role / Company", "Current role"], ["India Office", "India office"],
    ["Application URL", "Apply link"], ["Email / Mobile", "Email or mobile"], ["Evidence Confidence", "Confidence"],
    ["Source Type", "Found on"], ["Date Discovered", "First seen"], ["Key Field Confidence", "Key field confidence"], ["Evidence Count", "Evidence facts"],
    ["HQ / US Office", "HQ / US office"], ["Funding", "Funding"], ["Who to Contact", "Who to contact"], ["Confidence", "Confidence"],
    ["Last Round Amount USD", "Last round amount"], ["H-1B Filings (4Q)", "H-1B filings (last 4 quarters)"]]));
/** Names in the details dialog where the short table label would repeat another field's. */
const DETAIL_LABELS: Record<string, string> = { "Experience Years": "Experience (years)", "Experience": "Experience (as written)", "Salary Min": "Salary min", "Key Field Confidence": "Key field confidence" };
const detailLabel = (h: string) => DETAIL_LABELS[h] ?? LABELS[h] ?? h;
/** Candidate score columns: shown as the "Why this score" bar in the details, not as separate fields. */
const PTS = / pts$/i;

const views = {} as Record<AgentKey, View>;
let currentView = "candidate";

/** Companies in a CSV: non-empty lines under the header. */
function csvCompanies(text: string): number {
  return Math.max(0, text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim()).length - 1);
}

function buildView(key: AgentKey): View {
  const T = TEXT[key];
  const tpl = $<HTMLTemplateElement>("#agent-tpl");
  const root = tpl.content.firstElementChild!.cloneNode(true) as HTMLElement;
  $(`#view-${key}`).append(root);
  const titleId = `title-${key}`;
  const h1 = $(".title", root); h1.textContent = T.title; h1.id = titleId;
  root.setAttribute("aria-labelledby", titleId);
  $(".lede", root).textContent = T.lede;
  $(".run-btn", root).textContent = T.run;
  $(".pick-text", root).textContent = T.pick;
  $<HTMLInputElement>(".filter", root).placeholder = T.search;
  $(".lock-overlay", root).setAttribute("aria-labelledby", `lock-title-${key}`);
  $(".lock-title", root).id = `lock-title-${key}`;
  $(".lock-overlay", root).setAttribute("aria-describedby", `lock-text-${key}`);
  $(".lock-text", root).id = `lock-text-${key}`;
  $("table.sheet", root).setAttribute("aria-label", { company: "Jobs found", candidate: "Candidates found", account: "Companies researched" }[key]);

  // Empty state that teaches the three steps.
  const empty = $(".progress-empty", root);
  empty.append(el("h2", {}, "How it works"));
  const ol = el("ol");
  T.empty.forEach((t) => ol.append(el("li", {}, t)));
  empty.append(ol);

  const inputs = $(".inputs", root);
  let getInput: () => RunIn;
  let validate: () => string | null;
  let first: HTMLElement;
  const submitOnCtrlEnter = (e: KeyboardEvent) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $<HTMLFormElement>(".run-form", root).requestSubmit(); } };
  if (key === "candidate") {
    const id = "jd-input";
    const lab = el("label", { for: id }, "Job description");
    const ta = el("textarea", {
      id, spellcheck: "true",
      placeholder: "For example:\nSenior React Developer, Pune, 5+ years.\nMust have: React, TypeScript, Redux.\nNice to have: Next.js, testing.",
    });
    lab.append(ta);
    const file = el("input", { type: "file", accept: ".txt,.md,.text", class: "hidden", tabindex: "-1", "aria-hidden": "true" });
    const fname = el("span", { class: "muted" });
    const pick = button("Load from a file…", "ghost", () => file.click());
    file.onchange = async () => {
      const f = file.files?.[0];
      if (!f) return;
      ta.value = await f.text();
      fname.textContent = `Loaded ${f.name}`;
    };
    const row = el("div", { class: "file-row" });
    row.append(pick, fname, file);
    inputs.append(lab, row);
    ta.addEventListener("keydown", submitOnCtrlEnter);
    getInput = () => ({ jd: ta.value });
    validate = () => ta.value.trim().length < 20 ? "Paste a job description first. A few lines with the role, location and must-have skills is enough." : null;
    first = ta;
  } else if (key === "account") {
    const name = el("input", { type: "text", id: "account-input", placeholder: "For example: Razorpay", autocomplete: "off" });
    const dom = el("input", { type: "text", id: "account-domain-input", placeholder: "For example: razorpay.com", spellcheck: "false", autocomplete: "off" });
    const l1 = el("label", { for: "account-input" }, "Company name"); l1.append(name);
    const l2 = el("label", { for: "account-domain-input" }, "Company website (optional)"); l2.append(dom);
    l2.append(el("span", { class: "hint" }, "Helps the assistant find the right company when names are similar."));
    // Batch: a CSV of companies (columns company, domain, list). The app saves it in the assistant's folder.
    let csvText = "";
    const file = el("input", { type: "file", accept: ".csv,text/csv", class: "hidden", tabindex: "-1", "aria-hidden": "true" });
    const fname = el("span", { class: "muted" });
    const clear = button("Clear", "ghost hidden", () => setCsv("", ""));
    const runBtn = $(".run-btn", root);
    const setCsv = (text: string, label: string) => {
      csvText = text;
      fname.textContent = label;
      clear.classList.toggle("hidden", !text);
      name.disabled = dom.disabled = !!text;
      const count = text ? csvCompanies(text) : 0;
      runBtn.textContent = text ? `Research ${plural(count, "company", "companies")}` : T.run;
    };
    const pick = button("Load a CSV of companies…", "ghost", () => file.click());
    file.onchange = async () => {
      const f = file.files?.[0];
      file.value = "";
      if (!f) return;
      const text = await f.text();
      setCsv(text, `${f.name} · ${plural(csvCompanies(text), "company", "companies")}`);
    };
    const or = el("p", { class: "hint or-hint" }, "Or research a list: a CSV with a company column, and optional domain and list (sheet name) columns.");
    const row = el("div", { class: "file-row" });
    row.append(pick, file);
    const loaded = el("div", { class: "file-row csv-loaded" });
    loaded.append(fname, clear);
    // Opt-in: people's own published emails and phones (names, titles and public links are always kept).
    const keep = el("input", { type: "checkbox", id: "account-contacts-input" }) as HTMLInputElement;
    keep.checked = store.get(CONTACTS_KEY) === "1";
    keep.addEventListener("change", () => store.set(CONTACTS_KEY, keep.checked ? "1" : "0"));
    const l4 = el("label", { for: "account-contacts-input", class: "opt-check" });
    l4.append(keep, el("span", {}, "Keep published personal emails and phones"));
    const l4wrap = el("div", { class: "opt-wrap" });
    l4wrap.append(l4, el("span", { class: "hint" }, "Only details a person or company published themselves. Names, titles, public links and company lines are always kept."));
    inputs.append(l1, l2, l4wrap, or, row, loaded);
    name.addEventListener("keydown", submitOnCtrlEnter);
    dom.addEventListener("keydown", submitOnCtrlEnter);
    getInput = (): RunIn => (csvText ? { csv: csvText, contacts: keep.checked } : { company: name.value, domain: dom.value, contacts: keep.checked });
    validate = () => {
      if (csvText) {
        const head = csvText.replace(/^\uFEFF/, "").split(/\r?\n/).find((l) => l.trim()) ?? "";
        if (!head.split(",").some((c) => c.trim().replace(/^"|"$/g, "").toLowerCase() === "company")) return "The CSV needs a header row with a company column (optional: domain, list).";
        const n = csvCompanies(csvText);
        if (n === 0) return "The CSV has no companies under its header.";
        if (n > 200) return `The CSV lists ${n} companies; the limit is 200 per run.`;
        return null;
      }
      if (!name.value.trim()) return "Type the company name first, for example Razorpay, or load a CSV list.";
      const d = dom.value.trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
      if (d && !/^[a-z0-9.-]+\.[a-z0-9-]+$/i.test(d)) return `"${dom.value.trim()}" does not look like a website address. Use the form razorpay.com, or leave it empty.`;
      return null;
    };
    first = name;
  } else {
    const name = el("input", { type: "text", id: "company-input", placeholder: "For example: Razorpay", autocomplete: "off" });
    const dom = el("input", { type: "text", id: "domain-input", placeholder: "For example: razorpay.com", spellcheck: "false", autocomplete: "off" });
    const l1 = el("label", { for: "company-input" }, "Company name"); l1.append(name);
    const l2 = el("label", { for: "domain-input" }, "Company website (optional)"); l2.append(dom);
    l2.append(el("span", { class: "hint" }, "Helps the assistant find the right company when names are similar."));
    // Step 3 (public LinkedIn, Indeed and job-board listings via web search snippets): on unless the user turned it off.
    const pub = el("input", { type: "checkbox", id: "public-posts-input" }) as HTMLInputElement;
    pub.checked = store.get(PUBLIC_POSTS_KEY) !== "0";
    pub.addEventListener("change", () => store.set(PUBLIC_POSTS_KEY, pub.checked ? "1" : "0"));
    const l3 = el("label", { for: "public-posts-input", class: "opt-check" });
    l3.append(pub, el("span", {}, "Also search LinkedIn, Indeed and job boards (public listings)"));
    const l3wrap = el("div", { class: "opt-wrap" });
    l3wrap.append(l3, el("span", { class: "hint" }, "Slower and uses more allowance. Only listings that are publicly indexed can be found."));
    inputs.append(l1, l2, l3wrap);
    name.addEventListener("keydown", submitOnCtrlEnter);
    dom.addEventListener("keydown", submitOnCtrlEnter);
    getInput = () => ({ company: name.value, domain: dom.value, publicPosts: pub.checked });
    validate = () => {
      if (!name.value.trim()) return "Type the company name first, for example Razorpay.";
      const d = dom.value.trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
      if (d && !/^[a-z0-9.-]+\.[a-z0-9-]+$/i.test(d)) return `"${dom.value.trim()}" does not look like a website address. Use the form razorpay.com, or leave it empty.`;
      return null;
    };
    first = name;
  }

  const v: View = {
    key, root, running: false, sortCol: -1, sortDir: 1, filter: "", allCols: false, colFilters: {}, rows: [], index: [], cols: [], active: -1, range: [-1, -1], fitW: 0,
    steps: [], warns: 0, trustWarn: false, lastInput: {}, started: false, startedAt: 0, lockDismissed: false, lockShown: false,
    getInput, validate, focusInput: () => first.focus(),
  };
  $<HTMLFormElement>(".run-form", root).addEventListener("submit", (e) => { e.preventDefault(); runAgent(v); });
  $(".stop-btn", root).addEventListener("click", async () => {
    const ok = await confirmAction(`Stop this ${T.task}?`, `Anything already saved stays in your Excel file. You can start a new ${T.task} afterwards.`, `Stop the ${T.task}`, "Keep going");
    if (ok) invoke("stop_run", { agent: key }).catch(toastError);
  });
  $(".refresh-btn", root).addEventListener("click", () => loadSheet(v, true));
  $(".open-xlsx", root).addEventListener("click", () => openWorkbook(key));
  const crm = $<HTMLButtonElement>(".crm-btn", root);
  if (key === "company") crm.remove();
  else crm.addEventListener("click", () => exportCrm(v, crm));
  $(".cols-btn", root).addEventListener("click", (e) => {
    v.allCols = !v.allCols;
    const b = e.currentTarget as HTMLButtonElement;
    b.setAttribute("aria-pressed", String(v.allCols));
    b.textContent = v.allCols ? "Fewer columns" : "All columns";
    renderTable(v);
  });
  $<HTMLSelectElement>(".sheet-pick", root).addEventListener("change", (e) => {
    v.sheetName = (e.target as HTMLSelectElement).value; v.sortCol = -1; v.active = -1; loadSheet(v);
  });
  let filterTimer = 0;
  $<HTMLInputElement>(".filter", root).addEventListener("input", (e) => {
    clearTimeout(filterTimer);
    const val = (e.target as HTMLInputElement).value.toLowerCase().trim();
    filterTimer = window.setTimeout(() => { v.filter = val; v.active = -1; applyRows(v); }, 120);
  });
  const act = $<HTMLInputElement>(".show-activity", root);
  $(".log", root).classList.add("hide-activity");
  act.addEventListener("change", () => $(".log", root).classList.toggle("hide-activity", !act.checked));
  $(".lock-dismiss", root).addEventListener("click", () => dismissLock(v));
  $(".lock-overlay", root).addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); dismissLock(v); }
    if (e.key === "Tab") { e.preventDefault(); $(".lock-dismiss", root).focus(); } // the only control in the dialog
  });
  wireTable(v);
  return v;
}

function dismissLock(v: View) {
  v.lockDismissed = true;
  applyStatus(v.status!);
  $<HTMLTableElement>("table.sheet", v.root).focus();
}

async function openWorkbook(key: AgentKey) {
  try { await invoke("open_workbook", { agent: key }); toast("Opening the file in Excel…", "ok"); } catch (e) { toastError(e); }
}

// ---------- the step checklist ----------

const ICON_TICK = '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>';
const ICON_CROSS = '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>';
const ICON_SKIP = '<svg viewBox="0 0 16 16"><path d="M4 8h8"/></svg>';
const ICON_OPEN = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 4h5v5M16 4l-7 7"/><path d="M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3"/></svg>';

function resetSteps(v: View, n = TEXT[v.key].steps.length) {
  v.steps = Array.from({ length: n }, (_, i) => ({ label: TEXT[v.key].steps[i] ?? `Step ${i + 1}`, state: "pending", result: "", raw: "", sub: "" }));
}

/** Short sub-progress lines ("… crawled 20/150 pages") in plain words. */
function plainSub(t: string): string {
  let m: RegExpMatchArray | null;
  if ((m = t.match(/crawled (\d+)\/(\d+) pages?(?:.*?(\d+) job pages?)?/i))) return `Reading the website: ${m[1]} pages so far${m[3] ? `, ${m[3]} with jobs` : ""}`;
  if ((m = t.match(/scoring (\d+)\/(\d+)/i))) return `Scoring ${m[1]} of ${m[2]}`;
  if ((m = t.match(/checking (\d+)\/(\d+)/i))) return `Checking ${m[1]} of ${m[2]} for duplicates`;
  if ((m = t.match(/writing (\d+)\/(\d+)/i))) return `Saving ${m[1]} of ${m[2]}`;
  return cleanLine(t).replace(/\.$/, "");
}

function renderSteps(v: View) {
  const list = $(".steps", v.root);
  list.replaceChildren(...v.steps.map((s, i) => {
    const li = el("li", { class: `step ${s.state}` });
    const node = el("span", { class: "node", "aria-hidden": "true" });
    if (s.state === "done") node.innerHTML = ICON_TICK;
    else if (s.state === "failed") node.innerHTML = ICON_CROSS;
    else if (s.state === "skipped") node.innerHTML = ICON_SKIP;
    else if (s.state === "active") node.append(el("span", { class: "spinner" }));
    else node.textContent = String(i + 1);
    const body = el("div");
    const stateWord = { pending: "not started", active: "in progress", done: "done", failed: "did not work", skipped: "skipped" }[s.state];
    body.append(el("div", { class: "step-label" }, s.label), el("span", { class: "visually-hidden" }, `, ${stateWord}`));
    if (s.result) { const r = el("div", { class: "step-result" }, s.result); if (s.raw) r.title = s.raw; body.append(r); }
    else if (s.state === "active" && s.sub) body.append(el("div", { class: "step-sub" }, s.sub));
    li.append(node, body);
    return li;
  }));
  $(".starting", v.root).classList.toggle("hidden", !(v.running && v.steps.every((s) => s.state === "pending")));
  if (v.running) $(".step.active", v.root)?.scrollIntoView({ block: "nearest" });
}

function onCheckpoint(v: View, raw: string) {
  const t = raw.trim();
  let m: RegExpMatchArray | null;
  if ((m = t.match(/^Plan · .* · (\d+) steps/))) { resetSteps(v, Number(m[1]) || 6); }
  else if ((m = t.match(/^(\d+)\. (.+)$/))) {
    const i = Number(m[1]) - 1;
    if (v.steps[i] && !TEXT[v.key].steps[i]) v.steps[i].label = m[2];
  } else if ((m = t.match(/^([▶✓✗]) Step (\d+)\/(\d+) · ?(.*)$/))) {
    if (!v.steps.length) resetSteps(v, Number(m[3]));
    const i = Number(m[2]) - 1, s = v.steps[i];
    if (!s) return;
    if (m[1] === "▶") {
      v.steps.forEach((o, j) => { if (j < i && o.state === "active") o.state = "done"; });
      s.state = "active"; s.sub = "";
    } else if (m[1] === "✓") {
      s.state = /^skipped\b/i.test(m[4]) ? "skipped" : "done";
      s.result = plainResult(v.key, i + 1, m[4]); s.raw = m[4];
    } else { s.state = "failed"; s.result = plainResult(v.key, i + 1, m[4], true); s.raw = m[4]; }
  } else if (t.startsWith("…")) {
    const s = v.steps.find((o) => o.state === "active");
    if (s) s.sub = plainSub(t.replace(/^…\s*/, ""));
  } else return;
  renderSteps(v);
}

function logLine(v: View, kind: string, text: string) {
  const log = $(".log", v.root);
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.append(el("div", { class: kind }, text));
  if (nearBottom) log.scrollTop = log.scrollHeight;
  $("details.raw", v.root).classList.remove("hidden");
}

function showProgress(v: View, on: boolean) {
  $(".progress-empty", v.root).classList.toggle("hidden", on);
  $(".progress-live", v.root).classList.toggle("hidden", !on);
}

function setFormError(v: View, msg: string | null) {
  const e = $(".form-error", v.root);
  e.textContent = msg ?? "";
  e.classList.toggle("hidden", !msg);
}

async function runAgent(v: View) {
  if (v.running || v.status?.locked) return;
  const problem = v.validate();
  setFormError(v, problem);
  if (problem) { v.focusInput(); return; }
  try {
    $(".log", v.root).replaceChildren();
    $(".result-card", v.root).classList.add("hidden");
    $(".progress-title", v.root).textContent = "Progress";
    v.warns = 0; v.trustWarn = false; v.lastInput = v.getInput(); v.started = true; v.startedAt = Date.now();
    resetSteps(v);
    v.running = true;
    showProgress(v, true);
    renderSteps(v);
    const st = await invoke<Status>("start_run", { agent: v.key, input: v.lastInput });
    applyStatus(st);
  } catch (e) {
    v.running = false;
    renderSteps(v);
    if (!v.steps.some((s) => s.state !== "pending")) showProgress(v, false);
    setFormError(v, plainError(e));
  }
}

// Build the plain-language headline from the agent's step results.
function summary(v: View): string | null {
  const r = v.steps.map((s) => s.raw).join(" | ");
  if (v.key === "account") {
    // Step 7 writes the sheet; step 3's line names company-agent's sheet, so read only steps 7 and 8.
    const save = v.steps[6]?.raw ?? "", wrap = v.steps[7]?.raw ?? "";
    const saved = save.match(/(\d+) inserted, (\d+) updated/i), filled = wrap.match(/(\d+)(?:\/\d+)? fields? filled/i);
    const sheet = save.match(/sheet '([^']+)'/i);
    if (!saved && !filled) return null;
    const name = String(v.lastInput.company ?? "").trim();
    let s = v.lastInput.csv ? "Researched the companies in your list" : `Researched ${name || "the company"}`;
    if (filled && !v.lastInput.csv) s += `: ${filled[1]} of 19 fields filled`;
    if (sheet) s += `, saved to the ${sheet[1]} sheet`;
    s += ".";
    if (/no consented rows|add team\.csv/i.test(r)) s += " Add your team list (team.csv) to see warm connections.";
    return s;
  }
  if (v.key === "company") {
    const total = r.match(/company total (\d+)/i), open = r.match(/Open=(\d+)/), fresh = r.match(/(\d+) new\b/i);
    const sheet = r.match(/sheet '([^']+)'/i);
    const name = sheet?.[1] || String(v.lastInput.company ?? "").trim() || "the company";
    if (!total) return null;
    let s = `Found ${plural(Number(total[1]), "job")} at ${name}`;
    const bits = [open && `${open[1]} open`, fresh && Number(fresh[1]) > 0 && `${fresh[1]} new since the last check`].filter(Boolean);
    if (bits.length) s += `: ${bits.join(", ")}`;
    s += ".";
    if (/web search unavailable|no exa access/i.test(v.steps[2]?.raw ?? "")) s += " LinkedIn, Indeed and job boards were not searched because web search is not available.";
    return s;
  }
  const rows = r.match(/now (\d+) rows/i), top = r.match(/top (\d+)/i), ins = r.match(/(\d+) inserted/i);
  const sheet = r.match(/sheet '([^']+)'/i);
  if (!rows) return null;
  let s = `Found ${plural(Number(rows[1]), "candidate")}${sheet ? ` for ${sheet[1]}` : ""}`;
  const bits = [top && `best match ${top[1]} out of 100`, ins && Number(ins[1]) > 0 && `${ins[1]} new`].filter(Boolean);
  if (bits.length) s += `: ${bits.join(", ")}`;
  return s + ".";
}

/** "Apollo credits used: N" from the account run's step lines, or null when Apollo was not used. */
function apolloLine(v: View): string | null {
  const r = v.steps.map((s) => `${s.raw ?? ""} ${s.result ?? ""}`).join(" | ");
  const m = [...r.matchAll(/Apollo credits used:\s*(\d+)/gi)];
  if (m.length) {
    const n = Math.max(...m.map((x) => Number(x[1])));
    return `Apollo credits used: ${n}${n === 0 ? " (answers came from the 30-day cache or no leaders were found)" : ""}.`;
  }
  return null;
}

function showResult(v: View, status: string, counted: number) {
  const card = $(".result-card", v.root);
  const T = TEXT[v.key];
  card.className = "result-card";
  card.replaceChildren();
  const cap = v.status?.cap ?? 0;
  const used = cap ? Math.max(counted / cap * 100, 0) : 0;
  const usedText = counted > 0 ? `This ${T.task} used ${used < 1 ? "under 1%" : `${Math.round(used)}%`} of the allowance.` : "";
  const took = v.startedAt ? ` It took ${inHM(Date.now() - v.startedAt)}.` : "";
  let title: string, lines: string[] = [];
  const acts = el("div", { class: "actions" });
  if (status === "done") {
    card.classList.add("ok");
    title = summary(v) ?? "Finished. The results table is up to date.";
    lines.push(`The table now shows the latest ${T.nouns}.${took}`);
    acts.append(button("Open in Excel", "", () => openWorkbook(v.key)));
  } else if (status === "stopped") {
    title = `You stopped the ${T.task}.`;
    lines.push(`Anything saved before you stopped is in the table. Press ${T.run} to start again.`);
    acts.append(button("Open in Excel", "", () => openWorkbook(v.key)));
  } else if (status === "cap") {
    card.classList.add("warn");
    title = `The assistant ran out of allowance during this ${T.task}.`;
    lines.push("Results saved so far are in the table. It will be ready again after its break.");
    acts.append(button("Open in Excel", "", () => openWorkbook(v.key)));
  } else {
    card.classList.add("bad");
    const failed = v.steps.find((s) => s.state === "failed");
    title = failed ? `The ${T.task} stopped at “${failed.label}”.` : `The ${T.task} did not finish.`;
    if (failed?.result) lines.push(failed.result);
    if (v.trustWarn) {
      lines.push("Claude Code has not been allowed to work in this assistant's folder yet. Open the setup check and press Trust the agent folders.");
      acts.append(button("Open the setup check", "primary", () => { show("setup"); checkPrereqs(); }));
    } else {
      lines.push("Try again in a minute. If it keeps happening, run the setup check in Settings.");
      acts.append(button("Try again", "primary", () => $<HTMLFormElement>(".run-form", v.root).requestSubmit()));
    }
    acts.append(button("Show details", "", () => { const d = $<HTMLDetailsElement>("details.raw", v.root); d.open = true; $("summary", d).focus(); d.scrollIntoView({ block: "nearest" }); }));
  }
  if (v.key === "account" && status !== "failed") { const a = apolloLine(v); if (a) lines.push(a); }
  if (v.warns > 0 && status !== "done") lines.push(`${plural(v.warns, "action was", "actions were")} blocked by the assistant's safety rules. Show details lists them.`);
  if (usedText) lines.push(usedText);
  card.append(el("h3", {}, title), ...lines.map((l) => el("p", {}, l)), acts);
  card.classList.remove("hidden");
  $(".progress-title", v.root).textContent = status === "done" ? "Result" : "Progress";
  $(".progress", v.root).scrollTop = 0;
}

function applyStatus(s: Status) {
  const v = views[s.agent];
  if (!v) return;
  v.status = s;
  const wasRunning = v.running;
  v.running = s.running;
  const r = v.root;
  $(".usage-pct", r).textContent = pctText(s);
  $(".usage-label", r).textContent = s.shared ? "Your allowance used" : "Allowance used";
  const sharedLine = $(".usage-shared", r);
  sharedLine.textContent = s.shared ? sharedNote(company()) : "";
  sharedLine.classList.toggle("hidden", !s.shared);
  const bar = $(".bar", r);
  bar.setAttribute("aria-valuenow", String(Math.min(100, Math.round(s.percent))));
  bar.setAttribute("aria-label", "Allowance used");
  bar.setAttribute("aria-valuetext", `${pctText(s)}: ${fmt(s.counted)} of ${fmt(s.cap)} tokens`);
  const fill = $(".fill", r);
  fill.style.width = `${Math.min(100, s.percent)}%`;
  fill.classList.toggle("some", s.counted > 0);
  fill.classList.toggle("high", s.percent >= 80 && s.percent < 100 && !s.locked);
  fill.classList.toggle("full", s.percent >= 100 || s.locked);
  const note = $(".usage-note", r);
  note.title = `Counted: ${fmt(s.counted)} of ${fmt(s.cap)} tokens. Cached reads, not counted: ${fmt(s.cache_read)}.`;
  note.textContent = s.locked ? `Used up · ${short(s.counted)} of ${short(s.cap)} tokens` : `${short(s.counted)} of ${short(s.cap)} tokens`;
  // The window starts with the first token used; usage resets to zero when it closes (the company's window while signed in).
  const winMin = s.window_minutes ?? 240;
  const left = s.resets_at ? Math.max(0, s.resets_at - Date.now()) : 0;
  $(".reset-in", r).textContent = s.resets_at ? inHM(left) : windowText(winMin);
  $<HTMLElement>(".reset-fill", r).style.width = s.resets_at ? `${Math.min(100, 100 - (left / (winMin * 60 * 1000)) * 100)}%` : "0%";
  $(".reset-note", r).textContent = s.resets_at ? `at ${dayTime(s.resets_at)}` : "Starts with your first search";
  renderAllowanceCard();

  const capWarn = $(".cap-warn", r);
  capWarn.textContent = `Allowance is ${pctText(s)} used. A long ${TEXT[s.agent].task} may stop early; results saved before that are kept.`;
  capWarn.classList.toggle("hidden", !(s.percent >= 80 && !s.locked));

  const overlay = $(".lock-overlay", r);
  const lockNote = $(".lock-note", r);
  const content = [$(".agent-head", r), $(".workspace", r)];
  if (s.locked && s.unlock_at) {
    const at = dayTime(s.unlock_at), left = s.unlock_at - Date.now();
    $(".lock-title", r).textContent = `Available again at ${at}`;
    $(".lock-text", r).textContent = `This assistant has used its allowance for now. It will be available again at ${at} (in ${inHM(left)}).`;
    $(".lock-count", r).textContent = countdown(left);
    $(".lock-count", r).setAttribute("aria-label", `${inHM(left)} left`);
    const extra = $(".lock-extra", r);
    extra.textContent = s.clock_backwards ? "The computer's clock was moved back, so the break lasts until the clock catches up." : "";
    extra.classList.toggle("hidden", !s.clock_backwards);
    const showOverlay = !v.lockDismissed;
    overlay.classList.toggle("hidden", !showOverlay);
    content.forEach((c) => c.toggleAttribute("inert", showOverlay));
    if (showOverlay && !v.lockShown && currentView === s.agent) $(".lock-dismiss", r).focus();
    v.lockShown = showOverlay;
    lockNote.textContent = `Taking a break: available again at ${at} (in ${inHM(left)}). You can still look at saved results.`;
    lockNote.classList.toggle("hidden", showOverlay);
  } else {
    overlay.classList.add("hidden");
    content.forEach((c) => c.removeAttribute("inert"));
    lockNote.classList.add("hidden");
    v.lockDismissed = false; v.lockShown = false;
  }
  const run = $<HTMLButtonElement>(".run-btn", r);
  run.disabled = s.locked || s.running;
  run.textContent = s.running ? "Working…" : TEXT[s.agent].run;
  $(".stop-btn", r).classList.toggle("hidden", !s.running);
  $(".elapsed", r).classList.toggle("hidden", !s.running);
  if (s.running && !wasRunning && !v.started) {
    // A run already going when the window opened (or reloaded).
    v.started = true; v.startedAt = Date.now(); resetSteps(v); showProgress(v, true); renderSteps(v);
  }
  const st = $(`#state-${s.agent}`);
  st.className = "tab-state" + (s.running ? " running" : s.locked ? " locked" : "");
  st.textContent = s.running ? "Working" : s.locked ? "On a break" : "";
}

// Live countdown for locked sections and elapsed time for running ones.
setInterval(() => {
  for (const v of Object.values(views) as View[]) {
    const s = v.status;
    if (v.running && v.startedAt) $(".elapsed", v.root).textContent = `Running for ${clock(Date.now() - v.startedAt)}`;
    if (!s?.locked || !s.unlock_at) continue;
    const left = s.unlock_at - Date.now();
    $(".lock-count", v.root).textContent = countdown(left);
    if (left <= 0) refreshStatus();
  }
}, 1000);

// ---------- Excel viewer: virtualised table ----------

const ROW_H = 40;      // must match `table.sheet td { height }` in styles.css
const OVERSCAN = 8;

async function loadSheet(v: View, announce = false) {
  try {
    let res = await invoke<Workbook>("read_workbook", { agent: v.key, sheet: v.sheetName ?? null });
    // Evidence, Contacts and Company channels feed the details dialog; they are never offered as lists.
    const lists = listSheets(res.data.sheets);
    const views = viewsIn(v.key, res.data.sheets);
    const offered = [...lists, ...views.map((x) => x.sheet)];
    if (res.data.sheet && !offered.includes(res.data.sheet) && lists.length)
      res = await invoke<Workbook>("read_workbook", { agent: v.key, sheet: lists[0] });
    res.data.sheets = [...listSheets(res.data.sheets), ...viewsIn(v.key, res.data.sheets).map((x) => x.sheet)];
    if (res.data.sheet && !res.data.sheets.includes(res.data.sheet)) res.data = { ...res.data, sheet: null, headers: [], rows: [] };
    v.evidence = undefined;
    v.book = res;
    v.sheetName = res.data.sheet ?? undefined;
    // A lower-cased copy of each row, built once per load, keeps filtering fast on big sheets.
    v.index = res.data.rows.map((r) => r.join("\u0001").toLowerCase());
    const pick = $<HTMLSelectElement>(".sheet-pick", v.root);
    const option = (s: string, text: string) => { const o = el("option", { value: s }, text); if (s === res.data.sheet) o.selected = true; return o; };
    const lists2 = listSheets(res.data.sheets);
    const views2 = viewsIn(v.key, res.data.sheets);
    // Views sit in their own group so a list called "Companies" is never mistaken for the view of all lists.
    const group = el("optgroup", { label: "All lists together" });
    group.append(...views2.map((x) => option(x.sheet, x.label)));
    pick.replaceChildren(...lists2.map((s) => option(s, s)), ...(views2.length ? [group] : []));
    pick.disabled = res.data.sheets.length < 2;
    $(".updated", v.root).textContent = [res.exists && res.modified_ms ? lastUpdated(res.modified_ms) : "", res.source === "database" ? "from the company database" : ""].filter(Boolean).join(" · ");
    $<HTMLButtonElement>(".open-xlsx", v.root).disabled = !(res.local ?? res.exists);
    const crmBtn = $<HTMLButtonElement>(".crm-btn", v.root);
    if (crmBtn) crmBtn.disabled = !(res.local ?? res.exists);
    buildFilters(v);
    renderTable(v);
    if (announce) toast("Table refreshed.", "ok");
  } catch (e) {
    $(".updated", v.root).textContent = "";
    showTableEmpty(v, "The results file could not be opened", `${plainError(e)} If it is open in Excel, that is fine; press Refresh in a moment.`,
      ["Refresh", () => loadSheet(v, true)], true);
  }
}

const isUrl = (s: string) => /^https?:\/\/\S+$/i.test(s.trim());

function chipFor(header: string, val: string): HTMLElement | null {
  const h = header.toLowerCase(), t = val.trim(), low = t.toLowerCase();
  if (!t) return null;
  if (h.startsWith("match score")) {
    const n = Number(t);
    if (isNaN(n)) return null;
    const c = n >= 85 ? "green" : n >= 70 ? "blue" : n >= 50 ? "amber" : "red";
    return el("span", { class: `chip score ${c}`, title: `Match score ${n} out of 100` }, `${n}/100`);
  }
  if (h === "status" || h === "recruiter status") {
    const map: Record<string, string> = {
      open: "green", new: "blue", shortlisted: "green", contacted: "violet", interviewing: "violet", hired: "green",
      unverified: "amber", stale: "amber", "on hold": "amber", closed: "", rejected: "red", "not interested": "red",
    };
    const words: Record<string, string> = { unverified: "Not confirmed", stale: "Maybe old" };
    const chip = el("span", { class: `chip ${map[low] ?? ""}` }, words[low] ?? t);
    if (words[low]) chip.title = t;
    return chip;
  }
  if (h === "evidence confidence" || h === "confidence" || h === "key field confidence") {
    if (!/^(high|medium|low)$/.test(low)) return null;
    return el("span", { class: `chip ${low === "high" ? "green" : low === "medium" ? "blue" : "amber"}` }, t);
  }
  if (h === "freshness") {
    const c = low === "fresh" ? "green" : low === "ageing" ? "amber" : low === "stale" ? "red" : "";
    const tip: Record<string, string> = { fresh: "Checked in the last 30 days", ageing: "Checked 31 to 180 days ago", stale: "Checked more than 180 days ago" };
    const chip = el("span", { class: `chip ${c}` }, t);
    if (tip[low]) chip.title = tip[low];
    return chip;
  }
  return null;
}

function linkButton(url: string, what: string, text = "Open"): HTMLButtonElement {
  const b = button("", "open-link", () => invoke("open_url", { url: url.trim() }).catch(toastError));
  b.innerHTML = ICON_OPEN;
  b.append(text);
  b.title = url;
  b.tabIndex = -1;
  b.setAttribute("aria-label", `Open ${what} in your browser`);
  b.addEventListener("click", (e) => e.stopPropagation());
  return b;
}

const ICON_EMPTY = '<svg viewBox="0 0 48 48" aria-hidden="true"><rect x="8" y="10" width="32" height="28" rx="4"/><path d="M8 18h32M18 18v20M14 25h0M14 31h0"/></svg>';
const ICON_SEARCH = '<svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="21" cy="21" r="11"/><path d="M29 29l10 10M17 21h8"/></svg>';

function showTableEmpty(v: View, title: string, text: string, action?: [string, () => void], noHead = true) {
  const t = $<HTMLTableElement>("table.sheet", v.root);
  if (noHead) { t.tHead!.replaceChildren(); t.classList.add("hidden"); }
  t.tBodies[0].replaceChildren();
  const box = $(".sheet-empty", v.root);
  box.classList.toggle("no-head", noHead);
  box.innerHTML = v.filter && !noHead ? ICON_SEARCH : ICON_EMPTY;
  box.append(el("h2", {}, title), el("p", {}, text));
  if (action) box.append(button(action[0], "", action[1]));
  box.classList.remove("hidden");
  if (noHead) $(".count", v.root).textContent = "";
}

function visibleColumns(v: View, headers: string[]): number[] {
  if (v.allCols) return headers.map((_, i) => i);
  const find = (h?: string) => (h ? headers.findIndex((x) => x.trim().toLowerCase() === h.toLowerCase()) : -1);
  const view = VIEWS[v.key].find((x) => x.sheet === v.sheetName);
  const idx = view ? view.columns.map(([h]) => find(h)).filter((i) => i >= 0)
    : COLUMNS[v.key].map(([h, , alt]) => { const i = find(h); return i >= 0 ? i : find(alt); }).filter((i) => i >= 0);
  return idx.length >= 2 ? idx : headers.map((_, i) => i);
}

const widthKey = (v: View) => `colw:${v.key}:${v.sheetName ?? ""}:${v.allCols ? "all" : "short"}`;
function defaultWidth(header: string, n: number): number {
  const h = header.toLowerCase();
  if (/url|link/.test(h)) return 92;
  if (h.startsWith("match")) return 92;
  if (h === "experience years" || h === "evidence count") return 104;
  if (h === "employees" || h === "last round" || h === "h-1b filings (4q)") return 112;
  if (h === "last round amount usd" || h === "total raised usd") return 120;
  if (h === "hq country") return 84;
  if (h === "work mode" || h === "freshness") return 116;
  if (h === "country" || h === "function" || h === "seniority") return 124;
  if (h === "salary min") return 168;
  if (h === "status" || h === "recruiter status" || h.includes("confidence")) return 140;
  if (/date|posted|checked|discovered/.test(h)) return 116;
  return n === 0 ? 260 : 190;
}
/** Saved widths win; other columns start from a default, and in the short view text columns shrink to fit the panel. */
function columnWidths(v: View, headers: string[], cols: number[]): number[] {
  let saved: Record<string, number> = {};
  try { saved = JSON.parse(store.get(widthKey(v)) ?? "{}"); } catch { /* ignore a bad entry */ }
  const w = cols.map((i, n) => saved[headers[i]] ?? defaultWidth(headers[i], n));
  const avail = $(".table-wrap", v.root).clientWidth - 4;
  const flex = cols.map((i, n) => saved[headers[i]] === undefined && w[n] >= 190);
  const sum = w.reduce((a, b) => a + b, 0);
  if (!v.allCols && avail > 0 && sum > avail) {
    const fixed = w.reduce((a, b, n) => a + (flex[n] ? 0 : b), 0);
    const flexSum = w.reduce((a, b, n) => a + (flex[n] ? b : 0), 0);
    const room = Math.max(0, avail - fixed);
    w.forEach((x, n) => { if (flex[n]) w[n] = Math.max(110, Math.floor((x / flexSum) * room)); });
  }
  return w;
}
function saveWidth(v: View, header: string, w: number) {
  let saved: Record<string, number> = {};
  try { saved = JSON.parse(store.get(widthKey(v)) ?? "{}"); } catch { /* ignore */ }
  saved[header] = Math.round(w);
  store.set(widthKey(v), JSON.stringify(saved));
}

const label = (v: View, h: string) => {
  if (v.allCols) return h;
  const viewLabel = VIEWS[v.key].find((x) => x.sheet === v.sheetName)?.columns.find(([c]) => c === h)?.[1];
  return viewLabel ?? (LABELS[h] ? LABELS[h] : h);
};

/** Header, column widths and resize handles; then the rows through applyRows. */
function renderTable(v: View, keepScroll = false) {
  const t = $<HTMLTableElement>("table.sheet", v.root);
  const d = v.book?.data;
  const T = TEXT[v.key];
  $<HTMLButtonElement>(".cols-btn", v.root).disabled = !(v.book?.exists && d?.headers.length);
  if (!v.book?.exists || !d || !d.headers.length) {
    showTableEmpty(v, `No ${T.nouns} yet`, T.emptyTable, [v.key === "candidate" ? "Paste a job description" : "Type a company name", () => v.focusInput()]);
    return;
  }
  t.classList.remove("hidden");
  const cols = visibleColumns(v, d.headers);
  v.cols = cols;
  const widths = columnWidths(v, d.headers, cols);
  const cg = t.querySelector("colgroup")!;
  cg.replaceChildren(...widths.map((w) => { const c = el("col"); c.style.width = `${w}px`; return c; }));
  const setTableWidth = () => { t.style.width = `${[...cg.children].reduce((a, c) => a + parseFloat((c as HTMLElement).style.width), 0)}px`; };
  setTableWidth();

  const hr = el("tr", { "aria-rowindex": "1" });
  cols.forEach((i, n) => {
    const name = label(v, d.headers[i] || `Column ${i + 1}`);
    const th = el("th", { scope: "col", role: "columnheader" });
    const sorted = i === v.sortCol;
    th.setAttribute("aria-sort", sorted ? (v.sortDir === 1 ? "ascending" : "descending") : "none");
    const inner = el("div", { class: "th-in" });
    const b = el("button", { type: "button", class: "sort", title: `${name}: sort by this column` }, name);
    if (sorted) b.append(el("span", { class: "arrow", "aria-hidden": "true" }, v.sortDir === 1 ? "▲" : "▼"));
    b.onclick = () => { v.sortDir = v.sortCol === i ? (v.sortDir === 1 ? -1 : 1) : 1; v.sortCol = i; renderTable(v); };
    const grip = el("span", {
      class: "resize", role: "separator", tabindex: "0", "aria-orientation": "vertical",
      "aria-label": `Resize the ${name} column`, "aria-valuemin": "60", "aria-valuemax": "800", "aria-valuenow": String(widths[n]),
    });
    const col = cg.children[n] as HTMLElement;
    const setW = (w: number) => {
      const nw = Math.max(60, Math.min(800, Math.round(w)));
      col.style.width = `${nw}px`; grip.setAttribute("aria-valuenow", String(nw)); setTableWidth();
      return nw;
    };
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const x0 = e.clientX, w0 = parseFloat(col.style.width);
      grip.setPointerCapture(e.pointerId); grip.classList.add("dragging");
      const move = (ev: PointerEvent) => setW(w0 + ev.clientX - x0);
      const up = () => { grip.classList.remove("dragging"); grip.removeEventListener("pointermove", move); saveWidth(v, d.headers[i], parseFloat(col.style.width)); };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up, { once: true });
    });
    grip.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 48 : 16;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        saveWidth(v, d.headers[i], setW(parseFloat(col.style.width) + (e.key === "ArrowRight" ? step : -step)));
      }
    });
    grip.addEventListener("dblclick", () => { saveWidth(v, d.headers[i], setW(defaultWidth(d.headers[i], n))); });
    inner.append(b, grip);
    th.append(inner);
    hr.append(th);
  });
  t.tHead!.replaceChildren(hr);
  v.fitW = $(".table-wrap", v.root).clientWidth;
  applyRows(v, keepScroll);
}

/** A cell as the short table shows it: salary min, max and currency as one range, years with their unit. */
function shortCell(v: View, headers: string[], r: string[], i: number): string {
  const val = (r[i] ?? "").trim();
  if (v.allCols) return val;
  const h = headers[i];
  if (h === "Salary Min") return salaryText(headers, r);
  if (h === "Experience Years" && val !== "" && !isNaN(Number(val))) return `${val} yrs`;
  if (h === "Last Round Amount USD" || h === "Total Raised USD") return usdShort(val);
  if (h === "Employees" || h === "H-1B Filings (4Q)") return intText(val);
  return val;
}

/** Filter and sort the rows, then draw the visible window. */
function applyRows(v: View, keepScroll = false) {
  const d = v.book?.data;
  if (!d || !d.headers.length || !v.book?.exists) return;
  const T = TEXT[v.key];
  let idx = d.rows.map((_, i) => i);
  if (v.filter) idx = idx.filter((i) => v.index[i].includes(v.filter));
  const narrowed = hasColFilters(v);
  if (narrowed) idx = idx.filter((i) => passesFilters(d.headers, d.rows[i], v.colFilters, FILTERS[v.key]));
  if (v.sortCol >= 0) {
    const c = v.sortCol, dir = v.sortDir;
    idx.sort((ia, ib) => compareCells(d.rows[ia][c] ?? "", d.rows[ib][c] ?? "", dir));
  }
  v.rows = idx.map((i) => d.rows[i]);
  const t = $<HTMLTableElement>("table.sheet", v.root);
  t.setAttribute("aria-rowcount", String(v.rows.length + 1));
  t.setAttribute("aria-colcount", String(v.cols.length));
  const total = d.rows.length;
  $(".count", v.root).textContent = v.filter || narrowed ? `Showing ${fmt(v.rows.length)} of ${plural(total, T.noun, T.nouns)}` : plural(total, T.noun, T.nouns);
  const box = $(".sheet-empty", v.root);
  if (!v.rows.length) {
    const what = v.filter ? `Nothing matches “${v.filter}”` : narrowed ? `No ${T.nouns} match these filters` : `This ${T.pick.toLowerCase()} has no ${T.nouns} yet`;
    showTableEmpty(v, what, v.filter ? "Try a shorter word, or clear the search." : narrowed ? "Set a filter back to Any, or clear them all." : "Run a new search to fill it.",
      v.filter ? ["Clear search", () => clearSearch(v)] : narrowed ? ["Clear filters", () => clearFilters(v)] : undefined, false);
  } else box.classList.add("hidden");
  v.range = [-1, -1];
  if (!keepScroll) $(".table-wrap", v.root).scrollTop = 0;
  drawRows(v);
}

const hasColFilters = (v: View) => Object.values(v.colFilters).some(Boolean);

/** One picker per typed column the sheet has (Work mode, Country, Freshness, …). A choice that no longer exists is dropped. */
function buildFilters(v: View) {
  const box = $(".filters", v.root);
  const d = v.book?.data;
  box.replaceChildren();
  const kept: Record<string, string> = {};
  if (d && v.book?.exists && d.rows.length) {
    for (const f of FILTERS[v.key]) {
      if (d.headers.findIndex((h) => h.trim().toLowerCase() === f.header.toLowerCase()) < 0) continue;
      const values = f.kind === "enum" ? enumValues(d.headers, d.rows, f.header) : (f.steps ?? []).map(String);
      if (f.kind === "enum" && values.length < 2 && !(values.length === 1 && v.colFilters[f.header])) continue;
      const sel = el("select", { "aria-label": `Filter by ${f.label.toLowerCase()}` });
      sel.append(el("option", { value: "" }, `${f.label}: any`));
      for (const val of values) sel.append(el("option", { value: val }, f.kind === "min" ? `${f.label}: ${intText(val)}${f.header.startsWith("Experience") ? "+ yrs" : "+"}` : `${f.label}: ${val}`));
      const cur = v.colFilters[f.header] ?? "";
      if (values.includes(cur)) { sel.value = cur; kept[f.header] = cur; }
      sel.classList.toggle("on", !!sel.value);
      sel.addEventListener("change", () => {
        v.colFilters[f.header] = sel.value; v.active = -1;
        sel.classList.toggle("on", !!sel.value);
        $(".clear-filters", v.root).classList.toggle("hidden", !hasColFilters(v));
        applyRows(v);
      });
      box.append(sel);
    }
  }
  v.colFilters = kept;
  if (box.children.length) box.append(button("Clear filters", "ghost clear-filters" + (hasColFilters(v) ? "" : " hidden"), () => clearFilters(v)));
  box.classList.toggle("hidden", !box.children.length);
}

function clearFilters(v: View) {
  v.colFilters = {};
  buildFilters(v);
  applyRows(v);
}

function clearSearch(v: View) {
  const f = $<HTMLInputElement>(".filter", v.root);
  f.value = ""; v.filter = ""; applyRows(v); f.focus();
}

function drawRows(v: View, force = false) {
  if (!v.book) return;   // nothing loaded yet (for example while the sign-in screen is up): the resize observer can fire first
  const wrap = $(".table-wrap", v.root);
  const t = $<HTMLTableElement>("table.sheet", v.root);
  const head = t.tHead?.offsetHeight ?? 38;
  const n = v.rows.length;
  const first = Math.max(0, Math.floor((wrap.scrollTop - head) / ROW_H) - OVERSCAN);
  const last = Math.min(n, Math.ceil((wrap.scrollTop + wrap.clientHeight) / ROW_H) + OVERSCAN);
  if (!force && first === v.range[0] && last === v.range[1]) return;
  v.range = [first, last];
  const d = v.book!.data;
  const frag = document.createDocumentFragment();
  const spacer = (h: number) => { const tr = el("tr", { class: "spacer", "aria-hidden": "true" }); const td = el("td", { colspan: String(v.cols.length) }); td.style.height = `${h}px`; tr.append(td); return tr; };
  if (first > 0) frag.append(spacer(first * ROW_H));
  for (let k = first; k < last; k++) {
    const r = v.rows[k];
    const tr = el("tr", { class: "row" + (k === v.active ? " active" : ""), "aria-rowindex": String(k + 2), id: `${v.key}-row-${k}` });
    if (k === v.active) tr.setAttribute("aria-selected", "true");
    const name = r[v.cols[0]] || "this row";
    v.cols.forEach((i, c) => {
      const val = shortCell(v, d.headers, r, i);
      const td = el("td", { role: "gridcell" });
      const chip = chipFor(d.headers[i], val);
      if (c === 0) { td.className = "first-cell"; td.textContent = val || "(no name)"; td.title = val; }
      else if (chip) td.append(chip);
      else if (isUrl(val)) { td.className = "link-cell"; td.append(linkButton(val, `${label(v, d.headers[i]).toLowerCase()} for ${name}`)); }
      else if (!val) td.append(el("span", { class: "empty-cell", "aria-label": "empty" }, "–"));
      else if (notFound(val)) { td.append(el("span", { class: "empty-cell" }, notFound(val)!)); td.title = val; }
      else { td.textContent = val; td.title = val; }
      tr.append(td);
    });
    tr.addEventListener("click", () => { setActive(v, k, false); openRow(v, r); });
    frag.append(tr);
  }
  if (last < n) frag.append(spacer((n - last) * ROW_H));
  t.tBodies[0].replaceChildren(frag);
}

function setActive(v: View, k: number, scroll = true) {
  const n = v.rows.length;
  if (!n) return;
  v.active = Math.max(0, Math.min(n - 1, k));
  const t = $<HTMLTableElement>("table.sheet", v.root);
  t.setAttribute("aria-activedescendant", `${v.key}-row-${v.active}`);
  if (scroll) {
    const wrap = $(".table-wrap", v.root);
    const head = t.tHead?.offsetHeight ?? 38;
    const top = head + v.active * ROW_H;
    if (top - head < wrap.scrollTop) wrap.scrollTop = top - head;
    else if (top + ROW_H > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = top + ROW_H - wrap.clientHeight;
  }
  drawRows(v, true);
}

function wireTable(v: View) {
  const wrap = $(".table-wrap", v.root);
  const t = $<HTMLTableElement>("table.sheet", v.root);
  let raf = 0;
  wrap.addEventListener("scroll", () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; drawRows(v); }); }, { passive: true });
  let fitTimer = 0;
  new ResizeObserver(() => {
    drawRows(v);
    // Refit the columns when the panel width changes (window resized, or the section was hidden at load).
    clearTimeout(fitTimer);
    fitTimer = window.setTimeout(() => { if (v.book && wrap.clientWidth > 0 && Math.abs(wrap.clientWidth - v.fitW) > 8) renderTable(v, true); }, 150);
  }).observe(wrap);
  // Keyboard focus selects the first row. Mouse focus does not: redrawing the rows during the press would
  // swallow the click that opens the row's details.
  t.addEventListener("focus", () => { if (v.active < 0 && v.rows.length && t.matches(":focus-visible")) setActive(v, 0); });
  t.addEventListener("keydown", (e) => {
    if (e.target !== t) return;
    const page = Math.max(1, Math.floor(wrap.clientHeight / ROW_H) - 1);
    const go: Record<string, number> = { ArrowDown: 1, ArrowUp: -1, PageDown: page, PageUp: -page };
    if (e.key in go) { e.preventDefault(); setActive(v, (v.active < 0 ? 0 : v.active) + go[e.key]); }
    else if (e.key === "Home") { e.preventDefault(); setActive(v, 0); }
    else if (e.key === "End") { e.preventDefault(); setActive(v, v.rows.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { if (v.active >= 0) { e.preventDefault(); openRow(v, v.rows[v.active]); } }
  });
}

/** "Not found (checked: …)" cells: the short label for the table, or null for a real value. */
function notFound(val: string): string | null {
  const m = val.match(/^(Not found|No evidence found) \(checked:/);
  return m ? m[1] : null;
}

function openRow(v: View, r: string[]) {
  const d = v.book!.data;
  const dlg = $<HTMLDialogElement>("#row-dialog");
  const order = [...visibleColumns({ ...v, allCols: false }, d.headers)];
  d.headers.forEach((_, i) => { if (!order.includes(i)) order.push(i); });
  $("#row-title").textContent = r[order[0]] || "Details";
  const dl = $("#row-fields");
  dl.replaceChildren();
  const score = v.key === "candidate" ? scoreBreakdown(d.headers, r) : null;
  if (score) dl.append(scoreBlock(score));
  // Accounts: the [n] markers become links to their sources, so the raw Sources list is not repeated.
  const sources = sourceMap(cell(d.headers, r, "Sources"));
  for (const i of order.slice(1)) {
    const val = (r[i] ?? "").trim();
    if (!val) continue;
    const h = d.headers[i];
    if (score && PTS.test(h)) continue;
    if (h === "Sources" && sources.size) continue;
    if (v.key === "account" && CARD_COLUMNS.includes(h)) continue;
    if (v.key === "candidate" && DEV_COLUMNS.some(([x]) => x === h)) continue;
    const dd = el("dd");
    const chip = chipFor(h, val);
    if (chip) dd.append(chip);
    else if (notFound(val)) dd.append(el("span", { class: "empty-cell" }, val));
    else if (isUrl(val)) {
      const b = linkButton(val, LABELS[h] ?? h, val.length > 60 ? val.slice(0, 57) + "…" : val);
      b.tabIndex = 0;
      dd.append(b);
    } else if (sources.size && /\[\d+\]/.test(val)) dd.append(...withSourceLinks(val, sources));
    else dd.textContent = val;
    dl.append(el("dt", {}, detailLabel(h)), dd);
  }
  const cards = v.key === "account" ? el("div", { class: "fact-cards" }) : null;
  if (cards) dl.prepend(cards);
  const evBox = el("section", { class: "evidence-block", "aria-label": "Evidence" });
  dl.append(evBox);
  dlg.showModal();
  showEvidence(v, r, evBox).then((items) => { if (cards) factCards(d.headers, r, items, cards, evBox); });
  if (v.key === "account") showContacts(r[d.headers.indexOf("Domain")] ?? "", dl);
}

/** Text with [n] markers, the markers turned into small buttons that open source n. */
function withSourceLinks(text: string, sources: Map<string, string>): (string | HTMLElement)[] {
  const out: (string | HTMLElement)[] = [];
  let last = 0;
  for (const m of text.matchAll(/\[(\d+)\]/g)) {
    out.push(text.slice(last, m.index));
    const url = sources.get(m[1]);
    if (url) {
      const b = button(m[1], "src-ref", () => invoke("open_url", { url }).catch(toastError));
      b.title = url;
      b.setAttribute("aria-label", `Open source ${m[1]} in your browser`);
      out.push(b);
    } else out.push(m[0]);
    last = m.index! + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

const PART_COLORS = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"];

/** "Why this score": one bar of the positive parts out of 100, the cap shown as the struck-out end, and a legend. */
function scoreBlock(b: NonNullable<ReturnType<typeof scoreBreakdown>>): HTMLElement {
  const box = el("section", { class: "score-block", "aria-label": "Why this score" });
  const shown = b.score ?? b.total;
  const head = el("div", { class: "score-head" });
  head.append(el("h3", {}, "Why this score"), el("span", { class: "chip score " + (shown >= 85 ? "green" : shown >= 70 ? "blue" : shown >= 50 ? "amber" : "red") }, `${shown}/100`));
  box.append(head);
  const bar = el("div", { class: "score-bar", role: "img" });
  const pos = b.parts.filter((p) => p.pts > 0);
  const capPts = -b.cap;
  bar.setAttribute("aria-label", b.parts.map((p) => `${p.label} ${p.pts}`).join(", ") + `, total ${b.total} out of 100`);
  let used = 0;
  pos.forEach((p, n) => {
    const seg = el("span", { class: `seg ${PART_COLORS[n % PART_COLORS.length]}` });
    seg.style.width = `${Math.min(100 - used, p.pts)}%`;
    seg.title = `${p.label}: ${p.pts}`;
    used += p.pts;
    bar.append(seg);
  });
  if (capPts > 0) {
    const cap = el("span", { class: "seg cap" });
    cap.style.left = `${Math.max(0, Math.min(100, b.positive) - capPts)}%`;
    cap.style.width = `${Math.min(capPts, b.positive)}%`;
    cap.title = `Cap adjustment: ${b.cap}`;
    bar.append(cap);
  }
  box.append(bar);
  const legend = el("ul", { class: "score-legend" });
  let n = 0;
  for (const p of b.parts) {
    const li = el("li");
    const sw = el("span", { class: `sw ${p.pts < 0 ? "cap" : p.pts > 0 ? PART_COLORS[n++ % PART_COLORS.length] : "zero"}`, "aria-hidden": "true" });
    li.append(sw, el("span", {}, p.label), el("strong", {}, p.pts > 0 ? `+${p.pts}` : String(p.pts)));
    legend.append(li);
  }
  box.append(legend);
  const note = b.adds
    ? b.cap < 0 ? `The parts add up to ${b.total}. ${-b.cap} points were taken off by a gate or a confidence cap.` : `The parts add up to ${b.total}.`
    : `The parts add up to ${b.total}, but the sheet says ${b.score}. Run the search again to refresh this row.`;
  box.append(el("p", { class: "muted score-note" }, note));
  return box;
}

/** The Evidence sheet rows for this row (read once per load), each with its fact, quote, link, type and age. */
async function showEvidence(v: View, r: string[], box: HTMLElement): Promise<EvidenceItem[]> {
  const d = v.book!.data;
  if (v.key !== "company" && v.evidence === undefined) {
    try {
      const w = await invoke<Workbook>("read_workbook", { agent: v.key, sheet: EVIDENCE_SHEET });
      v.evidence = w.data.sheet === EVIDENCE_SHEET ? { headers: w.data.headers, rows: w.data.rows } : null;
    } catch { v.evidence = null; }
  }
  const items = evidenceFor(v.key, d.headers, r, v.evidence ?? null);
  box.append(el("h3", {}, items.length ? `Evidence (${items.length})` : "Evidence"));
  if (v.key === "candidate") devProfiles(d.headers, r, box);
  if (!items.length) {
    box.append(el("p", { class: "muted" }, v.key !== "company" && !v.evidence
      ? "This file has no Evidence sheet yet. It is added the next time the assistant saves results."
      : "No evidence rows were saved for this row yet. Run it again to collect them."));
    return items;
  }
  const groups = new Map<string, EvidenceItem[]>();
  for (const it of items) { const k = it.field || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k)!.push(it); }
  for (const [field, list] of groups) {
    if (field) box.append(el("h4", { "data-field": field, tabindex: "-1" }, LABELS[field] ?? field.replace(/\s*\(.*\)$/, "")));
    const ul = el("ul", { class: "evidence-list" });
    for (const it of list) {
      const li = el("li");
      li.append(el("p", { class: "ev-fact" }, it.fact || "Source found; the fact is filled in on the next scored run."));
      if (it.quote) li.append(el("blockquote", {}, `“${it.quote}”`));
      const meta = el("div", { class: "ev-meta" });
      if (it.type) meta.append(el("span", { class: "chip" }, it.type));
      if (it.confidence) { const c = chipFor("confidence", it.confidence); if (c) meta.append(c); }
      const age = el("span", { class: "muted" }, it.checked ? `checked ${ageText(it.age)}` : "check date unknown");
      if (it.checked) age.title = it.checked;
      meta.append(age);
      if (isUrl(it.url)) { const b = linkButton(it.url, `source for ${it.fact || field || "this fact"}`, it.url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 44)); b.tabIndex = 0; meta.append(b); }
      li.append(meta);
      ul.append(li);
    }
    box.append(ul);
  }
  return items;
}

/** GitHub languages, last activity and Stack Overflow tags, as written by the candidate assistant from the public APIs. */
function devProfiles(headers: string[], r: string[], box: HTMLElement) {
  const filled = DEV_COLUMNS.filter(([h]) => cell(headers, r, h));
  if (!filled.length) return;
  const sec = el("section", { class: "dev-block", "aria-label": "Developer profiles" });
  sec.append(el("h4", {}, "Developer profiles"));
  const dl = el("dl");
  for (const [h, lab] of filled) {
    const val = cell(headers, r, h);
    const dd = el("dd");
    const items = h === "GitHub Last Active" ? [] : splitList(val);
    if (items.length > 1) { const ul = el("ul", { class: "tag-list" }); for (const x of items) ul.append(el("li", { class: "chip" }, x)); dd.append(ul); }
    else dd.textContent = val;
    dl.append(el("dt", {}, lab), dd);
  }
  sec.append(dl);
  box.append(sec);
}

/** A small button that scrolls the dialog to the Evidence heading of a field and marks it for a moment. */
function evidenceLink(evBox: HTMLElement, field: string, what: string): HTMLButtonElement | null {
  const h = [...evBox.querySelectorAll<HTMLElement>("h4[data-field]")].find((x) => x.dataset.field === field);
  if (!h) return null;
  const b = button("Evidence", "ev-link", () => {
    h.scrollIntoView({ block: "start", behavior: "smooth" });
    h.focus({ preventScroll: true });
    h.classList.remove("flash"); void h.offsetWidth; h.classList.add("flash");
  });
  b.setAttribute("aria-label", `Show the evidence for ${what}`);
  return b;
}

/** Accounts: the Key facts card (typed columns) and the H-1B card, each line linked to its Evidence rows. */
function factCards(headers: string[], r: string[], items: EvidenceItem[], box: HTMLElement, evBox: HTMLElement) {
  const facts = keyFacts(headers, r);
  if (facts.length) {
    const sec = el("section", { class: "fact-card", "aria-label": "Key facts" });
    sec.append(el("h3", {}, "Key facts"));
    const dl = el("dl");
    for (const f of facts) {
      const dd = el("dd");
      dd.append(el("span", {}, f.value));
      const field = f.fields.find((x) => items.some((e) => e.field === x));
      const link = field ? evidenceLink(evBox, field, f.label.toLowerCase()) : null;
      if (link) dd.append(link); else dd.append(el("span", { class: "muted ev-none" }, "no evidence row"));
      dl.append(el("dt", {}, f.label), dd);
    }
    sec.append(dl);
    box.append(sec);
  }
  const h = h1bFor(headers, r, items);
  if (!h) return;
  const sec = el("section", { class: "fact-card h1b-card", "aria-label": "H-1B filings in the last 4 quarters" });
  sec.append(el("h3", {}, "H-1B (last 4 quarters)"));
  const top = el("div", { class: "h1b-count" });
  if (h.count === null) top.append(el("span", { class: "muted" }, "Not checked yet. It is filled the next time this company is researched."));
  else {
    top.append(el("strong", {}, intText(String(h.count))), el("span", {}, h.count === 1 ? " filing" : " filings"));
    if (h.window) top.append(el("span", { class: "muted" }, ` · decided ${h.window}`));
    const link = evidenceLink(evBox, "H-1B Filings (4Q)", "the H-1B filings");
    if (link) top.append(link);
  }
  sec.append(top);
  if (h.roles.length) {
    const ol = el("ol", { class: "h1b-roles", "aria-label": "Top job titles" });
    for (const x of h.roles) { const li = el("li"); li.append(el("span", {}, x.title)); if (x.n !== null) li.append(el("strong", {}, String(x.n))); ol.append(li); }
    sec.append(ol);
    const link = evidenceLink(evBox, "H-1B Top Roles", "the top H-1B job titles");
    if (link) sec.append(link);
  }
  sec.append(el("p", { class: "muted note" }, "Labor Condition Applications filed with the US Department of Labor: a sign of US hiring, never a claim about a person."));
  box.append(sec);
}

// ---------- CRM export ----------

/** Ask HubSpot or Salesforce, run the assistant's export_crm.py through the backend, then open the output folder. */
async function exportCrm(v: View, btn: HTMLButtonElement) {
  const d = $<HTMLDialogElement>("#crm-dialog");
  $("#crm-what").textContent = v.key === "account"
    ? "Writes a companies file (keyed by website) and a contacts file (only self-published or Apollo-verified emails)."
    : "Writes one contacts file. Each person is keyed by a self-published email when there is one, otherwise by profile link.";
  d.returnValue = "";
  d.showModal();
  const choice = await new Promise<string>((res) => d.addEventListener("close", () => res(d.returnValue), { once: true }));
  if (choice !== "hubspot" && choice !== "salesforce") return;
  await busy(btn, async () => {
    try { toast(await invoke<string>("export_crm", { agent: v.key, format: choice }), "ok"); } catch (e) { toastError(e); }
  });
}

/** A visible label for contact data that came from the paid Apollo API, apart from self-published facts. */
function apolloBadge(src: string) {
  const b = el("span", { class: "chip chip-apollo" }, /verified/.test(src) ? "Source: Apollo (verified)" : "Source: Apollo");
  b.title = src;
  return b;
}

/** A company's people and channels (the Contacts and Company channels sheets), under its details. */
async function showContacts(domain: string, dl: HTMLElement) {
  const box = el("section", { class: "contacts-block", "aria-label": "Leaders and contacts" });
  dl.append(box);
  const dom = domain.trim().toLowerCase();
  if (!dom) return;
  let people: string[][] = [], ph: string[] = [], chans: string[][] = [], ch: string[] = [];
  try {
    const [a, b] = await Promise.all(CONTACT_SHEETS.map((s) => invoke<Workbook>("read_workbook", { agent: "account", sheet: s })));
    if (a.data.sheet === "Contacts") { ph = a.data.headers; people = a.data.rows.filter((r) => (r[0] ?? "").toLowerCase() === dom); }
    if (b.data.sheet === "Company channels") { ch = b.data.headers; chans = b.data.rows.filter((r) => (r[0] ?? "").toLowerCase() === dom); }
  } catch (e) {
    box.append(el("p", { class: "muted" }, `Contacts could not be read: ${plainError(e)}`));
    return;
  }
  const col = (h: string[], r: string[], name: string) => (r[h.indexOf(name)] ?? "").trim();
  const value = (text: string, what: string) => {
    const span = el("span", { class: "c-val" });
    if (!text) span.append(el("span", { class: "empty-cell" }, "Not published"));
    else if (/^(Not published|Not found|Not stored)/.test(text)) {
      const s = el("span", { class: "empty-cell" }, text.split(" (")[0]);
      s.title = text;
      span.append(s);
    } else if (isUrl(text)) { const b = linkButton(text, what, text.replace(/^https?:\/\/(www\.)?/, "").slice(0, 48)); b.tabIndex = 0; span.append(b); }
    else {
      const url = text.match(/https?:\/\/\S+/)?.[0];
      span.append(url ? text.replace(url, "").trim() || url : text);
      if (url) { const b = linkButton(url, what, "Open"); b.tabIndex = 0; span.append(" ", b); }
    }
    return span;
  };
  box.append(el("h3", {}, people.length ? `Leaders and contacts (${people.length})` : "Leaders and contacts"));
  if (!people.length) box.append(el("p", { class: "muted" }, "No contacts saved for this company yet. Research it again to collect them."));
  for (const r of people) {
    const card = el("article", { class: "contact-card" });
    const head = el("div", { class: "c-head" });
    head.append(el("strong", {}, col(ph, r, "Name")), el("span", { class: "muted" }, [col(ph, r, "Title"), col(ph, r, "Location")].filter((x) => x && !/^Not published/.test(x)).join(" · ")));
    card.append(head);
    const grid = el("dl", { class: "c-grid" });
    const rows: [string, string][] = [["Email", "Email"], ["Phone", "Phone"], ["LinkedIn", "LinkedIn (from search results)"], ["X / Twitter", "X / Twitter"],
      ["GitHub", "GitHub"], ["Website / Blog", "Website / Blog"], ["Podcast / Newsletter", "Podcast / Newsletter"], ["Upcoming talks", "Upcoming Talks"], ["Booking link", "Booking Link"]];
    for (const [label, h] of rows) {
      const t = col(ph, r, h);
      if (!t || (/^Not published/.test(t) && !["Email", "Phone"].includes(label))) continue;
      const dd = el("dd");
      dd.append(value(t, `${label} of ${col(ph, r, "Name")}`));
      const src = col(ph, r, `${h} Source`);
      if (src) dd.title = /^Source: /.test(src) ? src : `Source: ${src}`;
      if (/^Source: Apollo/.test(src)) dd.append(" ", apolloBadge(src));
      grid.append(el("dt", {}, label), dd);
    }
    const srcDd = el("dd");
    srcDd.append(value(col(ph, r, "Source"), `source for ${col(ph, r, "Name")}`), el("span", { class: "muted nowrap" }, ` verified ${col(ph, r, "Last Verified")}`));
    grid.append(el("dt", {}, "Source"), srcDd);
    card.append(grid);
    box.append(card);
  }
  if (chans.length) {
    box.append(el("h3", {}, "Company channels"));
    const grid = el("dl", { class: "c-grid" });
    for (const r of chans) {
      const dd = el("dd");
      dd.append(value(col(ch, r, "Value"), col(ch, r, "Channel")));
      const src = col(ch, r, "Source");
      if (src && src !== col(ch, r, "Value")) { const b = linkButton(src, `source for ${col(ch, r, "Channel")}`, "Source"); b.tabIndex = 0; dd.append(" ", b); }
      grid.append(el("dt", {}, col(ch, r, "Channel")), dd);
    }
    box.append(grid);
  }
}

// ---------- setup check ----------

interface Plain {
  title: string; why: string; note?: string; steps?: string[]; cmd?: string; actions?: [string, () => void][];
  fallback?: [string, [string, () => void][]];
  keyField?: "exa" | "apollo";
}

const INSTALL_CLAUDE = { windows: "irm https://claude.ai/install.ps1 | iex", other: "curl -fsSL https://claude.ai/install.sh | bash" };
const INSTALL_UV = { windows: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"', other: "curl -LsSf https://astral.sh/uv/install.sh | sh" };
const shellName = () => platform === "windows" ? "PowerShell (search for it in the Start menu)" : "a terminal";
const openUrl = (url: string) => () => invoke("open_url", { url }).catch(toastError);

function openSetupStep(step: string) {
  invoke("open_setup", { step })
    .then(() => toast("A window has opened. Follow the steps shown there, then press Check again.", "ok"))
    .catch(toastError);
}

async function busy(btn: HTMLButtonElement | null, work: () => Promise<void>) {
  const old = btn?.textContent ?? "";
  if (btn) { btn.disabled = true; btn.textContent = "Working…"; }
  try { await work(); } finally { if (btn?.isConnected) { btn.disabled = false; btn.textContent = old; } }
}

async function saveExaKey(input: HTMLInputElement) { await saveKey("exa", input); }

/** Save an API key into the assistants' .env files. The key is never shown again, logged or kept in the page. */
async function saveKey(which: "exa" | "apollo", input: HTMLInputElement) {
  const key = input.value.trim();
  if (!key) { toast(which === "exa" ? "Paste your Exa key first." : "Paste your Apollo API key first."); input.focus(); return; }
  try {
    toast(await invoke<string>(which === "exa" ? "save_exa_key" : "save_apollo_key", { key }), "ok");
    input.value = "";
  } catch (e) {
    toastError(e);
  }
  await checkPrereqs();
}

async function prepareAgents() {
  toast("Preparing the assistants. This can take a few minutes…");
  try { toast(await invoke<string>("prepare_agents"), "ok"); } catch (e) { toastError(e); }
  await checkPrereqs();
}

async function trustFolders() {
  try { toast(await invoke<string>("trust_folders"), "ok"); } catch (e) { toastError(e); }
  await checkPrereqs();
}

function plainCheck(c: Check): Plain {
  const win = platform === "windows";
  if (c.name.startsWith("Claude Code CLI")) return {
    title: c.ok ? "Claude Code is installed" : "Install Claude Code",
    why: "Recruit Desk uses Claude Code to do the research. It is not installed on this computer yet, or Recruit Desk cannot find it.",
    steps: [`Press Copy to copy the install command.`, `Open ${shellName()}.`, "Paste the command and press Enter. Wait until it finishes.", "Close and reopen Recruit Desk."],
    cmd: win ? INSTALL_CLAUDE.windows : INSTALL_CLAUDE.other,
    actions: [["Open the install guide", openUrl("https://docs.claude.com/en/docs/claude-code/setup")]],
  };
  if (c.name.startsWith("Claude login")) return {
    title: c.ok ? "Signed in to Claude" : "Sign in to Claude",
    why: "The assistants work under your Claude account, so Claude Code needs you to sign in once.",
    steps: ["Press Sign in. A window opens and then your web browser.", "Sign in with your Claude account and approve the request.", "When the window says you are signed in, close it and press Check again."],
    actions: [["Sign in", () => openSetupStep("login")]],
  };
  if (c.name.startsWith("uv")) return {
    title: c.ok ? "Helper tool (uv) is installed" : "Install the helper tool (uv)",
    why: "The assistants use a small free helper called uv to run their scripts.",
    steps: ["Press Copy to copy the install command.", `Open ${shellName()}.`, "Paste the command and press Enter.", "Close and reopen Recruit Desk."],
    cmd: win ? INSTALL_UV.windows : INSTALL_UV.other,
    actions: [["Open the install guide", openUrl("https://docs.astral.sh/uv/getting-started/installation/")]],
  };
  if (c.name.startsWith("Exa search key")) return {
    title: c.ok ? "Exa search key saved" : "Exa search key (optional)",
    why: "Optional. Without it, Find candidates cannot search the web for new people, company scans skip public hiring posts and account research may be slowed by the free search limit; company job feeds, career pages and the Excel viewer still work. Exa gives you a free key; it is saved only on this computer.",
    steps: ["Press Get a free key and sign up at the Exa dashboard.", "Copy your API key.", "Paste it in the box below and press Save key."],
    actions: [["Get a free key", openUrl("https://dashboard.exa.ai")]],
    keyField: "exa",
  };
  if (c.name.startsWith("Apollo API key")) return {
    title: c.ok ? "Apollo API key saved" : "Apollo API key (optional, paid)",
    why: "Optional. With it, account research asks Apollo.io first for company leaders and keeps only the emails Apollo marks verified, at most 6 credits per company. Without it, account research uses free public sources only. The key is saved only on this computer.",
    steps: ["Sign in to Apollo.io and open Settings, Integrations, API.", "Create or copy an API key.", "Paste it in the box below and press Save key."],
    actions: [["Open Apollo API settings", openUrl("https://app.apollo.io/#/settings/integrations/api")]],
    keyField: "apollo",
  };
  if (c.name.startsWith("Assistant packages")) return {
    title: c.ok ? "Assistants prepared" : "Prepare the assistants",
    why: "The assistants need a few Python packages. Preparing them now makes the first search start quickly.",
    steps: ["Press Prepare now.", "Wait until Recruit Desk says all three assistants are prepared."],
    actions: [["Prepare now", prepareAgents]],
  };
  if (c.name.startsWith("Agent folders trusted")) return {
    title: c.ok ? "Claude Code may work in the agent folders" : "Allow Claude Code to work in the agent folders",
    why: "Claude Code asks once before it works in a new folder. Until all three assistant folders are allowed, the assistants cannot use their tools and every search stops early.",
    note: "First close any open Claude Code windows. An open one can undo this.",
    steps: ["Close any Claude Code windows that are open.", "Press Trust the agent folders.", "Recruit Desk checks again by itself."],
    actions: [["Trust the agent folders", trustFolders]],
    fallback: ["Or do it in a terminal instead:", [["candidate folder", () => openSetupStep("trust-candidate")], ["company folder", () => openSetupStep("trust-company")], ["account folder", () => openSetupStep("trust-account")]]],
  };
  return {
    title: c.ok ? "Assistant folders found" : "Find the assistants folder",
    why: "Recruit Desk could not find the folder that holds the three assistants (it is called AI-AGENTS and contains candidate-agent, company-agent and account-agent).",
    steps: ["Open Settings.", "Under Folders and programs, enter where the AI-AGENTS folder is.", "Press Save folders, then run the setup check again."],
    actions: [["Open Settings", () => { show("settings"); $<HTMLInputElement>("#set-root").focus(); }]],
  };
}

let lastChecks: Check[] = [];

/** Checks that may stay unset: they are shown, but never block setup or flag the Setup badge. */
const isOptional = (c: Check) => c.name.startsWith("Exa search key") || c.name.startsWith("Apollo API key");
const needsWork = (c: Check) => !c.ok && !isOptional(c);

async function checkPrereqs(): Promise<Check[]> {
  const msg = $("#setup-msg");
  msg.textContent = "Checking…";
  let checks: Check[];
  try { checks = await invoke<Check[]>("check_prereqs"); } catch (e) { msg.textContent = plainError(e); return lastChecks; }
  lastChecks = checks;
  keyHints();
  const bad = checks.filter(needsWork);
  const optional = checks.filter((c) => !c.ok && isOptional(c));
  const ordered = [...bad, ...optional, ...checks.filter((c) => c.ok)];
  $("#setup-list").replaceChildren(...ordered.map((c) => {
    const p = plainCheck(c);
    const li = el("li", { class: `setup-item ${c.ok ? "ok" : isOptional(c) ? "optional" : "bad"}` });
    const node = el("span", { class: "node", "aria-hidden": "true" });
    node.innerHTML = c.ok ? ICON_TICK : isOptional(c) ? "?" : "!";
    const body = el("div");
    body.append(el("h2", {}, p.title), el("span", { class: "visually-hidden" }, c.ok ? "Ready." : isOptional(c) ? "Optional." : "Needs your attention."));
    if (!c.ok) {
      body.append(el("p", {}, p.why));
      if (p.note) body.append(el("p", { class: "note" }, p.note));
      if (p.steps) { const ol = el("ol"); p.steps.forEach((s) => ol.append(el("li", {}, s))); body.append(ol); }
      if (p.cmd) {
        const row = el("div", { class: "cmd" });
        const cmd = p.cmd;
        row.append(el("code", {}, cmd), button("Copy", "", async () => {
          if (await copyText(cmd)) toast("Copied. Now paste it as the steps say.", "ok"); else toast("Could not copy. Select the text and copy it by hand.", "error");
        }));
        body.append(row);
      }
      if (p.keyField) {
        const row = el("div", { class: "cmd" });
        const which = p.keyField;
        const label = which === "exa" ? "Exa API key" : "Apollo API key";
        const input = el("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: `Paste your ${label}`, "aria-label": label });
        const save = button("Save key", "primary");
        save.onclick = () => busy(save, () => saveKey(which, input));
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") save.click(); });
        row.append(input, save);
        body.append(row);
      }
      if (p.actions?.length) {
        const acts = el("div", { class: "actions" });
        p.actions.forEach(([t, f], n) => {
          const b = button(t, n === 0 && !p.cmd && !p.keyField ? "primary" : "");
          b.onclick = () => busy(b, async () => { await f(); });
          acts.append(b);
        });
        body.append(acts);
      }
      if (p.fallback) {
        const fb = el("p", { class: "fallback muted" }, p.fallback[0]);
        p.fallback[1].forEach(([t, f], n) => fb.append(n ? " · " : " ", button(t, "link", f)));
        body.append(fb);
      }
      const det = el("details");
      det.append(el("summary", {}, "Technical details"), el("p", {}, `${c.detail} ${c.fix}`.trim()));
      body.append(det);
    }
    li.append(node, body);
    return li;
  }));
  const allOk = !bad.length;
  const ready = checks.filter((c) => c.ok).length;
  $("#setup-count").textContent = `${ready} of ${checks.length} ready${optional.length ? ` (${optional.length} optional left)` : ""}`;
  const sbar = $("#setup-bar");
  sbar.setAttribute("aria-valuemax", String(checks.length));
  sbar.setAttribute("aria-valuenow", String(ready));
  sbar.setAttribute("aria-label", "Setup progress");
  $(".fill", sbar).style.width = `${checks.length ? (ready / checks.length) * 100 : 0}%`;
  $("#setup-title").textContent = allOk ? "You're all set" : "Let's get Recruit Desk ready";
  $("#setup-lede").textContent = allOk
    ? "Everything the assistants need is in place."
    : `${plural(bad.length, "thing needs", "things need")} your attention before the assistants can work. Each one shows what to do.`;
  $("#setup-continue").textContent = allOk ? "Start using Recruit Desk" : "Skip for now";
  $("#setup-continue").className = allOk ? "primary" : "";
  $("#setup-recheck").classList.toggle("hidden", allOk);
  msg.textContent = `Checked at ${timeOf(Date.now())}.`;
  $("#setup-badge").classList.toggle("hidden", allOk);
  $("#setup-summary").textContent = allOk
    ? "Everything is ready."
    : `${plural(bad.length, "thing needs", "things need")} your attention. Run the setup check to fix ${bad.length === 1 ? "it" : "them"}.`;
  return checks;
}

// ---------- settings ----------

let savedCaps: Record<AgentKey, number> = { candidate: 0, company: 0, account: 0 };
const AGENT_KEYS: AgentKey[] = ["candidate", "company", "account"];
const isAgent = (v: string): v is AgentKey => (AGENT_KEYS as string[]).includes(v);

/** "Saved" or "Not set" under each key field, from the last setup check (never the key itself). */
function keyHints() {
  const state = (n: string) => lastChecks.find((c) => c.name.startsWith(n));
  const exa = state("Exa search key"), apollo = state("Apollo API key");
  if (exa) $("#hint-exa").textContent = exa.ok ? "Saved for every assistant." : "Not set. Find candidates and company scans search less without it.";
  if (apollo) $("#hint-apollo").textContent = apollo.ok
    ? "Saved. Account research asks Apollo first and keeps only verified emails, at most 6 credits per company."
    : "Not set. Account research uses free public sources only.";
}

async function loadSettings() {
  const r = await invoke<SettingsInfo>("get_settings");
  platform = r.platform;
  $<HTMLInputElement>("#set-root").value = r.settings.agents_root;
  $<HTMLInputElement>("#set-claude").value = r.settings.claude_path;
  savedCaps = r.settings.caps;
  $("#hint-root").textContent = r.detected_root ? `Found automatically: ${r.detected_root}` : "Not found automatically. Enter the folder's location.";
  $("#hint-claude").textContent = r.detected_claude ? `Found automatically: ${r.detected_claude}` : "Claude Code was not found automatically.";
  $("#data-dir").textContent = `Usage and settings are saved in ${r.data_dir}.`;
  $("#data-folder-line").textContent = r.settings.data_root ? `Folder: ${r.settings.data_root}` : "The folder is created the first time the app starts with a Documents folder.";
  $<HTMLButtonElement>("#open-data-folder").disabled = !r.settings.data_root;
  $("#nav-version").textContent = `Version ${r.version}`;
  $("#about-version").textContent = `Recruit Desk, version ${r.version}`;
}

async function saveSettings(ev: Event) {
  document.querySelectorAll(".save-msg").forEach((m) => (m.textContent = ""));
  const msg = ((ev.currentTarget as HTMLElement).parentElement!.querySelector(".save-msg") as HTMLElement);
  // The allowance is set by the owner and is not editable here; the backend keeps the stored caps.
  const caps = savedCaps;
  const s: Settings = {
    agents_root: $<HTMLInputElement>("#set-root").value.trim(),
    claude_path: $<HTMLInputElement>("#set-claude").value.trim(),
    caps,
  };
  try {
    await invoke("save_settings", { settings: s });
    msg.textContent = "Saved.";
    toast("Settings saved.", "ok");
    await Promise.all([refreshStatus(), checkPrereqs(), ...AGENT_KEYS.map((k) => loadSheet(views[k]))]);
  } catch (e) { msg.textContent = plainError(e); }
}

function applyTheme(t: string) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll<HTMLInputElement>("input[name=theme]").forEach((r) => (r.checked = r.value === t));
  store.set("theme", t);
}
function initTheme() {
  applyTheme(store.get("theme") ?? "system");
  document.querySelectorAll<HTMLInputElement>("input[name=theme]").forEach((r) => (r.onchange = () => applyTheme(r.value)));
}

async function refreshStatus() {
  try { (await invoke<Status[]>("get_status")).forEach(applyStatus); } catch { /* the next tick tries again */ }
}

// ---------- quick actions (command palette) ----------

interface Action { group: string; label: string; keys?: string; run(): void; when?(): boolean }

function agentHere(): View | null { return isAgent(currentView) ? views[currentView] : null; }

const ACTIONS: Action[] = [
  { group: "Go to", label: "Find candidates", keys: "Ctrl 1", run: () => go("candidate") },
  { group: "Go to", label: "Company jobs", keys: "Ctrl 2", run: () => go("company") },
  { group: "Go to", label: "Accounts", keys: "Ctrl 3", run: () => go("account") },
  { group: "Go to", label: "Settings", keys: "Ctrl 4", run: () => go("settings") },
  { group: "Account", label: "Search history", keys: "Ctrl 6", run: () => go("history"), when: () => historyAllowed() },
  { group: "Account", label: "Logs", keys: "Ctrl 7", run: () => go("logs"), when: () => isAdminSession() },
  { group: "Account", label: "People and access", keys: "Ctrl 5", run: () => go("admin"), when: () => isAdminSession() },
  { group: "Account", label: "Sign out", run: () => void signOut(), when: () => auth.enabled && auth.state === "signed_in" },
  { group: "Go to", label: "About Recruit Desk", run: () => { go("settings"); $("#about").scrollIntoView({ block: "start" }); } },
  { group: "Search", label: "Start the search in this section", keys: "Ctrl Enter", when: () => !!agentHere(), run: () => { const v = agentHere()!; v.focusInput(); $<HTMLFormElement>(".run-form", v.root).requestSubmit(); } },
  { group: "Table", label: "Search the results table", keys: "Ctrl F", when: () => !!agentHere(), run: () => $<HTMLInputElement>(".filter", agentHere()!.root).focus() },
  { group: "Table", label: "Refresh the results table", keys: "F5", when: () => !!agentHere(), run: () => loadSheet(agentHere()!, true) },
  { group: "Table", label: "Open the results in Excel", when: () => !!agentHere(), run: () => openWorkbook(agentHere()!.key) },
  { group: "Table", label: "Show all columns / fewer columns", when: () => !!agentHere(), run: () => $(".cols-btn", agentHere()!.root).click() },
  { group: "Setup", label: "Run the setup check", run: () => { show("setup"); checkPrereqs(); } },
  { group: "Theme", label: "Use the light theme", run: () => applyTheme("light") },
  { group: "Theme", label: "Use the dark theme", run: () => applyTheme("dark") },
  { group: "Theme", label: "Match my computer's theme", run: () => applyTheme("system") },
];

let palSel = 0;
let palItems: Action[] = [];
let palReturn: HTMLElement | null = null;

function renderPalette() {
  const q = $<HTMLInputElement>("#palette-input").value.toLowerCase().trim();
  palItems = ACTIONS.filter((a) => (!a.when || a.when()) && (!q || `${a.group} ${a.label}`.toLowerCase().includes(q)));
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  const list = $("#palette-list");
  if (!palItems.length) { list.replaceChildren(el("li", { class: "none", role: "option", "aria-disabled": "true" }, "No matching action")); return; }
  list.replaceChildren(...palItems.map((a, i) => {
    const li = el("li", { role: "option", id: `pal-${i}`, "aria-selected": String(i === palSel) });
    li.append(el("span", { class: "grp" }, a.group), el("span", {}, a.label));
    if (a.keys) li.append(el("kbd", {}, a.keys));
    li.addEventListener("click", () => runPalette(i));
    li.addEventListener("mousemove", () => { if (palSel !== i) { palSel = i; renderPalette(); } });
    return li;
  }));
  $("#palette-input").setAttribute("aria-activedescendant", `pal-${palSel}`);
  $(`#pal-${palSel}`)?.scrollIntoView({ block: "nearest" });
}
function openPalette() {
  const d = $<HTMLDialogElement>("#palette");
  if (d.open) return;
  palReturn = document.activeElement as HTMLElement;
  $<HTMLInputElement>("#palette-input").value = "";
  palSel = 0; renderPalette();
  d.showModal();
  $("#palette-input").focus();
}
function runPalette(i: number) {
  const a = palItems[i];
  const d = $<HTMLDialogElement>("#palette");
  palReturn = null;
  d.close();
  a?.run();
}
function wirePalette() {
  const d = $<HTMLDialogElement>("#palette");
  const input = $<HTMLInputElement>("#palette-input");
  input.addEventListener("input", () => { palSel = 0; renderPalette(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); palSel = (palSel + 1) % Math.max(1, palItems.length); renderPalette(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); palSel = (palSel - 1 + palItems.length) % Math.max(1, palItems.length); renderPalette(); }
    else if (e.key === "Enter") { e.preventDefault(); runPalette(palSel); }
  });
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  d.addEventListener("close", () => { palReturn?.focus?.(); palReturn = null; });
  $("#open-palette").onclick = openPalette;
}

// ---------- wiring ----------

function show(view: string) {
  currentView = view;
  document.querySelectorAll<HTMLElement>(".tab").forEach((t) => {
    if (t.dataset.view === view) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  });
  document.querySelectorAll<HTMLElement>(".view").forEach((s) => s.classList.toggle("hidden", s.id !== `view-${view}`));
  if (view === "history") void loadHistory(true);
  if (view === "logs") void loadLogs();
  if (view === "admin") void loadAdmin(); else closeLink();   // a one-time link never stays in the page after leaving the screen
  if (isAgent(view)) {
    store.set("view", view);
    const v = views[view];
    requestAnimationFrame(() => drawRows(v, true));
    if (v.status?.locked && !v.lockDismissed) $(".lock-dismiss", v.root).focus();
  }
}
function go(view: string) { show(view); $("#main").focus({ preventScroll: true }); }

function wireKeys() {
  document.addEventListener("keydown", (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && (e.key === "k" || e.key === "K")) { e.preventDefault(); openPalette(); return; }
    if (document.querySelector("dialog[open]")) return; // dialogs handle their own keys (Esc closes them)
    if (ctrl && e.key === "6" && historyAllowed()) { e.preventDefault(); go("history"); return; }
    if (ctrl && e.key === "5" && isAdminSession()) { e.preventDefault(); go("admin"); return; }
    if (ctrl && e.key === "7" && isAdminSession()) { e.preventDefault(); go("logs"); return; }
    if (ctrl && ["1", "2", "3", "4"].includes(e.key)) { e.preventDefault(); go(["candidate", "company", "account", "settings"][Number(e.key) - 1]); return; }
    const v = agentHere();
    if (!v) return;
    if (ctrl && (e.key === "f" || e.key === "F")) { e.preventDefault(); $<HTMLInputElement>(".filter", v.root).focus(); return; }
    if (e.key === "F5" || (ctrl && (e.key === "r" || e.key === "R"))) { e.preventDefault(); loadSheet(v, true); return; }
    if (e.key === "Escape" && document.activeElement?.classList.contains("filter") && v.filter) { e.preventDefault(); clearSearch(v); }
  });
}


// ---------- sign-in ----------
// Neon Auth decides who may use the app. While a company connection is set and nobody is signed in to a company, the sign-in screen covers
// the app (which is inert behind it) and the backend refuses real work with "Sign in first." Passwords go to the backend once and are never kept here.

type AuthStatus = AuthStatusLike;
let auth: AuthStatus = { enabled: false, server: "", state: "disabled", user: null, offline: false, offline_until: null, remembered: true, message: null };
let unlockWaiters: Array<() => void> = [];
let lastAuthCheck = 0;
let localNotice = "";   // a sentence the window itself wants shown on the sign-in screen ("Password changed..."); a re-check must not wipe it
const LAST_EMAIL_KEY = "rd.lastEmail";
const signedInNow = () => !auth.enabled || auth.state === "signed_in";

function setLocked(locked: boolean) {
  const login = $("#login");
  const wasLocked = !login.classList.contains("hidden");
  login.classList.toggle("hidden", !locked);
  document.querySelectorAll<HTMLElement>(".nav, main, .skip").forEach((e) => { e.toggleAttribute("inert", locked); e.setAttribute("aria-hidden", String(locked)); });
  if (locked && !wasLocked) {
    if (linkMode && linkMode !== "join") showLinkPanel(null);
    $<HTMLInputElement>("#login-password").value = "";
    const email = $<HTMLInputElement>("#login-email");
    if (!email.value) email.value = store.get(LAST_EMAIL_KEY) ?? "";
    requestAnimationFrame(() => (email.value ? $<HTMLInputElement>("#login-password") : email).focus());
  }
}

function showLoginError(text: string) {
  const box = $("#login-error");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
}

function renderAuth() {
  // Sidebar account box
  const inSession = auth.enabled && auth.state === "signed_in" && auth.user;
  $("#account-box").classList.toggle("hidden", !inSession);
  if (inSession && auth.user) {
    $("#account-email").textContent = auth.user.email;
    $("#account-email").title = auth.user.email;
    $("#account-role").textContent = roleLabel(auth.user.role);
    const tn = $("#account-tenant");
    tn.textContent = auth.user.tenant ?? "";
    tn.title = auth.user.tenant ?? "";
    tn.classList.toggle("hidden", !auth.user.tenant);
    $("#admin-intro").textContent = auth.user.tenant
      ? `Invite people to ${auth.user.tenant}, change who is an admin, and remove people. You only see and manage people in your own company.`
      : "Invite people, change who is an admin, and remove people from your company.";
    const off = $("#account-offline");
    off.classList.toggle("hidden", !(auth.offline && auth.offline_until));
    if (auth.offline && auth.offline_until) off.textContent = offlineText(auth.offline_until, Date.now());
  }
  renderAllowanceCard();
  // Search history belongs to everyone who is signed in to a company.
  $("#tab-history").classList.toggle("hidden", !historyAllowed());
  if (!historyAllowed() && currentView === "history") { clearHistory(); go("candidate"); }
  // The People tab belongs to admins; a person who loses admin rights while on that screen is taken off it.
  $("#tab-admin").classList.toggle("hidden", !isAdminSession());
  if (!isAdminSession() && currentView === "admin") { clearAdmin(); go("candidate"); }
  // So does the Logs tab.
  $("#tab-logs").classList.toggle("hidden", !isAdminSession());
  if (!isAdminSession() && currentView === "logs") { $("#logs-box").replaceChildren(); go("candidate"); }
  // Sign-in screen notice (why the person is signed out)
  const notice = $("#login-notice");
  const noticeText = loginNotice(localNotice, auth.message, linkMode !== null && linkMode !== "join");
  notice.textContent = noticeText;
  notice.classList.toggle("hidden", !noticeText);
  // Settings card
  $("#signin-summary").textContent = signInSummary(auth, Date.now());
  $("#signin-signout").classList.toggle("hidden", !inSession);
  renderRecords();
}

function applyAuth(next: AuthStatus) {
  const was = signedInNow();
  auth = next;
  renderAuth();
  const now = signedInNow();
  if (!now) { clearAdmin(); clearHistory(); }
  setLocked(!now);
  // Signed in, but no company yet: the sign-in screen asks for the invitation code.
  if (auth.enabled && auth.state === "no_company") { if (linkMode !== "join") showLinkPanel("join"); }
  else if (linkMode === "join") showLinkPanel(null);
  if (now && !was) {
    const waiting = unlockWaiters.splice(0);
    waiting.forEach((f) => f());
    void syncRecords();
    void syncHistory();
    void invoke("allowance_refresh").then(() => refreshStatus()).catch(() => {});   // the company's pool, read at once
    if (!waiting.length) { void refreshStatus(); AGENT_KEYS.forEach((k) => void loadSheet(views[k])); }   // signed in again after a lock
  }
}

/** Ask the backend to open or refresh the session (it reaches the server only when it must) and show the result. */
async function recheckAuth() {
  if (!auth.enabled) return;
  try { applyAuth(await invoke<AuthStatus>("auth_check")); lastAuthCheck = Date.now(); } catch { /* the next tick tries again */ }
}

/** At start: wait here until the person may use the app. Resolves at once when no sign-in server is set. */
async function authGate() {
  let st = await invoke<AuthStatus>("auth_status");
  if (!st.enabled) { applyAuth(st); return; }
  setLocked(true);
  $("#login-checking").classList.remove("hidden");
  $("#login-form").classList.add("hidden");
  $("#login-alt").classList.add("hidden");
  try { st = await invoke<AuthStatus>("auth_check"); lastAuthCheck = Date.now(); } catch { /* shown as signed out below */ }
  $("#login-checking").classList.add("hidden");
  $("#login-form").classList.remove("hidden");
  $("#login-alt").classList.remove("hidden");
  applyAuth(st);
  if (!signedInNow()) await new Promise<void>((resolve) => unlockWaiters.push(resolve));
}

async function submitLogin(ev: Event) {
  ev.preventDefault();
  const email = $<HTMLInputElement>("#login-email"), pw = $<HTMLInputElement>("#login-password");
  showLoginError("");
  localNotice = "";
  renderAuth();
  if (!email.value.trim()) { showLoginError("Enter your email address."); email.focus(); return; }
  if (!pw.value) { showLoginError("Enter your password."); pw.focus(); return; }
  const btn = $<HTMLButtonElement>("#login-submit");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const st = await invoke<AuthStatus>("auth_login", { email: email.value.trim(), password: pw.value });
    store.set(LAST_EMAIL_KEY, email.value.trim());
    pw.value = "";
    lastAuthCheck = Date.now();
    applyAuth(st);
    toast(`Signed in as ${st.user?.email ?? email.value.trim()}.`, "ok");
  } catch (e) {
    showLoginError(plainError(e));
    pw.select();
  } finally { btn.disabled = false; btn.textContent = "Sign in"; }
}

async function signOut() {
  const running = AGENT_KEYS.some((k) => views[k]?.running);
  const ok = await confirmAction("Sign out?", running
    ? "A search that is running keeps going, but you will need your email and password to see its results. Anything already saved stays on this computer."
    : "You will need your email and password to sign in again. Anything already saved stays on this computer.", "Sign out", "Stay signed in");
  if (!ok) return;
  try { applyAuth(await invoke<AuthStatus>("auth_logout")); } catch (e) { toast(plainError(e), "error"); }
}

function wireAuth() {
  $("#login-form").addEventListener("submit", submitLogin);
  $("#login-show").addEventListener("click", (e) => {
    const input = $<HTMLInputElement>("#login-password"), b = e.currentTarget as HTMLButtonElement;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    b.textContent = show ? "Hide" : "Show";
    b.setAttribute("aria-pressed", String(show));
    b.setAttribute("aria-label", show ? "Hide the password" : "Show the password");
    input.focus();
  });
  $("#account-signout").addEventListener("click", () => void signOut());
  $("#signin-signout").addEventListener("click", () => void signOut());
  // Re-check every 30 s while signed out (so it opens by itself once the network is back) and every 5 minutes while signed in.
  setInterval(() => {
    if (auth.enabled && Date.now() - lastAuthCheck > (signedInNow() ? 300_000 : 30_000)) void recheckAuth();
    if (auth.enabled && auth.state === "signed_in" && Date.now() - lastRecordsSync > 300_000) void syncRecords();   // also retries what could not be sent
    if (auth.enabled && auth.state === "signed_in" && Date.now() - lastHistorySync > 300_000) void syncHistory();
  }, 15_000);
}

// ---------- last records ----------
// Everything is processed on this computer. Each assistant leaves its newest record in a small file; while someone is signed in, the
// backend stores that one record on the sign-in server (and removes it again if the file is gone). Nothing is lost while offline.

let recordsResults: RecordsResult[] = [];
let recordsBusy = false;
let lastRecordsSync = 0;

function renderRecords() {
  const box = $("#records-box");
  const show = auth.enabled && auth.state === "signed_in" && (recordsResults.length > 0 || historyResult !== null);
  box.classList.toggle("hidden", !show);
  if (!show) return;
  $("#history-line").textContent = historyLine(historyResult);
  $("#live-line").textContent = lastLive ? `Live updates: this window asks the company database for changes (your allowance, History, People) every ${LIVE_MS / 1000} seconds while an assistant runs or this screen is open, and once a minute otherwise (last time ${new Date(lastLive).toLocaleTimeString()}).` : "";
  $("#records-list").replaceChildren(...recordsResults.map((r) => el("li", { class: r.state }, recordsLine(r))));
}

/** Ask the backend to bring the server's copy in step. Cheap when nothing changed; skipped when sign-in is off or nobody is signed in. */
async function syncRecords() {
  if (!auth.enabled || auth.state !== "signed_in" || recordsBusy) return;
  recordsBusy = true;
  try { recordsResults = await invoke<RecordsResult[]>("records_sync"); lastRecordsSync = Date.now(); renderRecords(); } catch { /* the next trigger tries again */ }
  finally { recordsBusy = false; }
}

function renderStored(rows: StoredRecord[]) {
  const box = $("#stored-box");
  if (!rows.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "No record has been stored yet.")); return; }
  const { table, body } = adminTable("The latest record of each assistant", ["Assistant", "Record", "Created", "Sent by"]);
  for (const r of rows) {
    const tr = el("tr");
    tr.append(el("td", { class: "who" }, AGENT_LABEL[r.agent] ?? r.agent), el("td", {}, recordSummary(r.agent, r.record)), el("td", {}, formatUtc(r.created_at)), el("td", {}, r.stored_by ?? "Unknown"));
    body.append(tr);
  }
  box.replaceChildren(table);
}


// ---------- invitation code and password reset (on the sign-in screen) ----------
// Creating an account needs the invitation code an admin handed over; a reset link comes by email. The app talks only to the company
// connection it is set up with, never to an address pasted into a link.

let linkMode: LinkMode | null = null;
const FIELD_CLASS: Record<LinkField, string> = { email: ".f-email", code: ".f-code", link: ".f-link", pw: ".f-pw" };

function showLinkPanel(mode: LinkMode | null) {
  linkMode = mode;
  const on = mode !== null;
  $("#link-form").classList.toggle("hidden", !on);
  $("#login-form").classList.toggle("hidden", on);
  $("#login-alt").classList.toggle("hidden", on);
  $("#login-title").textContent = mode ? LINK_TEXT[mode].title : "Sign in";
  $("#link-error").classList.add("hidden");
  renderAuth();   // the notice from an earlier session is hidden while this panel is open (except the "join your company" one)
  if (mode) {
    const x = LINK_TEXT[mode];
    $("#link-intro").textContent = x.intro;
    $("#link-submit").textContent = x.submit;
    (Object.keys(FIELD_CLASS) as LinkField[]).forEach((f) => document.querySelectorAll(`#link-form ${FIELD_CLASS[f]}`).forEach((e) => e.classList.toggle("hidden", !x.fields.includes(f))));
    for (const id of ["#link-input", "#link-code", "#newpw-1", "#newpw-2"]) $<HTMLInputElement>(id).value = "";
    const email = $<HTMLInputElement>("#link-email");
    email.value = mode === "create" || mode === "forgot" ? (store.get(LAST_EMAIL_KEY) ?? "") : "";
    $("#link-back").classList.toggle("hidden", mode === "join");
    $("#link-signout").classList.toggle("hidden", mode !== "join");
    $("#link-have").classList.toggle("hidden", mode !== "forgot");
    $("#link-sep").classList.toggle("hidden", mode === "join" || mode === "reset" || mode === "create");
    requestAnimationFrame(() => {
      const first = x.fields[0] === "email" ? email : x.fields[0] === "code" ? $<HTMLInputElement>("#link-code") : $<HTMLInputElement>("#link-input");
      first.focus();
    });
  } else {
    requestAnimationFrame(() => $<HTMLInputElement>("#login-email").focus());
  }
}

function linkError(text: string) {
  const box = $("#link-error");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
}

async function submitLink(ev: Event) {
  ev.preventDefault();
  if (!linkMode) return;
  const mode = linkMode, x = LINK_TEXT[mode];
  const email = $<HTMLInputElement>("#link-email"), code = $<HTMLInputElement>("#link-code"), link = $<HTMLInputElement>("#link-input");
  const a = $<HTMLInputElement>("#newpw-1"), b = $<HTMLInputElement>("#newpw-2");
  linkError("");
  if (x.fields.includes("email") && !validEmail(email.value)) { linkError("Enter a valid email address."); email.focus(); return; }
  if (x.fields.includes("code") && !looksLikeCode(code.value)) { linkError(code.value.trim() ? "That does not look like an invitation code. It is made of letters, numbers and dashes." : "Enter the invitation code you were given."); code.focus(); return; }
  if (x.fields.includes("link") && !link.value.trim()) { linkError("Paste the link from the reset email."); link.focus(); return; }
  if (x.fields.includes("pw")) {
    const problem = passwordProblem(a.value, b.value);
    if (problem) { linkError(problem); (a.value.length < 10 || a.value.length > 128 ? a : b).focus(); return; }
  }
  const btn = $<HTMLButtonElement>("#link-submit");
  btn.disabled = true; btn.textContent = x.busy;
  const clear = () => { for (const f of [code, link, a, b]) f.value = ""; };
  try {
    if (mode === "create") {
      const st = await invoke<AuthStatus>("auth_create_account", { email: email.value.trim(), password: a.value, code: code.value.trim() });
      store.set(LAST_EMAIL_KEY, email.value.trim());
      clear();
      showLinkPanel(null);
      lastAuthCheck = Date.now();
      applyAuth(st);
      if (st.state === "signed_in") toast("Your account is ready and you are signed in.", "ok");
    } else if (mode === "join") {
      const st = await invoke<AuthStatus>("auth_join_company", { code: code.value.trim() });
      clear();
      lastAuthCheck = Date.now();
      applyAuth(st);
      if (st.state === "signed_in") toast(`You joined ${st.user?.tenant ?? "your company"}.`, "ok");
    } else if (mode === "forgot") {
      const msg = await invoke<string>("auth_forgot_password", { email: email.value.trim() });
      showLinkPanel("reset");
      $("#link-intro").textContent = `${msg} ${LINK_TEXT.reset.intro}`;
    } else {
      const msg = await invoke<string>("auth_reset_password", { link: link.value, password: a.value });
      clear();
      showLinkPanel(null);
      localNotice = msg;
      renderAuth();
      toast(msg, "ok");
    }
  } catch (e) {
    linkError(plainError(e));
  } finally { btn.disabled = false; btn.textContent = x.submit; }
}

function wireLink() {
  $("#show-create").addEventListener("click", () => showLinkPanel("create"));
  $("#show-forgot").addEventListener("click", () => showLinkPanel("forgot"));
  $("#link-have").addEventListener("click", () => showLinkPanel("reset"));
  $("#link-back").addEventListener("click", () => showLinkPanel(null));
  $("#link-signout").addEventListener("click", async () => { try { applyAuth(await invoke<AuthStatus>("auth_logout")); } catch (e) { linkError(plainError(e)); } });
  $("#link-form").addEventListener("submit", submitLink);
  $("#newpw-show").addEventListener("click", (e) => {
    const show = $<HTMLInputElement>("#newpw-1").type === "password", b = e.currentTarget as HTMLButtonElement;
    for (const id of ["#newpw-1", "#newpw-2"]) $<HTMLInputElement>(id).type = show ? "text" : "password";
    b.textContent = show ? "Hide" : "Show";
    b.setAttribute("aria-pressed", String(show));
    b.setAttribute("aria-label", show ? "Hide the passwords" : "Show the passwords");
  });
}


// ---------- people and access (admins) ----------
// Every call goes through the backend, which holds the session and sends it to Neon Auth; the window never sees a cookie or a token.
// An invitation code is a secret until it is used: it is shown when created and from the waiting invitations, and cleared when the screen is left.

const isAdminSession = () => auth.enabled && auth.state === "signed_in" && auth.user?.role === "admin";
let adminBusy = false;

function adminError(text: string) {
  const box = $("#admin-error");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
}

function closeLink() {
  $("#link-box").classList.add("hidden");
  $<HTMLInputElement>("#link-url").value = "";
}

function clearAdmin() {
  closeLink();
  $("#users-box").replaceChildren();
  $("#invites-box").replaceChildren();
  $("#stored-box").replaceChildren();
  adminError("");
}

function showLink(title: string, text: string, url: string, warn: string) {
  $("#link-title").textContent = title;
  $("#link-text").textContent = text;
  $("#link-warn").textContent = warn;
  $<HTMLInputElement>("#link-url").value = url;
  const box = $("#link-box");
  box.classList.remove("hidden");
  $<HTMLInputElement>("#link-url").select();
  box.scrollIntoView({ block: "nearest" });
}

const pill = (text: string, cls = "") => el("span", { class: `pill ${cls}`.trim() }, text);
const company = () => auth.user?.tenant || "your company";

function adminTable(label: string, heads: string[]): { table: HTMLTableElement; body: HTMLTableSectionElement } {
  const table = el("table", { class: "admin-table", "aria-label": label });
  const head = el("tr");
  heads.forEach((h) => head.append(el("th", { scope: "col" }, h)));
  const thead = el("thead");
  thead.append(head);
  const body = el("tbody");
  table.append(thead, body);
  return { table, body };
}

function renderUsers(users: AdminUser[]) {
  const box = $("#users-box");
  if (!users.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "Nobody is in this company yet.")); return; }
  const { table, body } = adminTable("People in your company", ["Email", "Role", "Joined", ""]);
  for (const u of users) {
    const tr = el("tr");
    const who = el("td", { class: "who" }, u.email);
    if (u.you) who.append(el("span", { class: "you" }, "You"));
    const acts = el("div", { class: "acts" });
    for (const a of userActions(u, company())) {
      const b = button(a.label, a.kind === "remove" ? "risky" : "", () => void doUserAction(u, a));
      b.setAttribute("aria-label", a.aria);
      acts.append(b);
    }
    const actCell = el("td");
    actCell.append(acts);
    tr.append(who, el("td", {}), el("td", {}, formatUtc(u.joined_at, "")), actCell);
    tr.children[1].append(pill(roleLabel(u.role), u.role === "admin" ? "admin" : ""));
    body.append(tr);
  }
  box.replaceChildren(table);
}

function renderInvites(invites: AdminInvite[]) {
  const box = $("#invites-box");
  if (!invites.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "No invitation is waiting.")); return; }
  const { table, body } = adminTable("Invitations not yet used", ["Email", "Role", "Code works", ""]);
  for (const i of invites) {
    const tr = el("tr");
    const acts = el("div", { class: "acts" });
    const show = button("Show code", "", () => showInvite(i));
    show.setAttribute("aria-label", `Show the invitation code for ${i.email}`);
    const b = button("Withdraw", "", () => void withdrawInvite(i));
    b.setAttribute("aria-label", `Withdraw the invitation to ${i.email}`);
    acts.append(show, b);
    const actCell = el("td");
    actCell.append(acts);
    tr.append(el("td", { class: "who" }, i.email), el("td"), el("td", {}, expiresText(i.expires_at, Date.now())), actCell);
    tr.children[1].append(pill(roleLabel(i.role), i.role === "admin" ? "admin" : ""));
    body.append(tr);
  }
  box.replaceChildren(table);
}

function showInvite(i: { email: string; role: string; expires_at: string; code?: string; id?: string }) {
  const r: InviteResult = { email: i.email, role: i.role, expires_at: i.expires_at, code: i.code ?? i.id ?? "" };
  showLink(`Invitation code for ${r.email}`, inviteResultText(r, Date.now()), r.code, `Anyone who signs up with ${r.email} and this code joins ${company()} as ${roleLabel(r.role).toLowerCase()}. Hand it over privately.`);
}

async function loadAdmin(announce = false, keepError = false) {
  if (!isAdminSession()) return;
  if (!keepError) adminError("");
  try {
    const [users, invites, stored] = await Promise.all([invoke<AdminUser[]>("admin_members"), invoke<AdminInvite[]>("admin_invites"), invoke<StoredRecord[]>("records_stored")]);
    renderUsers(users);
    renderInvites(invites);
    renderStored(stored);
    if (announce) toast("List refreshed.", "ok");
  } catch (e) { adminError(plainError(e)); }
}

/** Run one admin change: ask first when it matters, say what happened, reload the lists. */
async function adminChange(work: () => Promise<unknown>, done: string) {
  if (adminBusy) return;
  adminBusy = true;
  adminError("");
  let failed = false;
  try { await work(); toast(done, "ok"); } catch (e) { failed = true; adminError(plainError(e)); } finally { adminBusy = false; }
  await loadAdmin(false, failed);   // refresh the lists either way, but do not wipe the reason a change was refused
}

async function doUserAction(u: AdminUser, a: UserAction) {
  if (a.confirm && !(await confirmAction(a.confirm.title, a.confirm.text, a.confirm.ok, "Cancel"))) return;
  if (a.kind === "role") {
    await adminChange(() => invoke("admin_set_role", { id: u.id, role: u.role === "admin" ? "user" : "admin" }), `${u.email} is now ${u.role === "admin" ? "a user" : "an admin"}.`);
  } else {
    await adminChange(() => invoke("admin_remove", { id: u.id }), `${u.email} was removed from ${company()}.`);
  }
}

async function withdrawInvite(i: AdminInvite) {
  if (!(await confirmAction("Withdraw this invitation?", `The code given to ${i.email} stops working at once.`, "Withdraw", "Keep it"))) return;
  await adminChange(() => invoke("admin_revoke_invite", { id: i.id }), `The invitation to ${i.email} was withdrawn.`);
}

async function submitInvite(ev: Event) {
  ev.preventDefault();
  const email = $<HTMLInputElement>("#invite-email"), role = $<HTMLSelectElement>("#invite-role");
  if (!validEmail(email.value)) { adminError("Enter a valid email address."); email.focus(); return; }
  const btn = $<HTMLButtonElement>("#invite-send");
  btn.disabled = true; btn.textContent = "Creating…";
  try {
    await adminChange(async () => {
      const r = await invoke<InviteResult>("admin_invite", { email: email.value.trim(), role: role.value });
      email.value = "";
      showInvite(r);
    }, "Invitation created.");
  } finally { btn.disabled = false; btn.textContent = "Create invitation code"; }
}

function wireAdmin() {
  $("#invite-form").addEventListener("submit", submitInvite);
  $("#admin-refresh").addEventListener("click", () => void loadAdmin(true));
  $("#link-close").addEventListener("click", closeLink);
  $("#link-copy").addEventListener("click", async () => {
    const url = $<HTMLInputElement>("#link-url").value;
    if (await copyText(url)) toast("Code copied.", "ok"); else { $<HTMLInputElement>("#link-url").select(); toast("Could not copy. Select the code and copy it.", "error"); }
  });
}


// ---------- the allowance card (admins) ----------
// The cap and the window are the company's and apply to each person separately, counted over all the assistants. Admins set them here
// and see everyone's usage; everyone else sees their own in the meters at the top of each assistant. The numbers are checked here and
// again by the database.

let allowancePeople: { email: string; counted: number; cap: number; window_minutes: number; window_started_ms: number | null; locked_at_ms: number | null }[] = [];
let allowancePeopleAt = 0;
let allowancePeopleBusy = false;
const allowanceDirty = new Set<string>();   // people whose boxes the admin is editing: a refresh must not overwrite what is being typed

function allowanceError(text: string) {
  const box = $("#allowance-error");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
}

async function loadAllowancePeople(force = false) {
  if (allowancePeopleBusy || (!force && Date.now() - allowancePeopleAt < 20_000)) return;
  allowancePeopleBusy = true;
  try {
    const r = await invoke<{ people: typeof allowancePeople }>("allowance_people");
    allowancePeople = r.people; allowancePeopleAt = Date.now();
    renderAllowanceCard();
  } catch { /* shown again at the next refresh */ }
  finally { allowancePeopleBusy = false; }
}

function renderAllowanceCard() {
  const statuses = AGENT_KEYS.map((k) => views[k]?.status).filter((x): x is Status => !!x);
  const show = isAdminSession() && statuses.length > 0 && statuses.every((x) => x.shared);
  const card = $("#card-allowance");
  card.classList.toggle("hidden", !show);
  if (!show) { $("#allowance-people").replaceChildren(); return; }
  $("#allowance-intro").textContent = allowanceIntro(company());
  renderAllowancePeople();
  void loadAllowancePeople();
}

function renderAllowancePeople() {
  const box = $("#allowance-people");
  if (!allowancePeople.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "Nobody has joined yet.")); return; }
  const list = el("div", { class: "al-list" });
  for (const p of allowancePeople) {
    const row = el("div", { class: "allowance-row" });
    row.dataset.email = p.email;
    const cap = el("input", { type: "text", inputmode: "numeric", class: "al-cap", "aria-label": `Cap in tokens for ${p.email}` }) as HTMLInputElement;
    const hours = el("input", { type: "text", inputmode: "decimal", class: "al-hours", "aria-label": `Window in hours for ${p.email}` }) as HTMLInputElement;
    cap.value = String(p.cap); hours.value = String(p.window_minutes / 60);
    // keep what the admin is typing for this person across refreshes
    const prev = box.querySelector<HTMLElement>(`[data-email="${CSS.escape(p.email)}"]`);
    if (prev && allowanceDirty.has(p.email)) { cap.value = prev.querySelector<HTMLInputElement>(".al-cap")!.value; hours.value = prev.querySelector<HTMLInputElement>(".al-hours")!.value; }
    for (const i of [cap, hours]) i.addEventListener("input", () => allowanceDirty.add(p.email));
    const save = button("Save", "primary", () => void saveAllowance(p.email, cap, hours));
    save.setAttribute("aria-label", `Save the allowance of ${p.email}`);
    const reset = button("Reset count", "risky", () => void resetAllowance(p.email));
    reset.setAttribute("aria-label", `Empty the count of ${p.email} now`);
    const acts = el("div", { class: "acts" }); acts.append(save, reset);
    const l1 = el("label", {}, "Cap (tokens)"), l2 = el("label", {}, "Window (hours)");
    l1.append(cap); l2.append(hours);
    const used = `${short(p.counted)} of ${short(p.cap)} used${p.locked_at_ms && p.counted >= p.cap ? " (used up)" : ""}`;
    row.append(el("strong", { class: "al-name" }, p.email), l1, l2, el("span", { class: "al-used" }, used), acts);
    list.append(row);
  }
  const all = button("Reset everyone's count", "risky", () => void resetAllowance(null));
  all.setAttribute("aria-label", "Empty everyone's count now");
  box.replaceChildren(list, all);
}

async function saveAllowance(email: string, cap: HTMLInputElement, hours: HTMLInputElement) {
  allowanceError("");
  const parsed = allowanceInput(cap.value, hours.value);
  if (typeof parsed === "string") { allowanceError(parsed); return; }
  try {
    await invoke("allowance_set_user", { email, cap: parsed.cap, minutes: parsed.minutes });
    allowanceDirty.delete(email);
    toast(`The allowance of ${email} was saved.`, "ok");
    await refreshStatus();
    await loadAllowancePeople(true);
  } catch (e) { allowanceError(plainError(e)); }
}

async function resetAllowance(email: string | null) {
  allowanceError("");
  const who = email ?? `everyone in ${company()}`;
  if (!(await confirmAction("Reset this allowance now?", email
      ? `${email}'s count goes back to zero and their window starts again with their next tokens. A lock is lifted.`
      : `The count of everyone in ${company()} goes back to zero and each window starts again with the next tokens. Locks are lifted.`, "Reset now", "Keep it"))) return;
  try {
    await invoke("allowance_reset", { email });
    toast(`The allowance of ${who} was reset.`, "ok");
    await refreshStatus();
    await loadAllowancePeople(true);
  } catch (e) { allowanceError(plainError(e)); }
}


// ---------- logs (admins) ----------
// What the app wrote about itself, from everyone in the company. The database lets only owners and admins read it.

interface LogLine { id: number; user_email: string | null; kind: string; level: string; agent: string | null; message: string; at: string }
const LOG_KIND: Record<string, string> = { event: "Event", run: "Run", signin: "Sign-in" };
const LOG_LEVEL: Record<string, string> = { info: "Information", warn: "Warning", error: "Error" };

async function loadLogs() {
  if (!isAdminSession()) return;
  const err = $("#logs-error");
  try {
    const lines = await invoke<LogLine[]>("logs_list", { kind: $<HTMLSelectElement>("#logs-kind").value || null, level: $<HTMLSelectElement>("#logs-level").value || null, email: $<HTMLInputElement>("#logs-person").value.trim() || null });
    err.classList.add("hidden");
    const box = $("#logs-box");
    if (!lines.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "No log lines match.")); return; }
    const table = el("table", { class: "admin-table logs-table" });
    const head = el("tr"); for (const h of ["Time", "Person", "Kind", "Level", "Assistant", "Message"]) head.append(el("th", {}, h));
    const thead = el("thead"); thead.append(head); table.append(thead);
    const body = el("tbody");
    for (const l of lines) {
      const tr = el("tr");
      tr.append(el("td", { class: "lg-time" }, formatUtc(l.at, "")), el("td", {}, l.user_email ?? ""), el("td", {}, LOG_KIND[l.kind] ?? l.kind),
        el("td", {}, LOG_LEVEL[l.level] ?? l.level), el("td", {}, l.agent ? assistantName(l.agent as AgentKey) : ""), el("td", { class: "lg-msg" }, l.message));
      body.append(tr);
    }
    table.append(body);
    box.replaceChildren(table);
  } catch (e) { err.textContent = plainError(e); err.classList.remove("hidden"); }
}

function wireLogs() {
  $("#logs-refresh").addEventListener("click", () => void loadLogs());
  for (const id of ["#logs-kind", "#logs-level"]) $(id).addEventListener("change", () => void loadLogs());
  $("#logs-person").addEventListener("change", () => void loadLogs());
}

// ---------- search history ----------
// Each person's searches and their result rows are stored in the company's database; the database decides who sees what: a person sees
// only their own, an admin sees everyone's in the company. There is no switch for it: it is how the app works while signed in.

const historyAllowed = () => auth.enabled && auth.state === "signed_in";
let historyResult: HistoryResult | null = null;
let historyBusy = false;
let lastHistorySync = 0;
const historyOpen = new Set<string>();

function clearHistory() {
  $("#history-box").replaceChildren();
  $("#history-error").classList.add("hidden");
  historyOpen.clear();
}

function historyError(text: string) {
  const box = $("#history-error");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
}

/** Ask the backend to store what is pending (searches, queued rows, erasures). Cheap when nothing changed. */
async function syncHistory() {
  if (!historyAllowed() || historyBusy) return;
  historyBusy = true;
  try { historyResult = await invoke<HistoryResult>("history_sync"); lastHistorySync = Date.now(); renderRecords(); } catch { /* the next trigger tries again */ }
  finally { historyBusy = false; }
}

// ---------- updates ----------
// A newer signed release on GitHub is offered, never installed without a click, and never while an assistant is running.

interface UpdateInfo { available: boolean; version: string; notes: string; message: string }
let updateOffered = "";
let updateDismissed = "";
let pendingUpdate: UpdateInfo | null = null;
let autoFailed = "";   // a version whose automatic install failed: not retried by itself, the person can press the button
let autoTimer: number | undefined;
const AUTO_UPDATE_SECONDS = 15;

// A new version installs on its own, but only at a quiet moment: no assistant running and nothing typed into a run form. A countdown is
// shown first and "Later" cancels it. The person can switch it off in About (kept in this window's storage, on by default).
const autoUpdateOn = () => { try { return localStorage.getItem("rd-auto-update") !== "off"; } catch { return true; } };
const setAutoUpdate = (on: boolean) => { try { localStorage.setItem("rd-auto-update", on ? "on" : "off"); } catch { /* the choice is then not remembered */ } };

function quietForUpdate(): boolean {
  if (AGENT_KEYS.some((k) => views[k].running)) return false;
  return AGENT_KEYS.every((k) => Object.values(views[k].getInput()).every((v) => typeof v !== "string" || v.trim() === ""));
}

function cancelAutoInstall() {
  if (autoTimer !== undefined) { clearTimeout(autoTimer); autoTimer = undefined; }
  $("#update-count").textContent = "";
}

function maybeAutoInstall() {
  const u = pendingUpdate;
  if (!u?.available || !autoUpdateOn() || autoTimer !== undefined || u.version === updateDismissed || u.version === autoFailed) return;
  if (!quietForUpdate()) return;   // looked at again a minute later
  let left = AUTO_UPDATE_SECONDS;
  const tick = () => {
    autoTimer = undefined;
    if (!quietForUpdate() || u.version === updateDismissed) { $("#update-count").textContent = ""; return; }   // work started: it waits
    if (left <= 0) { $("#update-count").textContent = ""; void installUpdate(); return; }
    $("#update-count").textContent = `Installing in ${left} ${left === 1 ? "second" : "seconds"}.`;
    left -= 1;
    autoTimer = window.setTimeout(tick, 1000);
  };
  tick();
}

function showUpdate(u: UpdateInfo | null) {
  pendingUpdate = u?.available ? u : null;
  if (!u?.available) cancelAutoInstall();
  const banner = $("#update-banner");
  const show = !!u?.available && u.version !== updateDismissed;
  banner.classList.toggle("hidden", !show);
  if (!u?.available) return;
  updateOffered = u.version;
  const first = u.notes.split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  $("#update-text").textContent = `Version ${u.version} is available.${first ? ` ${first}` : ""}`;
  const busy = AGENT_KEYS.some((k) => views[k].running);
  $<HTMLButtonElement>("#update-install").disabled = busy;
  $<HTMLButtonElement>("#update-install").title = busy ? "Update when the assistant has finished." : "";
  maybeAutoInstall();
}

async function checkUpdate(manual: boolean) {
  const line = $("#update-line");
  if (manual) line.textContent = "Checking…";
  try {
    const u = await invoke<UpdateInfo>("update_check");
    showUpdate(u);
    if (manual) { updateDismissed = ""; autoFailed = ""; showUpdate(u); line.textContent = u.available ? `Version ${u.version} is available.` : u.message; }
  } catch (e) { if (manual) line.textContent = plainError(e); }
}

async function installUpdate() {
  const b = $<HTMLButtonElement>("#update-install");
  b.disabled = true;
  cancelAutoInstall();
  $("#update-text").textContent = "Downloading the update. Recruit Desk restarts when it is installed…";
  try { await invoke("update_install"); }
  catch (e) { autoFailed = updateOffered; toastError(e); showUpdate({ available: true, version: updateOffered, notes: "", message: "" }); }
}

// ---------- live updates ----------
// The company database cannot push changes to the app, so while the window is open and someone is signed in, the app asks for them every
// few seconds: the person's allowance count and the screen that is open (History, People). The Excel results are not part of it: they
// stay in the data folder on this computer. A hidden window asks nothing until it is shown again.

const LIVE_MS = 10_000;
const LIVE_QUIET_MS = 60_000;   // on an assistant screen with nothing running, once a minute is enough
let lastLive = 0;
let liveBusy = false;

// Quick (every 10 seconds) only where changes made elsewhere matter: while an assistant runs, and on History, People, Logs and Settings.
// Otherwise the database is asked once a minute, so an idle window does not keep it awake all day. Showing or focusing the window asks at once.
const liveIsQuick = () => AGENT_KEYS.some((k) => views[k].running) || ["history", "admin", "logs", "settings"].includes(currentView);

async function liveTick(force = false) {
  if (liveBusy || (document.hidden && !force) || !auth.enabled || auth.state !== "signed_in") return;
  if (!force && !liveIsQuick() && Date.now() - lastLive < LIVE_QUIET_MS) return;
  liveBusy = true;
  try {
    await invoke("allowance_refresh").catch(() => {});
    await refreshStatus();
    if (currentView === "history") await loadHistory(false);
    if (currentView === "admin" && !adminBusy) await loadAdmin();
    if (currentView === "logs") await loadLogs();
    if (currentView === "settings") await loadAllowancePeople(true);
    lastLive = Date.now();
    renderRecords();
  } catch { /* the next tick tries again */ }
  finally { liveBusy = false; }
}

function renderHistoryRows(cell: HTMLElement, rows: HistoryRow[]) {
  if (!rows.length) { cell.replaceChildren(el("p", { class: "admin-empty" }, "No results were stored for this search.")); return; }
  const admin = isAdminSession();
  const list = el("ul", { class: "history-rows" });
  for (const r of rows) {
    const li = el("li");
    li.append(el("span", { class: "hr-text" }, recordSummary(r.agent, r.record) || "(no details)"));
    li.append(el("span", { class: "hr-meta" }, [formatUtc(r.created_at, ""), admin && r.who ? r.who : ""].filter(Boolean).join(" · ")));
    list.append(li);
  }
  cell.replaceChildren(list);
}

async function toggleHistoryRows(s: HistorySearch, detail: HTMLElement, button: HTMLButtonElement) {
  const open = !detail.classList.contains("hidden");
  if (open) { detail.classList.add("hidden"); historyOpen.delete(s.id); button.textContent = "Show results"; button.setAttribute("aria-expanded", "false"); return; }
  detail.classList.remove("hidden");
  historyOpen.add(s.id);
  button.textContent = "Hide results";
  button.setAttribute("aria-expanded", "true");
  const cell = detail.firstElementChild as HTMLElement;
  cell.replaceChildren(el("p", { class: "muted" }, "Loading…"));
  try { renderHistoryRows(cell, await invoke<HistoryRow[]>("history_rows", { searchId: s.id })); } catch (e) { cell.replaceChildren(el("p", { class: "admin-error" }, plainError(e))); }
}

function renderHistory(list: HistorySearch[]) {
  const box = $("#history-box");
  if (!list.length) { box.replaceChildren(el("p", { class: "admin-empty" }, "No search has been stored yet. A search you run shows up here a moment after it starts.")); return; }
  const admin = isAdminSession();
  const me = auth.user?.email.toLowerCase();
  const heads = ["When", ...(admin ? ["Person"] : []), "Assistant", "Searched for", "Status", "Results", ""];
  const { table, body } = adminTable("Searches", heads);
  for (const s of list) {
    const mine = (s.who ?? "").toLowerCase() === me;
    const tr = el("tr");
    tr.append(el("td", {}, formatUtc(s.started_at, "")));
    if (admin) { const who = el("td", { class: "who" }, s.who ?? "Unknown"); if (mine) who.append(el("span", { class: "you" }, "You")); tr.append(who); }
    tr.append(el("td", {}, assistantName(s.agent)), el("td", { class: "searched" }, searchSummary(s)), el("td", {}), el("td", {}, resultsText(s.rows)));
    const statusCell = tr.children[admin ? 4 : 3];
    statusCell.append(pill(statusLabel(s.status), s.status === "done" ? "on" : s.status === "running" ? "admin" : "offp"));
    const detail = el("tr", { class: "history-detail hidden" });
    const dcell = el("td", { colspan: String(heads.length) });
    detail.append(dcell);
    const acts = el("div", { class: "acts" });
    const show = button(s.rows ? "Show results" : "No results", "", () => void toggleHistoryRows(s, detail, show));
    show.setAttribute("aria-expanded", "false");
    show.setAttribute("aria-label", `Show the results of the search for ${searchSummary(s)}`);
    if (!s.rows) show.disabled = true;
    const rm = button("Remove", "risky", () => void removeSearch(s, mine));
    rm.setAttribute("aria-label", `Remove the search for ${searchSummary(s)}${admin && s.who ? ` by ${s.who}` : ""}`);
    acts.append(show, rm);
    const actCell = el("td");
    actCell.append(acts);
    tr.append(actCell);
    body.append(tr, detail);
    if (historyOpen.has(s.id) && s.rows) void toggleHistoryRows(s, detail, show);
  }
  box.replaceChildren(table);
}

async function loadHistory(sync = false, announce = false) {
  if (!historyAllowed()) return;
  historyError("");
  $("#history-intro").textContent = historyIntro(isAdminSession(), company());
  if (sync) await syncHistory();   // the person's newest search and rows first
  try {
    renderHistory(await invoke<HistorySearch[]>("history_list"));
    if (announce) toast("List refreshed.", "ok");
  } catch (e) { historyError(plainError(e)); $("#history-box").replaceChildren(); }
}

async function removeSearch(s: HistorySearch, mine: boolean) {
  if (!(await confirmAction("Remove this search?", removeSearchText(s, mine), "Remove", "Keep it"))) return;
  try { await invoke("history_delete", { searchId: s.id }); historyOpen.delete(s.id); toast("The search was removed.", "ok"); } catch (e) { historyError(plainError(e)); }
  await loadHistory(false);
}

function wireHistory() {
  $("#history-refresh").addEventListener("click", () => void loadHistory(true, true));
}


async function main() {
  initTheme();
  wireAuth();
  wireLink();
  wireAdmin();
  wireHistory();
  wireLogs();
  views.candidate = buildView("candidate");
  views.company = buildView("company");
  views.account = buildView("account");
  document.querySelectorAll<HTMLElement>(".tab").forEach((t) => (t.onclick = () => go(t.dataset.view!)));
  document.querySelectorAll<HTMLElement>(".save-btn").forEach((b) => (b.onclick = saveSettings));
  for (const which of ["exa", "apollo"] as const) {
    const input = $<HTMLInputElement>(`#set-${which}`), btn = $<HTMLButtonElement>(`#save-${which}`);
    btn.onclick = () => busy(btn, async () => { await saveKey(which, input); keyHints(); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); btn.click(); } });
  }
  $("#open-setup").onclick = () => { show("setup"); checkPrereqs(); };
  $("#setup-recheck").onclick = () => busy($<HTMLButtonElement>("#setup-recheck"), async () => { await checkPrereqs(); });
  $("#setup-continue").onclick = () => go(lastView());
  wirePalette();
  wireKeys();

  await listen<{ agent: AgentKey; kind: string; text: string }>("run-line", (p) => {
    const v = views[p.agent];
    const text = p.text.trim();
    if (!text) return;
    if (p.kind === "checkpoint") onCheckpoint(v, text);
    if (p.kind === "warn") { v.warns += /denied/i.test(text) ? 1 : 0; if (/not been trusted/i.test(text)) v.trustWarn = true; }
    logLine(v, p.kind, p.kind === "result" || p.kind === "error" ? text : p.text);
  });
  await listen<Status>("usage", applyStatus);
  await listen<{ agent: AgentKey; status: string; text: string; counted: number }>("run-ended", (p) => {
    const v = views[p.agent];
    v.running = false;
    v.steps.forEach((s) => { if (s.state === "active") s.state = p.status === "done" ? "done" : "failed"; });
    renderSteps(v);
    logLine(v, p.status === "done" ? "cp-done" : "warn", `${p.text} Counted tokens: ${fmt(p.counted)}.`);
    showResult(v, p.status, p.counted);
    if (currentView !== v.key) toast(p.status === "done" ? `${TEXT[v.key].nav}: finished.` : `${TEXT[v.key].nav}: the ${TEXT[v.key].task} ended early.`, p.status === "done" ? "ok" : "error");
    loadSheet(v);
    refreshStatus();
    void syncRecords();   // a run that ended may have created a new record
    void syncHistory();   // ...and new result rows, and the search itself has ended
  });

  await authGate();
  void syncRecords();   // a session restored at start-up has no sign-in event to trigger it
  void syncHistory();
  $("#open-data-folder").addEventListener("click", async () => {
    const err = $("#data-folder-error");
    err.classList.add("hidden");
    try { await invoke("open_data_folder"); } catch (e) { err.textContent = plainError(e); err.classList.remove("hidden"); }
  });
  $("#update-check").addEventListener("click", () => void checkUpdate(true));
  $("#update-install").addEventListener("click", () => void installUpdate());
  $("#update-later").addEventListener("click", () => { updateDismissed = updateOffered; cancelAutoInstall(); $("#update-banner").classList.add("hidden"); });
  const auto = $<HTMLInputElement>("#update-auto");
  auto.checked = autoUpdateOn();
  auto.addEventListener("change", () => { setAutoUpdate(auto.checked); if (auto.checked) maybeAutoInstall(); else cancelAutoInstall(); });
  setInterval(maybeAutoInstall, 60_000);   // an update that waited for a quiet moment is tried again every minute
  setTimeout(() => void checkUpdate(false), 5_000);
  setInterval(() => void liveTick(), LIVE_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void liveTick(true); });
  window.addEventListener("focus", () => void liveTick(true));
  setInterval(() => void checkUpdate(false), 3_600_000);
  await loadSettings();
  show(lastView());
  await refreshStatus();
  await Promise.all(AGENT_KEYS.map((k) => loadSheet(views[k])));
  const checks = await checkPrereqs();
  if (checks.some(needsWork)) show("setup");
  setInterval(refreshStatus, 5000);
}

function lastView(): string {
  const v = store.get("view");
  return v && isAgent(v) ? v : "candidate";
}

main();
export {};
