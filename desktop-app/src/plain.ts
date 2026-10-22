// Author: kaustubh.s.v
// Turns the agents' step result lines (machine-style counts) into short sentences a recruiter can read.
// The raw line is still kept in the technical log under "Show details".

export type AgentKey = "candidate" | "company" | "account";

const num = (s: string, re: RegExp): number | null => {
  const m = s.match(re);
  return m ? Number(m[1]) : null;
};
const n = (x: number, one: string, many = one + "s") => `${x.toLocaleString("en-US")} ${x === 1 ? one : many}`;
const host = (s: string) => s.replace(/https?:\/\/(?:www\.)?([^/\s,;)]+)[^\s,;)]*/g, "$1");
const sentence = (s: string) => {
  const t = s.trim().replace(/\s+/g, " ");
  if (!t) return t;
  return t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".");
};

// Words the agents use that a recruiter would not.
type Swap = string | ((m: string, ...g: string[]) => string);
const WORDS: [RegExp, Swap][] = [
  [/\bATS board\(s\)/gi, "job boards"], [/\bATS\b/g, "job board"], [/\bJD\b/g, "job description"],
  [/\bURLs blocked by robots\b/gi, "pages the site asked us not to read"], [/\bblocked by robots\b/gi, "the site asked us not to read"],
  [/\bExa search\b/gi, "web search"], [/\bExa\b/g, "web"], [/\brows?\b/gi, (m) => (m.toLowerCase() === "row" ? "entry" : "entries")],
  [/\binserted\b/gi, "added"], [/\bdeduplicat\w*/gi, "duplicate check"], [/\bmemory\b/gi, "the saved list"],
  [/\bUnverified\b/g, "not yet confirmed"], [/\bStale\b/g, "possibly out of date"], [/\bmismatch(es)?\b/gi, "difference$1"],
];

function words(s: string): string {
  let t = s;
  for (const [re, to] of WORDS) t = typeof to === "string" ? t.replace(re, to) : t.replace(re, to);
  return t;
}

/** Generic clean-up: hosts instead of URLs, Key=N lists as words, zero counts dropped, jargon replaced. */
export function cleanLine(raw: string): string {
  let t = host(raw)
    .replace(/^sheet '[^']+':\s*/i, "")
    .replace(/\((\w+=\d+(?:,\s*\w+=\d+)*)\)/g, (_, inner: string) =>
      "(" + inner.split(/,\s*/).map((p) => p.split("=")).filter(([, v]) => v !== "0").map(([k, v]) => `${v} ${k}`).join(", ") + ")")
    .replace(/\(\)/g, "");
  const parts = t.split(/\s*[;·]\s*|,\s+(?=\d)/).map((p) => p.trim()).filter(Boolean);
  const kept = parts.filter((p) => !/^0 \D/.test(p));
  t = (kept.length ? kept : parts).join(", ");
  return sentence(words(t));
}

/** Shown when step 3 could not reach any web search tool (no Exa access). The scan still finishes. */
export const NO_WEB_SEARCH = "Skipped: web search is not available, so LinkedIn, Indeed and job boards were not searched.";

function company(step: number, r: string): string | null {
  const skipped = r.match(/^skipped:?\s*(.*)$/i);
  if (skipped) {
    if (/careers page gave (\d+)/i.test(r)) return `Skipped: the careers page already listed ${n(num(r, /gave (\d+)/i)!, "job")}.`;
    if (/web search unavailable|no exa access/i.test(r)) return NO_WEB_SEARCH;
    if (/public posts not requested|no public rows file/i.test(r)) return "Skipped: LinkedIn, Indeed and job boards were not searched (turned off for this check).";
    return `Skipped${skipped[1] ? ": " + cleanLine(skipped[1]).replace(/\.$/, "") : ""}.`;
  }
  switch (step) {
    case 1: {
      const m = r.match(/^(\S+)\s+confirmed[^(]*\(([^)]+)\)/i);
      if (m) {
        const careers = /careers page (unknown|not found)/i.test(r) ? " Its careers page is not known yet." : "";
        return `Confirmed the company: ${m[2]} (${host(m[1])}).${careers}`;
      }
      return null;
    }
    case 2: {
      const pages = num(r, /(\d+) pages? crawled/i), roles = num(r, /(\d+) roles? found/i), boards = num(r, /(\d+) ATS board/i);
      const blocked = num(r, /(\d+) URLs? blocked/i);
      if (pages === null && roles === null) return null;
      const bits: string[] = [];
      if (pages !== null) bits.push(`Read ${n(pages, "page")} of the website`);
      if (roles !== null) bits.push(`found ${n(roles, "job")}${boards ? ` (${n(boards, "job board")} checked)` : ""}`);
      let s = bits.join(" and ") + ".";
      if (blocked) s += ` Skipped ${n(blocked, "page")} the site asked us not to read.`;
      return sentence(s);
    }
    case 3: {
      const kept = num(r, /(\d+) (?:public )?listings?(?:\(s\))? (?:kept|from)/i);
      if (kept !== null) {
        const dropped = num(r, /(\d+) dropped/i);
        return `Found ${n(kept, "public job listing")} on LinkedIn, Indeed and job boards` + (dropped ? `; left out ${n(dropped, "other result")}.` : ".");
      }
      const posts = num(r, /(\d+) (?:public )?(?:hiring )?posts?/i);
      return posts !== null ? `Found ${n(posts, "public hiring post")}.` : null;
    }
    case 4: {
      const total = num(r, /company total (\d+)/i);
      if (total === null) return null;
      const by = (k: string) => num(r, new RegExp(`${k}=(\\d+)`)) ?? 0;
      const open = by("Open"), unv = by("Unverified"), stale = by("Stale"), closed = by("Closed");
      const parts = [open && `${open} open`, unv && `${unv} not yet confirmed`, stale && `${stale} possibly out of date`, closed && `${closed} closed`].filter(Boolean);
      const fresh = num(r, /(\d+) new\b/i) ?? 0, upd = num(r, /(\d+) updated/i) ?? 0, dup = num(r, /(\d+) duplicates?/i) ?? 0;
      const changes = [fresh ? `${fresh} new` : "no new jobs", upd && `${upd} updated`, dup && `${dup} duplicate${dup === 1 ? "" : "s"} removed`].filter(Boolean);
      return `${n(total, "job")} on file${parts.length ? `: ${parts.join(", ")}` : ""}. This check: ${changes.join(", ")}.`;
    }
    case 5: {
      const ins = num(r, /(\d+) inserted/i), upd = num(r, /(\d+) updated/i), tot = num(r, /(\d+) rows in sheet/i);
      if (ins === null && upd === null) return null;
      const ch = [ins ? `${ins} added` : "", upd ? `${upd} updated` : ""].filter(Boolean).join(", ") || "no changes";
      return `Saved to Excel${tot !== null ? ` (${n(tot, "job")} on the sheet)` : ""}: ${ch}.`;
    }
    case 6: {
      const checked = num(r, /(\d+) rows checked/i), mis = num(r, /(\d+) mismatch/i);
      if (checked === null) return null;
      return mis ? `Double-checked ${n(checked, "job")}: ${mis} need a second look.` : `Double-checked ${n(checked, "job")}: all match.`;
    }
  }
  return null;
}

function candidate(step: number, r: string): string | null {
  if (/^skipped\b/i.test(r)) return `Skipped: ${cleanLine(r.replace(/^skipped:?\s*/i, "")).replace(/\.$/, "")}.`;
  switch (step) {
    case 1: {
      const must = num(r, /(\d+) mandatory/i), nice = num(r, /(\d+) preferred/i);
      if (must === null && nice === null) return null;
      const bits = [must !== null && n(must, "must-have skill"), nice !== null && n(nice, "nice-to-have skill")].filter(Boolean);
      return `Read the job: ${bits.join(" and ")}.`;
    }
    case 2: {
      const reused = r.match(/reused[^(]*\((\d+) rows?/i);
      if (reused) return `Reused ${n(Number(reused[1]), "profile")} found earlier${/no new/i.test(r) ? " (no new web search)" : ""}.`;
      const found = num(r, /(\d+) (?:candidates?|profiles?|results?|rows?)/i);
      return found !== null ? `Found ${n(found, "possible candidate")}.` : null;
    }
    case 3: {
      const scored = num(r, /(\d+) scored/i), top = num(r, /top (\d+)/i), low = num(r, /(\d+) Low confidence/i);
      const capped = num(r, /(\d+) capped/i);
      if (scored === null) return null;
      let s = `Scored ${n(scored, "candidate")}`;
      if (top !== null) s += `. Best match: ${top} out of 100`;
      s += ".";
      if (capped) s += ` ${capped} missed a must-have skill.`;
      if (low) s += ` ${low} ${low === 1 ? "has" : "have"} little public evidence.`;
      return s;
    }
    case 4: {
      const kept = num(r, /(\d+) kept/i), merged = num(r, /(\d+) merged/i), known = num(r, /(\d+) already in memory/i);
      if (kept === null) return null;
      let s = `Kept ${n(kept, "candidate")}; ${merged ? `${merged} duplicate${merged === 1 ? "" : "s"} merged` : "no duplicates"}.`;
      if (known) s += ` ${known} ${known === 1 ? "was" : "were"} already on file and ${known === 1 ? "was" : "were"} refreshed.`;
      return s;
    }
    case 5: {
      const now = num(r, /now (\d+) rows?/i), ins = num(r, /(\d+) inserted/i), upd = num(r, /(\d+) updated/i);
      if (now === null && ins === null) return null;
      const ch = [ins ? `${ins} new` : "", upd ? `${upd} updated` : ""].filter(Boolean).join(", ") || "no changes";
      return `Saved to Excel${now !== null ? `: ${n(now, "candidate")} on the sheet` : ""} (${ch}).`;
    }
    case 6: {
      const c = num(r, /(\d+) candidates?/i), top = num(r, /top (\d+)/i);
      if (c === null) return null;
      return `Finished with ${n(c, "candidate")}${top !== null ? `; best match ${top}` : ""}.`;
    }
  }
  return null;
}

/** Account research (8 steps): one row of business-development facts per company. */
function account(step: number, r: string): string | null {
  const skipped = r.match(/^skipped:?\s*(.*)$/i);
  if (skipped && step !== 3) return `Skipped${skipped[1] ? ": " + cleanLine(skipped[1]).replace(/\.$/, "") : ""}.`;
  switch (step) {
    case 1: {
      const m = r.match(/^(\S+)\s+confirmed/i);
      return m ? `Confirmed the company's website: ${host(m[1])}.` : null;
    }
    case 2: {
      const facts = num(r, /(\d+) facts?/i), srcs = num(r, /(\d+) sources?/i);
      if (facts === null) return null;
      const nf = r.match(/not found:\s*(.+)$/i);
      const miss = nf && !/^none\b/i.test(nf[1].trim()) ? ` Not found yet: ${nf[1].trim().replace(/\.$/, "")}.` : "";
      return `Gathered ${n(facts, "fact")}${srcs !== null ? ` from ${n(srcs, "source")}` : ""}, each with its link.${miss}`;
    }
    case 3: {
      if (/company-agent folder not found/i.test(r)) return "Skipped: the Company jobs assistant was not found, so open roles were not checked.";
      if (/no roles read|no company-agent sheet/i.test(r)) return "No open roles found: the company's careers page and job boards gave no jobs.";
      const eng = num(r, /(\d+) open engineering roles?/i), open = num(r, /of (\d+) open/i);
      if (eng === null) return null;
      return `Found ${n(eng, "open engineering role")}${open !== null ? ` (of ${n(open, "open job")})` : ""}.`;
    }
    case 4: {
      const leaders = num(r, /(\d+) leaders?/i);
      const ic = r.match(/India Connection:\s*([^;,.]+)/i);
      if (leaders === null && !ic) return null;
      const bits = [leaders !== null ? `Found ${n(leaders, "leader")}` : "", ic ? `India connection: ${/^yes/i.test(ic[1].trim()) ? "yes, with evidence" : "no evidence found"}` : ""].filter(Boolean);
      return bits.join(". ") + ".";
    }
    case 5: {
      if (/no consented rows|add team\.csv/i.test(r)) return "Team match skipped: add your team list (team.csv) to see warm connections and who should reach out.";
      const members = num(r, /(\d+) member/i);
      if (members === null) return null;
      const best = r.match(/best person:\s*([^;]+)/i);
      return `Compared with ${n(members, "team member")}: ${/common ground found/i.test(r) ? "common ground found" : "no common ground"}${best ? `; best person to reach out: ${best[1].trim()}` : ""}.`;
    }
    case 6: {
      if (/starter written/i.test(r)) return "Wrote a conversation starter from facts with links.";
      const why = r.match(/starter left blank:\s*(.*)$/i);
      return why ? `No conversation starter: ${cleanLine(why[1]).replace(/\.$/, "").toLowerCase()}.` : null;
    }
    case 7: {
      const ins = num(r, /(\d+) inserted/i), upd = num(r, /(\d+) updated/i), sheet = r.match(/sheet '([^']+)'/i);
      if (ins === null && upd === null) return null;
      const ch = [ins ? `${ins} added` : "", upd ? `${upd} updated` : ""].filter(Boolean).join(", ") || "no changes";
      return `Saved to Excel${sheet ? ` (sheet ${sheet[1]})` : ""}: ${ch}.`;
    }
    case 8: {
      const filled = num(r, /(\d+)(?:\/\d+)? fields? filled/i), blank = num(r, /(\d+) (?:blank|not found)/i);
      if (filled === null) return null;
      return `${filled} of 19 fields filled${blank ? `; ${blank} not found after searching the free sources (each cell says what was checked)` : ""}.`;
    }
  }
  return null;
}

/** Plain-language version of one "✓/✗ Step N/T · …" result line. `step` is 1-based. */
export function plainResult(agent: AgentKey, step: number, raw: string, failed = false): string {
  const r = raw.trim();
  if (!r) return "";
  if (failed) {
    if (/401|invalid api key|unauthori[sz]ed/i.test(r)) return "The web search key was not accepted. Check the Exa key in the setup check.";
    if (/timed? ?out|timeout/i.test(r)) return "This step took too long and was stopped. Try again in a few minutes.";
    if (/rate limit|429/i.test(r)) return "The website or search service asked us to slow down. Try again in a few minutes.";
    if (/permission|denied/i.test(r)) return "This step was blocked by the assistant's safety rules.";
    return cleanLine(r);
  }
  try {
    const out = agent === "company" ? company(step, r) : agent === "account" ? account(step, r) : candidate(step, r);
    return out ?? cleanLine(r);
  } catch {
    return cleanLine(r);
  }
}

// ---------- sign-in wording ----------

export interface AuthStatusLike {
  enabled: boolean;
  server: string;
  state: "disabled" | "signed_out" | "no_company" | "signed_in";
  user: { email: string; role: string; tenant?: string; tenant_id?: string } | null;
  offline: boolean;
  offline_until: number | null;
  remembered: boolean;
  message: string | null;
}

export const roleLabel = (role: string) => (role === "admin" ? "Admin" : "User");

/** "You are offline. You can keep working for about 3 days." from the signed deadline (unix seconds). */
export function offlineText(untilSec: number, nowMs: number): string {
  const left = untilSec * 1000 - nowMs;
  if (left <= 0) return "You are offline and the time you can work offline has run out. Connect to the internet.";
  const hours = left / 3_600_000;
  const span = hours < 1 ? "less than an hour" : hours < 48 ? `about ${Math.round(hours)} ${Math.round(hours) === 1 ? "hour" : "hours"}` : `about ${Math.round(hours / 24)} days`;
  return `You are offline. You can keep working for ${span}.`;
}

/** The line shown under Settings > Sign-in. */
export function signInSummary(st: AuthStatusLike, nowMs: number): string {
  if (!st.enabled) return "Sign-in is off: anyone who opens Recruit Desk on this computer can use it.";
  if (st.state === "signed_in" && st.user) {
    const where = st.user.tenant ? ` at ${st.user.tenant}` : "";
    const base = `Signed in as ${st.user.email} (${roleLabel(st.user.role)})${where}.`;
    return st.offline && st.offline_until ? `${base} ${offlineText(st.offline_until, nowMs)}` : base;
  }
  if (st.state === "no_company" && st.user) return `Signed in as ${st.user.email}, but not part of a company yet. Enter the invitation code your admin gave you.`;
  return "You are signed out.";
}

/** True for the backend's "Sign in first." refusal, so the window can show the sign-in screen instead of an error. */
export const isSignInRequired = (e: unknown) => /^(Error:\s*)?Sign in first\.?$/i.test(String(e).trim());

// ---------- admin screen wording ----------

export interface AdminUser { id: string; email: string; role: string; owner: boolean; you: boolean; joined_at: string | null }
export interface AdminInvite { id: string; email: string; role: string; expires_at: string; created_at: string }

/** Times come as UTC, with or without a zone ("2026-10-18T10:15:00" or "...Z"); show them in the viewer's own zone. */
export function formatUtc(stamp: string | null | undefined, empty = "Never"): string {
  if (!stamp) return empty;
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(stamp) ? stamp : `${stamp}Z`);
  if (Number.isNaN(d.getTime())) return empty;
  return d.toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}

/** "in 6 days", "in 3 hours", "less than an hour" for an invitation's end; "expired" once it has passed. */
export function expiresText(stamp: string, nowMs: number): string {
  const end = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(stamp) ? stamp : `${stamp}Z`).getTime();
  const left = end - nowMs;
  if (Number.isNaN(left) || left <= 0) return "expired";
  const hours = left / 3_600_000;
  if (hours < 1) return "in less than an hour";
  if (hours < 48) return `in ${Math.round(hours)} ${Math.round(hours) === 1 ? "hour" : "hours"}`;
  return `in ${Math.round(hours / 24)} days`;
}

export const validEmail = (s: string) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.trim()) && s.trim().length <= 320;

export interface UserAction { kind: "role" | "remove"; label: string; aria: string; confirm?: { title: string; text: string; ok: string } }

/** The actions an admin may take on one person. None on their own row or on the company owner: Neon Auth refuses them, so the screen does not offer them. */
export function userActions(u: AdminUser, company = "your company"): UserAction[] {
  if (u.you || u.owner) return [];
  const who = u.email;
  return [
    u.role === "admin"
      ? { kind: "role", label: "Make user", aria: `Make ${who} a user`, confirm: { title: "Remove admin rights?", text: `${who} will no longer be able to invite people or remove them.`, ok: "Make user" } }
      : { kind: "role", label: "Make admin", aria: `Make ${who} an admin`, confirm: { title: "Make this person an admin?", text: `${who} will be able to invite people, change roles and remove people from ${company}.`, ok: "Make admin" } },
    { kind: "remove", label: "Remove", aria: `Remove ${who} from ${company}`, confirm: { title: "Remove this person?", text: `${who} loses access to ${company}'s data at once, and Recruit Desk stops working for them within a few minutes. Files on their own computer are not touched. You can invite them again later.`, ok: "Remove" } },
  ];
}

export interface InviteResult { email: string; role: string; expires_at: string; code: string }

/** What to tell the admin after creating an invitation (nothing is emailed: the admin hands the code over). */
export function inviteResultText(r: InviteResult, nowMs: number): string {
  return `Give this code to ${r.email}. In Recruit Desk they choose "I have an invitation code", use exactly this email address, this code and a password of their own. It works once, only for that address, and ends ${expiresText(r.expires_at, nowMs)}. No email is sent.`;
}

export const looksLikeCode = (s: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s.trim());

// ---------- invitation and reset link ----------

export const MIN_PASSWORD = 10;

/** What is wrong with a new password before it is sent, or "" when nothing is. The server checks again. */
export function passwordProblem(pw: string, again: string, min = MIN_PASSWORD): string {
  if (!pw) return "Choose a password.";
  if (pw.length < min) return `Choose a password of at least ${min} characters.`;
  if (pw.length > 128) return "Choose a password of at most 128 characters.";
  if (pw !== again) return "The two passwords are different.";
  return "";
}

export type LinkMode = "create" | "join" | "forgot" | "reset";
export type LinkField = "email" | "code" | "link" | "pw";

export const LINK_TEXT: Record<LinkMode, { title: string; intro: string; submit: string; busy: string; fields: LinkField[] }> = {
  create: { title: "Create your account", intro: "Use the email address your admin invited and the invitation code they gave you, and choose a password of your own. You will be signed in.", submit: "Create my account", busy: "Creating…", fields: ["email", "code", "pw"] },
  join: { title: "Join your company", intro: "You are signed in, but not part of a company yet. Enter the invitation code your admin gave you.", submit: "Join", busy: "Joining…", fields: ["code"] },
  forgot: { title: "Forgot your password?", intro: "Enter your email address. If it has an account, an email with a reset link is sent to it.", submit: "Send reset email", busy: "Sending…", fields: ["email"] },
  reset: { title: "Choose a new password", intro: "Paste the link from the reset email (or just the code in it), then choose a new password. You will sign in with it afterwards.", submit: "Set new password", busy: "Saving…", fields: ["link", "pw"] },
};

/** The notice on the sign-in screen: a sentence the window set itself wins over the backend's reason; none while the link panel is open. */
export function loginNotice(local: string, backend: string | null, panelOpen: boolean): string {
  if (panelOpen) return "";
  return local || backend || "";
}

// ---------- the allowance (one pool for the whole company) ----------

/** 240 → "4 hours", 90 → "1 hour 30 minutes", 30 → "30 minutes". */
export function windowText(minutes: number): string {
  const h = Math.floor(minutes / 60), m = minutes % 60;
  const hp = h ? `${h} ${h === 1 ? "hour" : "hours"}` : "";
  const mp = m ? `${m} ${m === 1 ? "minute" : "minutes"}` : "";
  return [hp, mp].filter(Boolean).join(" ") || "0 minutes";
}

export interface AllowancePerson { email: string; counted: number; window_started_ms: number | null; locked_at_ms: number | null }

export const sharedNote = (_company: string) => "Your own total over all the assistants";

export function allowanceIntro(company: string): string {
  return `Every person in ${company} has their own allowance, counted over all three assistants together, with their own cap and window. A new person starts at 3,000,000 tokens per 4 hours. Set each person's cap and window below. A person's window opens with their first tokens counted in it and, once its length has passed, their count starts again. Only admins can change it.`;
}

/** The numbers an admin typed, as the cap in tokens and the window in minutes; or what is wrong with them. */
export function allowanceInput(capText: string, hoursText: string): { cap: number; minutes: number } | string {
  const cap = Number(capText.replace(/[\s,]/g, ""));
  const hours = Number(hoursText.replace(",", ".").trim());
  if (!Number.isFinite(cap) || !Number.isInteger(cap) || cap < 1000 || cap > 1_000_000_000) return "The cap must be a whole number of tokens between 1,000 and 1,000,000,000.";
  if (!Number.isFinite(hours) || hours < 0.25 || hours > 24) return "The window must be between 0.25 hours (15 minutes) and 24 hours.";
  return { cap, minutes: Math.round(hours * 60) };
}

// ---------- search history (kept in the company database) ----------

export interface HistorySearch { id: string; who: string | null; agent: string; inputs: Record<string, unknown>; started_at: string; finished_at: string | null; status: string; rows: number }
export interface HistoryRow { agent: string; record: Record<string, string | number | null>; created_at: string; who: string | null }
export interface HistoryResult { state: string; message: string; searches: number; rows: number; removed: number; at: number }

const ASSISTANT: Record<string, string> = { candidate: "Find candidates", company: "Company jobs", account: "Accounts" };
export const assistantName = (agent: string) => ASSISTANT[agent] ?? agent;

/** One short line for what was searched. */
export function searchSummary(s: Pick<HistorySearch, "agent" | "inputs">): string {
  const t = (k: string) => (typeof s.inputs?.[k] === "string" ? (s.inputs[k] as string).trim() : "");
  const cut = (x: string, n: number) => (x.length > n ? `${x.slice(0, n - 1).trimEnd()}…` : x);
  if (s.agent === "candidate") {
    const first = t("jd").split(/\r?\n/).map((l) => l.trim()).find((l) => l.length > 0) ?? "";
    return first ? cut(first, 90) : "A job description";
  }
  if (s.agent === "company") {
    const base = [t("company"), t("domain") ? `(${t("domain")})` : ""].filter(Boolean).join(" ") || "A company";
    return s.inputs?.publicPosts === true ? `${base}, with public posts` : base;
  }
  const csv = t("csv");
  if (csv) {
    const n = Math.max(0, csv.split(/\r?\n/).filter((l) => l.trim()).length - 1);
    return `A list of ${n} ${n === 1 ? "company" : "companies"}`;
  }
  return [t("company"), t("domain") ? `(${t("domain")})` : ""].filter(Boolean).join(" ") || "A company";
}

export const statusLabel = (status: string) => ({ running: "Running", done: "Finished", failed: "Ended early", stopped: "Stopped" } as Record<string, string>)[status] ?? status;
export const resultsText = (n: number) => (n === 0 ? "No results" : n === 1 ? "1 result" : `${n} results`);

export function historyIntro(admin: boolean, company: string): string {
  return admin
    ? `Every search in ${company}, with its results. People see only their own; as an admin you see everyone's.`
    : `Your searches and their results, as stored in ${company}'s database. Only you and your company's admins can see them.`;
}

/** One line for Settings: what the last history sync said. */
export function historyLine(r: HistoryResult | null): string {
  if (!r) return "";
  const text: Record<string, string> = {
    sent: "your searches and results are stored in the company database.",
    nothing: "everything is stored in the company database.",
    off: "sign-in is off, so nothing is stored.",
  };
  return `Search history: ${text[r.state] ?? r.message}`;
}

export const removeSearchText = (s: Pick<HistorySearch, "rows" | "who">, mine: boolean) =>
  `${mine ? "This search" : `${s.who ?? "This person"}'s search`}${s.rows > 0 ? ` and its ${resultsText(s.rows).toLowerCase()} are` : " is"} removed from the company database. Files on the computer it was run on are not touched.`;

// ---------- last records (kept in the company database) ----------

export interface RecordsResult { agent: string; state: string; message: string; at: number }
export interface StoredRecord { agent: string; record: Record<string, string | number | null>; created_at: string; stored_at: string; stored_by: string | null }

export const AGENT_LABEL: Record<string, string> = { candidate: "Find candidates", company: "Company jobs", account: "Accounts" };

/** One line for Settings: "Find candidates: the latest record is stored in the company database." */
export function recordsLine(r: RecordsResult): string {
  const name = AGENT_LABEL[r.agent] ?? r.agent;
  const text: Record<string, string> = {
    sent: "the latest record is stored in the company database.",
    unchanged: "the latest record is stored in the company database.",
    older: "a newer record from another computer is already stored.",
    removed: "the stored record was removed because it no longer exists on this computer.",
    nothing: "no record has been created yet.",
    off: "sign-in is off, so nothing is sent.",
  };
  return `${name}: ${text[r.state] ?? r.message}`;
}

/** A short description of a stored record, as an admin wants to see it. */
export function recordSummary(agent: string, rec: Record<string, string | number | null>): string {
  const v = (k: string) => (rec[k] === null || rec[k] === undefined ? "" : String(rec[k]));
  const join = (...xs: string[]) => xs.filter(Boolean).join(" · ");
  if (agent === "candidate") return join(v("name"), v("title"), v("company"), v("match_score") ? `${v("match_score")}/100` : "", v("jd"));
  if (agent === "company") return join(v("job_title"), v("company"), v("location"), v("status"));
  if (agent === "account") return join(v("company"), v("domain"), v("product_type"), v("funding"));
  return join(...Object.values(rec).map((x) => String(x)));
}
