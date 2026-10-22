// Author: kaustubh.s.v
//! Sign-in through Neon Auth, and access to the company's data through the Neon Data API. There is no server of our own: the rules
//! (who may create a company, one company per person, the fields of a stored record) live in the database itself, see
//! AI-AGENTS/neon-backend/setup.sql.
//!
//! Nothing about candidates, companies or accounts goes to Neon, and neither does any API key a user saves here. The only business
//! data is the last record each assistant created (records.rs). This module keeps one session:
//! - the session cookie in the operating system's keychain (never in a plain file; when there is no keychain the session lives in
//!   memory only and the person signs in each time the app opens);
//! - non-secret facts (the connection code it belongs to, the user's email, role and company) in auth.json;
//! - the time of the last successful check with Neon. While Neon cannot be reached the app keeps working for a limited time after
//!   it, then asks the person to connect and sign in again. This allowance is kept on this computer, so it is a convenience limit and
//!   not tamper-proof: the data itself is protected by the database, whatever this app says.
//!
//! The "connection code" (built into the installer, never typed by a person) holds the two Neon addresses (sign-in and data) and the app's origin name. The network,
//! the keychain and the file are behind small traits so the whole flow is tested with fakes (see `tests`).

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Mutex;

/// How long the app keeps working without reaching Neon after its last successful check.
pub const GRACE_SECS: i64 = 7 * 86_400;
/// The clock may differ from the saved time by this much before the app calls it wrong.
const CLOCK_SLACK_SECS: i64 = 300;
/// The profile and company are looked at again after this long, so a person removed from a company loses access soon.
const RECHECK_SECS: i64 = 600;
/// Fetch a new data token when the current one has less than this long left.
const TOKEN_MARGIN_SECS: i64 = 60;
const SESSION_COOKIE: &str = "__Secure-neon-auth.session_token";

pub const MSG_BAD_LOGIN: &str = "That email and password do not match.";
pub const MSG_DISABLED: &str = "This account is switched off. Ask an admin.";
pub const MSG_TOO_MANY: &str = "Too many attempts. Wait a few minutes and try again.";
pub const MSG_BAD_EMAIL: &str = "Enter a valid email address.";
pub const MSG_SERVER: &str = "The sign-in service had a problem. Try again in a minute.";
pub const MSG_OFFLINE_LOGIN: &str = "Cannot reach the sign-in service. Check your internet connection and try again.";
pub const MSG_UNEXPECTED: &str = "The sign-in service gave an answer Recruit Desk does not understand.";
pub const MSG_ENDED: &str = "Your session ended. Sign in again.";
pub const MSG_TOO_LONG_OFFLINE: &str = "You have been offline too long. Connect to the internet and sign in again.";
pub const MSG_CLOCK: &str = "This computer's clock looks wrong, so Recruit Desk cannot trust your saved sign-in. Fix the date and time, then sign in again.";
pub const MSG_NO_SERVER: &str = "This copy of Recruit Desk is not set up for sign-in.";
pub const MSG_BAD_CONNECTION: &str = "This copy of Recruit Desk has an unreadable sign-in setting. Ask your admin for a new installer.";
pub const MSG_ORIGIN: &str = "Sign-in is not set up correctly for this copy of Recruit Desk. Ask your admin for a new installer.";
pub const MSG_BAD_LINK: &str = "That does not look like a reset link. Paste the whole link from the email, or just the code in it.";
pub const MSG_RESET_INVALID: &str = "This reset link is no longer valid. Ask for a new one.";
pub const MSG_RESET_SENT: &str = "If that address has an account, an email with a reset link is on its way. It can take a few minutes.";
pub const MSG_HAVE_ACCOUNT: &str = "That email already has an account. Sign in instead.";
pub const MSG_PASSWORD_RULES: &str = "Choose a longer password.";
pub const MSG_PASSWORD_CHANGED: &str = "Password changed. Sign in with your new password.";
pub const MSG_CODE_FORMAT: &str = "That does not look like an invitation code. It is made of letters, numbers and dashes, like 1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed.";
pub const MSG_CODE_INVALID: &str = "That invitation code did not work. Check it, make sure you use the email address it was sent to, or ask your admin for a new one.";
pub const MSG_NO_COMPANY: &str = "You are not part of a company yet. Enter the invitation code your admin gave you.";
pub const MSG_REMOVED: &str = "You are no longer part of a company. Ask your admin to invite you again.";

// ---------- what the window sees ----------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UserInfo {
    pub email: String,
    /// "admin" (a company owner or admin) or "user". Empty when the person has no company yet.
    pub role: String,
    /// The company's name, empty until the person has joined one.
    #[serde(default)]
    pub tenant: String,
    #[serde(default)]
    pub tenant_id: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AuthStatus {
    pub enabled: bool,
    /// The connection code in use (not a secret: two web addresses and a name).
    pub server: String,
    /// "disabled" (nothing set: the app works without signing in), "signed_out", "no_company" (signed in, no invitation accepted yet)
    /// or "signed_in".
    pub state: String,
    pub user: Option<UserInfo>,
    /// Signed in, but Neon could not be reached; the app works until `offline_until`.
    pub offline: bool,
    pub offline_until: Option<i64>,
    /// False when there is no keychain: the session lasts until the app closes.
    pub remembered: bool,
    pub message: Option<String>,
}

// ---------- the outside world, behind traits ----------

#[derive(Debug, Clone)]
pub struct HttpReply {
    pub status: u16,
    pub body: String,
    /// The session cookie as "name=value", when the reply sets one.
    pub set_cookie: Option<String>,
}

impl HttpReply {
    pub fn new(status: u16, body: &str) -> Self {
        HttpReply { status, body: body.to_string(), set_cookie: None }
    }
}

#[derive(Debug, Clone)]
pub struct NetError(pub String);

pub struct Req<'a> {
    pub method: &'a str,
    pub url: &'a str,
    pub origin: &'a str,
    pub cookie: Option<&'a str>,
    pub bearer: Option<&'a str>,
    pub prefer: Option<&'a str>,
    pub body: Option<&'a Value>,
}

pub trait Http: Send + Sync {
    fn send(&self, req: &Req) -> Result<HttpReply, NetError>;
}

/// Where the secrets live: the OS keychain.
pub trait Vault: Send + Sync {
    fn load(&self) -> Result<Option<String>, String>;
    fn save(&self, value: &str) -> Result<(), String>;
    fn clear(&self) -> Result<(), String>;
}

/// Non-secret facts about the last sign-in (auth.json).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cache {
    pub server: String,
    pub user: UserInfo,
}

pub trait Store: Send + Sync {
    fn load(&self) -> Option<Cache>;
    fn save(&self, cache: &Cache);
    fn clear(&self);
}

/// What the keychain holds (one short JSON value: Windows limits a credential to about 1,280 characters).
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Blob {
    cookie: String,
    /// The last time Neon answered a check; the offline allowance counts from here.
    last_ok: i64,
    /// Latest time the app saw; a clock set back past it is refused (the offline allowance would otherwise stretch).
    seen: i64,
}

// ---------- the connection code ----------

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Conn {
    /// Neon Auth's address, ending in /auth.
    pub auth: String,
    /// The Data API's address, ending in /rest/v1.
    pub api: String,
    /// The origin name Neon Auth has been told to trust for this app.
    pub origin: String,
}

const CODE_PREFIX: &str = "rd1.";

/// A web address the app may send a password to: https, or http only for this computer (development).
fn plain_address(raw: &str) -> Result<String, ()> {
    let u = raw.trim();
    if u.is_empty() || u.chars().any(|c| c.is_whitespace() || c.is_control()) || u.contains('?') || u.contains('#') {
        return Err(());
    }
    let (secure, rest) = if let Some(r) = u.strip_prefix("https://") {
        (true, r)
    } else if let Some(r) = u.strip_prefix("http://") {
        (false, r)
    } else {
        return Err(());
    };
    let authority = rest.split('/').next().unwrap_or("");
    if authority.is_empty() || authority.contains('@') {
        return Err(());
    }
    let host = if let Some(h) = authority.strip_prefix('[') { h.split(']').next().unwrap_or("") } else { authority.split(':').next().unwrap_or("") };
    let local = matches!(host, "localhost" | "127.0.0.1" | "::1");
    if !secure && !local {
        return Err(());
    }
    Ok(u.trim_end_matches('/').to_string())
}

/// Read a connection code. Both addresses must be plain https addresses; the origin must be just a scheme and host.
pub fn parse_connection(raw: &str) -> Result<Conn, String> {
    let bad = || MSG_BAD_CONNECTION.to_string();
    let body = raw.trim().strip_prefix(CODE_PREFIX).ok_or_else(bad)?;
    let bytes = URL_SAFE_NO_PAD.decode(body.trim_end_matches('=')).map_err(|_| bad())?;
    let v: Value = serde_json::from_slice(&bytes).map_err(|_| bad())?;
    let field = |k: &str| v.get(k).and_then(Value::as_str).ok_or_else(bad);
    let (auth, api, origin) = (plain_address(field("a")?).map_err(|_| bad())?, plain_address(field("r")?).map_err(|_| bad())?, plain_address(field("o")?).map_err(|_| bad())?);
    if origin.matches('/').count() != 2 {
        return Err(bad()); // an origin has no path
    }
    Ok(Conn { auth, api, origin })
}

/// Build a connection code (the operator's script does the same; used by the tests and by `connection_code` below).
pub fn make_connection_code(auth: &str, api: &str, origin: &str) -> String {
    format!("{CODE_PREFIX}{}", URL_SAFE_NO_PAD.encode(json!({"a": auth, "r": api, "o": origin}).to_string()))
}

/// What is saved in the settings: the normalised code, or "" for no sign-in.
pub fn validate_server_url(raw: &str) -> Result<String, String> {
    let t = raw.trim();
    if t.is_empty() {
        return Ok(String::new());
    }
    let c = parse_connection(t)?;
    Ok(make_connection_code(&c.auth, &c.api, &c.origin))
}

/// The reset code from a pasted link (or a bare code). Only the code is used: the address in a pasted link is ignored, so the app
/// contacts the service it was set up with and nothing else.
pub fn extract_token(input: &str) -> Result<String, &'static str> {
    let t = input.trim();
    let raw = match t.find("token=") {
        Some(i) => t[i + 6..].split(['&', '#', ' ', '\n']).next().unwrap_or(""),
        None => t,
    };
    let ok = (16..=200).contains(&raw.len()) && raw.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '%'));
    if ok { Ok(raw.to_string()) } else { Err(MSG_BAD_LINK) }
}

/// An invitation code is the invitation's id: a UUID.
pub fn valid_code(input: &str) -> Result<String, &'static str> {
    let t = input.trim().to_lowercase();
    let groups: Vec<usize> = t.split('-').map(str::len).collect();
    if groups == [8, 4, 4, 4, 12] && t.chars().all(|c| c.is_ascii_hexdigit() || c == '-') { Ok(t) } else { Err(MSG_CODE_FORMAT) }
}

fn code_of(body: &str) -> String {
    serde_json::from_str::<Value>(body).ok().and_then(|v| v.get("code").and_then(Value::as_str).map(str::to_string)).unwrap_or_default()
}

fn app_role(role: &str) -> &'static str {
    if role == "owner" || role == "admin" { "admin" } else { "user" }
}

fn neon_role(role: &str) -> Option<&'static str> {
    match role {
        "admin" => Some("admin"),
        "user" => Some("member"),
        _ => None,
    }
}

// ---------- the service ----------

#[derive(Debug, Clone)]
struct Session {
    user: UserInfo,
    /// In memory only (also in the keychain blob); used for the admin calls, never handed to the window.
    cookie: String,
    /// The token for the Data API, short-lived; empty while the person has no company.
    jwt: String,
    jwt_exp: i64,
    last_ok: i64,
    offline: bool,
}

#[derive(Default)]
struct Inner {
    code: String,
    session: Option<Session>,
    /// Kept in memory only when the keychain is unavailable: (cookie, last_ok).
    memory: Option<(String, i64)>,
    remembered: bool,
    message: Option<String>,
}

pub struct AuthService {
    http: Box<dyn Http>,
    vault: Box<dyn Vault>,
    store: Box<dyn Store>,
    inner: Mutex<Inner>,
}

/// What looking at the account after a cookie was obtained can come to.
enum Look {
    Net,
    Ended,
    Server,
    Unexpected,
    Throttled,
}

struct Profile {
    user: UserInfo,
    cookie: String,
    jwt: String,
    jwt_exp: i64,
}

impl AuthService {
    pub fn new(http: Box<dyn Http>, vault: Box<dyn Vault>, store: Box<dyn Store>, code: &str) -> Self {
        let code = validate_server_url(code).unwrap_or_default();
        AuthService { http, vault, store, inner: Mutex::new(Inner { code, remembered: true, ..Inner::default() }) }
    }

    /// The saved connection code ("" when none).
    pub fn server(&self) -> String {
        self.inner.lock().unwrap().code.clone()
    }

    fn conn(&self) -> Result<Conn, String> {
        let code = self.server();
        if code.is_empty() { Err(MSG_NO_SERVER.into()) } else { parse_connection(&code) }
    }

    /// Point the app at another company connection (or none). Ending the session: a cookie from one Neon branch means nothing to another.
    pub fn set_server(&self, raw: &str) -> Result<(), String> {
        let new = validate_server_url(raw)?;
        let mut g = self.inner.lock().unwrap();
        if g.code != new {
            g.code = new;
            g.session = None;
            g.memory = None;
            g.message = None;
            drop(g);
            let _ = self.vault.clear();
            self.store.clear();
        }
        Ok(())
    }

    /// The current state, without touching the network.
    pub fn status(&self, now: i64) -> AuthStatus {
        let g = self.inner.lock().unwrap();
        Self::status_of(&g, now)
    }

    fn status_of(g: &Inner, now: i64) -> AuthStatus {
        let enabled = !g.code.is_empty();
        let live = g.session.as_ref().filter(|s| !s.offline || now < s.last_ok + GRACE_SECS);
        let state = if !enabled {
            "disabled"
        } else {
            match live {
                None => "signed_out",
                Some(s) if s.user.tenant_id.is_empty() => "no_company",
                Some(_) => "signed_in",
            }
        };
        AuthStatus {
            enabled,
            server: g.code.clone(),
            state: state.to_string(),
            user: live.map(|s| s.user.clone()),
            offline: live.map(|s| s.offline).unwrap_or(false),
            offline_until: live.filter(|s| s.offline).map(|s| s.last_ok + GRACE_SECS),
            remembered: g.remembered,
            message: g.message.clone().filter(|_| state != "signed_in"),
        }
    }

    /// May the app do its work? Always when nothing is set; otherwise only while signed in to a company and inside the allowance.
    pub fn allows(&self, now: i64) -> bool {
        let g = self.inner.lock().unwrap();
        g.code.is_empty() || Self::status_of(&g, now).state == "signed_in"
    }

    pub fn require(&self, now: i64) -> Result<(), String> {
        if self.allows(now) { Ok(()) } else { Err("Sign in first.".into()) }
    }

    /// The company id of the signed-in person, for the Data API calls.
    pub fn tenant_id(&self, now: i64) -> Option<String> {
        let g = self.inner.lock().unwrap();
        let st = Self::status_of(&g, now);
        st.user.map(|u| u.tenant_id).filter(|t| !t.is_empty())
    }

    fn end_session(&self, message: Option<&str>, clear_vault: bool) {
        let mut g = self.inner.lock().unwrap();
        g.session = None;
        g.memory = None;
        g.message = message.map(str::to_string);
        drop(g);
        if clear_vault {
            let _ = self.vault.clear();
            self.store.clear();
        }
    }

    // ----- talking to Neon Auth -----

    fn call(&self, conn: &Conn, method: &str, path: &str, cookie: Option<&str>, body: Option<&Value>) -> Result<HttpReply, NetError> {
        self.http.send(&Req { method, url: &format!("{}{}", conn.auth, path), origin: &conn.origin, cookie, bearer: None, prefer: None, body })
    }

    /// Who is this cookie, which company are they in, what role, and a token for the Data API.
    fn look(&self, conn: &Conn, cookie: &str, now: i64) -> Result<Profile, Look> {
        let get = |path: &str, ck: &str| self.call(conn, "GET", path, Some(ck), None).map_err(|_| Look::Net);
        let r = get("/get-session", cookie)?;
        let mut cookie = cookie.to_string();
        match r.status {
            200 => {}
            401 | 403 => return Err(Look::Ended),
            429 => return Err(Look::Throttled),
            500..=599 => return Err(Look::Server),
            _ => return Err(Look::Unexpected),
        }
        if let Some(c) = r.set_cookie {
            cookie = c;
        }
        let v: Value = serde_json::from_str(&r.body).map_err(|_| Look::Unexpected)?;
        if v.is_null() {
            return Err(Look::Ended);
        }
        let email = v.get("user").and_then(|u| u.get("email")).and_then(Value::as_str).ok_or(Look::Unexpected)?.to_lowercase();
        let r = get("/organization/list", &cookie)?;
        let (mut tenant, mut tenant_id, mut role) = (String::new(), String::new(), String::new());
        match r.status {
            200 => {
                let orgs: Value = serde_json::from_str(&r.body).map_err(|_| Look::Unexpected)?;
                if let Some(o) = orgs.as_array().and_then(|a| a.first()) {
                    tenant = o.get("name").and_then(Value::as_str).unwrap_or("").to_string();
                    tenant_id = o.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                }
            }
            401 => return Err(Look::Ended),
            429 => return Err(Look::Throttled),
            500..=599 => return Err(Look::Server),
            _ => return Err(Look::Unexpected),
        }
        let (mut jwt, mut jwt_exp) = (String::new(), 0);
        if !tenant_id.is_empty() {
            let r = get(&format!("/organization/list-members?organizationId={tenant_id}"), &cookie)?;
            match r.status {
                200 => {
                    let m: Value = serde_json::from_str(&r.body).map_err(|_| Look::Unexpected)?;
                    role = m.get("members").and_then(Value::as_array).and_then(|a| {
                        a.iter().find(|x| x.get("user").and_then(|u| u.get("email")).and_then(Value::as_str).map(str::to_lowercase).as_deref() == Some(email.as_str()))
                    }).and_then(|x| x.get("role").and_then(Value::as_str)).map(|r| app_role(r).to_string()).unwrap_or_default();
                }
                401 => return Err(Look::Ended),
                429 => return Err(Look::Throttled),
                500..=599 => return Err(Look::Server),
                _ => {} // not (or no longer) a member: treated as having no company below
            }
            if role.is_empty() {
                tenant.clear();
                tenant_id.clear();
            } else {
                let r = get("/token", &cookie)?;
                if r.status != 200 {
                    return Err(Look::Unexpected);
                }
                jwt = serde_json::from_str::<Value>(&r.body).ok().and_then(|v| v.get("token").and_then(Value::as_str).map(str::to_string)).ok_or(Look::Unexpected)?;
                jwt_exp = jwt_expiry(&jwt).unwrap_or(now + 600);
            }
        }
        Ok(Profile { user: UserInfo { email, role, tenant, tenant_id }, cookie, jwt, jwt_exp })
    }

    fn look_message(l: Look) -> String {
        match l {
            Look::Net => MSG_OFFLINE_LOGIN,
            Look::Ended => MSG_ENDED,
            Look::Server => MSG_SERVER,
            Look::Throttled => MSG_TOO_MANY,
            Look::Unexpected => MSG_UNEXPECTED,
        }
        .to_string()
    }

    /// Open the session for a cookie Neon has just accepted, and save it.
    fn open(&self, conn: &Conn, cookie: &str, now: i64) -> Result<AuthStatus, String> {
        let p = self.look(conn, cookie, now).map_err(Self::look_message)?;
        self.commit(p, now, None);
        Ok(self.status(now))
    }

    fn commit(&self, p: Profile, now: i64, message: Option<&str>) {
        let blob = Blob { cookie: p.cookie.clone(), last_ok: now, seen: now };
        let saved = serde_json::to_string(&blob).map_err(|e| e.to_string()).and_then(|s| self.vault.save(&s));
        self.store.save(&Cache { server: self.server(), user: p.user.clone() });
        let mut g = self.inner.lock().unwrap();
        g.remembered = saved.is_ok();
        g.memory = if saved.is_ok() { None } else { Some((p.cookie.clone(), now)) };
        g.message = message.map(str::to_string).or_else(|| p.user.tenant_id.is_empty().then(|| MSG_NO_COMPANY.to_string()));
        g.session = Some(Session { user: p.user, cookie: p.cookie, jwt: p.jwt, jwt_exp: p.jwt_exp, last_ok: now, offline: false });
    }

    fn sign_in_reply(&self, r: HttpReply, wrong: &str) -> Result<String, String> {
        match r.status {
            200 | 201 => r.set_cookie.ok_or_else(|| MSG_UNEXPECTED.to_string()),
            401 => Err(wrong.into()),
            403 if code_of(&r.body) == "INVALID_ORIGIN" => Err(MSG_ORIGIN.into()),
            403 => Err(MSG_DISABLED.into()),
            422 if code_of(&r.body).starts_with("USER_ALREADY_EXISTS") => Err(MSG_HAVE_ACCOUNT.into()),
            400 | 422 => Err(match code_of(&r.body).as_str() {
                "PASSWORD_TOO_SHORT" => MSG_PASSWORD_RULES.to_string(),
                "PASSWORD_TOO_LONG" => "That password is too long.".to_string(),
                _ => MSG_BAD_EMAIL.to_string(),
            }),
            429 => Err(MSG_TOO_MANY.into()),
            500..=599 => Err(MSG_SERVER.into()),
            _ => Err(MSG_UNEXPECTED.into()),
        }
    }

    pub fn login(&self, email: &str, password: &str, now: i64) -> Result<AuthStatus, String> {
        let conn = self.conn()?;
        let r = self.call(&conn, "POST", "/sign-in/email", None, Some(&json!({"email": email.trim(), "password": password}))).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        let cookie = self.sign_in_reply(r, MSG_BAD_LOGIN)?;
        self.open(&conn, &cookie, now)
    }

    /// Make an account with an invitation code, and sign in. If the account is made but the code does not work, the person stays signed in
    /// without a company and can try another code.
    pub fn create_account(&self, email: &str, password: &str, code: &str, now: i64) -> Result<AuthStatus, String> {
        let conn = self.conn()?;
        let code = valid_code(code)?;
        let email = email.trim();
        let name = email.split('@').next().filter(|n| !n.is_empty()).unwrap_or("user");
        let r = self.call(&conn, "POST", "/sign-up/email", None, Some(&json!({"email": email, "password": password, "name": name}))).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        let cookie = self.sign_in_reply(r, MSG_BAD_LOGIN)?;
        self.open(&conn, &cookie, now)?;
        self.join_company(&code, now).or_else(|m| {
            self.inner.lock().unwrap().message = Some(m);
            Ok(self.status(now))
        })
    }

    /// Accept an invitation code as the signed-in person.
    pub fn join_company(&self, code: &str, now: i64) -> Result<AuthStatus, String> {
        let conn = self.conn()?;
        let code = valid_code(code)?;
        let cookie = {
            let g = self.inner.lock().unwrap();
            g.session.as_ref().filter(|s| !s.offline).map(|s| s.cookie.clone()).ok_or("Sign in first.")?
        };
        let r = self.call(&conn, "POST", "/organization/accept-invitation", Some(&cookie), Some(&json!({"invitationId": code}))).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        match r.status {
            200 | 201 => {
                let p = self.look(&conn, &cookie, now).map_err(Self::look_message)?;
                self.commit(p, now, None);
                Ok(self.status(now))
            }
            401 => {
                self.end_session(Some(MSG_ENDED), true);
                Err(MSG_ENDED.into())
            }
            429 => Err(MSG_TOO_MANY.into()),
            500..=599 => Err(MSG_SERVER.into()),
            _ => Err(MSG_CODE_INVALID.into()),
        }
    }

    /// Ask for a password reset email. The answer never says whether the address has an account.
    pub fn forgot_password(&self, email: &str) -> Result<String, String> {
        let conn = self.conn()?;
        let e = email.trim();
        if e.is_empty() || !e.contains('@') || e.chars().any(char::is_whitespace) {
            return Err(MSG_BAD_EMAIL.into());
        }
        let r = self.call(&conn, "POST", "/request-password-reset", None, Some(&json!({"email": e, "redirectTo": format!("{}/reset", conn.origin)}))).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        match r.status {
            200 | 204 => Ok(MSG_RESET_SENT.to_string()),
            403 if code_of(&r.body) == "INVALID_ORIGIN" => Err(MSG_ORIGIN.into()),
            429 => Err(MSG_TOO_MANY.into()),
            500..=599 => Err(MSG_SERVER.into()),
            _ => Err(MSG_UNEXPECTED.into()),
        }
    }

    /// Choose a new password from the emailed link. The person signs in afterwards.
    pub fn reset_password(&self, link: &str, password: &str) -> Result<String, String> {
        let conn = self.conn()?;
        let token = extract_token(link)?;
        let r = self.call(&conn, "POST", "/reset-password", None, Some(&json!({"newPassword": password, "token": token}))).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        match r.status {
            200 | 204 => Ok(MSG_PASSWORD_CHANGED.to_string()),
            400 | 422 => Err(match code_of(&r.body).as_str() {
                "PASSWORD_TOO_SHORT" => MSG_PASSWORD_RULES.to_string(),
                _ => MSG_RESET_INVALID.to_string(),
            }),
            429 => Err(MSG_TOO_MANY.into()),
            500..=599 => Err(MSG_SERVER.into()),
            _ => Err(MSG_UNEXPECTED.into()),
        }
    }

    /// Open the saved session at start-up, and keep it fresh. Reaches Neon only when the last check is old or the data token is near its end.
    pub fn check(&self, now: i64) -> AuthStatus {
        self.check_inner(now, false)
    }

    fn check_inner(&self, now: i64, force: bool) -> AuthStatus {
        let Ok(conn) = self.conn() else { return self.status(now) };
        {
            let g = self.inner.lock().unwrap();
            if let Some(s) = &g.session {
                let token_ok = s.jwt.is_empty() || s.jwt_exp - now > TOKEN_MARGIN_SECS;
                if !force && !s.offline && now - s.last_ok < RECHECK_SECS && token_ok {
                    return Self::status_of(&g, now);
                }
            }
        }
        let (cookie, last_ok, seen) = {
            let mem = self.inner.lock().unwrap().memory.clone();
            match (mem, self.vault.load()) {
                (Some((c, ok)), _) => (c, ok, now),
                (None, Ok(Some(raw))) => match serde_json::from_str::<Blob>(&raw) {
                    Ok(b) => (b.cookie, b.last_ok, b.seen),
                    Err(_) => {
                        self.end_session(None, true);
                        return self.status(now);
                    }
                },
                _ => {
                    // Nothing saved: signed out.
                    self.inner.lock().unwrap().session = None;
                    return self.status(now);
                }
            }
        };
        if seen - now > CLOCK_SLACK_SECS {
            self.end_session(Some(MSG_CLOCK), false);
            return self.status(now);
        }
        let cache = self.store.load().filter(|c| c.server == self.server());
        let had_company = self.inner.lock().unwrap().session.as_ref().map(|s| !s.user.tenant_id.is_empty()).unwrap_or_else(|| cache.as_ref().map(|c| !c.user.tenant_id.is_empty()).unwrap_or(false));
        match self.look(&conn, &cookie, now) {
            Ok(p) => {
                let msg = (had_company && p.user.tenant_id.is_empty()).then_some(MSG_REMOVED);
                self.commit(p, now, msg);
            }
            Err(Look::Ended) => self.end_session(Some(MSG_ENDED), true),
            // Neon cannot be reached or is having a bad moment: the allowance since the last good check decides.
            Err(_) => return self.go_offline(cache, &cookie, last_ok, now),
        }
        self.status(now)
    }

    fn go_offline(&self, cache: Option<Cache>, cookie: &str, last_ok: i64, now: i64) -> AuthStatus {
        match cache.filter(|_| now < last_ok + GRACE_SECS) {
            Some(c) => {
                let mut g = self.inner.lock().unwrap();
                g.message = None;
                g.session = Some(Session { user: c.user, cookie: cookie.to_string(), jwt: String::new(), jwt_exp: 0, last_ok, offline: true });
            }
            None => {
                // Keep the saved cookie: it may work again once the person is back online.
                self.end_session(Some(MSG_TOO_LONG_OFFLINE), false);
            }
        }
        self.status(now)
    }

    /// Sign out: tell Neon (best effort), then forget everything saved here.
    pub fn logout(&self, now: i64) -> AuthStatus {
        let cookie = {
            let g = self.inner.lock().unwrap();
            g.session.as_ref().map(|s| s.cookie.clone()).or_else(|| g.memory.as_ref().map(|m| m.0.clone()))
        }
        .or_else(|| self.vault.load().ok().flatten().and_then(|raw| serde_json::from_str::<Blob>(&raw).ok()).map(|b| b.cookie));
        if let (Ok(conn), Some(c)) = (self.conn(), cookie) {
            let _ = self.call(&conn, "POST", "/sign-out", Some(&c), Some(&json!({})));
        }
        self.end_session(None, true);
        self.status(now)
    }
}

/// The `exp` of a data token (read only to know when to fetch a new one; Neon's TLS answer is what is trusted).
fn jwt_expiry(token: &str) -> Option<i64> {
    let payload = token.split('.').nth(1)?;
    let v: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(payload).ok()?).ok()?;
    v.get("exp").and_then(Value::as_i64)
}

// ---------- admin calls ----------

/// The admin actions, each a fixed Neon Auth endpoint: the window never names a path.
#[derive(Debug, Clone, PartialEq)]
pub enum AdminOp {
    Members,
    Invites,
    Invite { email: String, role: String },
    RevokeInvite { id: String },
    SetRole { id: String, role: String },
    Remove { id: String },
}

pub const MSG_ADMINS_ONLY: &str = "Only an admin can do that.";
pub const MSG_ADMIN_ONLINE: &str = "Admin actions need an internet connection.";
pub const MSG_GONE: &str = "That person or invitation no longer exists. Refresh the list.";
pub const MSG_REFUSED: &str = "That change was refused.";
pub const MSG_BAD_INPUT: &str = "Check the details and try again.";

fn uuid_like(s: &str) -> bool {
    valid_code(s).is_ok()
}

impl AdminOp {
    /// (method, path with the company id filled in, body), or what is wrong with the input. Nothing is sent when it is wrong.
    fn request(&self, tenant: &str) -> Result<(&'static str, String, Option<Value>), String> {
        let id_ok = |id: &str| if uuid_like(id) { Ok(id.trim().to_lowercase()) } else { Err(MSG_BAD_INPUT.to_string()) };
        Ok(match self {
            AdminOp::Members => ("GET", format!("/organization/list-members?organizationId={tenant}"), None),
            AdminOp::Invites => ("GET", format!("/organization/list-invitations?organizationId={tenant}"), None),
            AdminOp::Invite { email, role } => {
                let e = email.trim();
                let role = neon_role(role).ok_or_else(|| MSG_BAD_INPUT.to_string())?;
                if e.is_empty() || e.len() > 320 || e.chars().any(char::is_whitespace) || !e.contains('@') {
                    return Err(MSG_BAD_INPUT.into());
                }
                ("POST", "/organization/invite-member".into(), Some(json!({"email": e, "role": role, "organizationId": tenant})))
            }
            AdminOp::RevokeInvite { id } => ("POST", "/organization/cancel-invitation".into(), Some(json!({"invitationId": id_ok(id)?}))),
            AdminOp::SetRole { id, role } => {
                let role = neon_role(role).ok_or_else(|| MSG_BAD_INPUT.to_string())?;
                ("POST", "/organization/update-member-role".into(), Some(json!({"memberId": id_ok(id)?, "role": role, "organizationId": tenant})))
            }
            AdminOp::Remove { id } => ("POST", "/organization/remove-member".into(), Some(json!({"memberIdOrEmail": id_ok(id)?, "organizationId": tenant}))),
        })
    }
}

/// Neon's own sentence is never shown; a few of its codes become our wording, anything else gets a generic line.
fn known_code(body: &str) -> Option<&'static str> {
    Some(match code_of(body).as_str() {
        "YOU_CANNOT_LEAVE_THE_ORGANIZATION_AS_THE_ONLY_OWNER" => "There must be at least one admin.",
        "USER_IS_ALREADY_A_MEMBER_OF_THIS_ORGANIZATION" => "That person is already in your company.",
        "USER_IS_ALREADY_INVITED_TO_THIS_ORGANIZATION" => "That person was already invited. Withdraw the old invitation first.",
        "INVITATION_NOT_FOUND" => MSG_GONE,
        "MEMBER_NOT_FOUND" => MSG_GONE,
        _ => return None,
    })
}

/// The people and invitations the window shows: our own plain shapes, not Neon's.
fn shape_members(v: &Value, me: &str) -> Value {
    let list: Vec<Value> = v.get("members").and_then(Value::as_array).map(|a| a.as_slice()).unwrap_or(&[]).iter().filter_map(|m| {
        let email = m.get("user")?.get("email")?.as_str()?.to_lowercase();
        let role = m.get("role").and_then(Value::as_str).unwrap_or("member");
        Some(json!({"id": m.get("id")?, "email": email, "role": app_role(role), "owner": role == "owner", "you": email == me,
                    "joined_at": m.get("createdAt").cloned().unwrap_or(Value::Null)}))
    }).collect();
    Value::Array(list)
}

fn shape_invites(v: &Value, now: i64) -> Value {
    let list: Vec<Value> = v.as_array().map(|a| a.as_slice()).unwrap_or(&[]).iter().filter(|i| i.get("status").and_then(Value::as_str) == Some("pending")).filter(|i| {
        // an expired invitation is still "pending" in the table; it no longer works, so it is not listed
        i.get("expiresAt").and_then(Value::as_str).and_then(parse_iso).map(|t| t > now).unwrap_or(true)
    }).filter_map(|i| {
        Some(json!({"id": i.get("id")?, "email": i.get("email")?.as_str()?.to_lowercase(), "role": app_role(i.get("role").and_then(Value::as_str).unwrap_or("member")),
                    "expires_at": i.get("expiresAt").cloned().unwrap_or(Value::Null), "created_at": i.get("createdAt").cloned().unwrap_or(Value::Null)}))
    }).collect();
    Value::Array(list)
}

/// Seconds since 1970 of an ISO time like 2026-10-21T09:01:14.778Z (UTC); None when it does not parse.
pub fn parse_iso(s: &str) -> Option<i64> {
    let (d, t) = s.split_once('T')?;
    let mut dp = d.split('-').map(|x| x.parse::<i64>().ok());
    let (y, m, day) = (dp.next()??, dp.next()??, dp.next()??);
    let t = t.trim_end_matches('Z').split(['+', '.']).next()?;
    let mut tp = t.split(':').map(|x| x.parse::<i64>().ok());
    let (h, mi, sec) = (tp.next()??, tp.next()??, tp.next().flatten().unwrap_or(0));
    if !(1..=12).contains(&m) || !(1..=31).contains(&day) || h > 23 || mi > 59 || sec > 60 {
        return None;
    }
    // days from civil (Howard Hinnant's algorithm)
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + sec)
}

impl AuthService {
    /// One request to Neon Auth as the signed-in admin (or person): the cookie stays here and is checked first; a 401 ends the session.
    fn authed(&self, method: &str, path_of: impl Fn(&str) -> Result<(String, Option<Value>), String>, now: i64, admin_only: bool) -> Result<HttpReply, String> {
        let conn = self.conn()?;
        {
            let g = self.inner.lock().unwrap();
            let st = Self::status_of(&g, now);
            let s = g.session.as_ref().filter(|_| st.state == "signed_in").ok_or("Sign in first.")?;
            if admin_only && s.user.role != "admin" {
                return Err(MSG_ADMINS_ONLY.into());
            }
            if s.offline {
                return Err(MSG_ADMIN_ONLINE.into());
            }
        }
        let st = self.check_inner(now, false);
        let (cookie, tenant) = {
            let g = self.inner.lock().unwrap();
            match g.session.as_ref().filter(|s| !s.offline && !s.user.tenant_id.is_empty()) {
                Some(s) => (s.cookie.clone(), s.user.tenant_id.clone()),
                None => return Err(st.message.unwrap_or_else(|| if st.offline { MSG_ADMIN_ONLINE.into() } else { MSG_ENDED.into() })),
            }
        };
        if admin_only && self.status(now).user.map(|u| u.role != "admin").unwrap_or(true) {
            return Err(MSG_ADMINS_ONLY.into());
        }
        let (path, body) = path_of(&tenant)?;
        let r = self.call(&conn, method, &path, Some(&cookie), body.as_ref()).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
        if r.status == 401 {
            self.end_session(Some(MSG_ENDED), true);
            return Err(MSG_ENDED.into());
        }
        Ok(r)
    }

    /// Run one admin action as the signed-in admin. Neon Auth checks the role again on its side.
    pub fn admin(&self, op: &AdminOp, now: i64) -> Result<Value, String> {
        let me = self.status(now).user.map(|u| u.email).unwrap_or_default();
        let (method, _, _) = op.request("00000000-0000-0000-0000-000000000000")?; // input errors before anything is sent
        let reply = self.authed(method, |t| op.request(t).map(|(_, p, b)| (p, b)), now, true)?;
        match reply.status {
            200 | 201 => {
                let v: Value = serde_json::from_str(&reply.body).unwrap_or(Value::Null);
                Ok(match op {
                    AdminOp::Members => shape_members(&v, &me),
                    AdminOp::Invites => shape_invites(&v, now),
                    AdminOp::Invite { .. } => {
                        let id = v.get("id").and_then(Value::as_str).ok_or(MSG_UNEXPECTED)?;
                        json!({"email": v.get("email"), "role": app_role(v.get("role").and_then(Value::as_str).unwrap_or("member")), "expires_at": v.get("expiresAt"), "code": id})
                    }
                    _ => Value::Null,
                })
            }
            204 => Ok(Value::Null),
            403 => Err(known_code(&reply.body).unwrap_or(MSG_ADMINS_ONLY).into()),
            404 => Err(MSG_GONE.into()),
            400 | 409 => Err(known_code(&reply.body).unwrap_or(MSG_REFUSED).into()),
            422 => Err(MSG_BAD_INPUT.into()),
            429 => Err(MSG_TOO_MANY.into()),
            // the database refuses an invitation for someone who already belongs to a company with an error Neon Auth reports as a failure
            500 if matches!(op, AdminOp::Invite { .. }) => Err("That address cannot be invited. It may already belong to a company.".into()),
            500..=599 => Err(MSG_SERVER.into()),
            _ => Err(MSG_UNEXPECTED.into()),
        }
    }

    /// A request to the Data API by any signed-in person with a company (the last-record calls): the raw reply, for the caller to read.
    /// A 401 (the data token ran out) is retried once after fetching a new one, then ends the session.
    pub fn data_request(&self, method: &str, path: &str, body: Option<&Value>, prefer: Option<&str>, now: i64) -> Result<HttpReply, String> {
        let conn = self.conn()?;
        {
            let g = self.inner.lock().unwrap();
            let st = Self::status_of(&g, now);
            let s = g.session.as_ref().filter(|_| st.state == "signed_in").ok_or("Sign in first.")?;
            if s.offline {
                return Err(MSG_ADMIN_ONLINE.into());
            }
        }
        for attempt in 0..2 {
            let st = self.check_inner(now, attempt == 1);
            let jwt = {
                let g = self.inner.lock().unwrap();
                g.session.as_ref().filter(|s| !s.offline && !s.jwt.is_empty()).map(|s| s.jwt.clone())
            };
            let Some(jwt) = jwt else {
                return Err(st.message.unwrap_or_else(|| if st.offline { MSG_ADMIN_ONLINE.into() } else { MSG_ENDED.into() }));
            };
            let r = self.http.send(&Req { method, url: &format!("{}/{}", conn.api, path.trim_start_matches('/')), origin: &conn.origin, cookie: None, bearer: Some(&jwt), prefer, body }).map_err(|_| MSG_OFFLINE_LOGIN.to_string())?;
            if r.status == 401 {
                if attempt == 0 {
                    continue;
                }
                self.end_session(Some(MSG_ENDED), true);
                return Err(MSG_ENDED.into());
            }
            return Ok(r);
        }
        Err(MSG_ENDED.into())
    }
}

// ---------- the real network, keychain and file ----------

pub struct UreqHttp;

impl UreqHttp {
    fn agent() -> ureq::Agent {
        ureq::AgentBuilder::new()
            .timeout_connect(std::time::Duration::from_secs(8))
            .timeout(std::time::Duration::from_secs(15))
            .redirects(0) // a service that redirects is not the one it was set to
            .build()
    }
}

fn session_cookie_of(resp: &ureq::Response) -> Option<String> {
    resp.all("set-cookie").into_iter().find(|c| c.starts_with(&format!("{SESSION_COOKIE}="))).map(|c| c.split(';').next().unwrap_or("").to_string())
}

impl Http for UreqHttp {
    fn send(&self, r: &Req) -> Result<HttpReply, NetError> {
        let mut req = Self::agent().request(r.method, r.url).set("Origin", r.origin);
        if let Some(c) = r.cookie {
            req = req.set("Cookie", c);
        }
        if let Some(t) = r.bearer {
            req = req.set("Authorization", &format!("Bearer {t}"));
        }
        if let Some(p) = r.prefer {
            req = req.set("Prefer", p);
        }
        let result = match r.body {
            Some(b) => req.set("Content-Type", "application/json").send_string(&b.to_string()),
            None => req.call(),
        };
        match result {
            Ok(resp) => {
                let (status, set_cookie) = (resp.status(), session_cookie_of(&resp));
                Ok(HttpReply { status, body: resp.into_string().unwrap_or_default(), set_cookie })
            }
            Err(ureq::Error::Status(code, resp)) => {
                let set_cookie = session_cookie_of(&resp);
                Ok(HttpReply { status: code, body: resp.into_string().unwrap_or_default(), set_cookie })
            }
            Err(e) => Err(NetError(e.to_string())),
        }
    }
}

pub struct KeychainVault;

/// How long the system keychain gets to answer before the app goes on without it. A keychain service that never answers (a locked or
/// missing secret service, a dbus call that waits for two minutes) must not freeze the window: the sign-in then lives in memory for this
/// session only, and the keychain is not asked again for a minute.
const KEYCHAIN_WAIT_SECS: u64 = 8;
const KEYCHAIN_PAUSE_SECS: i64 = 60;
static KEYCHAIN_PAUSED_UNTIL: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(0);

#[cfg(any(windows, target_os = "linux", target_os = "macos"))]
fn guarded<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    guarded_for(KEYCHAIN_WAIT_SECS, f)
}

#[cfg(any(windows, target_os = "linux", target_os = "macos"))]
fn guarded_for<T: Send + 'static>(wait_secs: u64, f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    use std::sync::atomic::Ordering;
    let now = unix_now();
    if now < KEYCHAIN_PAUSED_UNTIL.load(Ordering::Relaxed) {
        return Err("The system keychain did not answer a moment ago.".into());
    }
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || { let _ = tx.send(f()); });
    match rx.recv_timeout(std::time::Duration::from_secs(wait_secs)) {
        Ok(r) => r,
        Err(_) => {
            KEYCHAIN_PAUSED_UNTIL.store(unix_now() + KEYCHAIN_PAUSE_SECS, Ordering::Relaxed);
            Err("The system keychain did not answer, so the sign-in is kept for this session only.".into())
        }
    }
}

#[cfg(any(windows, target_os = "linux", target_os = "macos"))]
impl KeychainVault {
    fn entry() -> Result<keyring::Entry, String> {
        keyring::Entry::new("in.humancloud.recruitdesk", "session").map_err(|e| e.to_string())
    }
}

#[cfg(any(windows, target_os = "linux", target_os = "macos"))]
impl Vault for KeychainVault {
    fn load(&self) -> Result<Option<String>, String> {
        guarded(|| match Self::entry()?.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.to_string()),
        })
    }
    fn save(&self, value: &str) -> Result<(), String> {
        let v = value.to_string();
        guarded(move || Self::entry()?.set_password(&v).map_err(|e| e.to_string()))
    }
    fn clear(&self) -> Result<(), String> {
        guarded(|| match Self::entry()?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.to_string()),
        })
    }
}

#[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
impl Vault for KeychainVault {
    fn load(&self) -> Result<Option<String>, String> { Ok(None) }
    fn save(&self, _: &str) -> Result<(), String> { Err("no keychain on this system".into()) }
    fn clear(&self) -> Result<(), String> { Ok(()) }
}

pub struct FileStore(pub PathBuf);

impl Store for FileStore {
    fn load(&self) -> Option<Cache> {
        std::fs::read_to_string(&self.0).ok().and_then(|s| serde_json::from_str(&s).ok())
    }
    fn save(&self, cache: &Cache) {
        if let Ok(s) = serde_json::to_string_pretty(cache) {
            let _ = std::fs::write(&self.0, s);
        }
    }
    fn clear(&self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

pub fn unix_now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

#[cfg(test)]
mod tests;

#[cfg(all(test, any(windows, target_os = "linux", target_os = "macos")))]
mod keychain_tests {
    use super::*;
    use std::sync::atomic::Ordering;

    #[test]
    fn a_keychain_that_never_answers_cannot_freeze_the_app() {
        KEYCHAIN_PAUSED_UNTIL.store(0, Ordering::Relaxed);
        assert_eq!(guarded_for(2, || Ok::<_, String>(5)).unwrap(), 5, "an answer in time is used");
        let t = std::time::Instant::now();
        let r = guarded_for(1, || { std::thread::sleep(std::time::Duration::from_secs(5)); Ok::<_, String>(1) });
        assert_eq!(r.unwrap_err(), "The system keychain did not answer, so the sign-in is kept for this session only.");
        assert!(t.elapsed().as_secs() < 3, "the wait was cut short");
        let t = std::time::Instant::now();
        assert_eq!(guarded_for(1, || Ok::<_, String>(2)).unwrap_err(), "The system keychain did not answer a moment ago.");
        assert!(t.elapsed().as_millis() < 200, "and it is not asked again at once, so a dead keychain costs one wait, not one per call");
        KEYCHAIN_PAUSED_UNTIL.store(0, Ordering::Relaxed);
    }
}
