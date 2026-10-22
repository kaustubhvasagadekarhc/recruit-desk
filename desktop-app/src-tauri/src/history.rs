// Author: kaustubh.s.v
//! Each person's search history, kept in the company's database on Neon (2026-10-20).
//!
//! Processing stays on this computer. What goes to Neon is, per person:
//! - each search they start (what was searched: a job description, a company, a CSV of companies; email addresses and phone numbers in
//!   that text are replaced by "[removed]" first), when, and how it ended;
//! - every NEW result row the assistants queue in `new_rows.jsonl` (a fixed list of fields per assistant, no email, phone, evidence text
//!   or notes; the key only as a hash). Rows are linked to the search that was running when they were created.
//! The database decides who sees what: a person sees only their own searches and rows, the company's owner and admins see everyone's
//! (see neon-backend/setup.sql). Who stored a row is set by the database from the sign-in, never by this app.
//!
//! Nothing is lost while offline or signed out: searches wait in `searches_pending.json` (app data folder) and rows in the assistants'
//! queue files. Erasure: `memory.py forget` removes a person's queued rows and leaves a tombstone in `erased_rows.jsonl`; the next sync
//! removes every stored copy of that person's rows (by hash) and the tombstone.

use crate::auth::{parse_iso, valid_code, AuthService};
use crate::core::RunInput;
use crate::records::clean_fields;
use crate::runner::EndReason;
use crate::settings::Agent;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const PENDING_FILE: &str = "searches_pending.json";
const ROWS_FILE: &str = "new_rows.jsonl";
const SENDING_FILE: &str = "new_rows.sending.jsonl";
const ERASED_FILE: &str = "erased_rows.jsonl";
const ERASED_SENDING_FILE: &str = "erased_rows.sending.jsonl";
const MAX_FILE_BYTES: u64 = 8 * 1024 * 1024;
const MAX_LINES: usize = 5_000;
const BATCH: usize = 100;
const MAX_TEXT: usize = 20_000;
const MAX_CSV: usize = 30_000;
const KEEP_SEARCHES: usize = 200;
const KEEP_SENT_SECS: i64 = 14 * 86_400;

static FILE_LOCK: Mutex<()> = Mutex::new(());

// ---------- small helpers ----------

/// A random version-4 style id for a search (not a secret; made from the system's random hasher keys).
pub fn new_id() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let mut b = [0u8; 16];
    for chunk in b.chunks_mut(8) {
        let mut h = RandomState::new().build_hasher();
        h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        chunk.copy_from_slice(&h.finish().to_le_bytes());
    }
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let x: String = b.iter().map(|v| format!("{v:02x}")).collect();
    format!("{}-{}-{}-{}-{}", &x[0..8], &x[8..12], &x[12..16], &x[16..20], &x[20..32])
}

/// 2026-10-21T09:01:14Z from seconds since 1970 (UTC).
pub fn iso(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // civil from days (Howard Hinnant's algorithm)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

fn cap(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Replace email addresses and phone numbers in free text by "[removed]". A run of digits that looks like a number range (every group
/// of 6 or more digits, like a salary "1200000-1800000") is kept.
pub fn redact(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        // an email: the word around an '@' that has a dot after it
        if c.is_alphanumeric() || matches!(c, '.' | '_' | '%' | '+' | '-') {
            let mut j = i;
            while j < chars.len() && (chars[j].is_alphanumeric() || matches!(chars[j], '.' | '_' | '%' | '+' | '-' | '@')) {
                j += 1;
            }
            while j > i + 1 && matches!(chars[j - 1], '.' | '_' | '%' | '+' | '-') {
                j -= 1;   // a full stop or dash that ends a sentence is not part of the address
            }
            let word: String = chars[i..j].iter().collect();
            if let Some(at) = word.find('@') {
                let (local, domain) = (&word[..at], &word[at + 1..]);
                let tld_ok = domain.rsplit('.').next().map(|t| t.len() >= 2 && t.chars().all(char::is_alphabetic)).unwrap_or(false);
                if !local.is_empty() && domain.contains('.') && tld_ok && !domain.contains('@') {
                    out.push_str("[removed]");
                    i = j;
                    continue;
                }
            }
        }
        // a phone number: a run of digits and separators with 9 to 15 digits
        if c.is_ascii_digit() || (matches!(c, '+' | '(') && chars.get(i + 1).map(|n| n.is_ascii_digit() || (c == '+' && matches!(*n, ' ' | '('))).unwrap_or(false)) {
            let mut j = i;
            while j < chars.len() && (chars[j].is_ascii_digit() || matches!(chars[j], ' ' | '.' | '(' | ')' | '-' | '+')) {
                j += 1;
            }
            while j > i && !chars[j - 1].is_ascii_digit() {
                j -= 1;
            }
            if j == i {   // only separators, no digit: an ordinary character
                out.push(c);
                i += 1;
                continue;
            }
            let run: String = chars[i..j].iter().collect();
            let groups: Vec<usize> = run.split(|ch: char| !ch.is_ascii_digit()).filter(|g| !g.is_empty()).map(str::len).collect();
            let digits: usize = groups.iter().sum();
            let range_like = groups.len() > 1 && groups.iter().all(|g| *g >= 6);
            if (9..=15).contains(&digits) && !range_like {
                out.push_str("[removed]");
                i = j;
                continue;
            }
            // not a phone: copy the run as it is
            out.push_str(&run);
            i = j.max(i + 1);
            continue;
        }
        out.push(c);
        i += 1;
    }
    out
}

/// What was searched, as it is stored: only the fields the database allows for that assistant, free text redacted and cut.
pub fn search_inputs(agent: Agent, input: &RunInput) -> Value {
    let mut m = Map::new();
    let mut text = |k: &str, v: &str, n: usize| {
        let v = cap(&redact(v.trim()), n);
        if !v.trim().is_empty() {
            m.insert(k.to_string(), Value::String(v));
        }
    };
    match agent {
        Agent::Candidate => text("jd", &input.jd, MAX_TEXT),
        Agent::Company => {
            text("company", &input.company, 200);
            text("domain", &input.domain, 200);
            m.insert("publicPosts".into(), Value::Bool(input.public_posts));
        }
        Agent::Account => {
            text("company", &input.company, 200);
            text("domain", &input.domain, 200);
            text("csv", &input.csv, MAX_CSV);
            m.insert("contacts".into(), Value::Bool(input.contacts));
        }
    }
    Value::Object(m)
}

// ---------- the pending searches (app data folder) ----------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Pending {
    pub id: String,
    pub agent: String,
    pub inputs: Value,
    pub started_at: i64,
    pub finished_at: Option<i64>,
    /// "running", "done", "failed" or "stopped".
    pub status: String,
    /// The status the database last has ("" when never sent).
    #[serde(default)]
    pub sent: String,
}

fn load_pending(dir: &Path) -> Vec<Pending> {
    std::fs::read_to_string(dir.join(PENDING_FILE)).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn save_pending(dir: &Path, list: &[Pending]) {
    let path = dir.join(PENDING_FILE);
    if let Ok(s) = serde_json::to_string(list) {
        let tmp = dir.join(format!("{PENDING_FILE}.tmp"));
        if std::fs::write(&tmp, s).is_ok() {
            let _ = std::fs::rename(&tmp, &path);
        }
    }
}

/// A search has started: remember it (the next sync stores it).
pub fn begin(dir: &Path, agent: Agent, input: &RunInput, now: i64) -> String {
    let _g = FILE_LOCK.lock().unwrap();
    let mut list = load_pending(dir);
    // a search the app never saw end (the app was closed mid-run) is closed as stopped
    for p in list.iter_mut().filter(|p| p.agent == agent.key() && p.status == "running") {
        p.status = "stopped".into();
        p.finished_at = Some(now);
    }
    let id = new_id();
    list.push(Pending { id: id.clone(), agent: agent.key().into(), inputs: search_inputs(agent, input), started_at: now, finished_at: None, status: "running".into(), sent: String::new() });
    // forget searches the database already has in their final state, after a while; keep the file small
    list.retain(|p| !(p.sent == p.status && p.status != "running" && now - p.finished_at.unwrap_or(p.started_at) > KEEP_SENT_SECS));
    if list.len() > KEEP_SEARCHES {
        let cut = list.len() - KEEP_SEARCHES;
        list.drain(0..cut);
    }
    save_pending(dir, &list);
    id
}

/// The run of this assistant ended: record how.
pub fn finish(dir: &Path, agent: Agent, reason: &EndReason, now: i64) {
    let status = match reason {
        EndReason::Finished { ok: true } => "done",
        EndReason::Stopped => "stopped",
        _ => "failed",
    };
    let _g = FILE_LOCK.lock().unwrap();
    let mut list = load_pending(dir);
    if let Some(p) = list.iter_mut().rev().find(|p| p.agent == agent.key() && p.status == "running") {
        p.status = status.into();
        p.finished_at = Some(now);
        save_pending(dir, &list);
    }
}

fn mark_sent(dir: &Path, sent: &[(String, String)]) {
    if sent.is_empty() {
        return;
    }
    let _g = FILE_LOCK.lock().unwrap();
    let mut list = load_pending(dir);
    for (id, status) in sent {
        if let Some(p) = list.iter_mut().find(|p| &p.id == id) {
            p.sent = status.clone();
        }
    }
    save_pending(dir, &list);
}

// ---------- the sync ----------

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct HistoryResult {
    /// "sent", "nothing", "waiting", "off" or "error".
    pub state: String,
    pub message: String,
    pub searches: usize,
    pub rows: usize,
    pub removed: usize,
    pub at: i64,
}

fn result(state: &str, message: &str, searches: usize, rows: usize, removed: usize, at: i64) -> HistoryResult {
    HistoryResult { state: state.into(), message: message.into(), searches, rows, removed, at }
}

/// What a status from the database means for a sync: Ok to go on, or the state and sentence to stop with.
fn stop_for(status: u16) -> (&'static str, &'static str) {
    match status {
        403 => ("waiting", "Your company access is still being set up. Your history is sent again shortly."),
        429 => ("waiting", "Too many requests. Your history is sent a little later."),
        500..=599 => ("waiting", "The service had a problem. Your history is sent again later."),
        _ => ("error", "The service gave an answer Recruit Desk does not understand."),
    }
}

#[derive(Default)]
pub struct HistorySync {
    pub(crate) lock: Mutex<()>,
    last: Mutex<Option<HistoryResult>>,
}

struct Row {
    line: String,
    value: Value,
}

fn read_lines(path: &Path) -> Vec<String> {
    match std::fs::metadata(path) {
        Ok(m) if m.len() > MAX_FILE_BYTES => {
            let _ = std::fs::remove_file(path);   // far larger than an assistant ever writes: not ours to trust
            Vec::new()
        }
        Ok(_) => std::fs::read_to_string(path).map(|s| s.lines().filter(|l| !l.trim().is_empty()).take(MAX_LINES).map(str::to_string).collect()).unwrap_or_default(),
        Err(_) => Vec::new(),
    }
}

/// Write what is left of a working file, or remove it when nothing is.
fn write_rest(path: &Path, rest: &[String]) {
    if rest.is_empty() {
        let _ = std::fs::remove_file(path);
    } else {
        let _ = std::fs::write(path, rest.join("\n") + "\n");
    }
}

/// Take the assistant's queue as the working copy (a leftover working copy from a failed sync comes first).
fn take_queue(dir: &Path, queue: &str, working: &str) -> PathBuf {
    let (q, w) = (dir.join(queue), dir.join(working));
    if !w.exists() && q.exists() {
        let _ = std::fs::rename(&q, &w);   // an assistant that has the file open keeps it: tried again next sync
    }
    w
}

fn valid_hash(h: &str) -> bool {
    h.len() == 64 && h.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f'))
}

impl HistorySync {
    pub fn new() -> Self {
        HistorySync::default()
    }

    pub fn last(&self) -> Option<HistoryResult> {
        self.last.lock().unwrap().clone()
    }

    /// Store pending searches and queued rows, and remove what was erased. Safe to call often.
    pub fn sync(&self, auth: &AuthService, dirs: &[(Agent, PathBuf)], data_dir: &Path, now: i64) -> HistoryResult {
        let Ok(_busy) = self.lock.try_lock() else {
            return self.last().unwrap_or_else(|| result("waiting", "A sync is already running.", 0, 0, 0, now));
        };
        let r = self.run(auth, dirs, data_dir, now);
        *self.last.lock().unwrap() = Some(r.clone());
        r
    }

    fn run(&self, auth: &AuthService, dirs: &[(Agent, PathBuf)], data_dir: &Path, now: i64) -> HistoryResult {
        let st = auth.status(now);
        if !st.enabled {
            return result("off", "Sign-in is off, so nothing is stored.", 0, 0, 0, now);
        }
        if st.state == "no_company" {
            return result("waiting", "Join your company to store your history. It stays on this computer until then.", 0, 0, 0, now);
        }
        if st.state != "signed_in" {
            return result("waiting", "Sign in to store your history. It stays on this computer until then.", 0, 0, 0, now);
        }
        if st.offline {
            return result("waiting", "You are offline. Your history is stored when you are back online.", 0, 0, 0, now);
        }
        let tenant = auth.tenant_id(now).unwrap_or_default();
        let (mut searches, mut rows, mut removed) = (0usize, 0usize, 0usize);

        // 1. searches whose state the database does not have yet
        let snapshot: Vec<Pending> = {
            let _g = FILE_LOCK.lock().unwrap();
            load_pending(data_dir)
        };
        let mut sent_now: Vec<(String, String)> = Vec::new();
        let mut stop: Option<(&str, &str)> = None;
        for p in snapshot.iter().filter(|p| p.sent != p.status) {
            let mut body = json!({"id": p.id, "tenant_id": tenant, "agent": p.agent, "inputs": p.inputs, "started_at": iso(p.started_at), "status": p.status});
            if let Some(f) = p.finished_at {
                body["finished_at"] = json!(iso(f));
            }
            match auth.data_request("POST", "searches?on_conflict=id", Some(&body), Some("resolution=merge-duplicates,return=minimal"), now) {
                Err(_) => {
                    stop = Some(("waiting", "Your history is stored when the service can be reached."));
                    break;
                }
                Ok(r) => match r.status {
                    200 | 201 | 204 => {
                        sent_now.push((p.id.clone(), p.status.clone()));
                        searches += 1;
                    }
                    // the database refused this one (it will never accept it): do not try again
                    400 | 422 => sent_now.push((p.id.clone(), p.status.clone())),
                    s => {
                        stop = Some(stop_for(s));
                        break;
                    }
                },
            }
        }
        mark_sent(data_dir, &sent_now);
        let known: Vec<&Pending> = snapshot.iter().filter(|p| p.sent == p.status || sent_now.iter().any(|(id, _)| id == &p.id)).collect();

        // 2. queued rows, per assistant
        if stop.is_none() {
            'agents: for (agent, dir) in dirs {
                let path = take_queue(dir, ROWS_FILE, SENDING_FILE);
                let lines = read_lines(&path);
                if lines.is_empty() {
                    write_rest(&path, &[]);
                    continue;
                }
                let mut ready: Vec<Row> = Vec::new();
                for line in &lines {
                    if let Some(v) = parse_row(*agent, line) {
                        ready.push(Row { line: line.clone(), value: v });
                    }   // a damaged or foreign line is dropped
                }
                let mut done = 0usize;
                while done < ready.len() {
                    let batch = &ready[done..(done + BATCH).min(ready.len())];
                    let body: Vec<Value> = batch.iter().map(|r| with_search(&tenant, *agent, &r.value, &known)).collect();
                    match auth.data_request("POST", "shared_rows?on_conflict=tenant_id,user_id,agent,subject_hash", Some(&Value::Array(body)), Some("resolution=ignore-duplicates,return=minimal"), now) {
                        Err(_) => {
                            stop = Some(("waiting", "Your history is stored when the service can be reached."));
                            break;
                        }
                        Ok(r) => match r.status {
                            200 | 201 | 204 => {
                                rows += batch.len();
                                done += batch.len();
                            }
                            400 | 422 => {
                                // one bad row spoils a batch: send them one at a time, dropping the refused ones
                                for r in batch {
                                    let one = Value::Array(vec![with_search(&tenant, *agent, &r.value, &known)]);
                                    match auth.data_request("POST", "shared_rows?on_conflict=tenant_id,user_id,agent,subject_hash", Some(&one), Some("resolution=ignore-duplicates,return=minimal"), now) {
                                        Ok(x) if matches!(x.status, 200 | 201 | 204) => rows += 1,
                                        Ok(x) if matches!(x.status, 400 | 422) => {}
                                        Ok(x) => {
                                            stop = Some(stop_for(x.status));
                                            break;
                                        }
                                        Err(_) => {
                                            stop = Some(("waiting", "Your history is stored when the service can be reached."));
                                            break;
                                        }
                                    }
                                    done += 1;
                                }
                                if stop.is_some() {
                                    break;
                                }
                            }
                            s => {
                                stop = Some(stop_for(s));
                                break;
                            }
                        },
                    }
                }
                let rest: Vec<String> = ready[done.min(ready.len())..].iter().map(|r| r.line.clone()).collect();
                write_rest(&path, &rest);
                if stop.is_some() {
                    break 'agents;
                }
            }
        }

        // 3. erased people: remove every stored copy of their rows
        if stop.is_none() {
            'erase: for (agent, dir) in dirs {
                let path = take_queue(dir, ERASED_FILE, ERASED_SENDING_FILE);
                let lines = read_lines(&path);
                let mut rest: Vec<String> = Vec::new();
                let mut seen: Vec<String> = Vec::new();
                for (i, line) in lines.iter().enumerate() {
                    let h = serde_json::from_str::<Value>(line).ok().filter(|v| v.get("agent").and_then(Value::as_str) == Some(agent.key())).and_then(|v| v.get("subject_hash").and_then(Value::as_str).map(str::to_string));
                    let Some(h) = h.filter(|h| valid_hash(h)) else { continue };   // damaged or foreign: dropped
                    if seen.contains(&h) {
                        continue;
                    }
                    match auth.data_request("DELETE", &format!("shared_rows?agent=eq.{}&subject_hash=eq.{h}", agent.key()), None, Some("return=minimal"), now) {
                        Ok(r) if matches!(r.status, 200 | 204 | 404) => {
                            seen.push(h);
                            removed += 1;
                        }
                        Ok(r) => {
                            stop = Some(stop_for(r.status));
                            rest.extend(lines[i..].iter().cloned());
                            break;
                        }
                        Err(_) => {
                            stop = Some(("waiting", "Your history is stored when the service can be reached."));
                            rest.extend(lines[i..].iter().cloned());
                            break;
                        }
                    }
                }
                write_rest(&path, &rest);
                if stop.is_some() {
                    break 'erase;
                }
            }
        }

        match stop {
            Some((state, msg)) => result(state, msg, searches, rows, removed, now),
            None if searches + rows + removed == 0 => result("nothing", "Nothing new to store.", 0, 0, 0, now),
            None => result("sent", "Your history is stored in the company database.", searches, rows, removed, now),
        }
    }
}

/// One queued line, checked again: this assistant's, a valid hash and time, only the allowed fields.
fn parse_row(agent: Agent, line: &str) -> Option<Value> {
    let v: Value = serde_json::from_str(line).ok()?;
    if v.get("agent").and_then(Value::as_str) != Some(agent.key()) {
        return None;
    }
    let hash = v.get("subject_hash").and_then(Value::as_str).filter(|h| valid_hash(h))?;
    let created = v.get("created_at").and_then(Value::as_str).filter(|c| (19..=40).contains(&c.len()) && parse_iso(c).is_some())?;
    let record = clean_fields(agent, v.get("record")?.as_object()?);
    if record.is_empty() {
        return None;
    }
    Some(json!({"subject_hash": hash, "created_at": created, "record": record}))
}

/// The row as it is sent, linked to the search of that assistant that was running when the row was created.
fn with_search(tenant: &str, agent: Agent, row: &Value, known: &[&Pending]) -> Value {
    let at = row.get("created_at").and_then(Value::as_str).and_then(parse_iso).unwrap_or(0);
    let search = known.iter().rev().find(|p| p.agent == agent.key() && p.started_at - 5 <= at && at <= p.finished_at.map(|f| f + 60).unwrap_or(i64::MAX)).map(|p| p.id.clone());
    json!({"tenant_id": tenant, "agent": agent.key(), "search_id": search, "subject_hash": row["subject_hash"], "created_at": row["created_at"], "record": row["record"]})
}

// ---------- reading it back (the History screen) ----------

fn read_error(status: u16) -> String {
    match status {
        429 => "Too many requests. Try again in a moment.".into(),
        500..=599 => "The service had a problem. Try again in a minute.".into(),
        _ => "The service gave an answer Recruit Desk does not understand.".into(),
    }
}

/// The searches the signed-in person may see (their own; an admin sees everyone's in the company), newest first.
pub fn list(auth: &AuthService, now: i64) -> Result<Value, String> {
    let r = auth.data_request("GET", "searches?select=id,user_email,agent,inputs,started_at,finished_at,status,shared_rows(count)&order=started_at.desc&limit=200", None, None, now)?;
    if r.status != 200 {
        return Err(read_error(r.status));
    }
    let rows: Vec<Value> = serde_json::from_str::<Value>(&r.body).ok().and_then(|v| v.as_array().cloned()).unwrap_or_default();
    Ok(Value::Array(rows.into_iter().map(|s| {
        let count = s.get("shared_rows").and_then(Value::as_array).and_then(|a| a.first()).and_then(|c| c.get("count")).and_then(Value::as_i64).unwrap_or(0);
        json!({"id": s["id"], "who": s["user_email"], "agent": s["agent"], "inputs": s["inputs"], "started_at": s["started_at"], "finished_at": s["finished_at"], "status": s["status"], "rows": count})
    }).collect()))
}

/// The result rows of one search.
pub fn rows(auth: &AuthService, search_id: &str, now: i64) -> Result<Value, String> {
    let id = valid_code(search_id).map_err(|_| "That search does not exist.".to_string())?;
    let r = auth.data_request("GET", &format!("shared_rows?search_id=eq.{id}&select=agent,record,created_at,user_email&order=created_at.desc&limit=1000"), None, None, now)?;
    if r.status != 200 {
        return Err(read_error(r.status));
    }
    let list: Vec<Value> = serde_json::from_str::<Value>(&r.body).ok().and_then(|v| v.as_array().cloned()).unwrap_or_default();
    Ok(Value::Array(list.into_iter().map(|x| json!({"agent": x["agent"], "record": x["record"], "created_at": x["created_at"], "who": x["user_email"]})).collect()))
}

/// Remove one search and its rows from the company database (the person's own, or any as an admin; the database decides).
pub fn delete(auth: &AuthService, search_id: &str, now: i64) -> Result<Value, String> {
    let id = valid_code(search_id).map_err(|_| "That search does not exist.".to_string())?;
    let r = auth.data_request("DELETE", &format!("searches?id=eq.{id}"), None, Some("return=representation"), now)?;
    match r.status {
        200 => {
            let gone = serde_json::from_str::<Value>(&r.body).ok().and_then(|v| v.as_array().map(|a| a.len())).unwrap_or(0);
            if gone == 0 { Err("That search no longer exists, or you are not allowed to remove it. Refresh the list.".into()) } else { Ok(Value::Null) }
        }
        204 => Ok(Value::Null),
        s => Err(read_error(s)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn times_are_written_as_utc() {
        assert_eq!(iso(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso(1_792_573_274), "2026-10-21T09:01:14Z");
        assert_eq!(iso(951_825_600), "2000-02-29T12:00:00Z");
        assert_eq!(parse_iso(&iso(1_800_000_123)), Some(1_800_000_123));
    }

    #[test]
    fn ids_look_like_uuids_and_differ() {
        let (a, b) = (new_id(), new_id());
        assert_ne!(a, b);
        assert!(valid_code(&a).is_ok(), "{a}");
        assert_eq!(a.chars().nth(14), Some('4'));
    }

    #[test]
    fn contact_details_are_removed_from_free_text() {
        assert_eq!(redact("Mail jobs@acme.com or call +91 98765 43210 today"), "Mail [removed] or call [removed] today");
        assert_eq!(redact("recruiter: Ann.Rao+hr@sub.example.co.in."), "recruiter: [removed].");
        assert_eq!(redact("Call (415) 555-0100 now"), "Call [removed] now");
        assert_eq!(redact("9876543210"), "[removed]");
    }

    #[test]
    fn ordinary_numbers_and_text_are_kept() {
        for s in ["5+ years of React, 10-12 team size, 2020-2024", "Salary 1200000-1800000 INR", "Remote (India), Pune", "version 3.4.5 build 20261019", "emoji ✓ and ünïcode", "a @ b", "user@localhost", ""] {
            assert_eq!(redact(s), s, "{s}");
        }
    }

    #[test]
    fn what_was_searched_is_stored_per_assistant_with_only_allowed_fields() {
        let i = RunInput { jd: "Senior React dev, mail hr@acme.com".into(), company: "Acme".into(), domain: "acme.com".into(), public_posts: true, csv: "company,domain\nA,a.com".into(), contacts: true, ..Default::default() };
        assert_eq!(search_inputs(Agent::Candidate, &i), json!({"jd": "Senior React dev, mail [removed]"}));
        assert_eq!(search_inputs(Agent::Company, &i), json!({"company": "Acme", "domain": "acme.com", "publicPosts": true}));
        assert_eq!(search_inputs(Agent::Account, &i), json!({"company": "Acme", "domain": "acme.com", "csv": "company,domain\nA,a.com", "contacts": true}));
        let long = RunInput { jd: "x".repeat(50_000), ..Default::default() };
        assert_eq!(search_inputs(Agent::Candidate, &long)["jd"].as_str().unwrap().len(), MAX_TEXT);
        assert_eq!(search_inputs(Agent::Candidate, &RunInput::default()), json!({}), "an empty field is left out");
    }
}
