// Author: kaustubh.s.v
//! Application logs kept in the company database (2026-10-21).
//!
//! The app writes short lines about itself: events and errors, how a run went (its step lines, how it ended, tokens used) and sign-ins and
//! sign-outs. A line is queued in a small file at once (`logs_pending.jsonl`) and sent to the database in batches while someone is signed in,
//! so nothing is lost offline. Lines never carry search text, candidates, companies or contact details: emails and phone numbers are
//! stripped and long text is cut. Only the company's owner and admins can read them (neon-backend/setup.sql, `app_logs`).

use crate::auth::AuthService;
use crate::history::redact;
use crate::settings::Agent;
use serde::Serialize;
use serde_json::{json, Value};
use std::path::Path;
use std::sync::Mutex;

pub const FILE: &str = "logs_pending.jsonl";
const MAX_PENDING: usize = 500;
const BATCH: usize = 50;
const MAX_MESSAGE: usize = 400;
static FILE_LOCK: Mutex<()> = Mutex::new(());

/// Queue one line. `kind` is "event", "run" or "signin"; `level` is "info", "warn" or "error".
pub fn add(dir: &Path, kind: &str, level: &str, agent: Option<Agent>, message: &str, details: Value, now_ms: i64) {
    let mut msg: String = redact(message.trim()).replace(['\n', '\r'], " ");
    if msg.chars().count() > MAX_MESSAGE {
        msg = msg.chars().take(MAX_MESSAGE).collect::<String>() + "…";
    }
    if msg.is_empty() {
        return;
    }
    let line = json!({"kind": kind, "level": level, "agent": agent.map(|a| a.key()), "message": msg, "details": details, "at_ms": now_ms});
    let _g = FILE_LOCK.lock().unwrap();
    let path = dir.join(FILE);
    let mut lines: Vec<String> = std::fs::read_to_string(&path).map(|s| s.lines().map(str::to_string).collect()).unwrap_or_default();
    lines.push(line.to_string());
    if lines.len() > MAX_PENDING {
        let drop = lines.len() - MAX_PENDING;
        lines.drain(..drop);   // the oldest go first when a computer has been offline for very long
    }
    write(&path, &lines);
}

fn write(path: &Path, lines: &[String]) {
    let tmp = path.with_extension("jsonl.tmp");
    let mut body = lines.join("\n");
    if !body.is_empty() {
        body.push('\n');
    }
    if std::fs::write(&tmp, body).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

pub fn pending(dir: &Path) -> usize {
    let _g = FILE_LOCK.lock().unwrap();
    std::fs::read_to_string(dir.join(FILE)).map(|s| s.lines().filter(|l| !l.trim().is_empty()).count()).unwrap_or(0)
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LogResult {
    /// "sent", "nothing", "waiting" or "off".
    pub state: String,
    pub sent: usize,
    pub pending: usize,
}

#[derive(Default)]
pub struct LogSync {
    lock: Mutex<()>,
}

impl LogSync {
    pub fn new() -> Self {
        LogSync::default()
    }

    /// Send what is queued, 50 lines at a time. Quiet when signed out, without a company or offline: the lines wait in the file.
    pub fn sync(&self, auth: &AuthService, dir: &Path, now: i64) -> LogResult {
        let Ok(_busy) = self.lock.try_lock() else { return LogResult { state: "waiting".into(), sent: 0, pending: pending(dir) } };
        let st = auth.status(now);
        if !st.enabled {
            return LogResult { state: "off".into(), sent: 0, pending: pending(dir) };
        }
        if st.state != "signed_in" || st.offline {
            return LogResult { state: "waiting".into(), sent: 0, pending: pending(dir) };
        }
        let mut sent = 0usize;
        for _ in 0..10 {
            let batch: Vec<String> = {
                let _g = FILE_LOCK.lock().unwrap();
                std::fs::read_to_string(dir.join(FILE)).map(|s| s.lines().filter(|l| !l.trim().is_empty()).take(BATCH).map(str::to_string).collect()).unwrap_or_default()
            };
            if batch.is_empty() {
                break;
            }
            let items: Vec<Value> = batch.iter().filter_map(|l| serde_json::from_str(l).ok()).collect();
            let r = auth.data_request("POST", "rpc/log_write", Some(&json!({"p_items": items})), None, now);
            let done = match r {
                Ok(r) if r.status == 200 => true,
                Ok(r) if matches!(r.status, 400 | 422) => true,   // a batch the database will not take is dropped, never sent forever
                _ => false,                                        // offline, throttled, not a member yet, a bad moment: tried again later
            };
            if !done {
                return LogResult { state: "waiting".into(), sent, pending: pending(dir) };
            }
            let _g = FILE_LOCK.lock().unwrap();
            let path = dir.join(FILE);
            let rest: Vec<String> = std::fs::read_to_string(&path).map(|s| s.lines().filter(|l| !l.trim().is_empty()).skip(batch.len()).map(str::to_string).collect()).unwrap_or_default();
            write(&path, &rest);
            sent += batch.len();
        }
        LogResult { state: if sent > 0 { "sent" } else { "nothing" }.into(), sent, pending: pending(dir) }
    }
}

fn encode(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

/// The newest lines (up to 300), for an admin; filters by kind, level and the person's email. A person who is not an admin gets nothing.
pub fn list(auth: &AuthService, kind: Option<&str>, level: Option<&str>, email: Option<&str>, now: i64) -> Result<Value, String> {
    let st = auth.status(now);
    if st.state != "signed_in" {
        return Err("Sign in first.".into());
    }
    if st.user.as_ref().map(|u| u.role.as_str()) != Some("admin") {
        return Err("Only an admin can read the logs.".into());
    }
    let mut q = String::from("app_logs?select=id,user_email,kind,level,agent,message,details,at&order=at.desc,id.desc&limit=300");
    if let Some(k) = kind.filter(|k| matches!(*k, "event" | "run" | "signin")) {
        q.push_str(&format!("&kind=eq.{k}"));
    }
    if let Some(l) = level.filter(|l| matches!(*l, "info" | "warn" | "error")) {
        q.push_str(&format!("&level=eq.{l}"));
    }
    if let Some(e) = email.map(str::trim).filter(|e| !e.is_empty()) {
        q.push_str(&format!("&user_email=eq.{}", encode(&e.to_lowercase())));
    }
    let r = auth.data_request("GET", &q, None, None, now)?;
    match r.status {
        200 => serde_json::from_str(&r.body).map_err(|_| "The service gave an answer Recruit Desk does not understand.".to_string()),
        403 => Err("Only an admin can read the logs.".into()),
        429 => Err("Too many requests. Try again in a moment.".into()),
        500..=599 => Err("The service had a problem. Try again in a minute.".into()),
        _ => Err("The service gave an answer Recruit Desk does not understand.".into()),
    }
}
