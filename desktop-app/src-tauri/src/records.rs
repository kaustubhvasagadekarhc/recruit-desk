// Author: kaustubh.s.v
//! The last record each assistant created, kept in step with the company's database on Neon (the Data API).
//!
//! Processing stays on this computer. Each assistant leaves a small plain file, `last_record.json`, in its own folder after an upsert
//! that created a new row (see the assistants' last_record.py). This module reads it, checks it again, and stores it in the company database
//! while someone is signed in to a company:
//! - a fixed list of fields per assistant (no email, phone, evidence or notes); anything else in the file is dropped here, so a
//!   damaged or edited file cannot carry more than the list;
//! - the record's key arrives hashed; the database keeps one row per assistant per company and the newer record wins;
//! - the hash of what was sent is remembered locally (records_sent.json). When the assistant's file is gone (the person it was about
//!   was erased) the stored copy is removed too, but only if it is still that record: a newer record from another computer stays.
//! Nothing is lost while offline or signed out: the file stays and is sent at the next sync.

use crate::auth::AuthService;
use crate::settings::Agent;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const FILE_NAME: &str = "last_record.json";
const MAX_FILE_BYTES: u64 = 16 * 1024;
const MAX_VALUE: usize = 300;
const MAX_URL: usize = 600;

/// The only fields that may be sent for each assistant (the database enforces the same list).
pub fn allowed(agent: Agent) -> &'static [&'static str] {
    match agent {
        Agent::Candidate => &["name", "title", "company", "location", "match_score", "profile_url", "jd", "date_found"],
        Agent::Company => &["job_title", "company", "location", "status", "source_type", "posted_date", "listing_url", "date_discovered"],
        Agent::Account => &["company", "domain", "product_type", "hq", "funding", "confidence", "last_checked"],
    }
}

/// Only the allowed fields of an assistant's record, scalars only, cut to length; empty text is left out. Shared by the last record and the
/// search history, so both carry the same fields.
pub fn clean_fields(agent: Agent, obj: &Map<String, Value>) -> Map<String, Value> {
    let mut record = Map::new();
    for (k, v) in obj {
        if !allowed(agent).contains(&k.as_str()) {
            continue;
        }
        match v {
            Value::String(s) if !s.trim().is_empty() => {
                let limit = if k.ends_with("_url") { MAX_URL } else { MAX_VALUE };
                record.insert(k.clone(), Value::String(s.chars().take(limit).collect()));
            }
            Value::Number(_) => {
                record.insert(k.clone(), v.clone());
            }
            _ => {}
        }
    }
    record
}

#[derive(Debug, Clone, PartialEq)]
pub struct LocalRecord {
    pub subject_hash: String,
    pub created_at: String,
    pub record: Map<String, Value>,
}

/// The assistant's file, checked again. None when it is missing, damaged, too large or not for this assistant.
pub fn read_local(dir: &Path, agent: Agent) -> Option<LocalRecord> {
    let path = dir.join(FILE_NAME);
    if std::fs::metadata(&path).ok()?.len() > MAX_FILE_BYTES {
        return None;
    }
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).ok()?).ok()?;
    if doc.get("agent").and_then(Value::as_str) != Some(agent.key()) {
        return None;
    }
    let hash = doc.get("subject_hash").and_then(Value::as_str)?;
    if hash.len() != 64 || !hash.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')) {
        return None;
    }
    let created = doc.get("created_at").and_then(Value::as_str)?;
    if !(19..=40).contains(&created.len()) || !created.chars().all(|c| c.is_ascii_digit() || matches!(c, '-' | ':' | 'T' | '+' | '.' | 'Z')) {
        return None;
    }
    let record = clean_fields(agent, doc.get("record")?.as_object()?);
    if record.is_empty() {
        return None;
    }
    Some(LocalRecord { subject_hash: hash.to_string(), created_at: created.to_string(), record })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Sent {
    pub subject_hash: String,
    pub created_at: String,
}

type SentMap = BTreeMap<String, Sent>;

fn load_sent(path: &Path) -> SentMap {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn save_sent(path: &Path, m: &SentMap) {
    if let Ok(s) = serde_json::to_string_pretty(m) {
        let _ = std::fs::write(path, s);
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SyncResult {
    pub agent: String,
    /// "sent", "unchanged", "older" (a newer one is stored), "removed", "nothing", "waiting", "off" or "error".
    pub state: String,
    pub message: String,
    pub at: i64,
}

fn res(agent: Agent, state: &str, message: &str, at: i64) -> SyncResult {
    SyncResult { agent: agent.key().to_string(), state: state.to_string(), message: message.to_string(), at }
}

#[derive(Default)]
pub struct RecordsSync {
    pub(crate) lock: Mutex<()>,
    last: Mutex<Vec<SyncResult>>,
}

impl RecordsSync {
    pub fn new() -> Self {
        RecordsSync::default()
    }

    /// What the last sync said, per assistant.
    pub fn results(&self) -> Vec<SyncResult> {
        self.last.lock().unwrap().clone()
    }

    /// Bring the stored copy in step with the assistants' files. Safe to call often: it sends only what changed, and a second
    /// call while one is running just returns the last answer.
    pub fn sync(&self, auth: &AuthService, dirs: &[(Agent, PathBuf)], sent_path: &Path, now: i64) -> Vec<SyncResult> {
        let Ok(_busy) = self.lock.try_lock() else { return self.results() };
        let st = auth.status(now);
        let gate = if !st.enabled {
            Some(("off", "Sign-in is off, so nothing is sent."))
        } else if st.state == "no_company" {
            Some(("waiting", "Join your company to send it. The record stays on this computer until then."))
        } else if st.state != "signed_in" {
            Some(("waiting", "Sign in to send it. The record stays on this computer until then."))
        } else if st.offline {
            Some(("waiting", "You are offline. It is sent when you are back online."))
        } else {
            None
        };
        let tenant = auth.tenant_id(now).unwrap_or_default();
        let mut sent = load_sent(sent_path);
        let mut out = Vec::new();
        for (agent, dir) in dirs {
            let agent = *agent;
            if let Some((state, msg)) = gate {
                out.push(res(agent, state, msg, now));
                continue;
            }
            let local = read_local(dir, agent);
            let key = agent.key().to_string();
            let r = match (local, sent.get(&key).cloned()) {
                (Some(l), Some(s)) if l.subject_hash == s.subject_hash && l.created_at == s.created_at => res(agent, "unchanged", "The company database already has the latest record.", now),
                (Some(l), _) => {
                    let body = json!({"tenant_id": tenant, "agent": key, "record": l.record, "subject_hash": l.subject_hash, "created_at": l.created_at});
                    // an upsert: a newer record replaces the stored one, an older one is refused by the database with 409
                    match auth.data_request("POST", "last_records?on_conflict=tenant_id,agent", Some(&body), Some("resolution=merge-duplicates,return=minimal"), now) {
                        Err(m) => res(agent, "waiting", &m, now),
                        Ok(reply) => match reply.status {
                            200 | 201 | 204 => {
                                sent.insert(key, Sent { subject_hash: l.subject_hash, created_at: l.created_at });
                                res(agent, "sent", "The latest record is stored in the company database.", now)
                            }
                            409 => {
                                // someone's newer record is already there: nothing to send, and nothing to retry
                                sent.insert(key, Sent { subject_hash: l.subject_hash, created_at: l.created_at });
                                res(agent, "older", "A newer record from another computer is already stored.", now)
                            }
                            400 | 422 => res(agent, "error", "The database did not accept this record.", now),
                            // Right after joining a company the database can take a moment to see the new membership: try again later
                            403 => res(agent, "waiting", "Your company access is still being set up. It is sent again shortly.", now),
                            429 => res(agent, "waiting", "Too many requests. It is sent a little later.", now),
                            500..=599 => res(agent, "waiting", "The service had a problem. It is sent again later.", now),
                            _ => res(agent, "error", "The service gave an answer Recruit Desk does not understand.", now),
                        },
                    }
                }
                (None, Some(s)) => {
                    // the assistant's file is gone (the person was erased, or the file was removed): take our copy off the database
                    // the filter includes the hash, so a newer record from another computer matches nothing and stays
                    match auth.data_request("DELETE", &format!("last_records?tenant_id=eq.{tenant}&agent=eq.{key}&subject_hash=eq.{}", s.subject_hash), None, None, now) {
                        Err(m) => res(agent, "waiting", &m, now),
                        Ok(reply) => match reply.status {
                            200 | 204 | 404 | 409 => {
                                sent.remove(&key);
                                res(agent, "removed", "The stored record was removed because it no longer exists on this computer.", now)
                            }
                            429 => res(agent, "waiting", "Too many requests. It is removed a little later.", now),
                            500..=599 => res(agent, "waiting", "The service had a problem. It is removed later.", now),
                            _ => res(agent, "error", "The service gave an answer Recruit Desk does not understand.", now),
                        },
                    }
                }
                (None, None) => res(agent, "nothing", "No record has been created yet.", now),
            };
            out.push(r);
        }
        if !sent.is_empty() || sent_path.exists() {
            save_sent(sent_path, &sent);
        }
        *self.last.lock().unwrap() = out.clone();
        out
    }
}

/// What the database holds now for this company (every assistant's last record), for the admin screen.
pub fn stored(auth: &AuthService, now: i64) -> Result<Value, String> {
    let reply = auth.data_request("GET", "last_records?select=agent,record,subject_hash,created_at,stored_at,stored_by_email&order=agent", None, None, now)?;
    match reply.status {
        200 => {
            let rows: Vec<Value> = serde_json::from_str::<Value>(&reply.body).ok().and_then(|v| v.as_array().cloned()).unwrap_or_default();
            Ok(Value::Array(rows.into_iter().map(|mut r| {
                if let Some(o) = r.as_object_mut() {
                    let who = o.remove("stored_by_email").unwrap_or(Value::Null);
                    o.insert("stored_by".into(), who);
                }
                r
            }).collect()))
        }
        429 => Err("Too many requests. Try again in a moment.".into()),
        500..=599 => Err("The service had a problem. Try again in a minute.".into()),
        _ => Err("The service gave an answer Recruit Desk does not understand.".into()),
    }
}
