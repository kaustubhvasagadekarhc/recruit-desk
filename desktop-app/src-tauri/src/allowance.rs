// Author: kaustubh.s.v
//! The token allowance: one cap per person, counted over all the assistants together (2026-10-21).
//!
//! While someone is signed in to a company, each person has their own cap and window length (a new person starts at 3,000,000 tokens per
//! 4 hours; the company's admins set each person's values, nothing is company-wide). A person's count adds up what all three assistants spend on their computers, their window opens with their first tokens
//! counted in it, and when the cap is reached every assistant stops for them until the window ends. The count and the clock live in the
//! company database (neon-backend/setup.sql, `allowance_*`), so nobody's own computer clock matters. With sign-in off the app keeps the
//! old per-computer allowance (cap.rs).
//!
//! This computer batches what it spends: tokens are added to a local `pending` count at once (the run can be stopped on the spot when the
//! cap is reached) and sent to the database every few seconds by `tick`. What is pending survives a restart (`allowance_pending.json`) and
//! is sent when the computer is back online. The same person on two computers sees the other's usage with the next refresh, within about
//! a minute.

use crate::auth::AuthService;
use crate::cap::{Tokens, DEFAULT_CAP};
use crate::settings::Agent;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub const PENDING_FILE: &str = "allowance_pending.json";
const REFRESH_EVERY_MS: i64 = 60_000;
const MAX_PER_CALL: u64 = 5_000_000;

#[derive(Debug, Clone, PartialEq)]
struct Pool {
    cap: u64,
    window_minutes: u32,
    counted: u64,
    /// Database time, ms since 1970.
    window_started: Option<i64>,
    locked_at: Option<i64>,
}

impl Default for Pool {
    fn default() -> Self {
        Pool { cap: DEFAULT_CAP, window_minutes: 240, counted: 0, window_started: None, locked_at: None }
    }
}

#[derive(Default)]
struct State {
    pool: Pool,
    /// Database time minus this computer's time, ms, as of the last answer.
    offset_ms: i64,
    pending: BTreeMap<Agent, u64>,
    last_refresh_ms: i64,
    fetched: bool,
}

/// What the window shows for one assistant, on this computer's clock.
#[derive(Debug, Clone, PartialEq)]
pub struct View {
    pub cap: u64,
    pub counted: u64,
    pub locked: bool,
    pub locked_at: Option<i64>,
    pub resets_at: Option<i64>,
    pub window_minutes: u32,
}

pub struct Allowance {
    auth: Arc<AuthService>,
    dir: PathBuf,
    st: Mutex<State>,
}

fn parse_pool(v: &Value) -> Option<(i64, Pool)> {
    let now = v.get("now_ms")?.as_i64()?;
    Some((now, Pool {
        cap: v.get("cap")?.as_u64()?,
        window_minutes: v.get("window_minutes")?.as_u64()? as u32,
        counted: v.get("counted")?.as_u64()?,
        window_started: v.get("window_started_ms").and_then(Value::as_i64),
        locked_at: v.get("locked_at_ms").and_then(Value::as_i64),
    }))
}

impl Allowance {
    pub fn new(auth: Arc<AuthService>, dir: PathBuf) -> Self {
        let pending: BTreeMap<String, u64> = std::fs::read_to_string(dir.join(PENDING_FILE)).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default();
        let mut st = State::default();
        for (k, n) in pending {
            if let (Ok(a), true) = (Agent::parse(&k), n > 0) {
                st.pending.insert(a, n);
            }
        }
        Allowance { auth, dir, st: Mutex::new(st) }
    }

    fn save_pending(&self, st: &State) {
        let map: BTreeMap<&str, u64> = st.pending.iter().filter(|(_, n)| **n > 0).map(|(a, n)| (a.key(), *n)).collect();
        if let Ok(s) = serde_json::to_string(&map) {
            let tmp = self.dir.join(format!("{PENDING_FILE}.tmp"));
            if std::fs::write(&tmp, s).is_ok() {
                let _ = std::fs::rename(&tmp, self.dir.join(PENDING_FILE));
            }
        }
    }

    /// Signed in to a company: this computer may talk to the pool.
    fn signed_in(&self, now_ms: i64) -> bool {
        self.auth.status(now_ms / 1000).state == "signed_in"
    }

    /// The pool is in charge only while signed in to a company **and** after it has been read successfully once. Until then (never
    /// reachable, or the database does not have the allowance yet) the old per-computer allowance applies, so nobody is locked by a
    /// count made up from defaults.
    fn in_charge(&self, now_ms: i64) -> bool {
        self.signed_in(now_ms) && self.st.lock().unwrap().fetched
    }

    fn view_of(st: &State, now_ms: i64) -> View {
        let pool = st.pool.clone();
        let win = pool.window_minutes as i64 * 60_000;
        let server_now = now_ms + st.offset_ms;
        let ended = pool.window_started.map(|w| server_now >= w + win).unwrap_or(false);
        let (counted, started, locked_at) = if ended { (0, None, None) } else { (pool.counted, pool.window_started, pool.locked_at) };
        let pending: u64 = st.pending.values().sum();   // over all the assistants
        let total = counted + pending;
        let started = started.or(if pending > 0 { Some(server_now) } else { None });   // tokens not yet sent open the window here
        let locked = locked_at.is_some() || total >= pool.cap;
        View {
            cap: pool.cap,
            counted: total,
            locked,
            locked_at: if locked { Some(locked_at.map(|t| t - st.offset_ms).unwrap_or(now_ms)) } else { None },
            resets_at: started.map(|w| w + win - st.offset_ms),
            window_minutes: pool.window_minutes,
        }
    }

    /// The person's allowance (the same whichever assistant asks: it is counted over all of them), or None when the old per-computer
    /// allowance applies.
    pub fn view(&self, _a: Agent, now_ms: i64) -> Option<View> {
        if !self.in_charge(now_ms) {
            return None;
        }
        Some(Self::view_of(&self.st.lock().unwrap(), now_ms))
    }

    /// Count tokens spent. Returns Some(true) while the pool still has room, Some(false) once it is full, None when the old allowance applies.
    pub fn add(&self, a: Agent, d: &Tokens, now_ms: i64) -> Option<bool> {
        if !self.in_charge(now_ms) {
            return None;
        }
        let mut st = self.st.lock().unwrap();
        let n = d.counted();
        if n > 0 {
            *st.pending.entry(a).or_insert(0) += n;
            self.save_pending(&st);
        }
        Some(!Self::view_of(&st, now_ms).locked)
    }

    fn apply(&self, v: &Value, now_ms: i64) -> bool {
        let Some((server_now, pool)) = parse_pool(v) else { return false };
        let mut st = self.st.lock().unwrap();
        st.pool = pool;
        st.offset_ms = server_now - now_ms;
        st.last_refresh_ms = now_ms;
        st.fetched = true;
        true
    }

    fn rpc(&self, name: &str, body: &Value, now_ms: i64) -> Result<Value, (u16, String)> {
        let r = self.auth.data_request("POST", &format!("rpc/{name}"), Some(body), None, now_ms / 1000).map_err(|m| (0u16, m))?;
        if r.status == 200 {
            serde_json::from_str(&r.body).map_err(|_| (200, "The service gave an answer Recruit Desk does not understand.".to_string()))
        } else {
            Err((r.status, r.body))
        }
    }

    /// Ask the database for the pool as it is now.
    pub fn refresh(&self, now_ms: i64) -> Result<(), String> {
        if !self.signed_in(now_ms) {
            return Ok(());
        }
        match self.rpc("allowance_state", &json!({}), now_ms) {
            Ok(v) if self.apply(&v, now_ms) => Ok(()),
            Ok(_) => Err("The service gave an answer Recruit Desk does not understand.".into()),
            Err((0, m)) => Err(m),
            Err((s, _)) => Err(match s {
                429 => "Too many requests. Try again in a moment.".to_string(),
                500..=599 => "The service had a problem. Try again in a minute.".to_string(),
                403 => "You are not part of a company yet.".to_string(),
                _ => "The service gave an answer Recruit Desk does not understand.".to_string(),
            }),
        }
    }

    /// Send what this computer has spent since the last time.
    pub fn flush(&self, now_ms: i64) {
        if !self.signed_in(now_ms) || self.auth.status(now_ms / 1000).offline {
            return;
        }
        for a in Agent::ALL {
            loop {
                let n = self.st.lock().unwrap().pending.get(&a).copied().unwrap_or(0).min(MAX_PER_CALL);
                if n == 0 {
                    break;
                }
                match self.rpc("allowance_add", &json!({"p_agent": a.key(), "p_tokens": n}), now_ms) {
                    Ok(v) => {
                        self.apply(&v, now_ms);
                        let mut st = self.st.lock().unwrap();
                        let left = st.pending.get(&a).copied().unwrap_or(0).saturating_sub(n);
                        st.pending.insert(a, left);
                        self.save_pending(&st);
                    }
                    // the database will never accept this amount for this assistant: drop it rather than send it forever
                    Err((400 | 422, _)) => {
                        let mut st = self.st.lock().unwrap();
                        st.pending.insert(a, 0);
                        self.save_pending(&st);
                        break;
                    }
                    Err(_) => return,   // offline, a bad moment or not a member yet: tried again at the next tick
                }
            }
        }
    }

    /// Called every few seconds from the background thread.
    pub fn tick(&self, now_ms: i64) {
        if !self.signed_in(now_ms) {
            return;
        }
        self.flush(now_ms);
        let due = {
            let st = self.st.lock().unwrap();
            !st.fetched || now_ms - st.last_refresh_ms >= REFRESH_EVERY_MS
        };
        if due {
            let _ = self.refresh(now_ms);
        }
    }

    fn admin_call(&self, name: &str, body: Value, now_ms: i64) -> Result<(), String> {
        let st = self.auth.status(now_ms / 1000);
        if st.state != "signed_in" {
            return Err("Sign in first.".into());
        }
        if st.user.as_ref().map(|u| u.role.as_str()) != Some("admin") {
            return Err("Only an admin can change the allowance.".into());
        }
        match self.rpc(name, &body, now_ms) {
            Ok(v) if self.apply(&v, now_ms) => Ok(()),
            Ok(_) => Err("The service gave an answer Recruit Desk does not understand.".into()),
            Err((0, m)) => Err(m),
            Err((s, _)) => Err(match s {
                403 => "Only an admin can change the allowance.".to_string(),
                400 | 422 => "Check the numbers and the address: the cap is at least 1,000 tokens, the window is between 15 minutes and 24 hours, and the person must be in your company.".to_string(),
                429 => "Too many requests. Try again in a moment.".to_string(),
                500..=599 => "The service had a problem. Try again in a minute.".to_string(),
                _ => "The service gave an answer Recruit Desk does not understand.".to_string(),
            }),
        }
    }

    /// An admin sets one person's own cap (tokens) and window (minutes), by email. Nothing is company-wide.
    pub fn set_user(&self, email: &str, cap: u64, window_minutes: u32, now_ms: i64) -> Result<(), String> {
        if cap < 1_000 || cap > 1_000_000_000 || !(15..=1440).contains(&window_minutes) {
            return Err("Check the numbers: the cap is at least 1,000 tokens and the window is between 15 minutes and 24 hours.".into());
        }
        if email.trim().is_empty() {
            return Err("Say whose allowance to change.".into());
        }
        self.flush(now_ms);   // what this computer spent first, so the new cap sees it
        self.admin_call("allowance_set_user", json!({"p_email": email.trim(), "p_cap": cap, "p_window_minutes": window_minutes}), now_ms)
    }

    /// An admin empties one person's count (by email) or everyone's (None). The window starts again with the next tokens.
    pub fn reset(&self, email: Option<&str>, now_ms: i64) -> Result<(), String> {
        let own = self.auth.status(now_ms / 1000).user.map(|u| u.email.to_lowercase());
        let body = match email { Some(e) => json!({"p_email": e}), None => json!({}) };
        self.admin_call("allowance_reset", body, now_ms)?;
        if email.is_none() || email.map(str::to_lowercase) == own {
            let mut st = self.st.lock().unwrap();
            st.pending.clear();   // what was spent here before the reset belongs to the old window
            self.save_pending(&st);
        }
        Ok(())
    }

    /// An admin: everyone's count at a glance.
    pub fn people(&self, now_ms: i64) -> Result<Value, String> {
        let st = self.auth.status(now_ms / 1000);
        if st.state != "signed_in" {
            return Err("Sign in first.".into());
        }
        if st.user.as_ref().map(|u| u.role.as_str()) != Some("admin") {
            return Err("Only an admin can see everyone's allowance.".into());
        }
        match self.rpc("allowance_people", &json!({}), now_ms) {
            Ok(v) => Ok(v),
            Err((0, m)) => Err(m),
            Err((403, _)) => Err("Only an admin can see everyone's allowance.".into()),
            Err((429, _)) => Err("Too many requests. Try again in a moment.".into()),
            Err((500..=599, _)) => Err("The service had a problem. Try again in a minute.".into()),
            Err(_) => Err("The service gave an answer Recruit Desk does not understand.".into()),
        }
    }
}
