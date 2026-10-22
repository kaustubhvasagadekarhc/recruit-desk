// Author: kaustubh.s.v
//! App state shared by the Tauri commands and the headless runner: settings, usage,
//! running agents. UI-neutral; events go out through an `Emit` callback.

use crate::cap::{AgentUsage, Tokens};
use crate::runner::{self, EndReason, RunHandle, Sink};
use crate::settings::{self, Agent, Settings, UsageStore};
use crate::stream::Event;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

pub type Emit = Arc<dyn Fn(&str, Value) + Send + Sync>;

pub fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub agent: Agent,
    pub counted: u64,
    pub cache_read: u64,
    pub cap: u64,
    pub percent: f64,
    pub locked: bool,
    pub locked_at: Option<i64>,
    pub unlock_at: Option<i64>,
    /// When the current 4-hour usage window closes; None before any usage.
    pub resets_at: Option<i64>,
    pub clock_backwards: bool,
    pub running: bool,
    /// True when the cap and the window are the company's one shared pool (signed in to a company).
    pub shared: bool,
    pub window_minutes: Option<u32>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RunInput {
    #[serde(default)]
    pub jd: String,
    #[serde(default)]
    pub company: String,
    #[serde(default)]
    pub domain: String,
    /// Company scans: also search public LinkedIn, Indeed and job-board listings (step 3).
    #[serde(default, rename = "publicPosts")]
    pub public_posts: bool,
    /// Account research in batch: the text of a CSV of companies (columns company, domain, list).
    #[serde(default)]
    pub csv: String,
    /// Account research: keep people's own published emails and phones (opt-in; `--contacts`).
    #[serde(default)]
    pub contacts: bool,
    /// Set by start_run: where the CSV was saved, relative to account-agent/.
    #[serde(skip)]
    pub csv_path: String,
}

/// Most companies a CSV batch may hold (the agent's batch.py has the same limit).
pub const MAX_CSV_COMPANIES: usize = 200;

/// Check a CSV of companies before a batch run: a header with a `company` column and 1..=200 rows.
/// Returns the number of companies.
pub fn check_csv(text: &str) -> Result<usize, String> {
    if text.len() > 1_000_000 {
        return Err("That CSV is too large (over 1 MB).".into());
    }
    let mut lines = text.trim_start_matches('\u{feff}').lines().filter(|l| !l.trim().is_empty());
    let header = lines.next().ok_or("The CSV is empty.")?;
    let cols: Vec<String> = header.split(',').map(|c| c.trim().trim_matches('"').to_lowercase()).collect();
    if !cols.iter().any(|c| c == "company") {
        return Err("The CSV needs a header row with a 'company' column (optional: domain, list).".into());
    }
    let n = lines.count();
    if n == 0 {
        return Err("The CSV has no companies under its header.".into());
    }
    if n > MAX_CSV_COMPANIES {
        return Err(format!("The CSV lists {n} companies; the limit is {MAX_CSV_COMPANIES} per run."));
    }
    Ok(n)
}

/// A company name and an optional domain, cleaned and checked (company and account searches).
fn company_and_domain(input: &RunInput) -> Result<(String, String), String> {
    let name: String = input.company.split_whitespace().collect::<Vec<_>>().join(" ");
    if name.is_empty() {
        return Err("Enter a company name.".into());
    }
    if name.len() > 120 {
        return Err("The company name is too long.".into());
    }
    let domain = input
        .domain
        .trim()
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_lowercase();
    if !domain.is_empty()
        && !(domain.contains('.') && domain.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-'))
    {
        return Err(format!("'{domain}' does not look like a domain (example: acme.com)."));
    }
    Ok((name, domain))
}

/// Build the slash command for an agent. The prompt goes to claude on stdin.
pub fn build_prompt(agent: Agent, input: &RunInput) -> Result<String, String> {
    match agent {
        Agent::Candidate => {
            let jd = input.jd.trim();
            if jd.len() < 20 {
                return Err("Paste a job description (or pick a file) first.".into());
            }
            Ok(format!("/scout {jd}"))
        }
        Agent::Company => {
            let (name, domain) = company_and_domain(input)?;
            let mut prompt = if domain.is_empty() { format!("/company-scan {name}") } else { format!("/company-scan {name} {domain}") };
            if input.public_posts {
                prompt.push_str(" --public-posts");
            }
            Ok(prompt)
        }
        Agent::Account => {
            if !input.csv_path.is_empty() {
                let ok = input.csv_path.chars().all(|c| c.is_ascii_alphanumeric() || "-_./".contains(c));
                if !ok || input.csv_path.contains("..") {
                    return Err("Internal error: unexpected CSV path.".into());
                }
                let mut prompt = format!("/account-scan --csv {}", input.csv_path);
                if input.contacts {
                    prompt.push_str(" --contacts");
                }
                return Ok(prompt);
            }
            let (name, domain) = company_and_domain(input)?;
            let mut prompt = if domain.is_empty() { format!("/account-scan {name}") } else { format!("/account-scan {name} {domain}") };
            if input.contacts {
                prompt.push_str(" --contacts");
            }
            Ok(prompt)
        }
    }
}

pub struct Core {
    pub data_dir: PathBuf,
    pub settings: Mutex<Settings>,
    usage: Mutex<UsageStore>,
    runs: Mutex<HashMap<Agent, RunHandle>>,
    /// The company's shared allowance, once set up; the old per-computer allowance applies while it is not in charge.
    shared: Mutex<Option<Arc<crate::allowance::Allowance>>>,
}

impl Core {
    pub fn new(data_dir: PathBuf) -> Arc<Core> {
        let mut s: Settings = settings::load_json(&data_dir.join("settings.json"));
        if s.agents_root.is_empty() || !settings::is_agents_root(std::path::Path::new(&s.agents_root)) {
            if let Some(p) = settings::detect_agents_root() {
                s.agents_root = p.display().to_string();
            }
        }
        for a in Agent::ALL {
            s.caps.entry(a).or_insert(crate::cap::DEFAULT_CAP);
        }
        let usage: UsageStore = settings::load_json(&data_dir.join("usage.json"));
        let core = Arc::new(Core { data_dir, settings: Mutex::new(s), usage: Mutex::new(usage), runs: Mutex::new(HashMap::new()), shared: Mutex::new(None) });
        core.statuses(); // tick once at start: accrues lock time spent while closed
        core
    }

    pub fn set_shared(&self, a: Arc<crate::allowance::Allowance>) {
        *self.shared.lock().unwrap() = Some(a);
    }

    fn shared(&self) -> Option<Arc<crate::allowance::Allowance>> {
        self.shared.lock().unwrap().clone()
    }

    fn usage_path(&self) -> PathBuf {
        self.data_dir.join("usage.json")
    }

    pub fn save_settings(&self, s: Settings) -> Result<(), String> {
        if s.caps.values().any(|c| *c < 1000) {
            return Err("A cap must be at least 1,000 tokens.".into());
        }
        settings::save_json(&self.data_dir.join("settings.json"), &s)?;
        let now = now_ms();
        {
            let mut u = self.usage.lock().unwrap();
            for a in Agent::ALL {
                u.entry(a).or_default().enforce(s.cap(a), now);
            }
            settings::save_json(&self.usage_path(), &*u)?;
        }
        *self.settings.lock().unwrap() = s;
        Ok(())
    }

    /// The assistants with a run in progress.
    pub fn running_agents(&self) -> Vec<Agent> {
        self.runs.lock().unwrap().keys().copied().collect()
    }

    fn status_of(&self, a: Agent, u: &AgentUsage, cap: u64) -> Status {
        Status {
            agent: a,
            counted: u.counted,
            cache_read: u.cache_read,
            cap,
            percent: if cap == 0 { 100.0 } else { (u.counted as f64 / cap as f64 * 100.0).min(100.0) },
            locked: u.is_locked(),
            locked_at: u.locked_at,
            unlock_at: u.unlock_at(),
            resets_at: u.resets_at(),
            clock_backwards: u.clock_backwards,
            running: self.runs.lock().unwrap().contains_key(&a),
            shared: false,
            window_minutes: None,
        }
    }

    /// The status of one assistant from the company's shared pool.
    fn shared_status(&self, a: Agent, v: &crate::allowance::View) -> Status {
        Status {
            agent: a,
            counted: v.counted,
            cache_read: 0,
            cap: v.cap,
            percent: if v.cap == 0 { 100.0 } else { (v.counted as f64 / v.cap as f64 * 100.0).min(100.0) },
            locked: v.locked,
            locked_at: v.locked_at,
            unlock_at: if v.locked { v.resets_at } else { None },
            resets_at: v.resets_at,
            clock_backwards: false,
            running: self.runs.lock().unwrap().contains_key(&a),
            shared: true,
            window_minutes: Some(v.window_minutes),
        }
    }

    /// Advance the clock for both agents (unlocking any whose window ended) and report.
    pub fn statuses(&self) -> Vec<Status> {
        let s = self.settings.lock().unwrap().clone();
        let now = now_ms();
        let mut store = self.usage.lock().unwrap();
        let before = store.clone();
        for a in Agent::ALL {
            store.entry(a).or_default().tick(now);
        }
        if *store != before {
            let _ = settings::save_json(&self.usage_path(), &*store);
        }
        let shared = self.shared();
        Agent::ALL
            .iter()
            .map(|a| match shared.as_ref().and_then(|sh| sh.view(*a, now)) {
                Some(v) => self.shared_status(*a, &v),
                None => self.status_of(*a, &store[a], s.cap(*a)),
            })
            .collect()
    }

    pub fn status(&self, a: Agent) -> Status {
        self.statuses().into_iter().find(|s| s.agent == a).unwrap()
    }

    pub fn claude_path(&self) -> Result<PathBuf, String> {
        let s = self.settings.lock().unwrap();
        if !s.claude_path.is_empty() {
            return Ok(PathBuf::from(&s.claude_path));
        }
        settings::detect_claude().ok_or_else(|| "claude was not found. Set its path in Settings.".into())
    }

    pub fn start_run(self: &Arc<Self>, a: Agent, input: &RunInput, emit: Emit) -> Result<Status, String> {
        let st = self.status(a);
        if st.locked {
            return Err("This agent reached its token cap and is locked.".into());
        }
        if st.running {
            return Err("A run is already in progress.".into());
        }
        let dir = self.settings.lock().unwrap().agent_dir(a);
        if !dir.join("CLAUDE.md").is_file() {
            return Err(format!("{} is not an agent folder. Check the AI-AGENTS path in Settings.", dir.display()));
        }
        let mut input = input.clone();
        if a == Agent::Account && !input.csv.trim().is_empty() {
            check_csv(&input.csv)?;
            // The agent reads the list from its own folder; the app never passes outside paths.
            let rel = format!("runs/app-batch-{}.csv", now_ms());
            std::fs::create_dir_all(dir.join("runs")).map_err(|e| format!("Could not prepare the runs folder: {e}"))?;
            std::fs::write(dir.join(&rel), input.csv.as_bytes()).map_err(|e| format!("Could not save the CSV for the run: {e}"))?;
            input.csv_path = rel;
        }
        let prompt = build_prompt(a, &input)?;
        let claude = self.claude_path()?;
        let sink: Arc<dyn Sink> = Arc::new(CoreSink { core: self.clone(), agent: a, emit: emit.clone() });
        // Hold the runs lock across start so a fast-ending run cannot remove itself first.
        let mut runs = self.runs.lock().unwrap();
        let (workbook_dir, data_root) = {
            let s = self.settings.lock().unwrap();
            (s.workbook_dir(a), if s.data_root.trim().is_empty() { None } else { Some(PathBuf::from(&s.data_root)) })
        };
        let _ = std::fs::create_dir_all(&workbook_dir);
        let h = runner::start(&claude, &dir, Some(&workbook_dir), data_root.as_deref(), &prompt, sink)?;
        emit(
            "run-line",
            json!({"agent": a, "kind": "info", "text": format!("Started: {} (pid {}) in {}", first_line(&prompt), h.pid(), dir.display())}),
        );
        runs.insert(a, h);
        drop(runs);
        crate::history::begin(&self.data_dir, a, &input, now_ms() / 1000);   // the next sync stores the search in the person's history
        Ok(self.status(a))
    }

    pub fn stop_run(&self, a: Agent) -> bool {
        match self.runs.lock().unwrap().get(&a) {
            Some(h) => {
                h.stop(EndReason::Stopped);
                true
            }
            None => false,
        }
    }

    /// Record tokens. Returns false once the agent is at its cap.
    fn add_usage(&self, a: Agent, d: &Tokens) -> bool {
        if let Some(ok) = self.shared().and_then(|sh| sh.add(a, d, now_ms())) {
            return ok;   // the company's pool is in charge: counted here, sent to the database in a moment
        }
        let cap = self.settings.lock().unwrap().cap(a);
        let mut store = self.usage.lock().unwrap();
        let locked = store.entry(a).or_default().add(d, cap, now_ms());
        let _ = settings::save_json(&self.usage_path(), &*store);
        !locked
    }

    fn log_run(&self, a: Agent, reason: &EndReason, total: &Tokens) {
        use std::io::Write;
        let line = json!({"at": now_ms(), "agent": a, "end": format!("{reason:?}"), "tokens": total}).to_string();
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.data_dir.join("runs.jsonl")) {
            let _ = writeln!(f, "{line}");
        }
    }
}

fn first_line(s: &str) -> String {
    let l = s.lines().next().unwrap_or("");
    if l.chars().count() > 90 { format!("{}…", l.chars().take(90).collect::<String>()) } else { l.to_string() }
}

struct CoreSink {
    core: Arc<Core>,
    agent: Agent,
    emit: Emit,
}

impl Sink for CoreSink {
    fn event(&self, ev: &Event) {
        let (kind, text) = match ev {
            Event::Checkpoint(t) => ("checkpoint", t.clone()),
            Event::Info(t) => ("info", t.clone()),
            Event::Warn(t) => ("warn", t.clone()),
            Event::Result { ok, text } => (if *ok { "result" } else { "error" }, text.clone()),
            Event::Usage(_) => return,
        };
        // The run's finished and failed steps and its result go to the log (never the start of a step, never search text).
        let step_line = kind == "checkpoint" && (text.starts_with('✓') || text.starts_with('✗'));
        if step_line || kind == "result" || kind == "error" {
            let level = if text.starts_with('✗') || kind == "error" { "error" } else { "info" };
            crate::logs::add(&self.core.data_dir, "run", level, Some(self.agent), &text, json!({}), now_ms());
        }
        (self.emit)("run-line", json!({"agent": self.agent, "kind": kind, "text": text}));
    }
    fn usage(&self, d: &Tokens) -> bool {
        let go_on = self.core.add_usage(self.agent, d);
        (self.emit)("usage", serde_json::to_value(self.core.status(self.agent)).unwrap_or_default());
        go_on
    }
    fn stderr(&self, line: &str) {
        // Strip ANSI colour codes; flag the "workspace not trusted" warning, which means
        // the agent's allow list is ignored and its scripts will be denied.
        let mut clean = String::new();
        let mut esc = false;
        for ch in line.chars() {
            match (esc, ch) {
                (false, '\u{1b}') => esc = true,
                (true, c) if c.is_ascii_alphabetic() => esc = false,
                (true, _) => {}
                (false, c) => clean.push(c),
            }
        }
        let kind = if clean.contains("not been trusted") { "warn" } else { "stderr" };
        (self.emit)("run-line", json!({"agent": self.agent, "kind": kind, "text": clean}));
    }
    fn ended(&self, reason: &EndReason, total: &Tokens) {
        self.core.runs.lock().unwrap().remove(&self.agent);
        self.core.log_run(self.agent, reason, total);
        crate::history::finish(&self.core.data_dir, self.agent, reason, now_ms() / 1000);
        {
            let (level, what) = match reason {
                EndReason::Finished { ok: true } => ("info", "Run finished".to_string()),
                EndReason::Finished { ok: false } => ("error", "Run ended with an error".to_string()),
                EndReason::Stopped => ("warn", "Run stopped by the person".to_string()),
                EndReason::Cap => ("warn", "Run stopped: the allowance was reached".to_string()),
                EndReason::Failed(e) => ("error", format!("Run failed: {e}")),
            };
            crate::logs::add(&self.core.data_dir, "run", level, Some(self.agent), &what, json!({"counted_tokens": total.counted()}), now_ms());
        }
        let (status, text) = match reason {
            EndReason::Finished { ok: true } => ("done", "Run finished.".to_string()),
            EndReason::Finished { ok: false } => ("error", "Run ended with an error.".to_string()),
            EndReason::Stopped => ("stopped", "Run stopped by you.".to_string()),
            EndReason::Cap => ("cap", "Run stopped: the agent reached its token cap.".to_string()),
            EndReason::Failed(e) => ("error", format!("Run failed: {e}")),
        };
        (self.emit)(
            "run-ended",
            json!({"agent": self.agent, "status": status, "text": text, "tokens": total, "counted": total.counted()}),
        );
        (self.emit)("usage", serde_json::to_value(self.core.status(self.agent)).unwrap_or_default());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prompts() {
        let c = RunInput { company: " Humancloud  Technologies ".into(), domain: "https://humancloud.co.in/".into(), ..Default::default() };
        assert_eq!(build_prompt(Agent::Company, &c).unwrap(), "/company-scan Humancloud Technologies humancloud.co.in");
        let bad = RunInput { company: "X".into(), domain: "rm -rf /".into(), ..Default::default() };
        assert!(build_prompt(Agent::Company, &bad).is_err());
        let jd = RunInput { jd: "Senior React developer, Pune, 5+ years\nMust: React".into(), ..Default::default() };
        assert!(build_prompt(Agent::Candidate, &jd).unwrap().starts_with("/scout Senior React"));
        assert!(build_prompt(Agent::Candidate, &RunInput::default()).is_err());
    }

    #[test]
    fn public_posts_flag() {
        let on = RunInput { company: "Razorpay".into(), domain: "razorpay.com".into(), public_posts: true, ..Default::default() };
        assert_eq!(build_prompt(Agent::Company, &on).unwrap(), "/company-scan Razorpay razorpay.com --public-posts");
        let no_domain = RunInput { company: "Razorpay".into(), public_posts: true, ..Default::default() };
        assert_eq!(build_prompt(Agent::Company, &no_domain).unwrap(), "/company-scan Razorpay --public-posts");
        let off = RunInput { company: "Razorpay".into(), domain: "razorpay.com".into(), ..Default::default() };
        assert!(!build_prompt(Agent::Company, &off).unwrap().contains("--public-posts"));
        // The UI sends camelCase; a missing field means off (older front ends).
        let j: RunInput = serde_json::from_str(r#"{"company":"Razorpay","publicPosts":true}"#).unwrap();
        assert!(j.public_posts);
        let j: RunInput = serde_json::from_str(r#"{"company":"Razorpay"}"#).unwrap();
        assert!(!j.public_posts);
        // The candidate agent ignores the flag.
        let jd = RunInput { jd: "Senior React developer, Pune, 5+ years".into(), public_posts: true, ..Default::default() };
        assert!(!build_prompt(Agent::Candidate, &jd).unwrap().contains("--public-posts"));
    }

    #[test]
    fn account_prompts_and_csv() {
        let one = RunInput { company: "  Razorpay ".into(), domain: "https://Razorpay.com/".into(), ..Default::default() };
        assert_eq!(build_prompt(Agent::Account, &one).unwrap(), "/account-scan Razorpay razorpay.com");
        let name_only = RunInput { company: "Razorpay".into(), ..Default::default() };
        assert_eq!(build_prompt(Agent::Account, &name_only).unwrap(), "/account-scan Razorpay");
        assert!(build_prompt(Agent::Account, &RunInput::default()).is_err());
        let bad = RunInput { company: "X".into(), domain: "a b; rm".into(), ..Default::default() };
        assert!(build_prompt(Agent::Account, &bad).is_err());
        let batch = RunInput { csv_path: "runs/app-batch-1.csv".into(), ..Default::default() };
        assert_eq!(build_prompt(Agent::Account, &batch).unwrap(), "/account-scan --csv runs/app-batch-1.csv");
        let opt_in = RunInput { company: "Razorpay".into(), domain: "razorpay.com".into(), contacts: true, ..Default::default() };
        assert_eq!(build_prompt(Agent::Account, &opt_in).unwrap(), "/account-scan Razorpay razorpay.com --contacts");
        let batch_in = RunInput { csv_path: "runs/app-batch-1.csv".into(), contacts: true, ..Default::default() };
        assert_eq!(build_prompt(Agent::Account, &batch_in).unwrap(), "/account-scan --csv runs/app-batch-1.csv --contacts");
        let evil = RunInput { csv_path: "../x.csv; rm".into(), ..Default::default() };
        assert!(build_prompt(Agent::Account, &evil).is_err());
        assert_eq!(check_csv("\u{feff}Company,Domain\nRazorpay,razorpay.com\n\nAcme,\n"), Ok(2));
        assert!(check_csv("name,site\nAcme,acme.com").is_err());
        assert!(check_csv("company\n").is_err());
        assert!(check_csv("").is_err());
        let many = format!("company\n{}", "x\n".repeat(MAX_CSV_COMPANIES + 1));
        assert!(check_csv(&many).is_err());
        let j: RunInput = serde_json::from_str(r#"{"csv":"company\nA","csvPath":"../../etc"}"#).unwrap();
        assert!(j.csv_path.is_empty(), "the CSV path is never taken from the front end");
    }

    #[test]
    fn usage_survives_a_restart_of_core() {
        let dir = std::env::temp_dir().join(format!("recruit-desk-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let core = Core::new(dir.clone());
        let mut s = core.settings.lock().unwrap().clone();
        s.caps.insert(Agent::Company, 1000);
        core.save_settings(s).unwrap();
        assert!(!core.add_usage(Agent::Company, &Tokens { output: 1500, ..Default::default() }));
        drop(core);
        let again = Core::new(dir.clone());
        let st = again.status(Agent::Company);
        assert!(st.locked);
        assert_eq!(st.counted, 1500);
        assert_eq!(st.cap, 1000);
        assert!(!again.status(Agent::Candidate).locked);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
