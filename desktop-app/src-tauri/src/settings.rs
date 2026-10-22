// Author: kaustubh.s.v
//! Settings, agent definitions, path detection and the prerequisite check.

use crate::cap::{AgentUsage, DEFAULT_CAP};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Agent {
    Candidate,
    Company,
    Account,
}

impl Agent {
    pub const ALL: [Agent; 3] = [Agent::Candidate, Agent::Company, Agent::Account];

    pub fn parse(s: &str) -> Result<Agent, String> {
        match s {
            "candidate" => Ok(Agent::Candidate),
            "company" => Ok(Agent::Company),
            "account" => Ok(Agent::Account),
            _ => Err(format!("unknown agent '{s}'")),
        }
    }
    pub fn key(self) -> &'static str {
        match self {
            Agent::Candidate => "candidate",
            Agent::Company => "company",
            Agent::Account => "account",
        }
    }
    pub fn folder(self) -> &'static str {
        match self {
            Agent::Candidate => "candidate-agent",
            Agent::Company => "company-agent",
            Agent::Account => "account-agent",
        }
    }
    /// The name of this assistant's folder inside the data folder (where its Excel file lives).
    pub fn data_folder(self) -> &'static str {
        match self {
            Agent::Candidate => "Find candidates",
            Agent::Company => "Company jobs",
            Agent::Account => "Accounts",
        }
    }
    pub fn workbook(self) -> &'static str {
        match self {
            Agent::Candidate => "Candidate_Research.xlsx",
            Agent::Company => "Company_Hiring_Intelligence.xlsx",
            Agent::Account => "Account_Research.xlsx",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// The AI-AGENTS folder that holds candidate-agent/, company-agent/ and account-agent/.
    pub agents_root: String,
    /// The data folder (Documents/RecruitDesk-Data by default): one subfolder per assistant, each holding that assistant's Excel file.
    /// The results stay on this computer; only people, the company and the allowance are kept in the company database.
    pub data_root: String,
    /// The claude executable; empty means auto-detect.
    pub claude_path: String,
    pub caps: BTreeMap<Agent, u64>,
    /// The sign-in server (https://...). Empty: the app works without signing in. Not editable through save_settings.
    pub auth_server: String,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            agents_root: String::new(),
            data_root: String::new(),
            claude_path: String::new(),
            caps: Agent::ALL.iter().map(|a| (*a, DEFAULT_CAP)).collect(),
            auth_server: String::new(),
        }
    }
}

impl Settings {
    /// What the settings screen may change is the folders and the program path. The allowance and the sign-in server belong
    /// to the owner: whatever the window sends for them is replaced by what is stored.
    pub fn keep_owner_fields(mut self, stored: &Settings) -> Settings {
        self.caps = stored.caps.clone();
        self.auth_server = stored.auth_server.clone();
        self.data_root = stored.data_root.clone();
        self
    }

    pub fn cap(&self, a: Agent) -> u64 {
        self.caps.get(&a).copied().unwrap_or(DEFAULT_CAP)
    }
    pub fn agent_dir(&self, a: Agent) -> PathBuf {
        Path::new(&self.agents_root).join(a.folder())
    }

    /// The folder of this assistant's Excel file (the assistant's own folder while no data folder is set up).
    pub fn workbook_dir(&self, a: Agent) -> PathBuf {
        if self.data_root.trim().is_empty() { self.agent_dir(a) } else { Path::new(&self.data_root).join(a.data_folder()) }
    }

    pub fn workbook_path(&self, a: Agent) -> PathBuf {
        self.workbook_dir(a).join(a.workbook())
    }

    /// Make sure the data folder and one folder per assistant exist, and move a workbook an older version left in an assistant's own
    /// folder into its new place (never over a file that is already there). `documents` is the person's Documents folder; it is used
    /// once, to choose the default. Returns true when the setting changed and must be saved.
    pub fn ensure_data_folders(&mut self, documents: Option<&Path>) -> bool {
        let mut changed = false;
        if self.data_root.trim().is_empty() {
            let Some(docs) = documents else { return false };
            self.data_root = docs.join("RecruitDesk-Data").display().to_string();
            changed = true;
        }
        for a in Agent::ALL {
            let dir = self.workbook_dir(a);
            if std::fs::create_dir_all(&dir).is_err() { continue; }
            let old = self.agent_dir(a).join(a.workbook());
            let new = dir.join(a.workbook());
            if old.is_file() && !new.exists() && old != new {
                if std::fs::rename(&old, &new).is_err() && std::fs::copy(&old, &new).is_ok() {
                    let _ = std::fs::remove_file(&old);
                }
            }
        }
        changed
    }
}

/// Usage for every agent, persisted as usage.json in the app data dir.
pub type UsageStore = BTreeMap<Agent, AgentUsage>;

pub fn load_json<T: for<'de> Deserialize<'de> + Default>(path: &Path) -> T {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Write via a temp file and rename, so a crash never leaves a half-written file.
pub fn save_json<T: Serialize>(path: &Path, v: &T) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(v).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| e.to_string())
}

pub fn is_agents_root(p: &Path) -> bool {
    Agent::ALL.iter().all(|a| p.join(a.folder()).join("CLAUDE.md").is_file())
}

/// Look for the AI-AGENTS folder next to the app: the exe's folder and its parents,
/// each checked directly and for an "AI-AGENTS" child; then where the installer put it.
pub fn detect_agents_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    for dir in exe.ancestors().skip(1) {
        for cand in [dir.to_path_buf(), dir.join("AI-AGENTS")] {
            if is_agents_root(&cand) {
                return Some(cand);
            }
        }
    }
    // Installed by the Windows installer: the path it recorded, or its default.
    crate::agents::installed_root(exe.parent(), home().as_deref())
}

fn exe_names() -> &'static [&'static str] {
    if cfg!(windows) {
        &["claude.exe", "claude.cmd"]
    } else {
        &["claude"]
    }
}

/// Find a program on PATH (with Windows extensions) or in the usual install places.
pub fn find_program(names: &[&str], extra_dirs: &[PathBuf]) -> Option<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    dirs.extend_from_slice(extra_dirs);
    for d in dirs {
        for n in names {
            let p = d.join(n);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

fn home() -> Option<PathBuf> {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}

fn extra_dirs() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Some(h) = home() {
        v.push(h.join(".local").join("bin"));
        v.push(h.join(".cargo").join("bin"));
        v.push(h.join(".claude").join("local"));
    }
    if let Some(a) = std::env::var_os("APPDATA") {
        v.push(PathBuf::from(a).join("npm"));
    }
    if let Some(a) = std::env::var_os("LOCALAPPDATA") {
        v.push(PathBuf::from(a).join("Programs").join("claude"));
    }
    v
}

pub fn detect_claude() -> Option<PathBuf> {
    find_program(exe_names(), &extra_dirs())
}

pub fn detect_uv() -> Option<PathBuf> {
    let names: &[&str] = if cfg!(windows) { &["uv.exe"] } else { &["uv"] };
    find_program(names, &extra_dirs())
}

/// Whether Claude Code has recorded the trust dialog as accepted for `dir`
/// (projects[dir].hasTrustDialogAccepted in ~/.claude.json). None if unreadable.
pub fn folder_trusted(dir: &Path) -> Option<bool> {
    let cfg = crate::trust::config_path()?;
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(cfg).ok()?).ok()?;
    let raw = dir.display().to_string();
    Some(crate::trust::trusted_in(&v, &raw) || crate::trust::trusted_in(&v, &crate::trust::config_key(dir)))
}

/// One check for all agent folders: Claude Code ignores an agent's allow list until its
/// folder is trusted, so every script call would be denied.
fn trust_check(s: &Settings) -> Check {
    let name = "Agent folders trusted by Claude Code".to_string();
    let fix = "Press Trust the agent folders in the setup check (close every Claude Code window first). Or open a terminal in each agent folder, run: claude — accept the trust dialog, type /exit, then press Re-check.".to_string();
    let mut untrusted = Vec::new();
    for a in Agent::ALL {
        let dir = s.agent_dir(a);
        match folder_trusted(&dir) {
            Some(true) => {}
            Some(false) => untrusted.push(dir.display().to_string()),
            None => {
                return Check { name, ok: false, detail: "Could not read Claude Code's trust list (.claude.json).".into(), fix };
            }
        }
    }
    if untrusted.is_empty() {
        Check { name, ok: true, detail: Agent::ALL.iter().map(|a| s.agent_dir(*a).display().to_string()).collect::<Vec<_>>().join(", "), fix: String::new() }
    } else {
        Check { name, ok: false, detail: format!("Not trusted yet: {}.", untrusted.join(", ")), fix }
    }
}

/// The Exa key (the installer's "Skip, add later" leaves it unset) and the
/// prepared Python packages (the installer's optional `uv sync`).
fn agent_checks(s: &Settings) -> Vec<Check> {
    let dirs: Vec<PathBuf> = Agent::ALL.iter().map(|a| s.agent_dir(*a)).collect();
    let no_key: Vec<String> = dirs.iter().filter(|d| !crate::agents::env_has(d, crate::agents::EXA_KEY)).map(|d| d.display().to_string()).collect();
    let unprepared: Vec<String> = dirs.iter().filter(|d| !crate::agents::prepared(d)).map(|d| d.display().to_string()).collect();
    vec![
        if no_key.is_empty() {
            Check { name: "Exa search key".into(), ok: true, detail: "Saved for every assistant.".into(), fix: String::new() }
        } else {
            Check {
                name: "Exa search key".into(),
                ok: false,
                detail: format!("EXA_API_KEY is not set in the .env of: {}.", no_key.join(", ")),
                fix: "Get a free key at https://dashboard.exa.ai and paste it here.".into(),
            }
        },
        if crate::agents::env_has(&s.agent_dir(Agent::Account), crate::agents::APOLLO_KEY) {
            Check { name: "Apollo API key".into(), ok: true, detail: "Saved for account research.".into(), fix: String::new() }
        } else {
            Check {
                name: "Apollo API key".into(),
                ok: false,
                detail: "APOLLO_API_KEY is not set in the account-agent .env (optional, paid).".into(),
                fix: "Optional. Paste your Apollo API key to add verified leader emails to account research.".into(),
            }
        },
        if unprepared.is_empty() {
            Check { name: "Assistant packages prepared".into(), ok: true, detail: "Every assistant has its Python packages.".into(), fix: String::new() }
        } else {
            Check {
                name: "Assistant packages prepared".into(),
                ok: false,
                detail: format!("No .venv yet in: {}.", unprepared.join(", ")),
                fix: "Press Prepare now, or run `uv sync` in each folder.".into(),
            }
        },
    ]
}

/// A Command that never flashes a console window on Windows.
pub fn quiet_command(program: &Path) -> Command {
    #[allow(unused_mut)]
    let mut c = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        c.creation_flags(CREATE_NO_WINDOW);
    }
    c
}

#[derive(Debug, Serialize)]
pub struct Check {
    pub name: String,
    pub ok: bool,
    pub detail: String,
    pub fix: String,
}

/// Check claude, uv, the claude login and the agents folder. Runs at startup.
pub fn check_prereqs(s: &Settings) -> Vec<Check> {
    let mut out = Vec::new();
    let claude = if s.claude_path.is_empty() { detect_claude() } else { Some(PathBuf::from(&s.claude_path)) };
    let claude_ok = match &claude {
        Some(p) => match quiet_command(p).arg("--version").output() {
            Ok(o) if o.status.success() => {
                out.push(Check {
                    name: "Claude Code CLI".into(),
                    ok: true,
                    detail: format!("{} ({})", String::from_utf8_lossy(&o.stdout).trim(), p.display()),
                    fix: String::new(),
                });
                true
            }
            other => {
                out.push(Check {
                    name: "Claude Code CLI".into(),
                    ok: false,
                    detail: format!("{} does not run: {:?}", p.display(), other.err()),
                    fix: "Reinstall Claude Code, or set the correct path to claude / claude.cmd in Settings.".into(),
                });
                false
            }
        },
        None => {
            out.push(Check {
                name: "Claude Code CLI".into(),
                ok: false,
                detail: "claude was not found on PATH.".into(),
                fix: if cfg!(windows) {
                    "Install it in PowerShell: irm https://claude.ai/install.ps1 | iex — then restart this app, or set its path in Settings."
                } else {
                    "Install it: curl -fsSL https://claude.ai/install.sh | bash — or set its path in Settings."
                }
                .into(),
            });
            false
        }
    };
    if claude_ok {
        let p = claude.clone().unwrap();
        let logged = quiet_command(&p)
            .args(["auth", "status"])
            .output()
            .ok()
            .and_then(|o| serde_json::from_slice::<serde_json::Value>(&o.stdout).ok())
            .and_then(|v| v.get("loggedIn").and_then(|b| b.as_bool()));
        out.push(match logged {
            Some(true) => Check { name: "Claude login".into(), ok: true, detail: "Logged in.".into(), fix: String::new() },
            Some(false) => Check {
                name: "Claude login".into(),
                ok: false,
                detail: "Claude Code is not logged in.".into(),
                fix: "Open a terminal, run: claude — and sign in once. Then press Re-check.".into(),
            },
            None => Check {
                name: "Claude login".into(),
                ok: false,
                detail: "Could not read `claude auth status`.".into(),
                fix: "Run `claude auth status` in a terminal; update Claude Code if the command is unknown.".into(),
            },
        });
    }
    // Right after sign-in: trusting needs the .claude.json that signing in creates.
    if is_agents_root(Path::new(&s.agents_root)) {
        out.push(trust_check(s));
    }
    if is_agents_root(Path::new(&s.agents_root)) {
        out.extend(agent_checks(s));
    }
    out.push(match detect_uv() {
        Some(p) => Check { name: "uv (Python runner)".into(), ok: true, detail: p.display().to_string(), fix: String::new() },
        None => Check {
            name: "uv (Python runner)".into(),
            ok: false,
            detail: "uv was not found; the agents run their Python scripts with it.".into(),
            fix: if cfg!(windows) {
                "Install it in PowerShell: powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\" — then restart this app."
            } else {
                "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
            }
            .into(),
        },
    });
    let root = Path::new(&s.agents_root);
    out.push(if !s.agents_root.is_empty() && is_agents_root(root) {
        Check { name: "Agents folder".into(), ok: true, detail: s.agents_root.clone(), fix: String::new() }
    } else {
        Check {
            name: "Agents folder".into(),
            ok: false,
            detail: format!("'{}' does not hold candidate-agent/, company-agent/ and account-agent/.", s.agents_root),
            fix: "Open Settings and enter the path of the AI-AGENTS folder.".into(),
        }
    });
    out
}

/// Open a terminal window in `dir` that runs `claude` with `args`, so the user can answer
/// Claude Code's own prompts (sign-in, "trust this folder"). The app never answers them itself.
pub fn open_setup_terminal(claude: &Path, dir: &Path, args: &[&str]) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let line = format!(
            "/C start \"Recruit Desk setup\" /D \"{}\" cmd.exe /K \"\"{}\" {}\"",
            dir.display(),
            claude.display(),
            args.join(" ")
        );
        return Command::new("cmd.exe").raw_arg(line).spawn().map(|_| ()).map_err(|e| e.to_string());
    }
    #[cfg(target_os = "macos")]
    {
        let _ = (claude, dir, args);
        return Err("Opening a terminal is not supported on macOS yet.".into());
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let mut cmdline: Vec<String> = vec![claude.display().to_string()];
        cmdline.extend(args.iter().map(|s| s.to_string()));
        let tries: [(&str, &[&str]); 5] = [
            ("x-terminal-emulator", &["-e"]),
            ("gnome-terminal", &["--"]),
            ("konsole", &["-e"]),
            ("xfce4-terminal", &["-x"]),
            ("xterm", &["-e"]),
        ];
        for (term, pre) in tries {
            if let Some(p) = find_program(&[term], &[]) {
                return Command::new(p)
                    .args(pre.iter())
                    .args(&cmdline)
                    .current_dir(dir)
                    .spawn()
                    .map(|_| ())
                    .map_err(|e| e.to_string());
            }
        }
        Err("No terminal program was found.".into())
    }
}

#[cfg(test)]
mod data_folder_tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("rd-data-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn the_data_folder_has_one_folder_per_assistant_and_is_chosen_once() {
        let docs = tmp("docs");
        let mut s = Settings { agents_root: tmp("agents").display().to_string(), ..Default::default() };
        assert_eq!(s.workbook_path(Agent::Candidate), Path::new(&s.agents_root).join("candidate-agent").join("Candidate_Research.xlsx"), "before the data folder is set up the file stays with the assistant");
        assert!(s.ensure_data_folders(Some(&docs)), "the default is chosen and must be saved");
        assert_eq!(s.data_root, docs.join("RecruitDesk-Data").display().to_string());
        for (a, folder, file) in [(Agent::Candidate, "Find candidates", "Candidate_Research.xlsx"), (Agent::Company, "Company jobs", "Company_Hiring_Intelligence.xlsx"), (Agent::Account, "Accounts", "Account_Research.xlsx")] {
            assert!(docs.join("RecruitDesk-Data").join(folder).is_dir(), "{folder}");
            assert_eq!(s.workbook_path(a), docs.join("RecruitDesk-Data").join(folder).join(file));
        }
        assert!(!s.ensure_data_folders(Some(&tmp("other"))), "an existing choice is kept");
        assert_eq!(s.data_root, docs.join("RecruitDesk-Data").display().to_string());
        assert!(!Settings::default().clone().ensure_data_folders(None), "without a Documents folder nothing is invented");
    }

    #[test]
    fn a_workbook_an_older_version_left_with_the_assistant_moves_into_its_folder_and_is_never_overwritten() {
        let docs = tmp("docs2");
        let agents = tmp("agents2");
        for a in Agent::ALL { std::fs::create_dir_all(agents.join(a.folder())).unwrap(); }
        std::fs::write(agents.join("candidate-agent").join("Candidate_Research.xlsx"), b"old candidate").unwrap();
        std::fs::write(agents.join("company-agent").join("Company_Hiring_Intelligence.xlsx"), b"old company").unwrap();
        let mut s = Settings { agents_root: agents.display().to_string(), ..Default::default() };
        // a file already in the new place wins
        let company_new = docs.join("RecruitDesk-Data").join("Company jobs");
        std::fs::create_dir_all(&company_new).unwrap();
        std::fs::write(company_new.join("Company_Hiring_Intelligence.xlsx"), b"new company").unwrap();
        s.ensure_data_folders(Some(&docs));
        assert_eq!(std::fs::read(s.workbook_path(Agent::Candidate)).unwrap(), b"old candidate");
        assert!(!agents.join("candidate-agent").join("Candidate_Research.xlsx").exists(), "moved, not copied");
        assert_eq!(std::fs::read(s.workbook_path(Agent::Company)).unwrap(), b"new company");
        assert!(agents.join("company-agent").join("Company_Hiring_Intelligence.xlsx").exists(), "the older file is left alone when there is a newer one");
        assert!(!s.workbook_path(Agent::Account).exists());
    }

    #[test]
    fn a_run_is_told_where_its_excel_file_lives() {
        let s = Settings { data_root: "/data/RecruitDesk-Data".into(), agents_root: "/agents".into(), ..Default::default() };
        assert_eq!(s.workbook_dir(Agent::Account), Path::new("/data/RecruitDesk-Data/Accounts"));
    }
}
