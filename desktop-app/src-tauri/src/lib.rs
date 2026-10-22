// Author: kaustubh.s.v
//! Tauri commands: the bridge between the window and the shared core.

pub mod agents;
pub mod allowance;
pub mod auth;
pub mod history;
pub mod logs;
pub mod cap;
pub mod core;
pub mod excel;
pub mod records;
pub mod runner;
pub mod settings;
pub mod stream;
pub mod trust;
pub mod update;

#[cfg(test)]
mod installer_tests;
#[cfg(test)]
mod ui_tests;

use crate::auth::{AdminOp, AuthService, AuthStatus};
use crate::core::{Core, Emit, RunInput, Status};
use crate::history::{HistoryResult, HistorySync};
use crate::logs::LogSync;
use crate::records::{RecordsSync, SyncResult};
use crate::settings::{Agent, Check, Settings};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;

type CoreState<'a> = State<'a, Arc<Core>>;
type AuthState<'a> = State<'a, Arc<AuthService>>;

#[tauri::command]
fn get_settings(app: tauri::AppHandle, core: CoreState) -> Value {
    let s = core.settings.lock().unwrap().clone();
    json!({
        "version": app.package_info().version.to_string(),
        "settings": s,
        "detected_root": settings::detect_agents_root().map(|p| p.display().to_string()),
        "detected_claude": settings::detect_claude().map(|p| p.display().to_string()),
        "data_dir": core.data_dir.display().to_string(),
        "platform": std::env::consts::OS,
    })
}

#[tauri::command]
fn save_settings(core: CoreState, auth: AuthState, settings: Settings) -> Result<(), String> {
    auth.require(auth::unix_now())?;
    // The allowance (token caps) and the sign-in server are the owner's: the window cannot change them here.
    let settings = settings.keep_owner_fields(&core.settings.lock().unwrap());
    core.save_settings(settings)
}

// ---------- sign-in (auth.rs) ----------

/// The sign-in state without touching the network.
#[tauri::command]
fn auth_status(auth: AuthState) -> AuthStatus {
    auth.status(auth::unix_now())
}

/// Open the saved session and keep it fresh. May reach the server, so it runs off the window's thread.
#[tauri::command]
async fn auth_check(auth: State<'_, Arc<AuthService>>) -> Result<AuthStatus, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || a.check(auth::unix_now())).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn auth_login(core: CoreState<'_>, auth: State<'_, Arc<AuthService>>, logsync: State<'_, Arc<LogSync>>, email: String, password: String) -> Result<AuthStatus, String> {
    let (a, dir, l) = (Arc::clone(&auth), core.data_dir.clone(), Arc::clone(&logsync));
    tauri::async_runtime::spawn_blocking(move || {
        let r = a.login(&email, &password, auth::unix_now());
        if let Ok(st) = &r { log_signin(&a, &l, &dir, st, "Signed in"); }
        r
    }).await.map_err(|e| e.to_string())?
}

/// Make an account with an invitation code (the address the code was sent for, a password of the person's choice), and sign in.
#[tauri::command]
async fn auth_create_account(core: CoreState<'_>, auth: State<'_, Arc<AuthService>>, logsync: State<'_, Arc<LogSync>>, email: String, password: String, code: String) -> Result<AuthStatus, String> {
    let (a, dir, l) = (Arc::clone(&auth), core.data_dir.clone(), Arc::clone(&logsync));
    tauri::async_runtime::spawn_blocking(move || {
        let r = a.create_account(&email, &password, &code, auth::unix_now());
        if let Ok(st) = &r { log_signin(&a, &l, &dir, st, "Account created and signed in"); }
        r
    }).await.map_err(|e| e.to_string())?
}

/// A signed-in person with no company yet enters their invitation code.
#[tauri::command]
async fn auth_join_company(core: CoreState<'_>, auth: State<'_, Arc<AuthService>>, logsync: State<'_, Arc<LogSync>>, code: String) -> Result<AuthStatus, String> {
    let (a, dir, l) = (Arc::clone(&auth), core.data_dir.clone(), Arc::clone(&logsync));
    tauri::async_runtime::spawn_blocking(move || {
        let r = a.join_company(&code, auth::unix_now());
        if let Ok(st) = &r { log_signin(&a, &l, &dir, st, "Joined the company"); }
        r
    }).await.map_err(|e| e.to_string())?
}

/// Ask for a password reset email.
#[tauri::command]
async fn auth_forgot_password(auth: State<'_, Arc<AuthService>>, email: String) -> Result<String, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || a.forgot_password(&email)).await.map_err(|e| e.to_string())?
}

/// Choose a new password from a reset link. Returns a plain sentence; the person signs in afterwards.
#[tauri::command]
async fn auth_reset_password(auth: State<'_, Arc<AuthService>>, link: String, password: String) -> Result<String, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || a.reset_password(&link, &password)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn auth_logout(core: CoreState<'_>, auth: State<'_, Arc<AuthService>>, logsync: State<'_, Arc<LogSync>>) -> Result<AuthStatus, String> {
    let (a, dir, l) = (Arc::clone(&auth), core.data_dir.clone(), Arc::clone(&logsync));
    tauri::async_runtime::spawn_blocking(move || {
        // the line is sent while the sign-in still exists
        logs::add(&dir, "signin", "info", None, "Signed out", json!({}), core::now_ms());
        let _ = l.sync(&a, &dir, auth::unix_now());
        a.logout(auth::unix_now())
    }).await.map_err(|e| e.to_string())
}

/// Queue a sign-in line and send the queue now (the person has just signed in, so it can go).
fn log_signin(auth: &AuthService, l: &LogSync, dir: &std::path::Path, st: &AuthStatus, text: &str) {
    if st.state == "signed_in" {
        logs::add(dir, "signin", "info", None, text, json!({"offline": st.offline}), core::now_ms());
        let _ = l.sync(auth, dir, auth::unix_now());
    }
}

// ---------- last records (the assistants' newest record, kept on the server) ----------

/// Send what changed, and take down what no longer exists here. Runs off the window's thread; safe to call often.
#[tauri::command]
async fn records_sync(core: State<'_, Arc<Core>>, auth: State<'_, Arc<AuthService>>, sync: State<'_, Arc<RecordsSync>>) -> Result<Vec<SyncResult>, String> {
    let (a, r, c) = (Arc::clone(&auth), Arc::clone(&sync), Arc::clone(&core));
    tauri::async_runtime::spawn_blocking(move || {
        let s = c.settings.lock().unwrap().clone();
        let dirs: Vec<_> = Agent::ALL.iter().map(|ag| (*ag, s.agent_dir(*ag))).collect();
        r.sync(&a, &dirs, &c.data_dir.join("records_sent.json"), auth::unix_now())
    })
    .await
    .map_err(|e| e.to_string())
}

// ---------- allowance (each person's own cap and window over all the assistants; admins set them one by one) ----------

/// Send what this computer spent and read the person's allowance as it is now.
#[tauri::command]
async fn allowance_refresh(allowance: State<'_, Arc<allowance::Allowance>>) -> Result<(), String> {
    let a = Arc::clone(&allowance);
    tauri::async_runtime::spawn_blocking(move || {
        let now = core::now_ms();
        a.flush(now);
        a.refresh(now)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// An admin sets one person's own cap (tokens) and window (minutes).
#[tauri::command]
async fn allowance_set_user(allowance: State<'_, Arc<allowance::Allowance>>, email: String, cap: u64, minutes: u32) -> Result<(), String> {
    let a = Arc::clone(&allowance);
    tauri::async_runtime::spawn_blocking(move || a.set_user(&email, cap, minutes, core::now_ms())).await.map_err(|e| e.to_string())?
}

/// An admin empties one person's count (their email) or, without an email, everyone's.
#[tauri::command]
async fn allowance_reset(allowance: State<'_, Arc<allowance::Allowance>>, email: Option<String>) -> Result<(), String> {
    let a = Arc::clone(&allowance);
    tauri::async_runtime::spawn_blocking(move || a.reset(email.as_deref(), core::now_ms())).await.map_err(|e| e.to_string())?
}

/// An admin: everyone's count at a glance.
#[tauri::command]
async fn allowance_people(allowance: State<'_, Arc<allowance::Allowance>>) -> Result<Value, String> {
    let a = Arc::clone(&allowance);
    tauri::async_runtime::spawn_blocking(move || a.people(core::now_ms())).await.map_err(|e| e.to_string())?
}

// ---------- application logs (admins read them) ----------

/// The newest log lines, for an admin; filters are optional.
#[tauri::command]
async fn logs_list(auth: State<'_, Arc<AuthService>>, kind: Option<String>, level: Option<String>, email: Option<String>) -> Result<Value, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || logs::list(&a, kind.as_deref(), level.as_deref(), email.as_deref(), auth::unix_now())).await.map_err(|e| e.to_string())?
}

// ---------- search history (each person's own searches and rows; an admin sees everyone's) ----------

/// Store pending searches and queued rows, and remove what was erased. Runs off the window's thread; safe to call often.
#[tauri::command]
async fn history_sync(core: State<'_, Arc<Core>>, auth: State<'_, Arc<AuthService>>, sync: State<'_, Arc<HistorySync>>) -> Result<HistoryResult, String> {
    let (a, h, c) = (Arc::clone(&auth), Arc::clone(&sync), Arc::clone(&core));
    tauri::async_runtime::spawn_blocking(move || {
        let s = c.settings.lock().unwrap().clone();
        let dirs: Vec<_> = Agent::ALL.iter().map(|ag| (*ag, s.agent_dir(*ag))).collect();
        let r = h.sync(&a, &dirs, &c.data_dir, auth::unix_now());
        if r.state == "error" { logs::add(&c.data_dir, "event", "error", None, &format!("Search history could not be stored: {}", r.message), json!({}), core::now_ms()); }
        r
    })
    .await
    .map_err(|e| e.to_string())
}

/// The searches the signed-in person may see, newest first.
#[tauri::command]
async fn history_list(auth: State<'_, Arc<AuthService>>) -> Result<Value, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || history::list(&a, auth::unix_now())).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn history_rows(auth: State<'_, Arc<AuthService>>, search_id: String) -> Result<Value, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || history::rows(&a, &search_id, auth::unix_now())).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn history_delete(auth: State<'_, Arc<AuthService>>, search_id: String) -> Result<Value, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || history::delete(&a, &search_id, auth::unix_now())).await.map_err(|e| e.to_string())?
}

/// What the last sync said, per assistant (no network).
#[tauri::command]
fn records_status(sync: State<'_, Arc<RecordsSync>>) -> Vec<SyncResult> {
    sync.results()
}

/// What the server holds now: every assistant's last record.
#[tauri::command]
async fn records_stored(auth: State<'_, Arc<AuthService>>) -> Result<Value, String> {
    let a = Arc::clone(&auth);
    tauri::async_runtime::spawn_blocking(move || records::stored(&a, auth::unix_now())).await.map_err(|e| e.to_string())?
}

// ---------- admin (an admin only; Neon Auth checks again) ----------

async fn admin_run(auth: &State<'_, Arc<AuthService>>, op: AdminOp) -> Result<Value, String> {
    let a = Arc::clone(auth);
    tauri::async_runtime::spawn_blocking(move || a.admin(&op, auth::unix_now())).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn admin_members(auth: State<'_, Arc<AuthService>>) -> Result<Value, String> {
    admin_run(&auth, AdminOp::Members).await
}

#[tauri::command]
async fn admin_invites(auth: State<'_, Arc<AuthService>>) -> Result<Value, String> {
    admin_run(&auth, AdminOp::Invites).await
}

#[tauri::command]
async fn admin_invite(auth: State<'_, Arc<AuthService>>, email: String, role: String) -> Result<Value, String> {
    admin_run(&auth, AdminOp::Invite { email, role }).await
}

#[tauri::command]
async fn admin_revoke_invite(auth: State<'_, Arc<AuthService>>, id: String) -> Result<Value, String> {
    admin_run(&auth, AdminOp::RevokeInvite { id }).await
}

#[tauri::command]
async fn admin_set_role(auth: State<'_, Arc<AuthService>>, id: String, role: String) -> Result<Value, String> {
    admin_run(&auth, AdminOp::SetRole { id, role }).await
}

#[tauri::command]
async fn admin_remove(auth: State<'_, Arc<AuthService>>, id: String) -> Result<Value, String> {
    admin_run(&auth, AdminOp::Remove { id }).await
}

#[tauri::command]
async fn check_prereqs(core: CoreState<'_>) -> Result<Vec<Check>, String> {
    let s = core.settings.lock().unwrap().clone();
    Ok(settings::check_prereqs(&s))
}

#[tauri::command]
fn get_status(core: CoreState) -> Vec<Status> {
    core.statuses()
}

#[tauri::command]
fn start_run(app: tauri::AppHandle, core: CoreState, auth: AuthState, agent: String, input: RunInput) -> Result<Status, String> {
    auth.require(auth::unix_now())?;
    let a = Agent::parse(&agent)?;
    let emit: Emit = Arc::new(move |name: &str, payload: Value| {
        let _ = app.emit(name, payload);
    });
    core.start_run(a, &input, emit)
}

#[tauri::command]
fn stop_run(core: CoreState, agent: String) -> Result<bool, String> {
    Ok(core.stop_run(Agent::parse(&agent)?))
}

#[tauri::command]
async fn read_workbook(core: CoreState<'_>, auth: AuthState<'_>, agent: String, sheet: Option<String>) -> Result<Value, String> {
    auth.require(auth::unix_now())?;
    let a = Agent::parse(&agent)?;
    let path = core.settings.lock().unwrap().workbook_path(a);
    let data = excel::read_sheet(&path, sheet.as_deref())?;
    let modified_ms = std::fs::metadata(&path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as i64);
    Ok(json!({ "path": path.display().to_string(), "exists": path.exists(), "local": path.exists(), "modified_ms": modified_ms, "data": data, "source": "file" }))
}

/// Open the data folder (one subfolder per assistant, each with its Excel file) in the system's file manager.
#[tauri::command]
fn open_data_folder(app: tauri::AppHandle, core: CoreState, auth: AuthState) -> Result<(), String> {
    auth.require(auth::unix_now())?;
    let dir = {
        let s = core.settings.lock().unwrap();
        if s.data_root.trim().is_empty() { return Err("The data folder is not set up yet.".into()); }
        std::path::PathBuf::from(&s.data_root)
    };
    std::fs::create_dir_all(&dir).map_err(|e| format!("Could not open {}: {e}", dir.display()))?;
    app.opener().open_path(dir.display().to_string(), None::<&str>).map_err(|e| e.to_string())
}

/// Open an agent's workbook in the system's default app (Excel on most Windows PCs).
#[tauri::command]
fn open_workbook(app: tauri::AppHandle, core: CoreState, auth: AuthState, agent: String) -> Result<(), String> {
    auth.require(auth::unix_now())?;
    let a = Agent::parse(&agent)?;
    let path = core.settings.lock().unwrap().workbook_path(a);
    if !path.exists() {
        return Err("There is no results file yet. Run a search first.".into());
    }
    app.opener().open_path(path.display().to_string(), None::<&str>).map_err(|e| e.to_string())
}

/// Open a terminal for a setup step: "login" signs in to Claude Code, "trust-candidate",
/// "trust-company" and "trust-account" start claude in that agent's folder so its trust question can be answered.
#[tauri::command]
fn open_setup(core: CoreState, auth: AuthState, step: String) -> Result<(), String> {
    auth.require(auth::unix_now())?;
    let claude = core.claude_path()?;
    let s = core.settings.lock().unwrap().clone();
    match step.as_str() {
        "login" => settings::open_setup_terminal(&claude, &s.agent_dir(Agent::Candidate), &["auth", "login"]),
        "trust-candidate" => settings::open_setup_terminal(&claude, &s.agent_dir(Agent::Candidate), &[]),
        "trust-company" => settings::open_setup_terminal(&claude, &s.agent_dir(Agent::Company), &[]),
        "trust-account" => settings::open_setup_terminal(&claude, &s.agent_dir(Agent::Account), &[]),
        _ => Err(format!("unknown setup step '{step}'")),
    }
}

/// Mark every agent folder as trusted in Claude Code's config (the setup step
/// "Allow Claude Code to work in the agent folders"). Returns a plain-language result.
#[tauri::command]
async fn trust_folders(core: CoreState<'_>, auth: AuthState<'_>) -> Result<String, String> {
    auth.require(auth::unix_now())?;
    let s = core.settings.lock().unwrap().clone();
    if !settings::is_agents_root(std::path::Path::new(&s.agents_root)) {
        return Err("Recruit Desk cannot find the assistants folder yet. Set it in Settings first.".into());
    }
    let dirs: Vec<_> = Agent::ALL.iter().map(|a| s.agent_dir(*a)).collect();
    let out = trust::trust_folders(&dirs)?;
    Ok(if out.changed.is_empty() {
        "All three folders were already trusted. Nothing was changed.".into()
    } else {
        "Done. Claude Code may now work in all three assistant folders.".into()
    })
}

/// Save the Exa key typed on the setup screen into every assistant's .env file.
#[tauri::command]
fn save_exa_key(core: CoreState, auth: AuthState, key: String) -> Result<String, String> {
    auth.require(auth::unix_now())?;
    let root = core.settings.lock().unwrap().agents_root.clone();
    if !settings::is_agents_root(std::path::Path::new(&root)) {
        return Err("Recruit Desk cannot find the assistants folder yet. Set it in Settings first.".into());
    }
    agents::save_exa_key(std::path::Path::new(&root), &key)?;
    Ok("Saved. All three assistants can now search with Exa.".into())
}

/// Save the optional Apollo API key into account-agent's .env (masked in the UI, never logged or returned).
#[tauri::command]
fn save_apollo_key(core: CoreState, auth: AuthState, key: String) -> Result<String, String> {
    auth.require(auth::unix_now())?;
    let root = core.settings.lock().unwrap().agents_root.clone();
    if !settings::is_agents_root(std::path::Path::new(&root)) {
        return Err("Recruit Desk cannot find the assistants folder yet. Set it in Settings first.".into());
    }
    agents::save_apollo_key(std::path::Path::new(&root), &key)?;
    Ok("Saved. Account research now asks Apollo first for leaders' verified emails.".into())
}

/// Run `uv sync` in every assistant folder (what the installer tries after copying).
#[tauri::command]
async fn prepare_agents(core: CoreState<'_>, auth: AuthState<'_>) -> Result<String, String> {
    auth.require(auth::unix_now())?;
    let root = core.settings.lock().unwrap().agents_root.clone();
    if !settings::is_agents_root(std::path::Path::new(&root)) {
        return Err("Recruit Desk cannot find the assistants folder yet. Set it in Settings first.".into());
    }
    let uv = agents::uv_path().ok_or("The helper tool uv was not found. Install it first.")?;
    agents::prepare(std::path::Path::new(&root), &uv)
}

/// Export an assistant's results for HubSpot or Salesforce (account and candidate only), then open the
/// exports folder. Runs export_crm.py with fixed arguments; no shell is involved.
#[tauri::command]
async fn export_crm(app: tauri::AppHandle, core: CoreState<'_>, auth: AuthState<'_>, agent: String, format: String) -> Result<String, String> {
    auth.require(auth::unix_now())?;
    let a = Agent::parse(&agent)?;
    agents::crm_args(a, &format)?;
    let dir = core.settings.lock().unwrap().agent_dir(a);
    let uv = agents::uv_path().ok_or("The helper tool uv was not found. Install it first.")?;
    let (folder, files) = agents::export_crm(&dir, a, &format, &uv)?;
    let opened = app.opener().open_path(folder.display().to_string(), None::<&str>).is_ok();
    let name = if format == "hubspot" { "HubSpot" } else { "Salesforce" };
    Ok(format!(
        "Saved for {name}: {}.{}",
        files.join(", "),
        if opened { " The exports folder is open." } else { "" }
    ))
}

#[tauri::command]
fn open_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let u = url.trim();
    if !(u.starts_with("https://") || u.starts_with("http://")) {
        return Err("Only http and https links open from the viewer.".into());
    }
    app.opener().open_url(u, None::<&str>).map_err(|e| e.to_string())
}

/// A percent-encoded piece of a mailto: address (letters, digits and - . _ ~ stay; a line break becomes CRLF as mail programs expect).
fn mail_encode(s: &str) -> String {
    s.replace("\r\n", "\n").split('\n').map(|line| line.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect::<String>()).collect::<Vec<_>>().join("%0D%0A")
}

/// The address a ready-written email opens with, or why it cannot be made (a short check on the address and the lengths).
pub fn mailto(to: &str, subject: &str, body: &str) -> Result<String, String> {
    let to = to.trim();
    let ok = to.len() <= 254 && to.matches('@').count() == 1 && !to.starts_with('@') && !to.ends_with('@') && to.rsplit('@').next().map(|d| d.contains('.')).unwrap_or(false)
        && to.chars().all(|c| c.is_ascii_graphic() && !",;<>\"()[]\\%?&=#".contains(c));
    if !ok {
        return Err("That does not look like an email address.".into());
    }
    if subject.chars().count() > 200 || body.chars().count() > 1800 {
        return Err("The message is too long for a ready-written email.".into());
    }
    Ok(format!("mailto:{to}?subject={}&body={}", mail_encode(subject), mail_encode(body)))
}

/// Open the person's own mail program with a ready-written email (nothing is sent from here: they press Send).
#[tauri::command]
fn open_mail(app: tauri::AppHandle, auth: AuthState, to: String, subject: String, body: String) -> Result<(), String> {
    auth.require(auth::unix_now())?;
    let url = mailto(&to, &subject, &body)?;
    app.opener().open_url(url, None::<&str>).map_err(|_| "Could not open a mail program on this computer.".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            let core = Core::new(dir.clone());
            // The data folder (Documents/RecruitDesk-Data, one folder per assistant) is made here too, for the copies the installer did not make
            // it for, and a workbook an older version left in an assistant's own folder is moved into its new place.
            {
                // The person's Documents folder; where the system names none (a Linux home without user-dirs), ~/Documents.
                let docs = app.path().document_dir().ok().or_else(|| app.path().home_dir().ok().map(|h| h.join("Documents")));
                let mut s = core.settings.lock().unwrap().clone();
                if s.ensure_data_folders(docs.as_deref()) {
                    let _ = core.save_settings(s);
                } else {
                    let _ = s.ensure_data_folders(None);
                }
            }
            // The company connection code is fixed by the build (RECRUIT_DESK_AUTH_URL at compile time) or, for development, by the same
            // variable at run time; there is no screen to change it. A value saved by an older version is used only when neither is set.
            let server = {
                let fixed = std::env::var("RECRUIT_DESK_AUTH_URL").ok().or_else(|| option_env!("RECRUIT_DESK_AUTH_URL").map(str::to_string)).filter(|c| !c.trim().is_empty());
                fixed.unwrap_or_else(|| core.settings.lock().unwrap().auth_server.clone())
            };
            let auth = Arc::new(AuthService::new(
                Box::new(auth::UreqHttp), Box::new(auth::KeychainVault), Box::new(auth::FileStore(dir.join("auth.json"))), &server));
            let allowance = Arc::new(allowance::Allowance::new(Arc::clone(&auth), dir.clone()));
            core.set_shared(Arc::clone(&allowance));
            // Every few seconds: send what this computer spent and, once a minute, read the company's pool again.
            let ticker = Arc::clone(&allowance);
            // The log lines queued on this computer go to the database in the same rhythm (quietly, while signed in).
            let log_sync = Arc::new(LogSync::new());
            logs::add(&dir, "event", "info", None, "App started", json!({"version": app.package_info().version.to_string(), "os": std::env::consts::OS}), core::now_ms());
            let (log_ticker, log_auth, log_dir) = (Arc::clone(&log_sync), Arc::clone(&auth), dir.clone());
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(15));
                ticker.tick(core::now_ms());
                let _ = log_ticker.sync(&log_auth, &log_dir, auth::unix_now());
            });
            app.manage(core);
            app.manage(Arc::new(RecordsSync::new()));
            app.manage(Arc::new(HistorySync::new()));
            app.manage(Arc::clone(&log_sync));
            app.manage(auth);
            app.manage(allowance);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            auth_status,
            auth_check,
            auth_login,
            auth_logout,
            auth_create_account,
            auth_join_company,
            auth_forgot_password,
            auth_reset_password,
            records_sync,
            allowance_refresh,
            allowance_set_user,
            allowance_reset,
            allowance_people,
            history_sync,
            update::update_check,
            update::update_install,
            history_list,
            history_rows,
            history_delete,
            logs_list,
            records_status,
            records_stored,
            admin_members,
            admin_invites,
            admin_invite,
            admin_revoke_invite,
            admin_set_role,
            admin_remove,
            check_prereqs,
            get_status,
            start_run,
            stop_run,
            read_workbook,
            open_workbook,
            open_data_folder,
            open_setup,
            trust_folders,
            save_exa_key,
            save_apollo_key,
            prepare_agents,
            export_crm,
            open_url
        ])
        .run(tauri::generate_context!())
        .expect("error while running Recruit Desk");
}

#[cfg(test)]
mod mail_tests {
    use super::mailto;

    #[test]
    fn a_ready_written_email_is_encoded_for_a_mail_program() {
        let u = mailto("ann@example.com", "You are invited to join Acme on Recruit Desk", "Hello,\n\nInvitation id: 1b9d6bcd-bbfd\nDownload: https://github.com/x/y/releases/latest/download/Recruit-Desk-Setup.exe\nTom & Jerry (100%) \"quoted\"").unwrap();
        assert!(u.starts_with("mailto:ann@example.com?subject=You%20are%20invited%20to%20join%20Acme%20on%20Recruit%20Desk&body=Hello%2C%0D%0A%0D%0AInvitation%20id%3A%201b9d6bcd-bbfd%0D%0A"), "{u}");
        assert!(u.contains("https%3A%2F%2Fgithub.com%2Fx%2Fy%2Freleases%2Flatest%2Fdownload%2FRecruit-Desk-Setup.exe"));
        assert!(u.contains("Tom%20%26%20Jerry%20%28100%25%29%20%22quoted%22") || u.contains("Tom%20%26%20Jerry%20%28100%25%29"), "{u}");
        assert!(!u[7..].contains(' ') && !u.contains('\n') && !u.contains('"'));
    }

    #[test]
    fn a_bad_address_or_an_over_long_message_is_refused_before_anything_opens() {
        for bad in ["", "ann", "ann@", "@example.com", "ann@example", "a@b@example.com", "ann smith@example.com", "ann@example.com?cc=boss@example.com", "ann@example.com,boss@example.com", "ann@example.com&bcc=x@y.zz"] {
            assert!(mailto(bad, "s", "b").is_err(), "{bad}");
        }
        assert!(mailto("ann@example.com", &"s".repeat(201), "b").is_err());
        assert!(mailto("ann@example.com", "s", &"b".repeat(1801)).is_err());
        assert!(mailto("ann.o+tag@sub.example.co.in", "s", "b").is_ok());
    }
}
