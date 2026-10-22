// Author: kaustubh.s.v
// Screenshot harness: a fake window.__TAURI__ so the real front end runs in a browser with fixture data.
// Scenario comes from ?s=...; the driver script can also call window.__mock.* to push events.
(function () {
  const q = new URLSearchParams(location.search);
  const S = q.get("s") || "idle";
  const theme = q.get("theme");
  try { localStorage.clear(); if (theme) localStorage.setItem("theme", theme); if (q.get("view")) localStorage.setItem("view", q.get("view")); } catch {}
  let FIX = null;
  const fx = fetch("fixtures.json").then((r) => r.json()).then((j) => (FIX = j));
  const listeners = {};
  const now = Date.now();
  const cap = 3000000;
  const POOLWIN = { candidate: 240, company: 240, account: 240 };
  let PEOPLE = [
    { email: "admin@humancloud.co.in", cap: 3000000, window_minutes: 240, counted: 210000, window_started_ms: Date.now() - 3600000, locked_at_ms: null },
    { email: "ann@humancloud.co.in", cap: 3000000, window_minutes: 240, counted: 2950000, window_started_ms: Date.now() - 1800000, locked_at_ms: null },
    { email: "bob@humancloud.co.in", cap: 3000000, window_minutes: 240, counted: 3000000, window_started_ms: Date.now() - 7200000, locked_at_ms: Date.now() - 600000 },
  ];
  const status = {
    candidate: { agent: "candidate", counted: 0, cache_read: 0, cap, percent: 0, locked: false, locked_at: null, unlock_at: null, clock_backwards: false, running: false },
    company: { agent: "company", counted: 210000, cache_read: 1800000, cap, percent: 7, locked: false, locked_at: null, unlock_at: null, clock_backwards: false, running: false },
    account: { agent: "account", counted: 102000, cache_read: 1500000, cap, percent: 3.4, locked: false, locked_at: null, unlock_at: now + 3 * 3600000, resets_at: now + (3 * 3600 + 22 * 60) * 1000, clock_backwards: false, running: false },
  };
  if (S === "near") Object.assign(status.company, { counted: 2610000, percent: 87 }), Object.assign(status.candidate, { counted: 2610000, percent: 87 });
  if (S === "locked") for (const a of ["company", "candidate", "account"]) Object.assign(status[a], { counted: 3004120, percent: 100.1, locked: true, locked_at: now - 47 * 60000, unlock_at: now + (3 * 3600 + 13 * 60 + 9) * 1000 });
  const okChecks = [
    { name: "Claude Code CLI", ok: true, detail: "claude 2.1.300", fix: "" },
    { name: "Claude login", ok: true, detail: "Logged in.", fix: "" },
    { name: "Agent folders trusted by Claude Code", ok: true, detail: "All three trusted.", fix: "" },
    { name: "uv", ok: true, detail: "uv 0.9.2", fix: "" },
    { name: "Agents folder", ok: true, detail: "/home/user/Documents/Recruit Desk/AI-AGENTS", fix: "" },
    { name: "Exa search key", ok: true, detail: "Saved for every assistant.", fix: "" },
    { name: "Assistant packages prepared", ok: true, detail: "Every assistant has its Python packages.", fix: "" },
  ];
  const badChecks = okChecks.map((c) => ({ ...c }));
  badChecks[1] = { name: "Claude login", ok: false, detail: "claude auth status: loggedIn=false.", fix: "Run: claude auth login" };
  badChecks[2] = { name: "Agent folders trusted by Claude Code", ok: false, detail: "Not trusted yet: account-agent.", fix: "Press Trust the agent folders." };
  badChecks[5] = { name: "Exa search key", ok: false, detail: "EXA_API_KEY is empty in both .env files.", fix: "Get a free key at https://dashboard.exa.ai and paste it here." };
  badChecks[6] = { name: "Assistant packages prepared", ok: false, detail: "No .venv yet.", fix: "Press Prepare now, or run `uv sync` in each folder." };
  // ?auth=out|ended|in|admin|offline|nocompany (default: no sign-in set, as before). Password of the fake service: "correct horse battery".
  // The invitation code the fake service accepts is 11111111-2222-3333-4444-555555555555.
  const CODE = "rd1.eyJhIjoiaHR0cHM6Ly9hdXRoLmV4YW1wbGUuY29tL2F1dGgiLCJyIjoiaHR0cHM6Ly9hcGkuZXhhbXBsZS5jb20vcmVzdC92MSIsIm8iOiJodHRwczovL3JlY3J1aXRkZXNrLmV4YW1wbGUifQ";
  const GOOD_INVITE = "11111111-2222-3333-4444-555555555555";
  const signedIn = (email, role, extra = {}) => ({ enabled: true, server: CODE, state: "signed_in", user: { email, role, tenant: window.__mockTenant === "" ? undefined : (window.__mockTenant || "Humancloud Pvt Ltd"), tenant_id: "a0000000-0000-0000-0000-00000000000a" }, offline: false, offline_until: null, remembered: true, message: null, ...extra });
  const A = q.get("auth");
  let AUTH = { enabled: false, server: "", state: "disabled", user: null, offline: false, offline_until: null, remembered: true, message: null };
  if (A === "out") AUTH = { ...AUTH, enabled: true, server: CODE, state: "signed_out" };
  if (A === "ended") AUTH = { ...AUTH, enabled: true, server: CODE, state: "signed_out", message: "Your session ended. Sign in again." };
  if (A === "in") AUTH = signedIn("ann@humancloud.co.in", "user");
  if (A === "admin") AUTH = signedIn("admin@humancloud.co.in", "admin");
  if (A === "offline") AUTH = signedIn("ann@humancloud.co.in", "user", { offline: true, offline_until: Math.floor(Date.now() / 1000) + 3 * 86400 });
  if (A === "nocompany") AUTH = { ...AUTH, enabled: true, server: CODE, state: "no_company", user: { email: "new.person@humancloud.co.in", role: "", tenant: "", tenant_id: "" }, message: "You are not part of a company yet. Enter the invitation code your admin gave you." };
  // People and access (?auth=admin): an in-memory fake of the admin calls. ?admin=empty starts with only the admin; ?admin=fail makes every call fail.
  const uid = (n) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
  const ADMIN = { nextId: 10, users: [
    { id: uid(1), email: "admin@humancloud.co.in", role: "admin", owner: true, joined_at: "2026-10-01T09:00:00.000Z" },
    { id: uid(2), email: "ann@humancloud.co.in", role: "user", owner: false, joined_at: "2026-10-05T09:00:00.000Z" },
    { id: uid(3), email: "ravi.k@humancloud.co.in", role: "admin", owner: false, joined_at: "2026-10-06T09:00:00.000Z" },
  ], invites: [
    { id: uid(5), email: "new.recruiter@humancloud.co.in", role: "user", expires_at: new Date(Date.now() + 40 * 3600000).toISOString(), created_at: "2026-10-16T09:00:00Z" },
    { id: uid(6), email: "lead@humancloud.co.in", role: "admin", expires_at: new Date(Date.now() + 20 * 3600000).toISOString(), created_at: "2026-10-17T09:00:00Z" },
  ] };
  if (q.get("admin") === "empty") { ADMIN.users = ADMIN.users.slice(0, 1); ADMIN.invites = []; }
  // Search history (?hist=empty|fail|waiting): searches of three people; a user sees only their own, an admin sees all.
  const iso = (minsAgo) => new Date(Date.now() - minsAgo * 60000).toISOString();
  const HIST = { searches: [
    { id: "00000000-0000-0000-0000-0000000000a1", who: "ann@humancloud.co.in", agent: "candidate", inputs: { jd: "Senior React Frontend Developer\nPune, 5+ years, TypeScript" }, started_at: iso(50), finished_at: iso(40), status: "done" },
    { id: "00000000-0000-0000-0000-0000000000a2", who: "admin@humancloud.co.in", agent: "company", inputs: { company: "Razorpay", domain: "razorpay.com", publicPosts: true }, started_at: iso(300), finished_at: iso(290), status: "done" },
    { id: "00000000-0000-0000-0000-0000000000a3", who: "ravi.k@humancloud.co.in", agent: "account", inputs: { csv: "company,domain\nAcme,acme.com\nBeta,beta.io\nGamma,gamma.dev\nDelta,delta.ai\nEps,eps.co" }, started_at: iso(1500), finished_at: iso(1490), status: "stopped" },
    { id: "00000000-0000-0000-0000-0000000000a4", who: "ann@humancloud.co.in", agent: "company", inputs: { company: "Humancloud", domain: "" , publicPosts: false }, started_at: iso(2), finished_at: null, status: "running" },
  ], rows: {
    "00000000-0000-0000-0000-0000000000a1": [
      { agent: "candidate", record: { name: "Ann Rao", title: "Senior Frontend Engineer", company: "Acme", match_score: 85, jd: "Senior React Frontend Developer" }, created_at: iso(45), who: "ann@humancloud.co.in" },
      { agent: "candidate", record: { name: "Bob Sen", title: "Frontend Lead", company: "Beta", match_score: 78, jd: "Senior React Frontend Developer" }, created_at: iso(44), who: "ann@humancloud.co.in" },
      { agent: "candidate", record: { name: "Cy Das", title: "UI Engineer", company: "Gamma", match_score: 71, jd: "Senior React Frontend Developer" }, created_at: iso(43), who: "ann@humancloud.co.in" } ],
    "00000000-0000-0000-0000-0000000000a2": [
      { agent: "company", record: { job_title: "SRE", company: "Razorpay", location: "Bengaluru", status: "Open" }, created_at: iso(295), who: "admin@humancloud.co.in" },
      { agent: "company", record: { job_title: "Backend Engineer", company: "Razorpay", location: "Remote", status: "Open" }, created_at: iso(294), who: "admin@humancloud.co.in" } ],
    "00000000-0000-0000-0000-0000000000a3": [
      { agent: "account", record: { company: "Acme", domain: "acme.com", product_type: "SaaS", funding: "Series B" }, created_at: iso(1495), who: "ravi.k@humancloud.co.in" } ],
  } };
  if (q.get("hist") === "empty") { HIST.searches = []; }
  const histSee = () => HIST.searches.filter((x) => AUTH.user && (AUTH.user.role === "admin" || x.who === AUTH.user.email)).map((x) => ({ ...x, rows: (HIST.rows[x.id] || []).length }));
  const histFail = () => { if (q.get("hist") === "fail") throw "The service had a problem. Try again in a minute."; };
  let REC = [];
  const STORED = [
    { agent: "account", record: { company: "Gamma", domain: "gamma.dev", product_type: "SaaS", funding: "Series A" }, created_at: "2026-10-18T12:00:00", stored_at: "2026-10-18T12:01:00", stored_by: "ann@humancloud.co.in" },
    { agent: "candidate", record: { name: "Ann Rao", title: "Senior Frontend Engineer", company: "Acme", match_score: 85, jd: "Senior React Frontend Developer" }, created_at: "2026-10-18T10:00:00", stored_at: "2026-10-18T10:01:00", stored_by: "ravi.k@humancloud.co.in" },
    { agent: "company", record: { job_title: "SRE", company: "Razorpay", location: "Bengaluru", status: "Open" }, created_at: "2026-10-18T09:00:00", stored_at: "2026-10-18T09:01:00", stored_by: null },
  ];
  async function adminCall(fn) {
    await new Promise((ok) => setTimeout(ok, 150));
    if (AUTH.state !== "signed_in" || AUTH.user.role !== "admin") throw "Only an admin can do that.";
    if (q.get("admin") === "fail") throw "The service had a problem. Try again in a minute.";
    return fn();
  }
  // ?live=1: Accounts runs go to live-bridge.mjs, which drives the app's real backend (headless binary).
  const LIVE = q.get("live") === "1";
  async function liveRun(input) {
    const r = await fetch(`/api/start?company=${encodeURIComponent(input.company || "")}&domain=${encodeURIComponent(input.domain || "")}&contacts=${input.contacts ? 1 : 0}`).then((x) => x.json());
    if (!r.ok) { emit("run-ended", { agent: "account", status: "error", text: r.error, counted: 0 }); status.account.running = false; return; }
    let from = 0;
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 1000));
      const { events, next } = await fetch(`/api/events?from=${from}`).then((x) => x.json());
      from = next;
      for (const e of events) {
        if (e.type === "line") emit("run-line", { agent: "account", kind: e.kind, text: e.text });
        else if (e.type === "usage") { Object.assign(status.account, { counted: e.counted, percent: e.percent }); emit("usage", { ...status.account }); }
        else if (e.type === "end") { status.account.running = false; emit("run-ended", { agent: "account", status: e.status, text: e.text, counted: e.counted }); return; }
      }
    }
  }
  function emit(name, payload) { (listeners[name] || []).forEach((cb) => cb({ payload })); }
  async function invoke(cmd, args = {}) {
    await fx;
    switch (cmd) {
      case "get_settings": return { settings: { agents_root: "/home/user/Documents/Recruit Desk/AI-AGENTS", claude_path: "", caps: { candidate: cap, company: cap, account: cap }, data_root: q.get("data") === "none" ? "" : "C:\\Users\\ann\\Documents\\RecruitDesk-Data" }, version: window.__mockVersion || "0.6.2", detected_root: "/home/user/Documents/Recruit Desk/AI-AGENTS", detected_claude: "/home/user/.local/bin/claude", data_dir: "/home/user/.local/share/in.humancloud.recruitdesk", platform: "windows" };
      case "get_status": {
        const shared = AUTH.state === "signed_in" && !AUTH.offline && q.get("pool") !== "local";
        const total = ["candidate", "company", "account"].reduce((n, a) => n + status[a].counted, 0);
        const common = status.company.cap;
        return ["candidate", "company", "account"].map((a) => {
          const st = status[a];
          if (!shared) return { ...st, shared, window_minutes: null, percent: Math.min(100, st.counted / st.cap * 100) };
          // one allowance per person over all the assistants: every assistant shows the person's total
          const locked = st.locked || ["candidate", "company", "account"].some((x) => status[x].locked) || total >= common;
          return { ...st, shared, window_minutes: POOLWIN.company, counted: total, cap: common, locked, percent: Math.min(100, total / common * 100) };
        });
      }
      case "allowance_refresh": window.__mock.counts.allowance_refresh = (window.__mock.counts.allowance_refresh || 0) + 1; return null;
      case "allowance_set_user": {
        if (!AUTH.user || AUTH.user.role !== "admin") throw "Only an admin can change the allowance.";
        if (q.get("pool") === "fail") throw "The service had a problem. Try again in a minute.";
        if (args.cap < 1000 || args.cap > 1000000000 || args.minutes < 15 || args.minutes > 1440) throw "Check the numbers and the address: the cap is at least 1,000 tokens, the window is between 15 minutes and 24 hours, and the person must be in your company.";
        const who = PEOPLE.find((p) => p.email === args.email);
        if (!who) throw "Check the numbers and the address: the cap is at least 1,000 tokens, the window is between 15 minutes and 24 hours, and the person must be in your company.";
        who.cap = args.cap; who.window_minutes = args.minutes; who.locked_at_ms = who.counted >= args.cap ? (who.locked_at_ms || Date.now()) : null;
        if (args.email === AUTH.user.email) for (const a of ["candidate", "company", "account"]) { status[a].cap = args.cap; POOLWIN[a] = args.minutes; status[a].locked = false; }
        window.__mock.allowance = { ...(window.__mock.allowance || {}), [args.email]: { cap: args.cap, minutes: args.minutes } };
        return null;
      }
      case "allowance_reset": {
        if (!AUTH.user || AUTH.user.role !== "admin") throw "Only an admin can change the allowance.";
        window.__mock.resets = (window.__mock.resets || 0) + 1;
        window.__mock.resetOf = args.email ?? null;
        if (args.email == null || args.email === AUTH.user.email) for (const a of ["candidate", "company", "account"]) Object.assign(status[a], { counted: 0, cache_read: 0, locked: false, locked_at: null, unlock_at: null, resets_at: null });
        PEOPLE = args.email == null ? PEOPLE.map((p) => ({ ...p, counted: 0, locked_at_ms: null })) : PEOPLE.map((p) => (p.email === args.email ? { ...p, counted: 0, locked_at_ms: null } : p));
        return null;
      }
      case "allowance_people": {
        if (!AUTH.user || AUTH.user.role !== "admin") throw "Only an admin can see everyone's allowance.";
        window.__mock.counts.allowance_people = (window.__mock.counts.allowance_people || 0) + 1;
        return { now_ms: Date.now(), people: PEOPLE.map((p) => ({ ...p })) };
      }
      case "check_prereqs": return S === "setup-bad" ? badChecks : okChecks;
      case "read_workbook": {
        window.__mock.counts.read_workbook = (window.__mock.counts.read_workbook || 0) + 1;
        const a = args.agent;
        if (LIVE && a === "account") {
          const b = await fetch("/api/workbook").then((r) => r.json());
          if (!b) return { path: "x.xlsx", exists: false, modified_ms: null, data: { sheets: [], sheet: null, headers: [], rows: [] } };
          const sheet = args.sheet && b.sheets[args.sheet] ? args.sheet : b.order[0];
          return { path: "account-agent/Account_Research.xlsx", exists: true, modified_ms: Date.now(), data: { sheets: b.order, sheet, headers: b.sheets[sheet].headers, rows: b.sheets[sheet].rows } };
        }
        if (S === "empty" || (S === "empty-cand" && a === "candidate")) return { path: "x.xlsx", exists: false, modified_ms: null, data: { sheets: [], sheet: null, headers: [], rows: [] } };
        const b = FIX[a];
        if (!b) return { path: "x.xlsx", exists: false, modified_ms: null, data: { sheets: [], sheet: null, headers: [], rows: [] } };
        const sheet = args.sheet && b.sheets[args.sheet] ? args.sheet : (q.get("sheet") && b.sheets[q.get("sheet")] ? q.get("sheet") : b.order[0]);
        const d = b.sheets[sheet];
        return { path: `/x/${a}.xlsx`, exists: true, modified_ms: now - 26 * 60000, data: { sheets: b.order, sheet, headers: d.headers, rows: d.rows } };
      }
      case "start_run": {
        window.__mock.counts.start_run = (window.__mock.counts.start_run || 0) + 1;
        const s = status[args.agent]; s.running = true; window.__mock.lastInput = args.input;
        if (LIVE && args.agent === "account") liveRun(args.input);
        return { ...s };
      }
      case "stop_run": return true;
      case "admin_members": return adminCall(() => ADMIN.users.map((u) => ({ ...u, you: u.email === AUTH.user.email })));
      case "admin_invites": return adminCall(() => ADMIN.invites.map((i) => ({ ...i })));
      case "admin_invite": return adminCall(() => {
        if (ADMIN.users.some((u) => u.email === args.email.toLowerCase())) throw "That person is already in your company.";
        if (ADMIN.invites.some((i) => i.email === args.email.toLowerCase())) throw "That person was already invited. Withdraw the old invitation first.";
        const inv = { id: uid(++ADMIN.nextId), email: args.email.toLowerCase(), role: args.role, expires_at: new Date(Date.now() + 2 * 86400000).toISOString(), created_at: new Date().toISOString() };
        ADMIN.invites.push(inv);
        return { email: inv.email, role: inv.role, expires_at: inv.expires_at, code: inv.id };
      });
      case "admin_revoke_invite": return adminCall(() => {
        if (!ADMIN.invites.some((i) => i.id === args.id)) throw "That person or invitation no longer exists. Refresh the list.";
        ADMIN.invites = ADMIN.invites.filter((i) => i.id !== args.id); return null;
      });
      case "admin_set_role": return adminCall(() => {
        const u = ADMIN.users.find((x) => x.id === args.id);
        if (!u) throw "That person or invitation no longer exists. Refresh the list.";
        if (u.owner) throw "There must be at least one admin.";
        u.role = args.role;
        return null;
      });
      case "admin_remove": return adminCall(() => {
        const u = ADMIN.users.find((x) => x.id === args.id);
        if (!u) throw "That person or invitation no longer exists. Refresh the list.";
        if (u.owner) throw "There must be at least one admin.";
        ADMIN.users = ADMIN.users.filter((x) => x.id !== args.id);
        return null;
      });
      case "history_sync": {
        window.__mock.counts.history_sync = (window.__mock.counts.history_sync || 0) + 1;
        await new Promise((ok) => setTimeout(ok, 80));
        return q.get("hist") === "waiting" ? { state: "waiting", message: "You are offline. Your history is stored when you are back online.", searches: 0, rows: 0, removed: 0, at: Date.now() }
          : { state: "sent", message: "Your history is stored in the company database.", searches: 1, rows: 3, removed: 0, at: Date.now() };
      }
      case "update_check": {
        window.__mock.counts.update_check = (window.__mock.counts.update_check || 0) + 1;
        return q.get("upd") === "new" ? { available: true, version: "0.2.0", notes: "Faster tables and a fix for restoring workbooks.\nMore later.", message: "" }
          : q.get("upd") === "fail" ? Promise.reject("Could not reach the update server. Check your internet connection and try again.")
          : { available: false, version: "", notes: "", message: "Recruit Desk is up to date." };
      }
      case "update_install": {
        window.__mock.counts.update_install = (window.__mock.counts.update_install || 0) + 1;
        return null;
      }
      case "open_data_folder": window.__mock.counts.open_data_folder = (window.__mock.counts.open_data_folder || 0) + 1; if (q.get("data") === "fail") throw "Could not open the folder."; return null;
      case "logs_list": {
        if (!AUTH.user || AUTH.user.role !== "admin") throw "Only an admin can read the logs.";
        if (q.get("logs") === "fail") throw "The service had a problem. Try again in a minute.";
        window.__mock.counts.logs_list = (window.__mock.counts.logs_list || 0) + 1;
        window.__mock.lastLogFilter = { kind: args.kind, level: args.level, email: args.email };
        const now = Date.now();
        const all = [
          { id: 5, user_email: "bob@humancloud.co.in", kind: "run", level: "error", agent: "company", message: "✗ Step 2/6 · the careers page could not be read", at: new Date(now - 60000).toISOString() },
          { id: 4, user_email: "ann@humancloud.co.in", kind: "run", level: "info", agent: "candidate", message: "Run finished", at: new Date(now - 600000).toISOString() },
          { id: 3, user_email: "ann@humancloud.co.in", kind: "signin", level: "info", agent: null, message: "Signed in", at: new Date(now - 3600000).toISOString() },
          { id: 2, user_email: "bob@humancloud.co.in", kind: "event", level: "warn", agent: null, message: "Search history could not be stored: waiting", at: new Date(now - 7200000).toISOString() },
          { id: 1, user_email: "admin@humancloud.co.in", kind: "event", level: "info", agent: null, message: "App started", at: new Date(now - 9000000).toISOString() },
        ];
        return all.filter((l) => (!args.kind || l.kind === args.kind) && (!args.level || l.level === args.level) && (!args.email || l.user_email === String(args.email).toLowerCase()));
      }
      case "history_list": return (histFail(), histSee());
      case "history_rows": return (histFail(), (HIST.rows[args.searchId] || []).filter(() => histSee().some((x) => x.id === args.searchId)).map((r) => ({ ...r })));
      case "history_delete": {
        histFail();
        const x = HIST.searches.find((y) => y.id === args.searchId);
        if (!x || !histSee().some((y) => y.id === x.id)) throw "That search no longer exists, or you are not allowed to remove it. Refresh the list.";
        HIST.searches = HIST.searches.filter((y) => y.id !== x.id); delete HIST.rows[x.id];
        return null;
      }
      case "records_sync": {
        window.__mock.counts.records_sync = (window.__mock.counts.records_sync || 0) + 1;
        await new Promise((ok) => setTimeout(ok, 120));
        const mode = q.get("rec");
        REC = ["candidate", "company", "account"].map((a) => (mode === "waiting" ? { agent: a, state: "waiting", message: "You are offline. It is sent when you are back online.", at: Date.now() }
          : mode === "error" && a === "company" ? { agent: a, state: "error", message: "The database did not accept this record.", at: Date.now() }
          : a === "account" && mode === "older" ? { agent: a, state: "older", message: "x", at: Date.now() }
          : a === "company" && mode === "none" ? { agent: a, state: "nothing", message: "x", at: Date.now() } : { agent: a, state: "sent", message: "x", at: Date.now() }));
        return REC;
      }
      case "records_status": return REC;
      case "records_stored": return adminCall(() => STORED.map((r) => ({ ...r })));
      case "auth_status": case "auth_check": return { ...AUTH };
      case "auth_login": {
        await new Promise((ok) => setTimeout(ok, 400));
        if (args.password !== "correct horse battery") throw "That email and password do not match.";
        AUTH = signedIn(args.email.trim().toLowerCase(), args.email.toLowerCase().startsWith("admin") ? "admin" : "user");
        return { ...AUTH };
      }
      case "auth_create_account": {
        await new Promise((ok) => setTimeout(ok, 300));
        if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(String(args.code).trim())) throw "That does not look like an invitation code. It is made of letters, numbers and dashes, like 1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed.";
        if (args.password.length < 10) throw "Choose a password of at least 10 characters.";
        if (args.email.toLowerCase() === "ann@humancloud.co.in") throw "That email already has an account. Sign in instead.";
        if (args.code.trim().toLowerCase() !== GOOD_INVITE) {
          AUTH = { ...AUTH, enabled: true, server: CODE, state: "no_company", user: { email: args.email.trim().toLowerCase(), role: "", tenant: "", tenant_id: "" }, message: "That invitation code did not work. Check it, make sure you use the email address it was sent to, or ask your admin for a new one." };
          return { ...AUTH };
        }
        AUTH = signedIn(args.email.trim().toLowerCase(), "user");
        return { ...AUTH };
      }
      case "auth_join_company": {
        await new Promise((ok) => setTimeout(ok, 300));
        if (String(args.code).trim().toLowerCase() !== GOOD_INVITE) throw "That invitation code did not work. Check it, make sure you use the email address it was sent to, or ask your admin for a new one.";
        AUTH = signedIn(AUTH.user.email, "user");
        return { ...AUTH };
      }
      case "auth_forgot_password": {
        await new Promise((ok) => setTimeout(ok, 300));
        return "If that address has an account, an email with a reset link is on its way. It can take a few minutes.";
      }
      case "auth_reset_password": {
        await new Promise((ok) => setTimeout(ok, 300));
        if (!String(args.link).includes("mockResetToken")) throw "This reset link is no longer valid. Ask for a new one.";
        return "Password changed. Sign in with your new password.";
      }
      case "auth_logout": AUTH = { ...AUTH, state: "signed_out", user: null, offline: false, offline_until: null, message: null }; return { ...AUTH };
      default: return "OK";
    }
  }
  window.__TAURI__ = {
    core: { invoke },
    event: { listen: async (name, cb) => { (listeners[name] ||= []).push(cb); return () => {}; } },
  };
  window.__mock = {
    emit, status, counts: {},
    line(agent, text, kind = "checkpoint") { emit("run-line", { agent, kind, text }); },
    end(agent, st, counted = 69000) { status[agent].running = false; emit("run-ended", { agent, status: st, text: "Run finished.", counted }); },
  };
})();
