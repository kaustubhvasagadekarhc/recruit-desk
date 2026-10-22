// Author: kaustubh.s.v
// Tests for src/plain.ts (compiled to dist/plain.js by tsc): real step result lines must read as plain language.
import assert from "node:assert/strict";
import { plainResult, cleanLine, offlineText, roleLabel, signInSummary, isSignInRequired, formatUtc, expiresText, validEmail, userActions, inviteResultText, looksLikeCode, passwordProblem, LINK_TEXT, MIN_PASSWORD, loginNotice, recordsLine, recordSummary, AGENT_LABEL, searchSummary, statusLabel, resultsText, historyIntro, historyLine, removeSearchText, assistantName, windowText, sharedNote, allowanceIntro, allowanceInput } from "../dist/plain.js";

const cases = [
  ["company", 1, "humancloud.co.in confirmed from site (Humancloud Technologies); memory has same name under humancloud.dev; careers page unknown, ATS none",
    "Confirmed the company: Humancloud Technologies (humancloud.co.in). Its careers page is not known yet."],
  ["company", 2, "32 pages crawled, careers page https://humancloud.dev/careers, 0 ATS board(s), 6 roles found, 0 URLs blocked by robots",
    "Read 32 pages of the website and found 6 jobs."],
  ["company", 3, "skipped: official careers page gave 6 roles, public posts not requested", "Skipped: the careers page already listed 6 jobs."],
  ["company", 3, "skipped: web search unavailable (no Exa access)", "Skipped: web search is not available, so LinkedIn, Indeed and job boards were not searched."],
  ["company", 3, "skipped: no public rows file passed (--public-rows)", "Skipped: LinkedIn, Indeed and job boards were not searched (turned off for this check)."],
  ["company", 3, "12 listings kept, 9 dropped (namesakes, aggregator copies)", "Found 12 public job listings on LinkedIn, Indeed and job boards; left out 9 other results."],
  ["company", 3, "1 public listing(s) from runs/razorpay-public.rows.json", "Found 1 public job listing on LinkedIn, Indeed and job boards."],
  ["company", 4, "6 rows in, 0 duplicates dropped, 0 new, 6 updated, 0 closed, 0 stale; company total 10 (Open=4, Unverified=5, Stale=1, Closed=0)",
    "10 jobs on file: 4 open, 5 not yet confirmed, 1 possibly out of date. This check: no new jobs, 6 updated."],
  ["company", 5, "sheet 'Humancloud Technologies': 0 inserted, 10 updated, 10 rows in sheet", "Saved to Excel (10 jobs on the sheet): 10 updated."],
  ["company", 6, "10 rows checked against memory listing (title, location, URL, status present), 0 mismatches; row-auditor not run", "Double-checked 10 jobs: all match."],
  ["candidate", 1, "JD 'senior-react-frontend-pune' registered · sheet 'Senior React Frontend Developer' · 3 mandatory, 6 preferred skills",
    "Read the job: 3 must-have skills and 6 nice-to-have skills."],
  ["candidate", 2, "reused the build-test batch (16 rows, no new Exa search)", "Reused 16 profiles found earlier (no new web search)."],
  ["candidate", 3, "15 scored, top 94, 0 capped by a mandatory-skill gate, 1 Low confidence", "Scored 15 candidates. Best match: 94 out of 100. 1 has little public evidence."],
  ["candidate", 4, "15 in, 15 kept, 0 merged as duplicates, 15 already in memory (refresh)", "Kept 15 candidates; no duplicates. 15 were already on file and were refreshed."],
  ["candidate", 5, "sheet 'Senior React Frontend Developer': 0 inserted, 15 updated, now 15 rows", "Saved to Excel: 15 candidates on the sheet (15 updated)."],
  ["candidate", 6, "run 4 closed, 15 candidates, top 94", "Finished with 15 candidates; best match 94."],
  ["account", 1, "razorpay.com confirmed from the home page title and JSON-LD", "Confirmed the company's website: razorpay.com."],
  ["account", 2, "24 facts from 11 sources, 9 Exa searches", "Gathered 24 facts from 11 sources, each with its link."],
  ["account", 3, "4 open engineering roles of 32 open (sheet 'Razorpay' in company-agent, read-only)", "Found 4 open engineering roles (of 32 open jobs)."],
  ["account", 3, "no roles read: company-agent has no sheet for this company; Development Requirement left blank", "No open roles found: the company's careers page and job boards gave no jobs."],
  ["account", 4, "3 leaders, India Connection: No evidence found", "Found 3 leaders. India connection: no evidence found."],
  ["account", 4, "2 leaders, India Connection: Yes (CTO studied at IIT Delhi)", "Found 2 leaders. India connection: yes, with evidence."],
  ["account", 5, "team.csv has no consented rows: Warm Connection and Best Person left blank ('add team.csv')", "Team match skipped: add your team list (team.csv) to see warm connections and who should reach out."],
  ["account", 5, "team.csv: 4 member(s); common ground found; best person: Priya Sharma (Director, Sales (US))", "Compared with 4 team members: common ground found; best person to reach out: Priya Sharma (Director, Sales (US))."],
  ["account", 6, "starter written, citing [3, 7]", "Wrote a conversation starter from facts with links."],
  ["account", 6, "starter left blank: no dated hook in the cited facts", "No conversation starter: no dated hook in the cited facts."],
  ["account", 7, "sheet 'Accounts': 1 inserted, 0 updated (Razorpay); 1 companies in sheet", "Saved to Excel (sheet Accounts): 1 added."],
  ["account", 8, "15 fields filled, 4 blank", "15 of 19 fields filled; 4 not found after searching the free sources (each cell says what was checked)."],
  ["account", 8, "17 fields filled, 2 not found", "17 of 19 fields filled; 2 not found after searching the free sources (each cell says what was checked)."],
  ["account", 2, "14 facts from 9 sources, 6 Exa searches, not found: none", "Gathered 14 facts from 9 sources, each with its link."],
  ["account", 2, "11 facts from 8 sources, 9 Exa searches, not found: employee size", "Gathered 11 facts from 8 sources, each with its link. Not found yet: employee size."],
];
let n = 0;
for (const [a, s, raw, want] of cases) { assert.equal(plainResult(a, s, raw), want, `${a} step ${s}`); n++; }

// No raw Key=N counts, URLs or jargon may survive, even for unknown lines.
for (const raw of ["company total 7 (Open=3, Unverified=4, Closed=0)", "3 ATS board(s), see https://x.com/a/b", "12 rows in sheet; 0 dropped"]) {
  const t = cleanLine(raw);
  assert.doesNotMatch(t, /\w=\d|https?:|\bATS\b|\brows?\b|\b0 dropped/, t); n++;
}
assert.equal(plainResult("candidate", 2, "Exa search failed: HTTP 401 invalid API key", true), "The web search key was not accepted. Check the Exa key in the setup check."); n++;
assert.equal(plainResult("company", 9, ""), ""); n++;
// ---------- round 1 typed columns (src/data.ts) ----------
const D = await import("../dist/data.js");
// Sheet pickers never offer the Evidence, Contacts or Company channels sheets.
assert.deepEqual(D.listSheets(["Sr. Software Engineer - Python", "Senior React Frontend Developer", "Evidence"]), ["Sr. Software Engineer - Python", "Senior React Frontend Developer"]); n++;
assert.deepEqual(D.listSheets(["Accounts", "Contacts", "Company channels", "Evidence", "Q4 India"]), ["Accounts", "Q4 India"]); n++;
assert.deepEqual(D.listSheets(["Razorpay", "Intuit"]), ["Razorpay", "Intuit"]); n++;
assert.deepEqual(D.listSheets(["Evidence"]), []); n++;
assert.deepEqual(D.listSheets(["Sr. Software Engineer - Python", "Candidates", "Matches", "Evidence"]), ["Sr. Software Engineer - Python"]); n++;

// Views ("All lists together"): offered only when the workbook has the entity sheet, in their own group after the lists.
assert.deepEqual(D.viewsIn("candidate", ["Sr. Software Engineer - Python", "Candidates", "Matches", "Evidence"]).map((x) => x.label), ["All candidates", "All matches"]); n++;
assert.deepEqual(D.viewsIn("candidate", ["Sr. Software Engineer - Python", "Evidence"]), []); n++;
assert.deepEqual(D.viewsIn("company", ["Razorpay", "Companies", "Jobs"]).map((x) => x.sheet), ["Jobs", "Companies"]); n++;
assert.deepEqual(D.viewsIn("account", ["Accounts", "Companies", "Contacts"]).map((x) => x.sheet), ["Companies"]); n++;
assert.deepEqual(D.viewsIn("account", ["Accounts", "Jobs"]), []); n++;   // Jobs is a company-agent view only
assert.deepEqual(D.listSheets(["Accounts", "Companies", "Jobs", "Q4 India"]), ["Accounts", "Q4 India"]); n++;
// A candidate row from the Candidates view finds its Evidence rows by Candidate Key.
const EVH = ["Candidate Key", "Fact", "Quote", "Source URL", "Source Type", "Fetched On", "Candidate Name", "JD Id"];
const got = D.evidenceFor("candidate", ["Candidate Key", "Candidate Name"], ["url:https://www.linkedin.com/in/x", "X Y"],
  { headers: EVH, rows: [["url:https://www.linkedin.com/in/x", "7+ YOE", "", "https://www.linkedin.com/in/x", "Exa search", "2026-10-16", "X Y", "jd"]] }, new Date("2026-10-18"));
assert.equal(got.length, 1); n++;
for (const [agent, defs] of Object.entries(D.VIEWS)) for (const d of defs) assert.ok(d.columns.length >= 2 && d.columns.every(([h, l]) => h && l), `${agent}/${d.sheet} columns`); n++;

// Score breakdown: the 8 pts columns add up to Match Score (real row from the Python sheet: capped at 40 by a gate).
const CH = ["Candidate Name", "Match Score (0-100)", "Profile URL", "Core Skills pts", "Experience pts", "Domain pts", "Seniority pts",
  "Location pts", "Preferred pts", "OTW pts", "Cap Adjustment pts"];
const yog = ["Yogesh Singh", "40", "https://www.linkedin.com/in/yogeshsingh0720", "20", "15", "2", "6", "0", "7", "0", "-10"];
let b = D.scoreBreakdown(CH, yog);
assert.equal(b.parts.length, 8); assert.equal(b.positive, 50); assert.equal(b.cap, -10); assert.equal(b.total, 40); assert.equal(b.adds, true); n += 5;
assert.equal(b.parts[0].label, "Core skills"); assert.equal(b.parts[7].label, "Cap adjustment"); n += 2;
b = D.scoreBreakdown(CH, ["A", "70", "", "24", "19", "4", "6", "10", "2", "5", "0"]);
assert.equal(b.total, 70); assert.equal(b.cap, 0); assert.equal(b.adds, true); n += 3;
b = D.scoreBreakdown(CH, ["A", "90", "", "24", "19", "4", "6", "10", "2", "5", "0"]);
assert.equal(b.adds, false, "a score that the parts do not add up to is flagged, not hidden"); n++;
assert.equal(D.scoreBreakdown(CH, ["A", "55", "", "", "", "", "", "", "", "", ""]), null, "blank pts = no bar"); n++;
assert.equal(D.scoreBreakdown(["Candidate Name", "Match Score (0-100)"], ["A", "55"]), null, "old sheets have no bar"); n++;

// Evidence: candidate rows match on the agent's own key (url:<normalised profile URL>).
assert.equal(D.normaliseUrl("https://in.linkedin.com/in/YogeshSingh0720/?trk=x"), "https://www.linkedin.com/in/yogeshsingh0720"); n++;
assert.equal(D.normaliseUrl("https://www.AnshuRajSingh.com/"), "https://anshurajsingh.com"); n++;
assert.equal(D.candidateKey("", "Rahul Sethi", "Senior Engineer @ Acme Corp"), "nc:rahul sethi|acme corp"); n++;
const now = new Date(2026, 9, 16);
const CEV = { headers: ["Candidate Key", "Fact", "Quote", "Source URL", "Source Type", "Fetched On", "Candidate Name", "JD Id"], rows: [
  ["url:https://www.linkedin.com/in/yogeshsingh0720", "5 yrs experience", "5 yrs Experience", "https://www.linkedin.com/in/yogeshsingh0720", "Exa search (LinkedIn public profile)", "2026-10-15", "Yogesh Singh", "py"],
  ["url:https://anshurajsingh.com", "", "", "https://anshurajsingh.com/", "Exa search (personal site)", "2026-10-01", "Anshu Raj", "react"]] };
let ev = D.evidenceFor("candidate", CH, yog, CEV, now);
assert.equal(ev.length, 1); assert.equal(ev[0].fact, "5 yrs experience"); assert.equal(ev[0].age, 1); n += 3;
ev = D.evidenceFor("candidate", CH, ["Anshu Raj", "60", "https://anshurajsingh.com"], CEV, now);
assert.equal(ev.length, 1); assert.equal(ev[0].age, 15); assert.equal(ev[0].type, "Exa search (personal site)"); n += 3;
// Accounts match on Domain; the age is recomputed from Checked On, not read from the frozen Age (days).
const AEV = { headers: ["Domain", "Field", "Fact", "Source URL", "Source Type", "Checked On", "Age (days)", "Confidence"], rows: [
  ["freshworks.com", "Funding", "Raised $250M", "https://sec.gov/x", "Filing", "2026-07-08", "0", "High"],
  ["razorpay.com", "Funding", "Series F", "https://razorpay.com/n", "Company site", "2026-10-16", "0", "High"]] };
ev = D.evidenceFor("account", ["Company", "Domain"], ["Freshworks", "Freshworks.com"], AEV, now);
assert.equal(ev.length, 1); assert.equal(ev[0].age, 100); assert.equal(ev[0].confidence, "High"); n += 3;
assert.deepEqual(D.evidenceFor("account", ["Company", "Domain"], ["X", ""], AEV, now), []); n++;
// Company jobs have no Evidence sheet: the listing itself is the one item.
ev = D.evidenceFor("company", ["Job Title", "Source Type", "Job / Post URL", "Last Checked"], ["SRE", "Careers Page", "https://x.com/j/1", "2026-10-14"], null, now);
assert.equal(ev.length, 1); assert.equal(ev[0].age, 2); assert.equal(ev[0].type, "Careers Page"); n += 3;
assert.equal(D.ageText(0), "today"); assert.equal(D.ageText(1), "1 day ago"); assert.equal(D.ageText(100), "3 months ago"); assert.equal(D.ageText(null), "date unknown"); n += 4;
assert.deepEqual([...D.sourceMap("[1] https://a.com/\n[2] https://b.com/x").entries()], [["1", "https://a.com/"], ["2", "https://b.com/x"]]); n++;

// Filters and salary.
const JH = ["Job Title", "Work Mode", "Country", "Seniority", "Salary Min", "Salary Max", "Currency"];
const jobs = [["A", "Remote", "India", "Senior", "1200000", "1800000", "INR"], ["B", "Hybrid", "India; Malaysia", "Mid", "", "", ""], ["C", "Unknown", "", "", "90000", "", "USD"]];
assert.deepEqual(D.enumValues(JH, jobs, "Work Mode"), ["Hybrid", "Remote", "Unknown"]); n++;
assert.deepEqual(D.enumValues(JH, jobs, "Country"), ["India", "Malaysia"]); n++;
const F = D.FILTERS.company;
assert.deepEqual(jobs.filter((r) => D.passesFilters(JH, r, { Country: "Malaysia" }, F)).map((r) => r[0]), ["B"]); n++;
assert.deepEqual(jobs.filter((r) => D.passesFilters(JH, r, { Country: "India", "Work Mode": "Remote" }, F)).map((r) => r[0]), ["A"]); n++;
assert.deepEqual(jobs.filter((r) => D.passesFilters(JH, r, {}, F)).length, 3); n++;
const EH = ["Candidate Name", "Experience Years"];
assert.deepEqual([["a", "9.5"], ["b", "3"], ["c", ""]].filter((r) => D.passesFilters(EH, r, { "Experience Years": "5" }, D.FILTERS.candidate)).map((r) => r[0]), ["a"]); n++;
assert.equal(D.salaryText(JH, jobs[0]), "INR 1,200,000–1,800,000"); assert.equal(D.salaryText(JH, jobs[1]), ""); assert.equal(D.salaryText(JH, jobs[2]), "from USD 90,000"); n += 3;
console.log(`plain-language tests: ${n} passed`);

// ---------- sign-in wording ----------
const HOUR = 3_600_000, T0 = 1_800_000_000_000;
const sec = (ms) => Math.floor(ms / 1000);
assert.equal(roleLabel("admin"), "Admin");
assert.equal(roleLabel("user"), "User");
assert.equal(roleLabel("anything else"), "User");
assert.equal(offlineText(sec(T0 + 30 * 60_000), T0), "You are offline. You can keep working for less than an hour.");
assert.equal(offlineText(sec(T0 + HOUR), T0), "You are offline. You can keep working for about 1 hour.");
assert.equal(offlineText(sec(T0 + 5 * HOUR), T0), "You are offline. You can keep working for about 5 hours.");
assert.equal(offlineText(sec(T0 + 3 * 24 * HOUR), T0), "You are offline. You can keep working for about 3 days.");
assert.match(offlineText(sec(T0 - 1000), T0), /has run out/);
const base = { enabled: true, server: "https://auth.example.com", state: "signed_in", user: { email: "ann@example.com", role: "user" }, offline: false, offline_until: null, remembered: true, message: null };
assert.equal(signInSummary({ ...base, enabled: false, state: "disabled", user: null }, T0), "Sign-in is off: anyone who opens Recruit Desk on this computer can use it.");
assert.equal(signInSummary(base, T0), "Signed in as ann@example.com (User).");
assert.equal(signInSummary({ ...base, user: { email: "boss@example.com", role: "admin" } }, T0), "Signed in as boss@example.com (Admin).");
assert.equal(signInSummary({ ...base, user: { email: "ann@example.com", role: "user", tenant: "Acme Corp" } }, T0), "Signed in as ann@example.com (User) at Acme Corp.");
assert.match(signInSummary({ ...base, offline: true, offline_until: sec(T0 + 2 * 24 * HOUR) }, T0), /^Signed in as ann@example.com \(User\)\. You are offline\. You can keep working for about 2 days\.$/);
assert.equal(signInSummary({ ...base, state: "signed_out", user: null }, T0), "You are signed out.");
assert.equal(signInSummary({ ...base, state: "no_company", user: { email: "new@example.com", role: "", tenant: "" } }, T0), "Signed in as new@example.com, but not part of a company yet. Enter the invitation code your admin gave you.");
assert.equal(isSignInRequired("Sign in first."), true);
assert.equal(isSignInRequired("Error: Sign in first."), true);
assert.equal(isSignInRequired(new Error("Sign in first.")), true);
assert.equal(isSignInRequired("Please sign in first to see your files"), false);
assert.equal(isSignInRequired("That email and password do not match."), false);
console.log("sign-in wording tests passed");

// ---------- admin screen wording ----------
assert.equal(formatUtc(null), "Never");
assert.equal(formatUtc(""), "Never");
assert.equal(formatUtc("not a date"), "Never");
assert.equal(formatUtc(undefined, "Not yet"), "Not yet");
// a server time without a zone is UTC: the same instant with and without the Z
assert.equal(formatUtc("2026-10-18T10:15:00"), formatUtc("2026-10-18T10:15:00Z"));
assert.equal(formatUtc("2026-10-18T10:15:00"), formatUtc("2026-10-18T15:45:00+05:30"));
const NOWMS = Date.parse("2026-10-18T10:00:00Z");
assert.equal(expiresText("2026-10-18T09:00:00", NOWMS), "expired");
assert.equal(expiresText("2026-10-18T10:30:00", NOWMS), "in less than an hour");
assert.equal(expiresText("2026-10-18T11:00:00", NOWMS), "in 1 hour");
assert.equal(expiresText("2026-10-18T15:00:00", NOWMS), "in 5 hours");
assert.equal(expiresText("2026-10-25T10:00:00", NOWMS), "in 7 days");
assert.equal(expiresText("garbage", NOWMS), "expired");
for (const ok of ["a@b.co", " ann@humancloud.co.in ", "x.y+z@sub.example.com"]) assert.equal(validEmail(ok), true, ok);
for (const bad of ["", "ann", "ann@", "@b.co", "a b@c.co", "a@b", "a@b.c", "a@@b.co", "a".repeat(330) + "@b.co"]) assert.equal(validEmail(bad), false, bad);
const U = { id: "00000000-0000-0000-0000-000000000002", email: "ann@example.com", role: "user", owner: false, you: false, joined_at: "2026-10-01T00:00:00Z" };
assert.deepEqual(userActions({ ...U, you: true }), [], "no actions on your own row");
assert.deepEqual(userActions({ ...U, owner: true, role: "admin" }), [], "no actions on the company owner");
assert.deepEqual(userActions(U).map((a) => a.kind + ":" + a.label), ["role:Make admin", "remove:Remove"]);
assert.deepEqual(userActions({ ...U, role: "admin" }).map((a) => a.label), ["Make user", "Remove"]);
assert.ok(userActions(U)[0].confirm && userActions(U)[1].confirm, "making an admin and removing both ask first");
assert.match(userActions(U)[0].aria, /ann@example.com/);
assert.match(userActions(U, "Acme Corp")[1].aria, /Remove ann@example.com from Acme Corp/);
assert.match(userActions(U, "Acme Corp")[1].confirm.text, /loses access to Acme Corp's data at once.*not touched.*invite them again/);
assert.match(userActions({ ...U, role: "admin" })[0].confirm.title, /Remove admin rights/);
const CODE = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed";
for (const ok of [CODE, CODE.toUpperCase(), ` ${CODE} `]) assert.equal(looksLikeCode(ok), true, ok);
for (const bad of ["", "1b9d6bcd", CODE + "x", CODE.replace(/-/g, ""), "zzzzzzzz-bbfd-4b2d-9b5d-ab8dfbbd4bed", `https://x.test/?c=${CODE}`]) assert.equal(looksLikeCode(bad), false, bad);
const inv = inviteResultText({ email: "a@b.co", role: "user", expires_at: "2026-10-20T10:00:00Z", code: CODE }, NOWMS);
assert.match(inv, /Give this code to a@b.co/);
assert.match(inv, /exactly this email address/);
assert.match(inv, /ends in 2 days/);
assert.match(inv, /No email is sent/);
console.log("admin wording tests passed");

// ---------- invitation and reset link ----------
assert.equal(MIN_PASSWORD, 10);
assert.equal(passwordProblem("", ""), "Choose a password.");
assert.equal(passwordProblem("short", "short"), "Choose a password of at least 10 characters.");
assert.equal(passwordProblem("123456789", "123456789"), "Choose a password of at least 10 characters.");
assert.equal(passwordProblem("a".repeat(129), "a".repeat(129)), "Choose a password of at most 128 characters.");
assert.equal(passwordProblem("a long enough password", "a long enough passwordX"), "The two passwords are different.");
assert.equal(passwordProblem("a long enough password", "a long enough password"), "");
assert.equal(passwordProblem("1234567890", "1234567890"), "", "exactly the minimum is allowed");
assert.equal(passwordProblem("twelve chars", "twelve chars", 12), "");
assert.equal(passwordProblem("eleven char", "eleven char", 12), "Choose a password of at least 12 characters.");
assert.deepEqual(Object.keys(LINK_TEXT), ["create", "join", "forgot", "reset"]);
assert.equal(LINK_TEXT.create.title, "Create your account");
assert.equal(LINK_TEXT.reset.submit, "Set new password");
assert.deepEqual(LINK_TEXT.create.fields, ["email", "code", "pw"]);
assert.deepEqual(LINK_TEXT.join.fields, ["code"]);
assert.deepEqual(LINK_TEXT.forgot.fields, ["email"]);
assert.deepEqual(LINK_TEXT.reset.fields, ["link", "pw"]);
for (const k of Object.keys(LINK_TEXT)) for (const f of ["title", "intro", "submit", "busy"]) assert.ok(LINK_TEXT[k][f].length > 3, `${k}.${f}`);
console.log("link panel wording tests passed");

// the sign-in notice
assert.equal(loginNotice("", null, false), "");
assert.equal(loginNotice("", "Your session ended. Sign in again.", false), "Your session ended. Sign in again.");
assert.equal(loginNotice("Password changed. Sign in with your new password.", null, false), "Password changed. Sign in with your new password.", "survives a re-check that returns no message");
assert.equal(loginNotice("Password changed.", "Your session ended.", false), "Password changed.", "the window's own sentence wins");
assert.equal(loginNotice("Password changed.", "Your session ended.", true), "", "hidden while the account or reset panel is open");
console.log("notice tests passed");

// ---------- last records ----------
const R = (state, agent = "candidate", message = "m") => ({ agent, state, message, at: 0 });
assert.equal(recordsLine(R("sent")), "Find candidates: the latest record is stored in the company database.");
assert.equal(recordsLine(R("unchanged", "company")), "Company jobs: the latest record is stored in the company database.");
assert.equal(recordsLine(R("older", "account")), "Accounts: a newer record from another computer is already stored.");
assert.match(recordsLine(R("removed")), /no longer exists on this computer/);
assert.equal(recordsLine(R("nothing")), "Find candidates: no record has been created yet.");
assert.equal(recordsLine(R("off")), "Find candidates: sign-in is off, so nothing is sent.");
assert.equal(recordsLine(R("waiting", "company", "You are offline. It is sent when you are back online.")), "Company jobs: You are offline. It is sent when you are back online.", "waiting and error show the app's own message");
assert.equal(recordsLine(R("error", "robot", "The database did not accept this record.")), "robot: The database did not accept this record.");
assert.deepEqual(Object.keys(AGENT_LABEL), ["candidate", "company", "account"]);
assert.equal(recordSummary("candidate", { name: "Ann Rao", title: "Senior Frontend Engineer", company: "Acme", match_score: 85, jd: "React" }), "Ann Rao · Senior Frontend Engineer · Acme · 85/100 · React");
assert.equal(recordSummary("candidate", { name: "Bob", match_score: null }), "Bob");
assert.equal(recordSummary("company", { job_title: "SRE", company: "Acme", location: "Pune", status: "Open" }), "SRE · Acme · Pune · Open");
assert.equal(recordSummary("account", { company: "Gamma", domain: "gamma.dev", funding: "Series A" }), "Gamma · gamma.dev · Series A");
assert.equal(recordSummary("robot", { a: 1, b: "x" }), "1 · x");
console.log("last record wording tests passed");

// ---------- search history ----------
assert.equal(searchSummary({ agent: "candidate", inputs: { jd: "\n  Senior React Developer\nPune, 5+ years" } }), "Senior React Developer");
assert.equal(searchSummary({ agent: "candidate", inputs: { jd: "x".repeat(200) } }).length, 90);
assert.equal(searchSummary({ agent: "candidate", inputs: {} }), "A job description");
assert.equal(searchSummary({ agent: "company", inputs: { company: "Razorpay", domain: "razorpay.com" } }), "Razorpay (razorpay.com)");
assert.equal(searchSummary({ agent: "company", inputs: { company: "Razorpay", publicPosts: true } }), "Razorpay, with public posts");
assert.equal(searchSummary({ agent: "company", inputs: {} }), "A company");
assert.equal(searchSummary({ agent: "account", inputs: { csv: "company,domain\nA,a.com\nB,b.com\n" } }), "A list of 2 companies");
assert.equal(searchSummary({ agent: "account", inputs: { csv: "company,domain\nA,a.com" } }), "A list of 1 company");
assert.equal(searchSummary({ agent: "account", inputs: { company: "Acme", domain: "acme.com" } }), "Acme (acme.com)");
assert.deepEqual(["running", "done", "failed", "stopped", "odd"].map(statusLabel), ["Running", "Finished", "Ended early", "Stopped", "odd"]);
assert.deepEqual([0, 1, 7].map(resultsText), ["No results", "1 result", "7 results"]);
assert.equal(assistantName("candidate"), "Find candidates");
assert.equal(assistantName("robot"), "robot");
assert.match(historyIntro(false, "Acme"), /Your searches.*Acme's database.*Only you and your company's admins/);
assert.match(historyIntro(true, "Acme"), /Every search in Acme.*as an admin you see everyone's/);
const HR = (state, message = "m") => ({ state, message, searches: 0, rows: 0, removed: 0, at: 0 });
assert.equal(historyLine(null), "");
assert.equal(historyLine(HR("sent")), "Search history: your searches and results are stored in the company database.");
assert.equal(historyLine(HR("nothing")), "Search history: everything is stored in the company database.");
assert.equal(historyLine(HR("off")), "Search history: sign-in is off, so nothing is stored.");
assert.equal(historyLine(HR("waiting", "You are offline.")), "Search history: You are offline.");
assert.match(removeSearchText({ rows: 3, who: "a@b.co" }, true), /^This search and its 3 results are removed from the company database\. Files on the computer it was run on are not touched\.$/);
assert.match(removeSearchText({ rows: 1, who: "ravi@b.co" }, false), /^ravi@b\.co's search and its 1 result are removed/);
assert.match(removeSearchText({ rows: 0, who: null }, false), /^This person's search is removed from the company database\./);
console.log("search history wording tests passed");

// ---------- the allowance (one pool for the company) ----------
assert.deepEqual([240, 90, 60, 30, 61, 1440, 15].map(windowText), ["4 hours", "1 hour 30 minutes", "1 hour", "30 minutes", "1 hour 1 minute", "24 hours", "15 minutes"]);
assert.equal(sharedNote("Acme"), "Your own total over all the assistants");
assert.equal(allowanceIntro("Acme"), "Every person in Acme has their own allowance, counted over all three assistants together, with their own cap and window. A new person starts at 3,000,000 tokens per 4 hours. Set each person's cap and window below. A person's window opens with their first tokens counted in it and, once its length has passed, their count starts again. Only admins can change it.");
assert.deepEqual(allowanceInput("3000000", "4"), { cap: 3000000, minutes: 240 });
assert.deepEqual(allowanceInput(" 2,500,000 ", "1,5"), { cap: 2500000, minutes: 90 });
assert.deepEqual(allowanceInput("1000", "0.25"), { cap: 1000, minutes: 15 });
assert.deepEqual(allowanceInput("1000000000", "24"), { cap: 1000000000, minutes: 1440 });
for (const [c, h] of [["999", "4"], ["1000000001", "4"], ["abc", "4"], ["", "4"], ["2.5", "4"], ["-5000", "4"]]) assert.match(allowanceInput(c, h), /^The cap must be a whole number of tokens between 1,000 and 1,000,000,000\.$/, `${c}`);
for (const [c, h] of [["5000", "0.2"], ["5000", "24.5"], ["5000", "x"], ["5000", ""]]) assert.match(allowanceInput(c, h), /^The window must be between 0\.25 hours \(15 minutes\) and 24 hours\.$/, `${h}`);
console.log("allowance wording tests passed");
