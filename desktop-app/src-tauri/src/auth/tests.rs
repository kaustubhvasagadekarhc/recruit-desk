// Author: kaustubh.s.v
use super::*;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

const NOW: i64 = 1_800_000_000;
const AUTH: &str = "https://auth.example.com/neondb/auth";
const API: &str = "https://api.example.com/neondb/rest/v1";
const ORIGIN: &str = "https://recruitdesk.example";
const COOKIE: &str = "__Secure-neon-auth.session_token=abc123.sig%3D";
const ORG: &str = "11111111-2222-3333-4444-555555555555";
const OTHER_ID: &str = "99999999-8888-7777-6666-555555555555";
const CODE_OK: &str = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed";

fn code() -> String {
    make_connection_code(AUTH, API, ORIGIN)
}

fn jwt(exp: i64) -> String {
    format!("e30.{}.sig", URL_SAFE_NO_PAD.encode(json!({"exp": exp, "email": "ann@example.com"}).to_string()))
}

// ---------- fakes ----------

#[derive(Debug, Clone)]
struct Sent {
    method: String,
    /// The address with the auth address cut off; data addresses are prefixed "api:".
    path: String,
    cookie: Option<String>,
    bearer: Option<String>,
    origin: String,
    prefer: Option<String>,
    body: Option<Value>,
}

#[derive(Default)]
struct FakeHttp {
    replies: Mutex<HashMap<String, VecDeque<Result<HttpReply, NetError>>>>,
    sent: Mutex<Vec<Sent>>,
}

fn key(method: &str, path: &str) -> String {
    format!("{method} {path}")
}

impl FakeHttp {
    fn push(&self, method: &str, path: &str, status: u16, body: &str) {
        self.replies.lock().unwrap().entry(key(method, path)).or_default().push_back(Ok(HttpReply::new(status, body)));
    }
    fn push_cookie(&self, method: &str, path: &str, status: u16, body: &str, cookie: &str) {
        let mut r = HttpReply::new(status, body);
        r.set_cookie = Some(cookie.to_string());
        self.replies.lock().unwrap().entry(key(method, path)).or_default().push_back(Ok(r));
    }
    fn push_net_error(&self, method: &str, path: &str) {
        self.replies.lock().unwrap().entry(key(method, path)).or_default().push_back(Err(NetError("offline".into())));
    }
    fn count(&self, method: &str, path: &str) -> usize {
        self.sent.lock().unwrap().iter().filter(|s| s.method == method && s.path == path).count()
    }
}

impl Http for Arc<FakeHttp> {
    fn send(&self, r: &Req) -> Result<HttpReply, NetError> {
        let path = match r.url.strip_prefix(AUTH) {
            Some(p) => p.to_string(),
            None => format!("api:{}", r.url.strip_prefix(API).unwrap_or(r.url)),
        };
        self.sent.lock().unwrap().push(Sent {
            method: r.method.to_string(), path: path.clone(), cookie: r.cookie.map(str::to_string), bearer: r.bearer.map(str::to_string),
            origin: r.origin.to_string(), prefer: r.prefer.map(str::to_string), body: r.body.cloned(),
        });
        self.replies.lock().unwrap().get_mut(&key(r.method, &path)).and_then(|q| q.pop_front()).unwrap_or(Err(NetError("no reply scripted".into())))
    }
}

#[derive(Default)]
struct FakeVault {
    value: Mutex<Option<String>>,
    broken: Mutex<bool>,
}

impl Vault for Arc<FakeVault> {
    fn load(&self) -> Result<Option<String>, String> {
        if *self.broken.lock().unwrap() { return Err("no keychain".into()); }
        Ok(self.value.lock().unwrap().clone())
    }
    fn save(&self, v: &str) -> Result<(), String> {
        if *self.broken.lock().unwrap() { return Err("no keychain".into()); }
        *self.value.lock().unwrap() = Some(v.to_string());
        Ok(())
    }
    fn clear(&self) -> Result<(), String> {
        *self.value.lock().unwrap() = None;
        Ok(())
    }
}

#[derive(Default)]
struct FakeStore(Mutex<Option<Cache>>);

impl Store for Arc<FakeStore> {
    fn load(&self) -> Option<Cache> { self.0.lock().unwrap().clone() }
    fn save(&self, c: &Cache) { *self.0.lock().unwrap() = Some(c.clone()); }
    fn clear(&self) { *self.0.lock().unwrap() = None; }
}

struct Rig {
    svc: Arc<AuthService>,
    http: Arc<FakeHttp>,
    vault: Arc<FakeVault>,
    store: Arc<FakeStore>,
}

fn rig_for(code: &str) -> Rig {
    let (http, vault, store) = (Arc::new(FakeHttp::default()), Arc::new(FakeVault::default()), Arc::new(FakeStore::default()));
    Rig { svc: Arc::new(AuthService::new(Box::new(http.clone()), Box::new(vault.clone()), Box::new(store.clone()), code)), http, vault, store }
}

fn rig() -> Rig {
    rig_for(&code())
}

impl Rig {
    /// What Neon answers when the cookie is looked at: the session, the companies, the members and a data token.
    fn script_profile(&self, email: &str, org: Option<&str>, role: &str) {
        self.http.push("GET", "/get-session", 200, &json!({"session": {"expiresAt": "2026-10-26T00:00:00Z"}, "user": {"email": email}}).to_string());
        match org {
            None => self.http.push("GET", "/organization/list", 200, "[]"),
            Some(name) => {
                self.http.push("GET", "/organization/list", 200, &json!([{"id": ORG, "name": name, "slug": "x"}]).to_string());
                self.http.push("GET", &format!("/organization/list-members?organizationId={ORG}"), 200,
                    &json!({"members": [{"id": CODE_OK, "role": role, "user": {"email": email}}, {"id": OTHER_ID, "role": "member", "user": {"email": "bob@example.com"}}], "total": 2}).to_string());
                self.http.push("GET", "/token", 200, &json!({"token": jwt(NOW + 900)}).to_string());
            }
        }
    }

    fn login_as(&self, role: &str) -> AuthStatus {
        self.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
        self.script_profile("ann@example.com", Some("Acme Corp"), role);
        self.svc.login("ann@example.com", "pw", NOW).unwrap()
    }

    fn login_ok(&self) -> AuthStatus {
        self.login_as("member")
    }

    fn sent(&self) -> Vec<Sent> {
        self.http.sent.lock().unwrap().clone()
    }
    fn sent_count(&self) -> usize {
        self.sent().len()
    }
    fn last_sent(&self) -> Sent {
        self.sent().last().cloned().expect("something was sent")
    }
}

/// Put a saved session in the keychain and the cache, as if the app had been closed after signing in.
fn saved(r: &Rig, last_ok: i64, seen: i64, tenant_id: &str) {
    *r.vault.value.lock().unwrap() = Some(serde_json::to_string(&Blob { cookie: COOKIE.into(), last_ok, seen }).unwrap());
    *r.store.0.lock().unwrap() = Some(Cache {
        server: code(),
        user: UserInfo { email: "ann@example.com".into(), role: "user".into(), tenant: "Acme Corp".into(), tenant_id: tenant_id.into() },
    });
}

// ---------- the connection code ----------

#[test]
fn a_connection_code_round_trips_and_is_read_back_whole() {
    let c = parse_connection(&code()).unwrap();
    assert_eq!((c.auth.as_str(), c.api.as_str(), c.origin.as_str()), (AUTH, API, ORIGIN));
    assert_eq!(validate_server_url(&format!("  {}  ", code())).unwrap(), code());
    assert_eq!(validate_server_url("   ").unwrap(), "", "empty means no sign-in");
    let local = make_connection_code("http://localhost:8000/auth", "http://127.0.0.1:3000/rest/v1", "http://localhost:3000");
    assert!(parse_connection(&local).is_ok(), "plain http is allowed for this computer only");
}

#[test]
fn a_bad_connection_code_is_refused_with_one_plain_sentence() {
    for bad in [
        "https://auth.example.com".to_string(),
        "rd1.".to_string(),
        "rd1.!!!notbase64".to_string(),
        format!("rd1.{}", URL_SAFE_NO_PAD.encode("not json")),
        format!("rd1.{}", URL_SAFE_NO_PAD.encode(json!({"a": AUTH}).to_string())),
        make_connection_code("http://auth.example.com/auth", API, ORIGIN),
        make_connection_code(AUTH, "http://api.example.com/rest/v1", ORIGIN),
        make_connection_code(AUTH, API, "https://recruitdesk.example/with/path"),
        make_connection_code("https://user:pw@auth.example.com/auth", API, ORIGIN),
        make_connection_code("https://auth.example.com/auth?x=1", API, ORIGIN),
        make_connection_code("https://auth.example.com/a b", API, ORIGIN),
        make_connection_code("ftp://auth.example.com/auth", API, ORIGIN),
    ] {
        assert_eq!(parse_connection(&bad), Err(MSG_BAD_CONNECTION.to_string()), "{bad}");
        assert_eq!(validate_server_url(&bad), Err(MSG_BAD_CONNECTION.to_string()), "{bad}");
    }
}

#[test]
fn an_unreadable_saved_code_turns_sign_in_off_not_on_to_a_bad_address() {
    let r = rig_for("https://auth.example.com");
    assert!(!r.svc.status(NOW).enabled);
    assert!(r.svc.allows(NOW));
}

#[test]
fn the_reset_code_is_taken_from_a_pasted_link_and_the_address_is_ignored() {
    assert_eq!(extract_token("https://evil.example/reset?token=abcdefghijklmnopqrstuvwxyz012345&x=1").unwrap(), "abcdefghijklmnopqrstuvwxyz012345");
    assert_eq!(extract_token("  abcdefghijklmnopqrstuvwxyz012345 \n").unwrap(), "abcdefghijklmnopqrstuvwxyz012345");
    assert_eq!(extract_token("https://x.test/reset?token=abcdefghijklmnopqrstuvwxyz012345#frag").unwrap(), "abcdefghijklmnopqrstuvwxyz012345");
    for bad in ["", "short", "has spaces in it which is not a code", "https://x.test/reset?token=", "a;b;c;d;e;f;g;h;i;j;k;l;m"] {
        assert_eq!(extract_token(bad), Err(MSG_BAD_LINK), "{bad}");
    }
}

#[test]
fn an_invitation_code_is_a_uuid() {
    assert_eq!(valid_code(&format!("  {}\n", CODE_OK.to_uppercase())).unwrap(), CODE_OK);
    for bad in ["", "1b9d6bcd", "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bez", "1b9d6bcdbbfd4b2d9b5dab8dfbbd4bed", "https://x.test/?c=1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"] {
        assert_eq!(valid_code(bad), Err(MSG_CODE_FORMAT), "{bad}");
    }
}

#[test]
fn iso_times_are_read_as_utc() {
    assert_eq!(parse_iso("1970-01-01T00:00:00Z"), Some(0));
    assert_eq!(parse_iso("2026-10-21T09:01:14.778Z"), Some(1_792_573_274));
    assert_eq!(parse_iso("2000-02-29T12:00:00Z"), Some(951_825_600));
    assert_eq!(parse_iso("nonsense"), None);
    assert_eq!(parse_iso("2026-13-01T00:00:00Z"), None);
}

// ---------- sign in ----------

#[test]
fn login_signs_in_and_saves_the_session_in_the_keychain_only() {
    let r = rig();
    let st = r.login_as("owner");
    assert_eq!((st.state.as_str(), st.enabled, st.offline), ("signed_in", true, false));
    let u = st.user.clone().unwrap();
    assert_eq!((u.email.as_str(), u.role.as_str(), u.tenant.as_str(), u.tenant_id.as_str()), ("ann@example.com", "admin", "Acme Corp", ORG), "an owner is an admin here");
    let blob: Blob = serde_json::from_str(&r.vault.value.lock().unwrap().clone().unwrap()).unwrap();
    assert_eq!((blob.cookie.as_str(), blob.last_ok), (COOKIE, NOW));
    let cache = r.store.0.lock().unwrap().clone().unwrap();
    assert_eq!(cache.user.tenant, "Acme Corp");
    let file = serde_json::to_string(&cache).unwrap();
    assert!(!file.contains("abc123") && !file.contains("pw"), "no cookie or password in the plain file: {file}");
    let sent = r.sent();
    assert_eq!(sent[0].path, "/sign-in/email");
    assert!(sent.iter().all(|s| s.origin == ORIGIN), "every request names the app's origin");
    assert_eq!(sent[0].body.as_ref().unwrap(), &json!({"email": "ann@example.com", "password": "pw"}));
    assert!(sent[1..].iter().all(|s| s.cookie.as_deref() == Some(COOKIE)));
    assert!(!serde_json::to_string(&st).unwrap().contains("abc123"), "the window never sees the cookie");
    assert!(r.svc.allows(NOW));
}

#[test]
fn a_member_is_a_user_and_the_email_is_lowercased() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    let st = r.svc.login(" Ann@Example.COM ", "pw", NOW).unwrap();
    let u = st.user.unwrap();
    assert_eq!((u.email.as_str(), u.role.as_str()), ("ann@example.com", "user"));
    assert_eq!(r.sent()[0].body.as_ref().unwrap()["email"], "Ann@Example.COM", "the typed address is sent trimmed");
}

#[test]
fn login_errors_are_plain_language() {
    let cases: Vec<(u16, &str, &str)> = vec![
        (401, r#"{"code":"INVALID_EMAIL_OR_PASSWORD"}"#, MSG_BAD_LOGIN),
        (403, r#"{"code":"INVALID_ORIGIN"}"#, MSG_ORIGIN),
        (403, r#"{"code":"BANNED_USER"}"#, MSG_DISABLED),
        (400, r#"{"code":"VALIDATION_ERROR"}"#, MSG_BAD_EMAIL),
        (429, "{}", MSG_TOO_MANY),
        (503, "{}", MSG_SERVER),
        (418, "{}", MSG_UNEXPECTED),
    ];
    for (status, body, want) in cases {
        let r = rig();
        r.http.push("POST", "/sign-in/email", status, body);
        assert_eq!(r.svc.login("a@b.co", "pw", NOW).unwrap_err(), want, "{status} {body}");
        assert_eq!(r.svc.status(NOW).state, "signed_out");
        assert!(r.vault.value.lock().unwrap().is_none());
    }
    let r = rig();
    r.http.push_net_error("POST", "/sign-in/email");
    assert_eq!(r.svc.login("a@b.co", "pw", NOW).unwrap_err(), MSG_OFFLINE_LOGIN);
    let r = rig();
    r.http.push("POST", "/sign-in/email", 200, "{}"); // no session cookie came back
    assert_eq!(r.svc.login("a@b.co", "pw", NOW).unwrap_err(), MSG_UNEXPECTED);
    assert_eq!(rig_for("").svc.login("a@b.co", "pw", NOW).unwrap_err(), MSG_NO_SERVER);
}

#[test]
fn a_session_neon_will_not_confirm_does_not_sign_anyone_in() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.http.push("GET", "/get-session", 200, "null");
    assert_eq!(r.svc.login("a@b.co", "pw", NOW).unwrap_err(), MSG_ENDED);
    assert!(r.vault.value.lock().unwrap().is_none(), "nothing is saved");
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.http.push("GET", "/get-session", 200, "[1,2]");
    assert_eq!(r.svc.login("a@b.co", "pw", NOW).unwrap_err(), MSG_UNEXPECTED);
}

#[test]
fn signed_in_without_a_company_the_app_does_no_work_and_says_what_to_do() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.script_profile("ann@example.com", None, "");
    let st = r.svc.login("ann@example.com", "pw", NOW).unwrap();
    assert_eq!(st.state, "no_company");
    assert_eq!(st.message.as_deref(), Some(MSG_NO_COMPANY));
    assert!(!r.svc.allows(NOW) && r.svc.require(NOW).is_err());
    assert_eq!(r.svc.tenant_id(NOW), None);
    assert_eq!(r.http.count("GET", "/token"), 0, "no data token without a company");
}

#[test]
fn a_member_list_that_does_not_hold_the_person_means_no_company() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.http.push("GET", "/get-session", 200, &json!({"user": {"email": "ann@example.com"}}).to_string());
    r.http.push("GET", "/organization/list", 200, &json!([{"id": ORG, "name": "Acme Corp"}]).to_string());
    r.http.push("GET", &format!("/organization/list-members?organizationId={ORG}"), 403, r#"{"code":"YOU_ARE_NOT_A_MEMBER_OF_THIS_ORGANIZATION"}"#);
    assert_eq!(r.svc.login("ann@example.com", "pw", NOW).unwrap().state, "no_company");
}

#[test]
fn without_a_keychain_the_session_lasts_until_the_app_closes() {
    let r = rig();
    *r.vault.broken.lock().unwrap() = true;
    let st = r.login_ok();
    assert_eq!((st.state.as_str(), st.remembered), ("signed_in", false));
    // still works in this run: a check inside the window needs no network
    assert_eq!(r.svc.check(NOW + 60).state, "signed_in");
    // after the recheck time the in-memory cookie is used to look again
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    assert_eq!(r.svc.check(NOW + 2 * RECHECK_SECS).state, "signed_in");
    assert_eq!(r.sent().last().unwrap().cookie.as_deref(), Some(COOKIE));
    // a new app run has nothing
    let fresh = rig();
    *fresh.vault.broken.lock().unwrap() = true;
    assert_eq!(fresh.svc.check(NOW).state, "signed_out");
}

// ---------- creating an account with an invitation code ----------

#[test]
fn create_account_signs_up_then_accepts_the_code_and_lands_in_the_company() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-up/email", 200, "{}", COOKIE);
    r.script_profile("new@example.com", None, ""); // looked at once right after sign-up
    r.http.push("POST", "/organization/accept-invitation", 200, "{}");
    r.script_profile("new@example.com", Some("Acme Corp"), "member"); // and again after the code was accepted
    let st = r.svc.create_account(" new@example.com ", "a long enough password", &CODE_OK.to_uppercase(), NOW).unwrap();
    assert_eq!(st.state, "signed_in");
    assert_eq!(st.user.unwrap().tenant, "Acme Corp");
    let sent = r.sent();
    assert_eq!(sent[0].path, "/sign-up/email");
    assert_eq!(sent[0].body.as_ref().unwrap(), &json!({"email": "new@example.com", "password": "a long enough password", "name": "new"}));
    let accept = sent.iter().find(|s| s.path == "/organization/accept-invitation").unwrap();
    assert_eq!(accept.body.as_ref().unwrap(), &json!({"invitationId": CODE_OK}));
    assert_eq!(accept.cookie.as_deref(), Some(COOKIE));
}

#[test]
fn a_code_that_does_not_work_leaves_the_account_signed_in_without_a_company() {
    let r = rig();
    r.http.push_cookie("POST", "/sign-up/email", 200, "{}", COOKIE);
    r.script_profile("new@example.com", None, "");
    r.http.push("POST", "/organization/accept-invitation", 400, r#"{"code":"INVITATION_NOT_FOUND"}"#);
    let st = r.svc.create_account("new@example.com", "a long enough password", CODE_OK, NOW).unwrap();
    assert_eq!(st.state, "no_company");
    assert_eq!(st.message.as_deref(), Some(MSG_CODE_INVALID));
    // and they can try again with the right one
    r.http.push("POST", "/organization/accept-invitation", 200, "{}");
    r.script_profile("new@example.com", Some("Acme Corp"), "member");
    assert_eq!(r.svc.join_company(CODE_OK, NOW).unwrap().state, "signed_in");
}

#[test]
fn create_account_refuses_bad_input_before_sending_anything() {
    let r = rig();
    assert_eq!(r.svc.create_account("a@b.co", "a long enough password", "not a code", NOW).unwrap_err(), MSG_CODE_FORMAT);
    assert_eq!(rig_for("").svc.create_account("a@b.co", "pw", CODE_OK, NOW).unwrap_err(), MSG_NO_SERVER);
    assert_eq!(r.sent_count(), 0);
}

#[test]
fn create_account_explains_what_went_wrong() {
    let cases: Vec<(u16, &str, &str)> = vec![
        (422, r#"{"code":"USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL"}"#, MSG_HAVE_ACCOUNT),
        (400, r#"{"code":"PASSWORD_TOO_SHORT"}"#, MSG_PASSWORD_RULES),
        (400, r#"{"code":"VALIDATION_ERROR"}"#, MSG_BAD_EMAIL),
        (403, r#"{"code":"INVALID_ORIGIN"}"#, MSG_ORIGIN),
        (429, "{}", MSG_TOO_MANY),
        (500, "{}", MSG_SERVER),
    ];
    for (status, body, want) in cases {
        let r = rig();
        r.http.push("POST", "/sign-up/email", status, body);
        assert_eq!(r.svc.create_account("a@b.co", "pw", CODE_OK, NOW).unwrap_err(), want, "{status}");
        assert_eq!(r.svc.status(NOW).state, "signed_out");
        assert!(r.vault.value.lock().unwrap().is_none());
    }
}

#[test]
fn joining_needs_a_session_and_answers_plainly() {
    let r = rig();
    assert_eq!(r.svc.join_company(CODE_OK, NOW).unwrap_err(), "Sign in first.");
    assert_eq!(r.svc.join_company("nope", NOW).unwrap_err(), MSG_CODE_FORMAT);
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.script_profile("ann@example.com", None, "");
    r.svc.login("ann@example.com", "pw", NOW).unwrap();
    for (status, want) in [(403, MSG_CODE_INVALID), (400, MSG_CODE_INVALID), (429, MSG_TOO_MANY), (500, MSG_SERVER)] {
        r.http.push("POST", "/organization/accept-invitation", status, "{}");
        assert_eq!(r.svc.join_company(CODE_OK, NOW).unwrap_err(), want, "{status}");
    }
    r.http.push("POST", "/organization/accept-invitation", 401, "{}");
    assert_eq!(r.svc.join_company(CODE_OK, NOW).unwrap_err(), MSG_ENDED);
    assert_eq!(r.svc.status(NOW).state, "signed_out");
}

// ---------- start-up and the periodic check ----------

#[test]
fn a_fresh_session_does_not_touch_the_network() {
    let r = rig();
    r.login_ok();
    let before = r.sent_count();
    assert_eq!(r.svc.check(NOW + 60).state, "signed_in");
    assert_eq!(r.sent_count(), before);
}

#[test]
fn start_up_opens_the_saved_session() {
    let r = rig();
    saved(&r, NOW - 3600, NOW - 3600, ORG);
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    let st = r.svc.check(NOW);
    assert_eq!((st.state.as_str(), st.offline), ("signed_in", false));
    assert_eq!(r.sent()[0].cookie.as_deref(), Some(COOKIE));
    let blob: Blob = serde_json::from_str(&r.vault.value.lock().unwrap().clone().unwrap()).unwrap();
    assert_eq!(blob.last_ok, NOW, "the successful check is remembered");
}

#[test]
fn the_check_is_repeated_after_a_while_and_when_the_data_token_runs_low() {
    let r = rig();
    r.login_ok();
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    r.svc.check(NOW + RECHECK_SECS + 1);
    assert_eq!(r.http.count("GET", "/get-session"), 2);
    // a data token about to run out is replaced even inside the recheck time
    let r = rig();
    r.login_ok();
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    r.svc.check(NOW + 900 - TOKEN_MARGIN_SECS + 5);
    assert_eq!(r.http.count("GET", "/get-session"), 2);
}

#[test]
fn a_session_neon_no_longer_knows_ends_and_clears_what_was_saved() {
    for reply in [(200u16, "null"), (401, "{}")] {
        let r = rig();
        saved(&r, NOW - 3600, NOW - 3600, ORG);
        r.http.push("GET", "/get-session", reply.0, reply.1);
        let st = r.svc.check(NOW);
        assert_eq!((st.state.as_str(), st.message.as_deref()), ("signed_out", Some(MSG_ENDED)), "{reply:?}");
        assert!(r.vault.value.lock().unwrap().is_none() && r.store.0.lock().unwrap().is_none());
    }
}

#[test]
fn offline_the_time_since_the_last_good_check_decides() {
    let r = rig();
    saved(&r, NOW - 2 * 86400, NOW - 2 * 86400, ORG);
    r.http.push_net_error("GET", "/get-session");
    let st = r.svc.check(NOW);
    assert_eq!((st.state.as_str(), st.offline), ("signed_in", true));
    assert_eq!(st.offline_until, Some(NOW - 2 * 86400 + GRACE_SECS));
    assert!(r.svc.allows(NOW) && st.message.is_none());
    // a bad moment at Neon counts the same as no network
    let r = rig();
    saved(&r, NOW - 3600, NOW - 3600, ORG);
    r.http.push("GET", "/get-session", 503, "{}");
    assert!(r.svc.check(NOW).offline);
}

#[test]
fn offline_past_the_allowance_signs_out_but_keeps_the_cookie_for_later() {
    let r = rig();
    saved(&r, NOW - GRACE_SECS - 10, NOW - GRACE_SECS - 10, ORG);
    r.http.push_net_error("GET", "/get-session");
    let st = r.svc.check(NOW);
    assert_eq!((st.state.as_str(), st.message.as_deref()), ("signed_out", Some(MSG_TOO_LONG_OFFLINE)));
    assert!(r.vault.value.lock().unwrap().is_some(), "it may work again once the person is back online");
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    assert_eq!(r.svc.check(NOW + 1).state, "signed_in", "back online, the same cookie opens the session again");
}

#[test]
fn a_clock_set_back_is_refused() {
    let r = rig();
    saved(&r, NOW + 10_000, NOW + 10_000, ORG);
    let st = r.svc.check(NOW);
    assert_eq!((st.state.as_str(), st.message.as_deref()), ("signed_out", Some(MSG_CLOCK)));
    assert_eq!(r.sent_count(), 0);
}

#[test]
fn removed_from_the_company_the_person_is_told_and_loses_the_data_access() {
    let r = rig();
    r.login_ok();
    r.script_profile("ann@example.com", None, "");
    let st = r.svc.check(NOW + RECHECK_SECS + 1);
    assert_eq!((st.state.as_str(), st.message.as_deref()), ("no_company", Some(MSG_REMOVED)));
    assert!(!r.svc.allows(NOW + RECHECK_SECS + 1));
    assert_eq!(r.svc.tenant_id(NOW + RECHECK_SECS + 1), None);
}

#[test]
fn nothing_saved_means_signed_out_without_a_message() {
    let r = rig();
    let st = r.svc.check(NOW);
    assert_eq!((st.state.as_str(), st.message), ("signed_out", None));
    assert_eq!(r.sent_count(), 0);
}

#[test]
fn a_damaged_keychain_entry_is_dropped() {
    let r = rig();
    *r.vault.value.lock().unwrap() = Some("{not json".into());
    assert_eq!(r.svc.check(NOW).state, "signed_out");
    assert!(r.vault.value.lock().unwrap().is_none());
}

#[test]
fn a_cookie_neon_rotates_replaces_the_saved_one() {
    let r = rig();
    saved(&r, NOW - 3600, NOW - 3600, ORG);
    let c2 = "__Secure-neon-auth.session_token=rotated.sig%3D";
    r.http.push_cookie("GET", "/get-session", 200, &json!({"user": {"email": "ann@example.com"}}).to_string(), c2);
    r.http.push("GET", "/organization/list", 200, "[]");
    r.svc.check(NOW);
    let blob: Blob = serde_json::from_str(&r.vault.value.lock().unwrap().clone().unwrap()).unwrap();
    assert_eq!(blob.cookie, c2);
}

// ---------- sign out, connection changes, the gate ----------

#[test]
fn logout_tells_neon_and_forgets_everything_even_when_offline() {
    let r = rig();
    r.login_ok();
    r.http.push("POST", "/sign-out", 200, r#"{"success":true}"#);
    let st = r.svc.logout(NOW);
    assert_eq!((st.state.as_str(), st.message), ("signed_out", None));
    let s = r.last_sent();
    assert_eq!((s.method.as_str(), s.path.as_str(), s.cookie.as_deref()), ("POST", "/sign-out", Some(COOKIE)));
    assert!(r.vault.value.lock().unwrap().is_none() && r.store.0.lock().unwrap().is_none());
    let r = rig();
    r.login_ok();
    r.http.push_net_error("POST", "/sign-out");
    assert_eq!(r.svc.logout(NOW).state, "signed_out");
    assert!(r.vault.value.lock().unwrap().is_none(), "forgotten here even when Neon could not be told");
}

#[test]
fn changing_the_connection_ends_the_session() {
    let r = rig();
    r.login_ok();
    let other = make_connection_code("https://other.example.com/auth", API, ORIGIN);
    assert!(r.svc.set_server("not a code").is_err());
    assert_eq!(r.svc.status(NOW).state, "signed_in", "a refused code changes nothing");
    r.svc.set_server(&other).unwrap();
    assert_eq!(r.svc.status(NOW).state, "signed_out");
    assert!(r.vault.value.lock().unwrap().is_none());
    assert_eq!(r.svc.server(), other);
    r.svc.set_server(&other).unwrap(); // the same code again changes nothing
    r.svc.set_server("").unwrap();
    assert_eq!(r.svc.status(NOW).state, "disabled");
}

#[test]
fn no_connection_means_the_app_works_as_before() {
    let r = rig_for("");
    let st = r.svc.status(NOW);
    assert_eq!((st.enabled, st.state.as_str()), (false, "disabled"));
    assert!(r.svc.allows(NOW) && r.svc.require(NOW).is_ok());
    assert_eq!(r.svc.check(NOW).state, "disabled");
    assert_eq!(r.sent_count(), 0);
}

#[test]
fn the_gate_is_closed_until_signed_in_to_a_company_and_closes_again_when_the_allowance_ends() {
    let r = rig();
    assert!(!r.svc.allows(NOW));
    assert_eq!(r.svc.require(NOW).unwrap_err(), "Sign in first.");
    r.login_ok();
    assert!(r.svc.allows(NOW));
    let r = rig();
    saved(&r, NOW, NOW, ORG);
    r.http.push_net_error("GET", "/get-session");
    r.svc.check(NOW);
    assert!(r.svc.allows(NOW + 100));
    assert!(!r.svc.allows(NOW + GRACE_SECS + 1), "offline too long");
}

// ---------- forgotten and reset passwords ----------

#[test]
fn a_reset_email_is_asked_for_with_the_apps_own_origin_and_the_answer_never_names_the_account() {
    let r = rig();
    r.http.push("POST", "/request-password-reset", 200, r#"{"status":true,"message":"If this email exists in our system, check your email"}"#);
    assert_eq!(r.svc.forgot_password(" ann@example.com ").unwrap(), MSG_RESET_SENT);
    let s = r.last_sent();
    assert_eq!(s.body.unwrap(), json!({"email": "ann@example.com", "redirectTo": format!("{ORIGIN}/reset")}));
    for bad in ["", "no-at-sign", "a b@c.d"] {
        assert_eq!(r.svc.forgot_password(bad).unwrap_err(), MSG_BAD_EMAIL, "{bad}");
    }
    for (status, want) in [(429u16, MSG_TOO_MANY), (500, MSG_SERVER), (418, MSG_UNEXPECTED)] {
        r.http.push("POST", "/request-password-reset", status, "{}");
        assert_eq!(r.svc.forgot_password("ann@example.com").unwrap_err(), want);
    }
    r.http.push("POST", "/request-password-reset", 403, r#"{"code":"INVALID_ORIGIN"}"#);
    assert_eq!(r.svc.forgot_password("ann@example.com").unwrap_err(), MSG_ORIGIN);
    r.http.push_net_error("POST", "/request-password-reset");
    assert_eq!(r.svc.forgot_password("ann@example.com").unwrap_err(), MSG_OFFLINE_LOGIN);
}

#[test]
fn a_reset_code_sets_a_new_password_and_does_not_sign_in() {
    let r = rig();
    r.http.push("POST", "/reset-password", 200, r#"{"status":true}"#);
    let msg = r.svc.reset_password("https://x.test/reset?token=abcdefghijklmnopqrstuvwx", "a new long password").unwrap();
    assert_eq!(msg, MSG_PASSWORD_CHANGED);
    assert_eq!(r.last_sent().body.unwrap(), json!({"newPassword": "a new long password", "token": "abcdefghijklmnopqrstuvwx"}));
    assert_eq!(r.svc.status(NOW).state, "signed_out");
    for (status, body, want) in [(400u16, r#"{"code":"INVALID_TOKEN"}"#, MSG_RESET_INVALID), (400, r#"{"code":"PASSWORD_TOO_SHORT"}"#, MSG_PASSWORD_RULES), (429, "{}", MSG_TOO_MANY), (500, "{}", MSG_SERVER)] {
        r.http.push("POST", "/reset-password", status, body);
        assert_eq!(r.svc.reset_password("abcdefghijklmnopqrstuvwx", "a new long password").unwrap_err(), want, "{status}");
    }
    let n = r.sent_count();
    assert_eq!(r.svc.reset_password("short", "pw").unwrap_err(), MSG_BAD_LINK);
    assert_eq!(r.sent_count(), n, "a bad link sends nothing");
}

// ---------- admin calls ----------

impl Rig {
    fn admin_ready(&self) {
        self.login_as("owner");
    }
    fn admin_reply(&self, method: &str, path: &str, status: u16, body: &str) {
        self.http.push(method, path, status, body);
    }
}

#[test]
fn an_admin_lists_the_people_of_their_company_with_the_cookie_and_the_window_never_sees_it() {
    let r = rig();
    r.admin_ready();
    let members = json!({"members": [
        {"id": CODE_OK, "role": "owner", "createdAt": "2026-10-01T00:00:00Z", "user": {"email": "Ann@Example.com"}},
        {"id": OTHER_ID, "role": "member", "createdAt": "2026-10-05T00:00:00Z", "user": {"email": "bob@example.com"}},
        {"id": "x", "role": "member", "user": {}}]});
    r.admin_reply("GET", &format!("/organization/list-members?organizationId={ORG}"), 200, &members.to_string());
    let v = r.svc.admin(&AdminOp::Members, NOW).unwrap();
    assert_eq!(v, json!([
        {"id": CODE_OK, "email": "ann@example.com", "role": "admin", "owner": true, "you": true, "joined_at": "2026-10-01T00:00:00Z"},
        {"id": OTHER_ID, "email": "bob@example.com", "role": "user", "owner": false, "you": false, "joined_at": "2026-10-05T00:00:00Z"}]));
    let s = r.last_sent();
    assert_eq!((s.method.as_str(), s.cookie.as_deref()), ("GET", Some(COOKIE)));
    assert!(!v.to_string().contains("abc123"));
}

#[test]
fn only_pending_unexpired_invitations_are_listed() {
    let r = rig();
    r.admin_ready();
    let soon = "2026-10-21T00:00:00Z";
    let past = "2020-01-01T00:00:00Z";
    r.admin_reply("GET", &format!("/organization/list-invitations?organizationId={ORG}"), 200, &json!([
        {"id": CODE_OK, "email": "New@X.com", "role": "member", "status": "pending", "expiresAt": soon, "createdAt": "2026-10-19T00:00:00Z"},
        {"id": OTHER_ID, "email": "old@x.com", "role": "member", "status": "pending", "expiresAt": past},
        {"id": "a", "email": "done@x.com", "role": "member", "status": "accepted", "expiresAt": soon},
        {"id": "b", "email": "gone@x.com", "role": "admin", "status": "canceled", "expiresAt": soon}]).to_string());
    let v = r.svc.admin(&AdminOp::Invites, 1_792_000_000).unwrap();
    assert_eq!(v, json!([{"id": CODE_OK, "email": "new@x.com", "role": "user", "expires_at": soon, "created_at": "2026-10-19T00:00:00Z"}]));
}

#[test]
fn only_a_signed_in_online_admin_may_call_and_nothing_is_sent_otherwise() {
    // signed out
    let r = rig();
    assert_eq!(r.svc.admin(&AdminOp::Members, NOW).unwrap_err(), "Sign in first.");
    assert_eq!(r.sent_count(), 0);
    // a plain user
    let r = rig();
    r.login_ok();
    let n = r.sent_count();
    assert_eq!(r.svc.admin(&AdminOp::Members, NOW).unwrap_err(), MSG_ADMINS_ONLY);
    assert_eq!(r.sent_count(), n, "a user's admin call never reaches Neon");
    // an admin who is offline
    let r = rig();
    saved(&r, NOW - 3600, NOW - 3600, ORG);
    *r.store.0.lock().unwrap() = Some(Cache { server: code(), user: UserInfo { email: "boss@example.com".into(), role: "admin".into(), tenant: "Acme Corp".into(), tenant_id: ORG.into() } });
    r.http.push_net_error("GET", "/get-session");
    assert!(r.svc.check(NOW).offline);
    let n = r.sent_count();
    assert_eq!(r.svc.admin(&AdminOp::Members, NOW).unwrap_err(), MSG_ADMIN_ONLINE);
    assert_eq!(r.sent_count(), n);
}

#[test]
fn each_action_is_one_fixed_request_in_the_admins_own_company() {
    let cases: Vec<(AdminOp, &str, String, Option<Value>, u16, &str)> = vec![
        (AdminOp::Invite { email: "  New@Example.com ".into(), role: "user".into() }, "POST", "/organization/invite-member".into(),
            Some(json!({"email": "New@Example.com", "role": "member", "organizationId": ORG})), 200, r#"{"id":"1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed","email":"new@example.com","role":"member","expiresAt":"2026-10-21T00:00:00Z"}"#),
        (AdminOp::Invite { email: "boss@example.com".into(), role: "admin".into() }, "POST", "/organization/invite-member".into(),
            Some(json!({"email": "boss@example.com", "role": "admin", "organizationId": ORG})), 200, r#"{"id":"1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"}"#),
        (AdminOp::RevokeInvite { id: CODE_OK.into() }, "POST", "/organization/cancel-invitation".into(), Some(json!({"invitationId": CODE_OK})), 200, "{}"),
        (AdminOp::SetRole { id: OTHER_ID.into(), role: "admin".into() }, "POST", "/organization/update-member-role".into(),
            Some(json!({"memberId": OTHER_ID, "role": "admin", "organizationId": ORG})), 200, "{}"),
        (AdminOp::SetRole { id: OTHER_ID.into(), role: "user".into() }, "POST", "/organization/update-member-role".into(),
            Some(json!({"memberId": OTHER_ID, "role": "member", "organizationId": ORG})), 200, "{}"),
        (AdminOp::Remove { id: OTHER_ID.into() }, "POST", "/organization/remove-member".into(), Some(json!({"memberIdOrEmail": OTHER_ID, "organizationId": ORG})), 200, "{}"),
    ];
    for (op, method, path, body, status, reply) in cases {
        let r = rig();
        r.admin_ready();
        r.admin_reply(method, &path, status, reply);
        r.svc.admin(&op, NOW).unwrap_or_else(|e| panic!("{op:?}: {e}"));
        let s = r.last_sent();
        assert_eq!((s.method.as_str(), s.path.as_str()), (method, path.as_str()), "{op:?}");
        assert_eq!(s.body, body, "{op:?}");
        assert_eq!(s.cookie.as_deref(), Some(COOKIE));
    }
}

#[test]
fn an_invitation_gives_back_the_code_to_hand_over() {
    let r = rig();
    r.admin_ready();
    r.admin_reply("POST", "/organization/invite-member", 200, r#"{"id":"1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed","email":"new@example.com","role":"admin","expiresAt":"2026-10-21T00:00:00Z","status":"pending"}"#);
    let v = r.svc.admin(&AdminOp::Invite { email: "new@example.com".into(), role: "admin".into() }, NOW).unwrap();
    assert_eq!(v, json!({"email": "new@example.com", "role": "admin", "expires_at": "2026-10-21T00:00:00Z", "code": CODE_OK}));
    r.admin_reply("POST", "/organization/invite-member", 200, r#"{"email":"x@y.zz"}"#);
    assert_eq!(r.svc.admin(&AdminOp::Invite { email: "x@y.zz".into(), role: "user".into() }, NOW).unwrap_err(), MSG_UNEXPECTED, "no code came back");
}

#[test]
fn bad_input_is_refused_before_anything_is_sent() {
    let r = rig();
    r.admin_ready();
    let n = r.sent_count();
    let bad = [
        AdminOp::Invite { email: "".into(), role: "user".into() },
        AdminOp::Invite { email: "no-at-sign".into(), role: "user".into() },
        AdminOp::Invite { email: "a b@c.d".into(), role: "user".into() },
        AdminOp::Invite { email: format!("{}@x.io", "a".repeat(320)), role: "user".into() },
        AdminOp::Invite { email: "a@b.co".into(), role: "owner".into() },
        AdminOp::Invite { email: "a@b.co".into(), role: "superuser".into() },
        AdminOp::RevokeInvite { id: "../../etc".into() },
        AdminOp::RevokeInvite { id: "".into() },
        AdminOp::SetRole { id: "not-an-id".into(), role: "admin".into() },
        AdminOp::SetRole { id: OTHER_ID.into(), role: "owner".into() },
        AdminOp::Remove { id: "1; drop table".into() },
    ];
    for op in bad {
        assert_eq!(r.svc.admin(&op, NOW).unwrap_err(), MSG_BAD_INPUT, "{op:?}");
    }
    assert_eq!(r.sent_count(), n);
}

#[test]
fn neons_answers_become_plain_sentences_never_neons_own_text() {
    let invite = AdminOp::Invite { email: "a@b.co".into(), role: "user".into() };
    let cases: Vec<(AdminOp, u16, &str, &str)> = vec![
        (AdminOp::Members, 403, r#"{"code":"YOU_ARE_NOT_A_MEMBER_OF_THIS_ORGANIZATION","message":"secret internal text"}"#, MSG_ADMINS_ONLY),
        (AdminOp::Members, 404, "{}", MSG_GONE),
        (AdminOp::Members, 429, "{}", MSG_TOO_MANY),
        (AdminOp::Members, 503, "{}", MSG_SERVER),
        (AdminOp::Members, 418, "{}", MSG_UNEXPECTED),
        (AdminOp::Remove { id: OTHER_ID.into() }, 400, r#"{"code":"YOU_CANNOT_LEAVE_THE_ORGANIZATION_AS_THE_ONLY_OWNER","message":"x"}"#, "There must be at least one admin."),
        (invite.clone(), 400, r#"{"code":"USER_IS_ALREADY_A_MEMBER_OF_THIS_ORGANIZATION"}"#, "That person is already in your company."),
        (invite.clone(), 400, r#"{"code":"USER_IS_ALREADY_INVITED_TO_THIS_ORGANIZATION"}"#, "That person was already invited. Withdraw the old invitation first."),
        (invite.clone(), 400, r#"{"code":"SOMETHING_ELSE","message":"leaked detail"}"#, MSG_REFUSED),
        (invite.clone(), 500, "{}", "That address cannot be invited. It may already belong to a company."),
        (AdminOp::SetRole { id: OTHER_ID.into(), role: "admin".into() }, 500, "{}", MSG_SERVER),
        (AdminOp::RevokeInvite { id: CODE_OK.into() }, 400, r#"{"code":"INVITATION_NOT_FOUND"}"#, MSG_GONE),
    ];
    for (op, status, body, want) in cases {
        let r = rig();
        r.admin_ready();
        let (_, path, _) = op.request(ORG).unwrap();
        let method = if matches!(op, AdminOp::Members | AdminOp::Invites) { "GET" } else { "POST" };
        r.admin_reply(method, &path, status, body);
        let e = r.svc.admin(&op, NOW).unwrap_err();
        assert_eq!(e, want, "{op:?} {status}");
        assert!(!e.contains("leaked") && !e.contains("secret"));
    }
}

#[test]
fn a_rejected_session_during_an_admin_call_ends_it() {
    let r = rig();
    r.admin_ready();
    r.admin_reply("GET", &format!("/organization/list-members?organizationId={ORG}"), 401, "{}");
    assert_eq!(r.svc.admin(&AdminOp::Members, NOW).unwrap_err(), MSG_ENDED);
    assert_eq!(r.svc.status(NOW).state, "signed_out");
    assert!(r.vault.value.lock().unwrap().is_none());
}

#[test]
fn a_role_change_takes_effect_at_the_next_check() {
    let r = rig();
    r.login_as("owner");
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    let st = r.svc.check(NOW + RECHECK_SECS + 1);
    assert_eq!(st.user.unwrap().role, "user");
    let n = r.sent_count();
    assert_eq!(r.svc.admin(&AdminOp::Members, NOW + RECHECK_SECS + 1).unwrap_err(), MSG_ADMINS_ONLY);
    assert_eq!(r.sent_count(), n);
}

// ---------- the data calls ----------

#[test]
fn a_data_call_carries_the_data_token_and_the_origin_to_the_data_address() {
    let r = rig();
    r.login_ok();
    r.http.push("POST", "api:/last_records?on_conflict=tenant_id,agent", 201, "");
    let body = json!({"x": 1});
    let reply = r.svc.data_request("POST", "last_records?on_conflict=tenant_id,agent", Some(&body), Some("resolution=merge-duplicates,return=minimal"), NOW).unwrap();
    assert_eq!(reply.status, 201);
    let s = r.last_sent();
    assert_eq!(s.path, "api:/last_records?on_conflict=tenant_id,agent");
    assert_eq!(s.bearer.as_deref(), Some(jwt(NOW + 900).as_str()));
    assert_eq!((s.cookie, s.origin.as_str(), s.prefer.as_deref()), (None, ORIGIN, Some("resolution=merge-duplicates,return=minimal")));
    assert_eq!(s.body, Some(body));
}

#[test]
fn a_data_call_needs_a_company_and_a_connection() {
    let r = rig();
    assert_eq!(r.svc.data_request("GET", "last_records", None, None, NOW).unwrap_err(), "Sign in first.");
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.script_profile("ann@example.com", None, "");
    r.svc.login("ann@example.com", "pw", NOW).unwrap();
    assert_eq!(r.svc.data_request("GET", "last_records", None, None, NOW).unwrap_err(), "Sign in first.", "signed in without a company");
    let r = rig();
    saved(&r, NOW, NOW, ORG);
    r.http.push_net_error("GET", "/get-session");
    r.svc.check(NOW);
    assert_eq!(r.svc.data_request("GET", "last_records", None, None, NOW).unwrap_err(), MSG_ADMIN_ONLINE);
}

#[test]
fn a_data_token_that_ran_out_is_replaced_once_and_the_call_retried() {
    let r = rig();
    r.login_ok();
    r.http.push("GET", "api:/last_records", 401, "{}");
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    r.http.push("GET", "api:/last_records", 200, "[]");
    let reply = r.svc.data_request("GET", "last_records", None, None, NOW).unwrap();
    assert_eq!(reply.status, 200);
    assert_eq!(r.http.count("GET", "api:/last_records"), 2);
    assert_eq!(r.http.count("GET", "/token"), 2);
}

#[test]
fn a_data_token_that_stays_rejected_ends_the_session() {
    let r = rig();
    r.login_ok();
    r.http.push("GET", "api:/last_records", 401, "{}");
    r.script_profile("ann@example.com", Some("Acme Corp"), "member");
    r.http.push("GET", "api:/last_records", 401, "{}");
    assert_eq!(r.svc.data_request("GET", "last_records", None, None, NOW).unwrap_err(), MSG_ENDED);
    assert_eq!(r.svc.status(NOW).state, "signed_out");
    assert!(r.vault.value.lock().unwrap().is_none());
}

// ---------- the last record (records.rs) ----------

use crate::records::{self, RecordsSync, SyncResult};
use crate::settings::Agent;

const HASH1: &str = "1111111111111111111111111111111111111111111111111111111111111111";
const HASH2: &str = "2222222222222222222222222222222222222222222222222222222222222222";
const UPSERT: &str = "api:/last_records?on_conflict=tenant_id,agent";

fn delete_path(agent: &str, hash: &str) -> String {
    format!("api:/last_records?tenant_id=eq.{ORG}&agent=eq.{agent}&subject_hash=eq.{hash}")
}

/// A scratch folder that removes itself (and everything in it) when the test is done.
struct Scratch(std::path::PathBuf);

impl std::ops::Deref for Scratch {
    type Target = std::path::PathBuf;
    fn deref(&self) -> &std::path::PathBuf { &self.0 }
}

impl Drop for Scratch {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

fn scratch() -> Scratch {
    use std::sync::atomic::{AtomicU32, Ordering};
    static N: AtomicU32 = AtomicU32::new(0);
    let d = std::env::temp_dir().join(format!("rd-records-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    std::fs::create_dir_all(&d).unwrap();
    Scratch(d)
}

fn write_record(dir: &std::path::Path, agent: &str, hash: &str, at: &str, record: Value) {
    std::fs::write(dir.join(records::FILE_NAME), json!({"agent": agent, "subject_hash": hash, "created_at": at, "record": record}).to_string()).unwrap();
}

fn cand() -> Value {
    json!({"name": "Ann Rao", "title": "Senior Frontend Engineer", "company": "Acme", "location": "Pune", "match_score": 85, "profile_url": "https://www.linkedin.com/in/ann", "jd": "React", "date_found": "2026-10-18"})
}

struct Setup { rig: Rig, dir: Scratch, sent: std::path::PathBuf, sync: RecordsSync }

fn setup() -> Setup {
    let rig = rig();
    rig.login_ok();
    let dir = scratch();
    Setup { sent: dir.join("records_sent.json"), rig, dir, sync: RecordsSync::new() }
}

impl Setup {
    fn run(&self, agents: &[Agent]) -> Vec<SyncResult> {
        let dirs: Vec<_> = agents.iter().map(|a| (*a, self.dir.clone())).collect();
        self.sync.sync(&self.rig.svc, &dirs, &self.sent, NOW)
    }
    fn states(&self, agents: &[Agent]) -> Vec<String> {
        self.run(agents).into_iter().map(|r| r.state).collect()
    }
}

#[test]
fn a_local_record_is_stored_once_with_the_data_token_and_not_again_until_it_changes() {
    let s = setup();
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    s.rig.http.push("POST", UPSERT, 201, "");
    assert_eq!(s.states(&[Agent::Candidate]), ["sent"]);
    let sent = s.rig.last_sent();
    assert_eq!((sent.method.as_str(), sent.path.as_str()), ("POST", UPSERT));
    assert!(sent.bearer.is_some());
    assert_eq!(sent.prefer.as_deref(), Some("resolution=merge-duplicates,return=minimal"));
    let b = sent.body.unwrap();
    assert_eq!((b["tenant_id"].as_str(), b["agent"].as_str(), b["subject_hash"].as_str(), b["created_at"].as_str(), b["record"]["name"].as_str()),
        (Some(ORG), Some("candidate"), Some(HASH1), Some("2026-10-18T10:00:00+00:00"), Some("Ann Rao")));
    let before = s.rig.sent_count();
    assert_eq!(s.states(&[Agent::Candidate]), ["unchanged"]);
    assert_eq!(s.rig.sent_count(), before, "nothing is sent again");
    // a newer record replaces it
    write_record(&s.dir, "candidate", HASH2, "2026-10-18T11:00:00+00:00", json!({"name": "Bob Sen"}));
    s.rig.http.push("POST", UPSERT, 204, "");
    assert_eq!(s.states(&[Agent::Candidate]), ["sent"]);
    assert_eq!(s.rig.last_sent().body.unwrap()["record"]["name"], "Bob Sen");
}

#[test]
fn only_the_listed_fields_leave_even_if_the_file_holds_more() {
    let s = setup();
    let mut rec = cand();
    rec["email"] = json!("ann@example.com");
    rec["phone"] = json!("+91 98765 43210");
    rec["evidence"] = json!("quotes");
    rec["nested"] = json!({"a": 1});
    rec["flag"] = json!(true);
    rec["title"] = json!("   ");
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", rec);
    s.rig.http.push("POST", UPSERT, 201, "");
    s.run(&[Agent::Candidate]);
    let sent = s.rig.last_sent().body.unwrap()["record"].clone();
    let mut keys: Vec<_> = sent.as_object().unwrap().keys().cloned().collect();
    keys.sort();
    assert_eq!(keys, ["company", "date_found", "jd", "location", "match_score", "name", "profile_url"]);
    assert!(!sent.to_string().contains("ann@example.com") && !sent.to_string().contains("98765"));
}

#[test]
fn a_file_for_another_assistant_or_with_a_bad_shape_is_ignored() {
    let s = setup();
    write_record(&s.dir, "company", HASH1, "2026-10-18T10:00:00+00:00", json!({"job_title": "SRE"}));
    assert!(records::read_local(&s.dir, Agent::Candidate).is_none(), "another assistant's file");
    assert!(records::read_local(&s.dir, Agent::Company).is_some());
    for (hash, at) in [("short", "2026-10-18T10:00:00+00:00"), (&"A".repeat(64), "2026-10-18T10:00:00+00:00"), (HASH1, ""), (HASH1, "yesterday evening, roughly")] {
        write_record(&s.dir, "company", hash, at, json!({"job_title": "SRE"}));
        assert!(records::read_local(&s.dir, Agent::Company).is_none(), "{hash} {at}");
    }
    write_record(&s.dir, "company", HASH1, "2026-10-18T10:00:00+00:00", json!({"email": "x@y.zz"}));
    assert!(records::read_local(&s.dir, Agent::Company).is_none(), "nothing listed left");
    std::fs::write(s.dir.join(records::FILE_NAME), "{not json").unwrap();
    assert!(records::read_local(&s.dir, Agent::Company).is_none());
    std::fs::write(s.dir.join(records::FILE_NAME), "x".repeat(20_000)).unwrap();
    assert!(records::read_local(&s.dir, Agent::Company).is_none(), "too large");
    std::fs::remove_file(s.dir.join(records::FILE_NAME)).unwrap();
    assert!(records::read_local(&s.dir, Agent::Company).is_none(), "missing");
}

#[test]
fn long_values_are_cut_to_what_the_database_accepts() {
    let s = setup();
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", json!({"name": "n".repeat(500), "profile_url": format!("https://x.test/{}", "a".repeat(900))}));
    let l = records::read_local(&s.dir, Agent::Candidate).unwrap();
    assert_eq!(l.record["name"].as_str().unwrap().len(), 300);
    assert_eq!(l.record["profile_url"].as_str().unwrap().len(), 600);
}

#[test]
fn when_the_file_is_gone_the_stored_copy_is_removed_by_its_hash() {
    for status in [200u16, 204, 404, 409] {
        let s = setup();
        write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
        s.rig.http.push("POST", UPSERT, 201, "");
        s.run(&[Agent::Candidate]);
        std::fs::remove_file(s.dir.join(records::FILE_NAME)).unwrap();   // the person was erased: the assistant removed the file
        s.rig.http.push("DELETE", &delete_path("candidate", HASH1), status, "");
        assert_eq!(s.states(&[Agent::Candidate]), ["removed"], "{status}");
        let sent = s.rig.last_sent();
        assert_eq!((sent.method.as_str(), sent.path.as_str()), ("DELETE", delete_path("candidate", HASH1).as_str()), "the filter holds the hash, so a newer record of someone else matches nothing");
        let before = s.rig.sent_count();
        assert_eq!(s.states(&[Agent::Candidate]), ["nothing"], "and it is not asked again");
        assert_eq!(s.rig.sent_count(), before);
    }
}

#[test]
fn a_newer_record_already_stored_by_someone_else_is_not_retried() {
    let s = setup();
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    s.rig.http.push("POST", UPSERT, 409, r#"{"code":"PT409","message":"A newer record is already stored"}"#);
    assert_eq!(s.states(&[Agent::Candidate]), ["older"]);
    assert_eq!(s.states(&[Agent::Candidate]), ["unchanged"]);
}

#[test]
fn problems_are_retried_next_time_and_nothing_is_marked_sent() {
    for (status, state) in [(400u16, "error"), (422, "error"), (403, "waiting"), (429, "waiting"), (503, "waiting"), (418, "error")] {
        let s = setup();
        write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
        s.rig.http.push("POST", UPSERT, status, "{}");
        assert_eq!(s.states(&[Agent::Candidate]), [state], "{status}");
        s.rig.http.push("POST", UPSERT, 201, "");
        assert_eq!(s.states(&[Agent::Candidate]), ["sent"], "retried after {status}");
    }
    let s = setup();
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    s.rig.http.push_net_error("POST", UPSERT);
    assert_eq!(s.states(&[Agent::Candidate]), ["waiting"]);
    s.rig.http.push("POST", UPSERT, 201, "");
    assert_eq!(s.states(&[Agent::Candidate]), ["sent"]);
}

#[test]
fn nothing_is_sent_when_sign_in_is_off_signed_out_without_a_company_or_offline() {
    let rs = RecordsSync::new();
    let dir = scratch();
    write_record(&dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    let run = |rig: &Rig| rs.sync(&rig.svc, &[(Agent::Candidate, dir.clone())], &dir.join("s.json"), NOW);
    let rig = rig_for("");
    assert_eq!((run(&rig)[0].state.as_str(), rig.sent_count()), ("off", 0));
    let rig = self::rig();
    assert_eq!((run(&rig)[0].state.as_str(), rig.sent_count()), ("waiting", 0));
    let rig = self::rig();
    rig.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    rig.script_profile("ann@example.com", None, "");
    rig.svc.login("ann@example.com", "pw", NOW).unwrap();
    let n = rig.sent_count();
    let r = run(&rig);
    assert_eq!(r[0].state, "waiting");
    assert!(r[0].message.contains("Join your company"));
    assert_eq!(rig.sent_count(), n);
    let rig = self::rig();
    saved(&rig, NOW, NOW, ORG);
    rig.http.push_net_error("GET", "/get-session");
    assert!(rig.svc.check(NOW).offline);
    let n = rig.sent_count();
    assert_eq!((run(&rig)[0].state.as_str(), rig.sent_count()), ("waiting", n));
    assert!(!dir.join("s.json").exists(), "nothing was marked sent");
}

#[test]
fn the_three_assistants_use_their_own_rows_and_a_busy_sync_returns_the_last_answer() {
    let s = setup();
    let c = scratch();
    let a = scratch();
    write_record(&s.dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    write_record(&c, "company", HASH2, "2026-10-18T10:00:00+00:00", json!({"job_title": "SRE", "company": "Acme"}));
    write_record(&a, "account", HASH1, "2026-10-18T10:00:00+00:00", json!({"company": "Acme", "domain": "acme.com"}));
    for _ in 0..3 {
        s.rig.http.push("POST", UPSERT, 201, "");
    }
    let dirs = vec![(Agent::Candidate, s.dir.clone()), (Agent::Company, c.clone()), (Agent::Account, a.clone())];
    let r = s.sync.sync(&s.rig.svc, &dirs, &s.sent, NOW);
    assert_eq!(r.iter().map(|x| (x.agent.as_str(), x.state.as_str())).collect::<Vec<_>>(), [("candidate", "sent"), ("company", "sent"), ("account", "sent")]);
    let agents: Vec<String> = s.rig.sent().iter().filter(|x| x.path == UPSERT).map(|x| x.body.as_ref().unwrap()["agent"].as_str().unwrap().to_string()).collect();
    assert_eq!(agents, ["candidate", "company", "account"]);
    // while one sync is running a second returns the last answer and sends nothing
    let guard = s.sync.lock.lock().unwrap();
    let before = s.rig.sent_count();
    assert_eq!(s.sync.sync(&s.rig.svc, &dirs, &s.sent, NOW), r);
    assert_eq!(s.rig.sent_count(), before);
    drop(guard);
    assert_eq!(s.sync.results(), r);
}

#[test]
fn the_stored_records_are_listed_for_the_admin_screen() {
    let s = setup();
    let path = "api:/last_records?select=agent,record,subject_hash,created_at,stored_at,stored_by_email&order=agent";
    s.rig.http.push("GET", path, 200, r#"[{"agent":"candidate","record":{"name":"Ann"},"stored_by_email":"a@b.co","stored_at":"2026-10-19T10:00:00Z"}]"#);
    let v = records::stored(&s.rig.svc, NOW).unwrap();
    assert_eq!((v[0]["record"]["name"].as_str(), v[0]["stored_by"].as_str(), v[0].get("stored_by_email")), (Some("Ann"), Some("a@b.co"), None));
    let s = setup();
    s.rig.http.push("GET", path, 503, "{}");
    assert!(records::stored(&s.rig.svc, NOW).unwrap_err().contains("had a problem"));
}

// ---------- search history (history.rs) ----------

use crate::core::RunInput;
use crate::history::{self, iso, HistoryResult, HistorySync};
use crate::runner::EndReason;

const SEARCHES: &str = "api:/searches?on_conflict=id";
const SHARED: &str = "api:/shared_rows?on_conflict=tenant_id,user_id,agent,subject_hash";

struct Hist { rig: Rig, data: Scratch, dir: Scratch, sync: HistorySync }

fn hist() -> Hist {
    let rig = rig();
    rig.login_ok();
    Hist { rig, data: scratch(), dir: scratch(), sync: HistorySync::new() }
}

impl Hist {
    fn run(&self) -> HistoryResult {
        self.sync.sync(&self.rig.svc, &[(Agent::Candidate, self.dir.0.clone())], &self.data, NOW)
    }
    fn queue(&self, lines: &[String]) {
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(self.dir.join("new_rows.jsonl")).unwrap();
        use std::io::Write;
        for l in lines { writeln!(f, "{l}").unwrap(); }
    }
    fn erased(&self, hashes: &[&str]) {
        let body: String = hashes.iter().map(|h| format!("{}\n", json!({"agent": "candidate", "subject_hash": h}))).collect();
        std::fs::write(self.dir.join("erased_rows.jsonl"), body).unwrap();
    }
    fn lines(&self, name: &str) -> Vec<String> {
        std::fs::read_to_string(self.dir.join(name)).map(|s| s.lines().map(str::to_string).collect()).unwrap_or_default()
    }
}

fn row_line(hash: &str, at: i64, record: Value) -> String {
    json!({"agent": "candidate", "subject_hash": hash, "created_at": format!("{}+00:00", iso(at).trim_end_matches('Z')), "record": record}).to_string()
}

fn hsh(c: char) -> String { c.to_string().repeat(64) }

#[test]
fn a_search_is_remembered_stored_and_updated_when_it_ends() {
    let h = hist();
    let id = history::begin(&h.data, Agent::Candidate, &RunInput { jd: "React developer, mail hr@acme.com".into(), ..Default::default() }, NOW);
    assert!(valid_code(&id).is_ok());
    h.rig.http.push("POST", SEARCHES, 201, "");
    let r = h.run();
    assert_eq!((r.state.as_str(), r.searches), ("sent", 1), "{r:?}");
    let sent = h.rig.last_sent();
    assert_eq!((sent.method.as_str(), sent.path.as_str(), sent.prefer.as_deref()), ("POST", SEARCHES, Some("resolution=merge-duplicates,return=minimal")));
    assert!(sent.bearer.is_some());
    let b = sent.body.unwrap();
    assert_eq!((b["id"].as_str(), b["tenant_id"].as_str(), b["agent"].as_str(), b["status"].as_str(), b["started_at"].as_str()), (Some(id.as_str()), Some(ORG), Some("candidate"), Some("running"), Some(iso(NOW).as_str())));
    assert_eq!(b["inputs"], json!({"jd": "React developer, mail [removed]"}), "contact details never leave");
    assert!(b.get("finished_at").is_none());
    let n = h.rig.sent_count();
    assert_eq!(h.run().state, "nothing");
    assert_eq!(h.rig.sent_count(), n, "an unchanged search is not sent again");
    history::finish(&h.data, Agent::Candidate, &EndReason::Finished { ok: true }, NOW + 60);
    h.rig.http.push("POST", SEARCHES, 200, "");
    assert_eq!(h.run().state, "sent");
    let b = h.rig.last_sent().body.unwrap();
    assert_eq!((b["status"].as_str(), b["finished_at"].as_str()), (Some("done"), Some(iso(NOW + 60).as_str())));
}

#[test]
fn how_a_run_ended_is_recorded_and_an_unseen_end_closes_the_old_search() {
    let dir = scratch();
    for (reason, want) in [(EndReason::Finished { ok: true }, "done"), (EndReason::Finished { ok: false }, "failed"), (EndReason::Stopped, "stopped"), (EndReason::Cap, "failed"), (EndReason::Failed("x".into()), "failed")] {
        history::begin(&dir, Agent::Company, &RunInput { company: "Acme".into(), ..Default::default() }, NOW);
        history::finish(&dir, Agent::Company, &reason, NOW + 5);
        let list: Vec<history::Pending> = serde_json::from_str(&std::fs::read_to_string(dir.join(history::PENDING_FILE)).unwrap()).unwrap();
        assert_eq!(list.last().unwrap().status, want);
    }
    history::begin(&dir, Agent::Account, &RunInput { company: "A".into(), ..Default::default() }, NOW);
    history::begin(&dir, Agent::Account, &RunInput { company: "B".into(), ..Default::default() }, NOW + 100);   // the app was closed mid-run
    let list: Vec<history::Pending> = serde_json::from_str(&std::fs::read_to_string(dir.join(history::PENDING_FILE)).unwrap()).unwrap();
    let acc: Vec<_> = list.iter().filter(|p| p.agent == "account").collect();
    assert_eq!((acc[0].status.as_str(), acc[0].finished_at, acc[1].status.as_str()), ("stopped", Some(NOW + 100), "running"));
    // a different assistant's run is not touched
    assert!(list.iter().filter(|p| p.agent == "company").all(|p| p.status != "running"));
}

#[test]
fn queued_rows_are_stored_linked_to_the_search_that_was_running() {
    let h = hist();
    let id = history::begin(&h.data, Agent::Candidate, &RunInput { jd: "React".into(), ..Default::default() }, NOW);
    history::finish(&h.data, Agent::Candidate, &EndReason::Finished { ok: true }, NOW + 100);
    h.queue(&[row_line(&hsh('a'), NOW + 10, json!({"name": "Ann Rao", "title": "Engineer", "match_score": 85, "email": "ann@x.test", "phone": "+91 98765 43210"})),
              row_line(&hsh('b'), NOW + 900, json!({"name": "Late Row"}))]);
    h.rig.http.push("POST", SEARCHES, 201, "");
    h.rig.http.push("POST", SHARED, 201, "");
    let r = h.run();
    assert_eq!((r.state.as_str(), r.searches, r.rows), ("sent", 1, 2), "{r:?}");
    let sent = h.rig.last_sent();
    assert_eq!((sent.path.as_str(), sent.prefer.as_deref()), (SHARED, Some("resolution=ignore-duplicates,return=minimal")));
    let arr = sent.body.unwrap();
    let arr = arr.as_array().unwrap();
    assert_eq!(arr.len(), 2);
    assert_eq!((arr[0]["search_id"].as_str(), arr[0]["tenant_id"].as_str(), arr[0]["agent"].as_str(), arr[0]["subject_hash"].as_str()), (Some(id.as_str()), Some(ORG), Some("candidate"), Some(hsh('a').as_str())));
    assert_eq!(arr[0]["record"], json!({"name": "Ann Rao", "title": "Engineer", "match_score": 85}), "only the listed fields leave");
    assert!(arr[1]["search_id"].is_null(), "a row outside any search's time is not linked");
    assert!(!sent_text(&h).contains("ann@x.test") && !sent_text(&h).contains("98765"));
    assert!(h.lines("new_rows.jsonl").is_empty() && h.lines("new_rows.sending.jsonl").is_empty(), "what was stored is removed from the queue");
}

fn sent_text(h: &Hist) -> String {
    h.rig.sent().iter().map(|s| s.body.as_ref().map(|b| b.to_string()).unwrap_or_default()).collect()
}

#[test]
fn a_damaged_or_foreign_line_is_dropped_and_never_sent() {
    let h = hist();
    let good = row_line(&hsh('c'), NOW, json!({"name": "Ok Person"}));
    let wrong_agent = json!({"agent": "company", "subject_hash": hsh('d'), "created_at": "2026-10-20T10:00:00+00:00", "record": {"job_title": "SRE"}}).to_string();
    let bad_hash = row_line("short", NOW, json!({"name": "X"}));
    let bad_time = json!({"agent": "candidate", "subject_hash": hsh('e'), "created_at": "yesterday", "record": {"name": "X"}}).to_string();
    let nothing_listed = row_line(&hsh('f'), NOW, json!({"email": "a@b.co", "notes": "secret"}));
    h.queue(&[good, wrong_agent, bad_hash, bad_time, nothing_listed, "{not json".into()]);
    h.rig.http.push("POST", SHARED, 201, "");
    let r = h.run();
    assert_eq!((r.state.as_str(), r.rows), ("sent", 1));
    assert_eq!(h.rig.last_sent().body.unwrap().as_array().unwrap().len(), 1);
    assert!(h.lines("new_rows.sending.jsonl").is_empty());
}

#[test]
fn problems_keep_the_rows_for_the_next_sync() {
    for (status, state) in [(429u16, "waiting"), (503, "waiting"), (403, "waiting"), (418, "error")] {
        let h = hist();
        h.queue(&[row_line(&hsh('a'), NOW, json!({"name": "A"})), row_line(&hsh('b'), NOW, json!({"name": "B"}))]);
        h.rig.http.push("POST", SHARED, status, "{}");
        let r = h.run();
        assert_eq!(r.state, state, "{status}");
        assert_eq!(h.lines("new_rows.sending.jsonl").len(), 2, "{status}: nothing is lost");
        h.rig.http.push("POST", SHARED, 201, "");
        assert_eq!(h.run().state, "sent", "retried after {status}");
        assert!(h.lines("new_rows.sending.jsonl").is_empty());
    }
    let h = hist();
    h.queue(&[row_line(&hsh('a'), NOW, json!({"name": "A"}))]);
    h.rig.http.push_net_error("POST", SHARED);
    assert_eq!(h.run().state, "waiting");
    assert_eq!(h.lines("new_rows.sending.jsonl").len(), 1);
}

#[test]
fn a_batch_the_database_refuses_is_sent_row_by_row_and_only_the_refused_row_is_dropped() {
    let h = hist();
    h.queue(&[row_line(&hsh('a'), NOW, json!({"name": "Good"})), row_line(&hsh('b'), NOW, json!({"name": "Refused"})), row_line(&hsh('c'), NOW, json!({"name": "Good Too"}))]);
    h.rig.http.push("POST", SHARED, 400, "{}");   // the whole batch
    h.rig.http.push("POST", SHARED, 201, "");     // row a
    h.rig.http.push("POST", SHARED, 422, "{}");   // row b
    h.rig.http.push("POST", SHARED, 201, "");     // row c
    let r = h.run();
    assert_eq!((r.state.as_str(), r.rows), ("sent", 2), "{r:?}");
    assert!(h.lines("new_rows.sending.jsonl").is_empty(), "a refused row would never be accepted, so it is not kept");
}

#[test]
fn more_than_one_batch_is_sent_for_a_long_queue() {
    let h = hist();
    let lines: Vec<String> = (0..230).map(|i| row_line(&format!("{i:064x}"), NOW, json!({"name": format!("P{i}")}))).collect();
    h.queue(&lines);
    for _ in 0..3 { h.rig.http.push("POST", SHARED, 201, ""); }
    let r = h.run();
    assert_eq!(r.rows, 230);
    let sizes: Vec<usize> = h.rig.sent().iter().filter(|s| s.path == SHARED).map(|s| s.body.as_ref().unwrap().as_array().unwrap().len()).collect();
    assert_eq!(sizes, [100, 100, 30]);
}

#[test]
fn a_working_copy_left_by_a_failed_sync_is_finished_first_and_the_new_queue_waits() {
    let h = hist();
    std::fs::write(h.dir.join("new_rows.sending.jsonl"), row_line(&hsh('a'), NOW, json!({"name": "Left Over"})) + "\n").unwrap();
    h.queue(&[row_line(&hsh('b'), NOW, json!({"name": "Newer"}))]);
    h.rig.http.push("POST", SHARED, 201, "");
    h.run();
    assert_eq!(h.rig.last_sent().body.unwrap()[0]["record"]["name"], "Left Over");
    assert_eq!(h.lines("new_rows.jsonl").len(), 1, "the newer queue is untouched until the next sync");
    h.rig.http.push("POST", SHARED, 201, "");
    h.run();
    assert_eq!(h.rig.last_sent().body.unwrap()[0]["record"]["name"], "Newer");
    assert!(h.lines("new_rows.jsonl").is_empty() && h.lines("new_rows.sending.jsonl").is_empty());
}

#[test]
fn an_erased_person_is_removed_by_hash_everywhere_and_the_tombstone_goes() {
    let h = hist();
    h.erased(&[&hsh('a'), &hsh('a'), &hsh('b')]);
    let del = |c: char| format!("api:/shared_rows?agent=eq.candidate&subject_hash=eq.{}", hsh(c));
    h.rig.http.push("DELETE", &del('a'), 204, "");
    h.rig.http.push("DELETE", &del('b'), 200, "[]");
    let r = h.run();
    assert_eq!((r.state.as_str(), r.removed), ("sent", 2), "{r:?}");
    assert_eq!(h.rig.sent().iter().filter(|s| s.method == "DELETE").count(), 2, "the same hash is removed once");
    assert!(h.lines("erased_rows.jsonl").is_empty() && h.lines("erased_rows.sending.jsonl").is_empty());
    // a problem keeps the rest of the tombstones
    let h = hist();
    h.erased(&[&hsh('a'), &hsh('b')]);
    h.rig.http.push("DELETE", &del('a'), 503, "{}");
    assert_eq!(h.run().state, "waiting");
    assert_eq!(h.lines("erased_rows.sending.jsonl").len(), 2);
    h.rig.http.push("DELETE", &del('a'), 204, "");
    h.rig.http.push("DELETE", &del('b'), 204, "");
    assert_eq!(h.run().removed, 2);
    // damaged or other-assistant tombstones are dropped, not sent
    let h = hist();
    std::fs::write(h.dir.join("erased_rows.jsonl"), format!("{{not json\n{}\n{}\n", json!({"agent": "company", "subject_hash": hsh('c')}), json!({"agent": "candidate", "subject_hash": "short"}))).unwrap();
    let n = h.rig.sent_count();
    assert_eq!(h.run().state, "nothing");
    assert_eq!(h.rig.sent_count(), n);
}

#[test]
fn nothing_is_stored_when_sign_in_is_off_signed_out_without_a_company_or_offline() {
    let dir = scratch();
    let data = scratch();
    std::fs::write(dir.join("new_rows.jsonl"), row_line(&hsh('a'), NOW, json!({"name": "A"})) + "\n").unwrap();
    history::begin(&data, Agent::Candidate, &RunInput { jd: "x".into(), ..Default::default() }, NOW);
    let go = |rig: &Rig| HistorySync::new().sync(&rig.svc, &[(Agent::Candidate, dir.0.clone())], &data, NOW);
    let r = rig_for("");
    assert_eq!((go(&r).state.as_str(), r.sent_count()), ("off", 0));
    let r = rig();
    assert_eq!((go(&r).state.as_str(), r.sent_count()), ("waiting", 0));
    let r = rig();
    r.http.push_cookie("POST", "/sign-in/email", 200, "{}", COOKIE);
    r.script_profile("ann@example.com", None, "");
    r.svc.login("ann@example.com", "pw", NOW).unwrap();
    let n = r.sent_count();
    let res = go(&r);
    assert_eq!(res.state, "waiting");
    assert!(res.message.contains("Join your company"));
    assert_eq!(r.sent_count(), n);
    let r = rig();
    saved(&r, NOW, NOW, ORG);
    r.http.push_net_error("GET", "/get-session");
    r.svc.check(NOW);
    let n = r.sent_count();
    assert_eq!(go(&r).state, "waiting");
    assert_eq!(r.sent_count(), n);
    assert!(dir.join("new_rows.jsonl").exists(), "the queue is untouched");
}

#[test]
fn a_search_the_database_refuses_is_not_retried_forever() {
    let h = hist();
    history::begin(&h.data, Agent::Candidate, &RunInput { jd: "x".into(), ..Default::default() }, NOW);
    h.rig.http.push("POST", SEARCHES, 422, "{}");
    h.run();
    let n = h.rig.sent_count();
    assert_eq!(h.run().state, "nothing");
    assert_eq!(h.rig.sent_count(), n);
}

#[test]
fn the_history_screens_read_searches_rows_and_remove_one() {
    let h = hist();
    let id = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed";
    h.rig.http.push("GET", "api:/searches?select=id,user_email,agent,inputs,started_at,finished_at,status,shared_rows(count)&order=started_at.desc&limit=200", 200,
        &json!([{"id": id, "user_email": "ann@example.com", "agent": "candidate", "inputs": {"jd": "React"}, "started_at": "2026-10-20T10:00:00Z", "finished_at": null, "status": "running", "shared_rows": [{"count": 3}]},
                {"id": "x", "user_email": "bob@example.com", "agent": "company", "inputs": {}, "started_at": "2026-10-19T10:00:00Z", "finished_at": "2026-10-19T10:05:00Z", "status": "done", "shared_rows": []}]).to_string());
    let v = history::list(&h.rig.svc, NOW).unwrap();
    assert_eq!(v[0], json!({"id": id, "who": "ann@example.com", "agent": "candidate", "inputs": {"jd": "React"}, "started_at": "2026-10-20T10:00:00Z", "finished_at": null, "status": "running", "rows": 3}));
    assert_eq!(v[1]["rows"], 0);
    h.rig.http.push("GET", &format!("api:/shared_rows?search_id=eq.{id}&select=agent,record,created_at,user_email&order=created_at.desc&limit=1000"), 200,
        &json!([{"agent": "candidate", "record": {"name": "Ann"}, "created_at": "2026-10-20T10:01:00Z", "user_email": "ann@example.com"}]).to_string());
    assert_eq!(history::rows(&h.rig.svc, id, NOW).unwrap(), json!([{"agent": "candidate", "record": {"name": "Ann"}, "created_at": "2026-10-20T10:01:00Z", "who": "ann@example.com"}]));
    assert!(history::rows(&h.rig.svc, "../../x", NOW).is_err(), "an id that is not a uuid never becomes part of a request");
    assert_eq!(h.rig.sent().iter().filter(|s| s.path.contains("..")).count(), 0);
    h.rig.http.push("DELETE", &format!("api:/searches?id=eq.{id}"), 200, r#"[{"id":"x"}]"#);
    assert!(history::delete(&h.rig.svc, id, NOW).is_ok());
    assert_eq!(h.rig.last_sent().prefer.as_deref(), Some("return=representation"));
    h.rig.http.push("DELETE", &format!("api:/searches?id=eq.{id}"), 200, "[]");
    assert!(history::delete(&h.rig.svc, id, NOW).unwrap_err().contains("no longer exists"), "nothing removed: not yours or already gone");
    h.rig.http.push("GET", "api:/searches?select=id,user_email,agent,inputs,started_at,finished_at,status,shared_rows(count)&order=started_at.desc&limit=200", 503, "{}");
    assert!(history::list(&h.rig.svc, NOW).unwrap_err().contains("had a problem"));
}

// ---------- the allowance: one cap per person over all the assistants (allowance.rs) ----------

use crate::allowance::Allowance;
use crate::cap::Tokens;

const NOW_MS: i64 = NOW * 1000;
const POOL_STATE: &str = "api:/rpc/allowance_state";
const POOL_ADD: &str = "api:/rpc/allowance_add";

/// What the database answers: its clock and the person's own allowance (cap and window of the company, the person's count).
fn pools(server_now_ms: i64, mine: (u64, u32, u64, Option<i64>, Option<i64>)) -> String {
    json!({"now_ms": server_now_ms, "cap": mine.0, "window_minutes": mine.1, "counted": mine.2, "window_started_ms": mine.3, "locked_at_ms": mine.4}).to_string()
}

struct Al { rig: Rig, dir: Scratch, al: Allowance }

fn al(role: &str) -> Al {
    let rig = rig();
    rig.login_as(role);
    let dir = scratch();
    let al = Allowance::new(rig.svc.clone(), dir.0.clone());
    Al { rig, dir, al }
}

type PoolRow = (u64, u32, u64, Option<i64>, Option<i64>);
const FRESH: PoolRow = (3_000_000, 240, 0, None, None);

/// The first successful read of the pool: only then does the shared allowance take charge.
fn read_pool(h: &Al, candidate: PoolRow) {
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, candidate));
    h.al.refresh(NOW_MS).unwrap();
}

fn toks(n: u64) -> Tokens {
    Tokens { input: n, ..Default::default() }
}

#[test]
fn the_pool_is_in_charge_only_while_signed_in_to_a_company_and_once_it_has_been_read() {
    let signed_out = rig();
    let a = Allowance::new(signed_out.svc.clone(), scratch().0.clone());
    assert!(a.view(Agent::Candidate, NOW_MS).is_none());
    assert!(a.add(Agent::Candidate, &toks(10), NOW_MS).is_none(), "the old per-computer allowance counts it");
    let none = Allowance::new(rig_for("").svc.clone(), scratch().0.clone());
    assert!(none.view(Agent::Candidate, NOW_MS).is_none(), "sign-in off: the old allowance");
    let h = al("member");
    assert!(h.al.view(Agent::Candidate, NOW_MS).is_none(), "signed in, but the pool has not been read yet: the old allowance");
    assert!(h.al.add(Agent::Candidate, &toks(10), NOW_MS).is_none());
    read_pool(&h, FRESH);
    assert!(h.al.view(Agent::Candidate, NOW_MS).is_some());
}

#[test]
fn a_database_that_does_not_have_the_allowance_yet_never_locks_anyone() {
    let h = al("member");
    h.rig.http.push("POST", POOL_STATE, 404, r#"{"code":"PGRST202","message":"Could not find the function"}"#);
    assert!(h.al.refresh(NOW_MS).is_err());
    assert!(h.al.view(Agent::Candidate, NOW_MS).is_none());
    assert!(h.al.add(Agent::Candidate, &toks(9_999_999), NOW_MS).is_none(), "the old allowance keeps counting; no phantom pool fills up");
    h.rig.http.push("POST", POOL_STATE, 404, "{}");
    h.al.tick(NOW_MS);   // keeps trying quietly
    assert!(h.al.view(Agent::Candidate, NOW_MS).is_none());
}

#[test]
fn the_view_shows_the_companys_cap_count_and_window_end_on_this_computers_clock() {
    let h = al("member");
    let ahead = 3_600_000;   // the database's clock is an hour ahead of this computer's
    let started = NOW_MS + ahead - 600_000;   // opened 10 minutes ago on the database clock
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS + ahead, (2_000_000, 60, 1_250_000, Some(started), None)));
    h.al.refresh(NOW_MS).unwrap();
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!((v.cap, v.counted, v.locked, v.window_minutes), (2_000_000, 1_250_000, false, 60));
    assert_eq!(v.resets_at, Some(NOW_MS - 600_000 + 3_600_000), "50 minutes from now here, whatever this computer's clock says");
    assert_eq!(h.al.view(Agent::Company, NOW_MS).unwrap().counted, 1_250_000, "the count is the person's, over all the assistants");
    assert_eq!(h.rig.last_sent().body, Some(json!({})));
    assert!(h.rig.last_sent().bearer.is_some());
}

#[test]
fn a_window_that_has_ended_on_the_database_clock_reads_as_empty_and_unlocked() {
    let h = al("member");
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, (1_000, 60, 5_000, Some(NOW_MS - 3_600_000 - 1), Some(NOW_MS - 3_000_000))));
    h.al.refresh(NOW_MS).unwrap();
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!((v.counted, v.locked, v.resets_at), (0, false, None));
    // and a lock inside the window shows as locked until the window's end, for everyone
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, (1_000, 60, 5_000, Some(NOW_MS - 600_000), Some(NOW_MS - 300_000))));
    h.al.refresh(NOW_MS).unwrap();
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!((v.locked, v.locked_at, v.resets_at), (true, Some(NOW_MS - 300_000), Some(NOW_MS + 3_000_000)));
}

#[test]
fn tokens_are_counted_at_once_and_a_full_pool_stops_the_run() {
    let h = al("member");
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, (10_000, 240, 9_000, Some(NOW_MS - 1000), None)));
    h.al.refresh(NOW_MS).unwrap();
    assert_eq!(h.al.add(Agent::Candidate, &toks(500), NOW_MS), Some(true));
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 9_500, "the database count plus what this computer has not sent yet");
    assert_eq!(h.al.add(Agent::Candidate, &Tokens { input: 300, output: 300, cache_creation: 0, cache_read: 9_999_999 }, NOW_MS), Some(false), "cache reads do not count; 600 more fills the pool");
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!((v.locked, v.counted), (true, 10_100));
    assert_eq!(h.al.view(Agent::Company, NOW_MS).unwrap().locked, true, "full for one assistant means full for all of them");
    assert_eq!(h.al.add(Agent::Account, &toks(1), NOW_MS), Some(false), "and every assistant's tokens count in the same total");
}

#[test]
fn what_is_pending_survives_a_restart() {
    let h = al("member");
    read_pool(&h, FRESH);
    h.al.add(Agent::Candidate, &toks(1234), NOW_MS);
    assert!(h.dir.join("allowance_pending.json").exists());
    let again = Allowance::new(h.rig.svc.clone(), h.dir.0.clone());
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, FRESH));
    again.refresh(NOW_MS).unwrap();
    assert_eq!(again.view(Agent::Candidate, NOW_MS).unwrap().counted, 1234);
    assert_eq!(again.view(Agent::Company, NOW_MS).unwrap().counted, 1234, "the same total whichever assistant asks");
}

#[test]
fn spending_is_sent_in_the_background_and_the_answer_updates_the_pool() {
    let h = al("member");
    read_pool(&h, FRESH);
    h.al.add(Agent::Candidate, &toks(700), NOW_MS);
    h.al.add(Agent::Account, &toks(300), NOW_MS);
    assert_eq!(h.al.view(Agent::Company, NOW_MS).unwrap().counted, 1_000, "both assistants' tokens are in one total");
    h.rig.http.push("POST", POOL_ADD, 200, &pools(NOW_MS, (3_000_000, 240, 50_700, Some(NOW_MS - 5000), None)));
    h.rig.http.push("POST", POOL_ADD, 200, &pools(NOW_MS, (3_000_000, 240, 51_000, Some(NOW_MS - 5000), None)));
    h.al.flush(NOW_MS);
    let bodies: Vec<_> = h.rig.sent().iter().filter(|s| s.path == POOL_ADD).map(|s| s.body.clone().unwrap()).collect();
    assert_eq!(bodies, [json!({"p_agent": "candidate", "p_tokens": 700}), json!({"p_agent": "account", "p_tokens": 300})], "the database logs which assistant spent what");
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!(v.counted, 51_000, "the same person's usage on another computer arrives with the answer; nothing is counted twice");
    assert!(!std::fs::read_to_string(h.dir.join("allowance_pending.json")).unwrap().contains("700"));
    let n = h.rig.sent_count();
    h.al.flush(NOW_MS);
    assert_eq!(h.rig.sent_count(), n, "nothing pending, nothing sent");
}

#[test]
fn a_large_amount_is_sent_in_pieces_and_a_problem_keeps_what_was_not_sent() {
    let h = al("member");
    read_pool(&h, FRESH);
    h.al.add(Agent::Candidate, &toks(12_000_000), NOW_MS);
    for _ in 0..3 { h.rig.http.push("POST", POOL_ADD, 200, &pools(NOW_MS, (3_000_000, 240, 1, Some(NOW_MS), None))); }
    h.al.flush(NOW_MS);
    let amounts: Vec<u64> = h.rig.sent().iter().filter(|s| s.path == POOL_ADD).map(|s| s.body.as_ref().unwrap()["p_tokens"].as_u64().unwrap()).collect();
    assert_eq!(amounts, [5_000_000, 5_000_000, 2_000_000]);
    for status in [429u16, 503, 403] {
        let h = al("member");
        read_pool(&h, FRESH);
        h.al.add(Agent::Candidate, &toks(900), NOW_MS);
        h.rig.http.push("POST", POOL_ADD, status, "{}");
        h.al.flush(NOW_MS);
        assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 900, "{status}: kept for the next try");
    }
    let h = al("member");
    read_pool(&h, FRESH);
    h.al.add(Agent::Candidate, &toks(900), NOW_MS);
    h.rig.http.push_net_error("POST", POOL_ADD);
    h.al.flush(NOW_MS);
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 900);
    h.rig.http.push("POST", POOL_ADD, 422, "{}");   // an amount the database refuses is dropped, not sent forever
    h.al.flush(NOW_MS);
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 0);
}

#[test]
fn offline_nothing_is_sent_and_the_count_is_kept() {
    // read while online, then the connection drops
    let h = al("member");
    read_pool(&h, FRESH);
    h.rig.http.push_net_error("GET", "/get-session");
    h.rig.svc.check(NOW + 700);   // past the recheck time, the service cannot be reached: offline for up to a week
    assert!(h.rig.svc.status(NOW + 700).offline);
    let n = h.rig.sent_count();
    assert_eq!(h.al.add(Agent::Candidate, &toks(40), NOW_MS + 700_000), Some(true));
    h.al.tick(NOW_MS + 700_000);
    assert_eq!(h.rig.sent_count(), n, "offline: no request");
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS + 700_000).unwrap().counted, 40, "the last known pool plus what was spent here");
}

#[test]
fn the_tick_sends_first_and_reads_the_pool_about_once_a_minute() {
    let h = al("member");
    // a computer that has not read the pool yet reads it at its first tick
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS, FRESH));
    h.al.tick(NOW_MS);
    assert_eq!(h.rig.http.count("POST", POOL_STATE), 1);
    h.al.add(Agent::Candidate, &toks(100), NOW_MS + 5_000);
    h.rig.http.push("POST", POOL_ADD, 200, &pools(NOW_MS + 20_000, (3_000_000, 240, 100, Some(NOW_MS), None)));
    h.al.tick(NOW_MS + 20_000);   // sends, and the answer to that is also a fresh read of the pool
    assert_eq!((h.rig.http.count("POST", POOL_ADD), h.rig.http.count("POST", POOL_STATE)), (1, 1), "no second read is needed");
    h.al.tick(NOW_MS + 40_000);
    assert_eq!(h.rig.http.count("POST", POOL_STATE), 1, "not due yet");
    h.rig.http.push("POST", POOL_STATE, 200, &pools(NOW_MS + 81_000, (3_000_000, 240, 100, Some(NOW_MS), None)));
    h.al.tick(NOW_MS + 81_000);
    assert_eq!(h.rig.http.count("POST", POOL_STATE), 2, "a minute after the last read");
}

#[test]
fn only_an_admin_changes_a_persons_cap_and_window_and_the_numbers_are_checked_first() {
    let u = al("member");
    let n = u.rig.sent_count();
    assert_eq!(u.al.set_user("bob@example.com", 5_000, 60, NOW_MS).unwrap_err(), "Only an admin can change the allowance.");
    assert_eq!(u.al.reset(None, NOW_MS).unwrap_err(), "Only an admin can change the allowance.");
    assert_eq!(u.rig.sent_count(), n, "a user's change never reaches the database");
    let h = al("owner");
    read_pool(&h, FRESH);
    let n = h.rig.sent_count();
    for (cap, mins) in [(999u64, 60u32), (1_000_000_001, 60), (5_000, 14), (5_000, 1441)] {
        assert!(h.al.set_user("bob@example.com", cap, mins, NOW_MS).unwrap_err().starts_with("Check the numbers"), "{cap} {mins}");
    }
    assert_eq!(h.al.set_user("  ", 5_000, 60, NOW_MS).unwrap_err(), "Say whose allowance to change.");
    assert_eq!(h.rig.sent_count(), n);
    h.al.add(Agent::Company, &toks(55), NOW_MS);
    h.rig.http.push("POST", POOL_ADD, 200, &pools(NOW_MS, (3_000_000, 240, 0, None, None)));   // what was pending goes first
    h.rig.http.push("POST", "api:/rpc/allowance_set_user", 200, &pools(NOW_MS, (2_000_000, 90, 0, None, None)));
    h.al.set_user("ann@example.com", 2_000_000, 90, NOW_MS).unwrap();
    let sent = h.rig.last_sent();
    assert_eq!((sent.path.as_str(), sent.body.unwrap()), ("api:/rpc/allowance_set_user", json!({"p_email": "ann@example.com", "p_cap": 2_000_000, "p_window_minutes": 90})));
    let v = h.al.view(Agent::Candidate, NOW_MS).unwrap();
    assert_eq!((v.cap, v.window_minutes), (2_000_000, 90));
    h.al.add(Agent::Candidate, &toks(77), NOW_MS);
    h.rig.http.push("POST", "api:/rpc/allowance_reset", 200, &pools(NOW_MS, (2_000_000, 90, 0, None, None)));
    h.al.reset(None, NOW_MS).unwrap();
    assert_eq!(h.rig.last_sent().body.unwrap(), json!({}), "no email: everyone");
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 0, "what was spent before the reset is not carried over");
    h.al.add(Agent::Candidate, &toks(11), NOW_MS);
    h.rig.http.push("POST", "api:/rpc/allowance_reset", 200, &pools(NOW_MS, (2_000_000, 90, 0, None, None)));
    h.al.reset(Some("bob@example.com"), NOW_MS).unwrap();
    assert_eq!(h.rig.last_sent().body.unwrap(), json!({"p_email": "bob@example.com"}));
    assert_eq!(h.al.view(Agent::Candidate, NOW_MS).unwrap().counted, 11, "someone else's reset leaves this person's pending tokens alone");
    h.rig.http.push("POST", "api:/rpc/allowance_people", 200, &json!({"now_ms": NOW_MS, "people": [{"email": "bob@example.com", "counted": 5, "window_started_ms": null, "locked_at_ms": null}]}).to_string());
    assert_eq!(h.al.people(NOW_MS).unwrap()["people"][0]["email"], "bob@example.com");
    assert_eq!(u.al.people(NOW_MS).unwrap_err(), "Only an admin can see everyone's allowance.");
    for (status, want) in [(403u16, "Only an admin can change the allowance."), (422, "Check the numbers and the address: the cap is at least 1,000 tokens, the window is between 15 minutes and 24 hours, and the person must be in your company."), (429, "Too many requests. Try again in a moment."), (503, "The service had a problem. Try again in a minute."), (418, "The service gave an answer Recruit Desk does not understand.")] {
        h.rig.http.push("POST", "api:/rpc/allowance_set_user", status, "{}");
        assert_eq!(h.al.set_user("bob@example.com", 5_000, 60, NOW_MS).unwrap_err(), want, "{status}");
    }
}

#[test]
fn the_core_shows_the_persons_allowance_while_signed_in_and_its_own_count_otherwise() {
    let dir = scratch();
    let core = crate::core::Core::new(dir.0.clone());
    let signed_out = rig();
    core.set_shared(Arc::new(Allowance::new(signed_out.svc.clone(), dir.0.clone())));
    assert!(core.statuses().iter().all(|s| !s.shared && s.window_minutes.is_none()), "signed out: the old allowance");
    let h = al("member");
    h.rig.http.push("POST", POOL_STATE, 200, &pools(crate::core::now_ms(), (2_000_000, 60, 1_000_000, Some(crate::core::now_ms() - 60_000), None)));
    let shared = Arc::new(Allowance::new(h.rig.svc.clone(), scratch().0.clone()));
    shared.refresh(crate::core::now_ms()).unwrap();
    core.set_shared(shared);
    let st = core.status(Agent::Candidate);
    assert_eq!((st.shared, st.cap, st.counted, st.window_minutes, st.locked), (true, 2_000_000, 1_000_000, Some(60), false));
    assert!((st.percent - 50.0).abs() < 0.01);
    assert_eq!(core.status(Agent::Company).counted, 1_000_000, "the same total for every assistant");
}

// ---------- against real Neon (cargo test --lib live_ -- --ignored) ----------
// A throwaway Neon BRANCH with Neon Auth, the Data API and neon-backend/setup.sql applied, a trusted origin for the app, and a company
// made with `operator.py create-company`. The test makes its own accounts. Never run it against production.
//   AUTH_LIVE_CODE=<connection code> AUTH_LIVE_OWNER_INVITE=<code printed by create-company> AUTH_LIVE_OWNER_EMAIL=<its address>
//   AUTH_LIVE_MEMBER_EMAIL=<a new address> AUTH_LIVE_PASSWORD='a long test password'

fn live_service(code: &str) -> Arc<AuthService> {
    Arc::new(AuthService::new(Box::new(UreqHttp), Box::new(Arc::new(FakeVault::default())), Box::new(Arc::new(FakeStore::default())), code))
}

/// Neon throttles sign-up and sign-in; a live test waits and tries again rather than failing on it.
fn patient<T>(mut f: impl FnMut() -> Result<T, String>) -> Result<T, String> {
    for _ in 0..8 {
        match f() {
            Err(e) if e == MSG_TOO_MANY => std::thread::sleep(std::time::Duration::from_secs(8)),
            other => return other,
        }
    }
    f()
}

fn settle() {
    std::thread::sleep(std::time::Duration::from_secs(2));
}

#[test]
#[ignore]
fn live_owner_and_member_flow_against_real_neon() {
    let env = |k: &str| std::env::var(k).unwrap_or_else(|_| panic!("set {k}"));
    let (code, owner_code, owner_email, member_email, pw) = (env("AUTH_LIVE_CODE"), env("AUTH_LIVE_OWNER_INVITE"), env("AUTH_LIVE_OWNER_EMAIL"), env("AUTH_LIVE_MEMBER_EMAIL"), env("AUTH_LIVE_PASSWORD"));
    let now = unix_now();
    // the owner makes an account with the operator's code
    let owner = live_service(&code);
    let st = patient(|| owner.create_account(&owner_email, &pw, &owner_code, now)).unwrap();
    assert_eq!(st.state, "signed_in", "{st:?}");
    let u = st.user.unwrap();
    assert_eq!((u.email.as_str(), u.role.as_str()), (owner_email.to_lowercase().as_str(), "admin"));
    assert!(!u.tenant.is_empty());
    // the same code cannot be used again
    let again = live_service(&code);
    assert_eq!(patient(|| again.create_account(&format!("x{owner_email}"), &pw, &owner_code, now)).unwrap().state, "no_company");
    // sign out and in
    owner.logout(now);
    assert_eq!(owner.status(now).state, "signed_out");
    assert_eq!(patient(|| owner.login(&owner_email, "wrong password here", now)).unwrap_err(), MSG_BAD_LOGIN);
    assert_eq!(patient(|| owner.login(&owner_email, &pw, now)).unwrap().state, "signed_in");
    // the owner invites a member, who joins with the code
    let inv = owner.admin(&AdminOp::Invite { email: member_email.clone(), role: "user".into() }, now).unwrap();
    let invite_code = inv["code"].as_str().unwrap().to_string();
    assert!(owner.admin(&AdminOp::Invites, now).unwrap().as_array().unwrap().iter().any(|i| i["email"] == member_email.to_lowercase().as_str()));
    let member = live_service(&code);
    let st = patient(|| member.create_account(&member_email, &pw, &invite_code, now)).unwrap();
    assert_eq!((st.state.as_str(), st.user.as_ref().map(|u| u.role.as_str())), ("signed_in", Some("user")), "{st:?}");
    settle(); // Neon Auth and the Data API can take a moment to agree about who joined
    assert_eq!(member.admin(&AdminOp::Members, now).unwrap_err(), MSG_ADMINS_ONLY);
    let people = owner.admin(&AdminOp::Members, now).unwrap();
    assert_eq!(people.as_array().unwrap().len(), 2);
    // the last record: the member stores one, the owner sees it, an older one is refused, erasure removes it
    let dir = scratch();
    let sent = dir.join("sent.json");
    let sync = RecordsSync::new();
    write_record(&dir, "candidate", HASH1, "2026-10-18T10:00:00+00:00", cand());
    let mut r = sync.sync(&member, &[(Agent::Candidate, dir.0.clone())], &sent, now);
    for _ in 0..10 {
        if r[0].state == "sent" { break; }
        settle();
        r = sync.sync(&member, &[(Agent::Candidate, dir.0.clone())], &sent, now);
    }
    assert_eq!(r[0].state, "sent", "{r:?}");
    let seen = records::stored(&owner, now).unwrap();
    assert_eq!((seen[0]["record"]["name"].as_str(), seen[0]["stored_by"].as_str()), (Some("Ann Rao"), Some(member_email.to_lowercase().as_str())));
    let other = scratch();
    write_record(&other, "candidate", HASH2, "2026-10-17T10:00:00+00:00", json!({"name": "Old One"}));
    let sync2 = RecordsSync::new();
    assert_eq!(sync2.sync(&owner, &[(Agent::Candidate, other.0.clone())], &other.join("sent.json"), now)[0].state, "older");
    assert_eq!(records::stored(&owner, now).unwrap()[0]["record"]["name"], "Ann Rao");
    std::fs::remove_file(dir.join(records::FILE_NAME)).unwrap();
    assert_eq!(sync.sync(&member, &[(Agent::Candidate, dir.0.clone())], &sent, now)[0].state, "removed");
    assert!(records::stored(&owner, now).unwrap().as_array().unwrap().is_empty());
    // the allowance: one cap per person over all the assistants, on the database's clock
    let (ao, am) = (Allowance::new(owner.clone(), scratch().0.clone()), Allowance::new(member.clone(), scratch().0.clone()));
    let mut read = am.refresh(crate::core::now_ms());
    for _ in 0..10 {
        if read.is_ok() { break; }
        settle();
        read = am.refresh(crate::core::now_ms());
    }
    read.unwrap();
    ao.refresh(crate::core::now_ms()).unwrap();
    let v = am.view(Agent::Candidate, crate::core::now_ms()).unwrap();
    assert_eq!((v.cap, v.counted, v.window_minutes, v.locked), (3_000_000, 0, 240, false), "a new company starts with the defaults");
    am.add(Agent::Candidate, &toks(1_000), crate::core::now_ms());
    am.add(Agent::Account, &toks(200), crate::core::now_ms());
    am.flush(crate::core::now_ms());
    am.refresh(crate::core::now_ms()).unwrap();
    assert_eq!(am.view(Agent::Company, crate::core::now_ms()).unwrap().counted, 1_200, "two assistants, one total");
    ao.refresh(crate::core::now_ms()).unwrap();
    assert_eq!(ao.view(Agent::Candidate, crate::core::now_ms()).unwrap().counted, 0, "the owner's own count is separate from the member's");
    ao.add(Agent::Candidate, &toks(300), crate::core::now_ms());
    ao.flush(crate::core::now_ms());
    let alp = ao.people(crate::core::now_ms()).unwrap();
    let count_of = |email: &str| alp["people"].as_array().unwrap().iter().find(|p| p["email"].as_str().map(str::to_lowercase) == Some(email.to_lowercase())).map(|p| p["counted"].as_u64().unwrap());
    assert_eq!((count_of(&member_email), count_of(&owner_email)), (Some(1_200), Some(300)), "the admin sees everyone's count");
    assert_eq!(am.people(crate::core::now_ms()).unwrap_err(), "Only an admin can see everyone's allowance.");
    assert_eq!(am.set_user(&member_email, 5_000, 60, crate::core::now_ms()).unwrap_err(), "Only an admin can change the allowance.");
    ao.set_user(&member_email, 2_000, 30, crate::core::now_ms()).unwrap();
    am.refresh(crate::core::now_ms()).unwrap();
    let v = am.view(Agent::Candidate, crate::core::now_ms()).unwrap();
    assert_eq!((v.cap, v.window_minutes, v.locked), (2_000, 30, false), "the member has their own cap and window");
    ao.refresh(crate::core::now_ms()).unwrap();
    assert_eq!(ao.view(Agent::Candidate, crate::core::now_ms()).unwrap().cap, 3_000_000, "the owner keeps their own");
    assert_eq!(am.add(Agent::Company, &toks(900), crate::core::now_ms()), Some(false), "1,200 + 900 passes 2,000: full at once on this computer");
    am.flush(crate::core::now_ms());
    am.refresh(crate::core::now_ms()).unwrap();
    assert!(am.view(Agent::Account, crate::core::now_ms()).unwrap().locked, "locked for every assistant");
    ao.refresh(crate::core::now_ms()).unwrap();
    assert!(!ao.view(Agent::Candidate, crate::core::now_ms()).unwrap().locked, "the owner, at 300, is not locked by the member's usage");
    ao.reset(Some(&member_email), crate::core::now_ms()).unwrap();
    am.refresh(crate::core::now_ms()).unwrap();
    let v = am.view(Agent::Candidate, crate::core::now_ms()).unwrap();
    assert_eq!((v.locked, v.counted), (false, 0), "an admin's reset empties that person's count");
    ao.reset(None, crate::core::now_ms()).unwrap();
    ao.refresh(crate::core::now_ms()).unwrap();
    assert_eq!(ao.view(Agent::Candidate, crate::core::now_ms()).unwrap().counted, 0, "and everyone's");

    // application logs: a member writes lines, only the admin reads them
    let ld = scratch();
    logs::add(&ld, "event", "info", None, "App started (live test)", json!({"os": "linux"}), crate::core::now_ms());
    logs::add(&ld, "run", "error", Some(Agent::Candidate), "✗ Step 2/6 · live test", json!({}), crate::core::now_ms());
    let mut r = LogSync::new().sync(&member, &ld, now);
    for _ in 0..10 {
        if r.state == "sent" { break; }
        settle();
        r = LogSync::new().sync(&member, &ld, now);
    }
    assert_eq!((r.state.as_str(), r.sent), ("sent", 2), "{r:?}");
    assert_eq!(logs::list(&member, None, None, None, now).unwrap_err(), "Only an admin can read the logs.");
    let seen = logs::list(&owner, Some("run"), Some("error"), Some(&member_email), now).unwrap();
    assert_eq!((seen[0]["message"].as_str(), seen[0]["user_email"].as_str(), seen[0]["agent"].as_str()), (Some("✗ Step 2/6 · live test"), Some(member_email.to_lowercase().as_str()), Some("candidate")));
    assert_eq!(logs::list(&owner, None, None, None, now).unwrap().as_array().unwrap().len(), 2, "the admin reads everyone's lines");

    // search history: each person's own searches and rows; the admin sees everyone's
    let (md, od, mdir, odir) = (scratch(), scratch(), scratch(), scratch());
    history::begin(&md, Agent::Candidate, &RunInput { jd: "React developer in Pune".into(), ..Default::default() }, now);
    history::finish(&md, Agent::Candidate, &EndReason::Finished { ok: true }, now + 30);
    std::fs::write(mdir.join("new_rows.jsonl"), format!("{}\n{}\n", row_line(&hsh('1'), now + 10, json!({"name": "Ann Rao", "title": "Engineer"})), row_line(&hsh('2'), now + 12, json!({"name": "Bob Sen"})))).unwrap();
    history::begin(&od, Agent::Candidate, &RunInput { jd: "Owner's own search".into(), ..Default::default() }, now);
    history::finish(&od, Agent::Candidate, &EndReason::Finished { ok: true }, now + 30);
    std::fs::write(odir.join("new_rows.jsonl"), row_line(&hsh('3'), now + 10, json!({"name": "Owner Find"})) + "\n").unwrap();
    let (hm, ho) = (HistorySync::new(), HistorySync::new());
    let mut r = hm.sync(&member, &[(Agent::Candidate, mdir.0.clone())], &md, now);
    for _ in 0..10 {
        if r.state == "sent" { break; }
        settle();
        r = hm.sync(&member, &[(Agent::Candidate, mdir.0.clone())], &md, now);
    }
    assert_eq!((r.state.as_str(), r.searches, r.rows), ("sent", 1, 2), "{r:?}");
    assert_eq!(ho.sync(&owner, &[(Agent::Candidate, odir.0.clone())], &od, now).state, "sent");
    let mine = history::list(&member, now).unwrap();
    assert_eq!(mine.as_array().unwrap().len(), 1, "a member sees only their own searches: {mine}");
    assert_eq!((mine[0]["who"].as_str(), mine[0]["rows"].as_i64(), mine[0]["status"].as_str()), (Some(member_email.to_lowercase().as_str()), Some(2), Some("done")));
    let everyone = history::list(&owner, now).unwrap();
    assert_eq!(everyone.as_array().unwrap().len(), 2, "the admin sees everyone's: {everyone}");
    let msid = mine[0]["id"].as_str().unwrap().to_string();
    let osid = everyone.as_array().unwrap().iter().find(|x| x["who"] == owner_email.to_lowercase().as_str()).unwrap()["id"].as_str().unwrap().to_string();
    assert_eq!(history::rows(&owner, &msid, now).unwrap().as_array().unwrap().len(), 2, "the admin reads a member's rows, linked to the right search");
    assert!(history::rows(&member, &osid, now).unwrap().as_array().unwrap().is_empty(), "a member cannot read the admin's rows");
    // erasing one person removes their stored rows
    std::fs::write(mdir.join("erased_rows.jsonl"), json!({"agent": "candidate", "subject_hash": hsh('1')}).to_string() + "\n").unwrap();
    assert_eq!(hm.sync(&member, &[(Agent::Candidate, mdir.0.clone())], &md, now).removed, 1);
    assert_eq!(history::rows(&owner, &msid, now).unwrap().as_array().unwrap().len(), 1);
    // a member removes their own search; an admin may remove anyone's
    history::delete(&member, &msid, now).unwrap();
    assert!(history::list(&member, now).unwrap().as_array().unwrap().is_empty());
    assert!(history::delete(&member, &osid, now).is_err(), "a member cannot remove the admin's search");
    history::delete(&owner, &osid, now).unwrap();
    // the owner makes the member an admin and back, then removes them: their access ends at once
    let id = people.as_array().unwrap().iter().find(|p| p["you"] == false).unwrap()["id"].as_str().unwrap().to_string();
    owner.admin(&AdminOp::SetRole { id: id.clone(), role: "admin".into() }, now).unwrap();
    owner.admin(&AdminOp::SetRole { id: id.clone(), role: "user".into() }, now).unwrap();
    owner.admin(&AdminOp::Remove { id: id.clone() }, now).unwrap();
    write_record(&dir, "candidate", HASH1, "2026-10-18T12:00:00+00:00", cand());
    let mut st = member.check_inner(now + RECHECK_SECS + 1, true);
    for _ in 0..10 {
        if st.state == "no_company" { break; }
        settle();
        st = member.check_inner(now + RECHECK_SECS + 1, true);
    }
    assert_eq!((st.state.as_str(), st.message.as_deref()), ("no_company", Some(MSG_REMOVED)));
    assert_eq!(sync.sync(&member, &[(Agent::Candidate, dir.0.clone())], &sent, now)[0].state, "waiting");
    // the removed person cannot be put back by guessing: the old code is spent
    assert!(member.join_company(&invite_code, now).is_err());
}

// ---------- application logs (logs.rs) ----------

use crate::logs::{self, LogSync};

const LOG_WRITE: &str = "api:/rpc/log_write";

fn queued(dir: &Scratch) -> Vec<Value> {
    std::fs::read_to_string(dir.join(logs::FILE)).map(|s| s.lines().map(|l| serde_json::from_str(l).unwrap()).collect()).unwrap_or_default()
}

#[test]
fn a_line_is_queued_without_contact_details_and_cut_to_length() {
    let dir = scratch();
    logs::add(&dir, "event", "info", None, "Could not reach ann@example.com or +91 98765 43210 for Ann", json!({"version": "0.1.0"}), NOW_MS);
    logs::add(&dir, "run", "error", Some(Agent::Candidate), &"x".repeat(900), json!({}), NOW_MS + 1);
    logs::add(&dir, "event", "info", None, "   ", json!({}), NOW_MS + 2);   // nothing to say: nothing queued
    let q = queued(&dir);
    assert_eq!(q.len(), 2);
    assert_eq!(q[0]["message"], "Could not reach [removed] or [removed] for Ann");
    assert_eq!((q[0]["kind"].as_str(), q[0]["level"].as_str(), q[0]["agent"].is_null(), q[0]["at_ms"].as_i64()), (Some("event"), Some("info"), true, Some(NOW_MS)));
    assert_eq!(q[1]["agent"], "candidate");
    assert!(q[1]["message"].as_str().unwrap().chars().count() <= 401);
    for i in 0..600 { logs::add(&dir, "event", "info", None, &format!("line {i}"), json!({}), NOW_MS + 10 + i); }
    assert_eq!(logs::pending(&dir), 500, "a computer offline for very long keeps the newest 500");
    assert_eq!(queued(&dir).last().unwrap()["message"], "line 599");
}

#[test]
fn queued_lines_are_sent_in_batches_while_signed_in_and_wait_otherwise() {
    let signed_out = rig();
    let d0 = scratch();
    logs::add(&d0, "event", "info", None, "App started", json!({}), NOW_MS);
    let r = LogSync::new().sync(&signed_out.svc, &d0, NOW);
    assert_eq!((r.state.as_str(), r.sent, r.pending), ("waiting", 0, 1));
    assert_eq!(signed_out.sent_count(), 0, "nothing is sent signed out");
    let rig = rig();
    rig.login_ok();
    let dir = scratch();
    for i in 0..120 { logs::add(&dir, "run", "info", Some(Agent::Company), &format!("✓ Step {i}"), json!({}), NOW_MS + i); }
    for _ in 0..3 { rig.http.push("POST", LOG_WRITE, 200, "50"); }
    let r = LogSync::new().sync(&rig.svc, &dir, NOW);
    assert_eq!((r.state.as_str(), r.sent, r.pending), ("sent", 120, 0), "{r:?}");
    let sizes: Vec<usize> = rig.sent().iter().filter(|s| s.path == LOG_WRITE).map(|s| s.body.as_ref().unwrap()["p_items"].as_array().unwrap().len()).collect();
    assert_eq!(sizes, [50, 50, 20]);
    let first = rig.sent().iter().find(|s| s.path == LOG_WRITE).unwrap().body.clone().unwrap();
    assert_eq!(first["p_items"][0]["message"], "✓ Step 0");
    assert_eq!(LogSync::new().sync(&rig.svc, &dir, NOW).state, "nothing");
}

#[test]
fn a_bad_moment_keeps_the_lines_and_a_refused_batch_is_dropped() {
    for status in [429u16, 503, 403] {
        let rig = rig();
        rig.login_ok();
        let dir = scratch();
        logs::add(&dir, "event", "info", None, "App started", json!({}), NOW_MS);
        rig.http.push("POST", LOG_WRITE, status, "{}");
        let r = LogSync::new().sync(&rig.svc, &dir, NOW);
        assert_eq!((r.state.as_str(), r.pending), ("waiting", 1), "{status}: kept for the next try");
    }
    let rig = rig();
    rig.login_ok();
    let dir = scratch();
    logs::add(&dir, "event", "info", None, "one", json!({}), NOW_MS);
    rig.http.push_net_error("POST", LOG_WRITE);
    assert_eq!(LogSync::new().sync(&rig.svc, &dir, NOW).pending, 1, "offline: kept");
    rig.http.push("POST", LOG_WRITE, 422, "{}");
    let r = LogSync::new().sync(&rig.svc, &dir, NOW);
    assert_eq!(r.pending, 0, "a batch the database refuses is dropped, not sent forever");
}

#[test]
fn only_an_admin_reads_the_logs_and_the_filters_are_exact() {
    let user = rig();
    user.login_ok();
    assert_eq!(logs::list(&user.svc, None, None, None, NOW).unwrap_err(), "Only an admin can read the logs.");
    let n = user.sent().iter().filter(|s| s.path.contains("app_logs")).count();
    assert_eq!(n, 0, "a user's request never reaches the database");
    let admin = rig();
    admin.login_as("owner");
    admin.http.push("GET", "api:/app_logs?select=id,user_email,kind,level,agent,message,details,at&order=at.desc,id.desc&limit=300&kind=eq.run&level=eq.error&user_email=eq.bob%40example.com", 200, r#"[{"id":1,"user_email":"bob@example.com","kind":"run","level":"error","agent":"company","message":"✗ Step 2/6","details":{},"at":"2026-10-21T10:00:00Z"}]"#);
    let v = logs::list(&admin.svc, Some("run"), Some("error"), Some(" Bob@Example.com "), NOW).unwrap();
    assert_eq!(v[0]["message"], "✗ Step 2/6");
    admin.http.push("GET", "api:/app_logs?select=id,user_email,kind,level,agent,message,details,at&order=at.desc,id.desc&limit=300", 200, "[]");
    assert!(logs::list(&admin.svc, Some("secret"), Some("fatal"), Some(""), NOW).unwrap().as_array().unwrap().is_empty(), "unknown filters are ignored, never put into the address");
    for (status, want) in [(403u16, "Only an admin can read the logs."), (429, "Too many requests. Try again in a moment."), (503, "The service had a problem. Try again in a minute."), (418, "The service gave an answer Recruit Desk does not understand.")] {
        admin.http.push("GET", "api:/app_logs?select=id,user_email,kind,level,agent,message,details,at&order=at.desc,id.desc&limit=300", status, "{}");
        assert_eq!(logs::list(&admin.svc, None, None, None, NOW).unwrap_err(), want, "{status}");
    }
}

// ---------- end to end: the real shipped assistant files, the real data folder, real Neon (cargo test --lib e2e_ -- --ignored) ----------
// Uses the same AUTH_LIVE_* variables as the live test (a fresh throwaway branch with a freshly made company) and `uv`. No paid AI calls: the
// company assistant's own upsert script is run on fixture rows, exactly as a run would, and everything after it is the app's own code.

fn copy_dir(from: &std::path::Path, to: &std::path::Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap() {
        let e = e.unwrap();
        let (src, dst) = (e.path(), to.join(e.file_name()));
        if e.file_type().unwrap().is_dir() { copy_dir(&src, &dst); } else { std::fs::copy(&src, &dst).unwrap(); }
    }
}

#[test]
#[ignore = "needs AUTH_LIVE_* (a throwaway Neon branch), uv and the staged assistants; see the comment above"]
fn e2e_a_run_s_results_stay_in_the_data_folder_and_only_the_people_side_reaches_neon() {
    let env = |k: &str| std::env::var(k).unwrap_or_else(|_| panic!("set {k}"));
    let (code, owner_code, owner_email, member_email, pw) = (env("AUTH_LIVE_CODE"), env("AUTH_LIVE_OWNER_INVITE"), env("AUTH_LIVE_OWNER_EMAIL"), env("AUTH_LIVE_MEMBER_EMAIL"), env("AUTH_LIVE_PASSWORD"));
    let now = unix_now();
    // 1. the installed layout: the shipped assistants, and a Documents folder with the data folder made for them
    let agents = scratch();
    copy_dir(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("windows").join("agents-bundle"), &agents);
    let docs = scratch();
    let mut settings = crate::settings::Settings { agents_root: agents.display().to_string(), ..Default::default() };
    assert!(settings.ensure_data_folders(Some(&docs)));
    for f in ["Find candidates", "Company jobs", "Accounts"] { assert!(docs.join("RecruitDesk-Data").join(f).is_dir(), "{f}"); }
    let company_dir = settings.agent_dir(Agent::Company);
    assert!(company_dir.join("scripts").join("excel_upsert.py").is_file(), "the shipped assistant is there");
    // 2. a run's last step, done by the assistant's own script, told where its file lives (as the app tells it)
    let row = |title: &str, n: u32| json!({"Company Name": "Acme", "Official Website": "https://acme.com/", "Career Page": "https://acme.com/careers", "Job Title": title,
        "Location / Work Mode": "Pune, India", "Source Type": "ATS: Greenhouse", "Job / Post URL": format!("https://boards.greenhouse.io/acme/jobs/{n}"),
        "Date Discovered": "2026-10-22", "Last Checked": "2026-10-22", "Status": "Open", "Description Summary": "Build things, mail jobs@acme.com", "Application URL": format!("https://boards.greenhouse.io/acme/jobs/{n}")});
    std::fs::create_dir_all(company_dir.join("runs")).unwrap();
    let rows = company_dir.join("runs").join("e2e-rows.json");
    std::fs::write(&rows, json!({"meta": {"company": "Acme", "domain": "acme.com"}, "rows": [row("Backend Engineer", 1), row("SRE", 2)]}).to_string()).unwrap();
    let out = std::process::Command::new("uv").args(["run", "python", "scripts/excel_upsert.py", "--rows", "runs/e2e-rows.json"]).current_dir(&company_dir)
        .env("RECRUIT_DESK_WORKBOOK_DIR", settings.workbook_dir(Agent::Company)).env("RECRUIT_DESK_HANDOFF_DIR", &company_dir).env("RECRUIT_DESK_DATA_ROOT", docs.join("RecruitDesk-Data")).output().unwrap();
    assert!(out.status.success(), "the assistant's script failed:\n{}\n{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    // 3. the results are in the data folder and nowhere else
    let wb = settings.workbook_path(Agent::Company);
    assert!(wb.is_file(), "the Excel file is in the Company jobs folder: {}", wb.display());
    assert!(!company_dir.join(Agent::Company.workbook()).exists(), "and not in the assistant's own folder");
    let sheet = crate::excel::read_sheet(&wb, Some("Acme")).unwrap();
    assert_eq!((sheet.sheets.contains(&"Acme".to_string()), sheet.rows.len()), (true, 2), "the window reads both jobs from the data folder: {:?}", sheet.sheets);
    assert!(sheet.headers.iter().any(|h| h == "Job Title") && sheet.rows.iter().any(|r| r.iter().any(|c| c == "SRE")));
    assert!(company_dir.join("last_record.json").is_file() && company_dir.join("new_rows.jsonl").is_file(), "the hand-off files for the history and the last record");
    // 4. two people on a real database: a member (who ran it) and an owner
    let owner = live_service(&code);
    assert_eq!(patient(|| owner.create_account(&owner_email, &pw, &owner_code, now)).unwrap().state, "signed_in");
    let inv = owner.admin(&AdminOp::Invite { email: member_email.clone(), role: "user".into() }, now).unwrap();
    let invite_code = inv["code"].as_str().unwrap().to_string();
    let member = live_service(&code);
    assert_eq!(patient(|| member.create_account(&member_email, &pw, &invite_code, now)).unwrap().state, "signed_in");
    settle();
    // 5. the run's search, its result rows and its last record reach the history and the records
    let data = scratch();
    history::begin(&data, Agent::Company, &RunInput { company: "Acme".into(), domain: "acme.com".into(), ..Default::default() }, now);
    history::finish(&data, Agent::Company, &EndReason::Finished { ok: true }, now + 30);
    let hs = HistorySync::new();
    let mut r = hs.sync(&member, &[(Agent::Company, company_dir.clone())], &data, now);
    for _ in 0..10 { if r.state == "sent" { break; } settle(); r = hs.sync(&member, &[(Agent::Company, company_dir.clone())], &data, now); }
    assert_eq!((r.state.as_str(), r.searches, r.rows), ("sent", 1, 2), "{r:?}");
    let rs = RecordsSync::new();
    let mut rr = rs.sync(&member, &[(Agent::Company, company_dir.clone())], &data.join("sent.json"), now);
    for _ in 0..10 { if rr[0].state == "sent" { break; } settle(); rr = rs.sync(&member, &[(Agent::Company, company_dir.clone())], &data.join("sent.json"), now); }
    assert_eq!(rr[0].state, "sent", "{rr:?}");
    let seen = history::list(&owner, now).unwrap();
    assert_eq!((seen.as_array().unwrap().len(), seen[0]["who"].as_str(), seen[0]["rows"].as_i64()), (1, Some(member_email.to_lowercase().as_str()), Some(2)), "the admin sees the member's search");
    let stored_rows = history::rows(&owner, seen[0]["id"].as_str().unwrap(), now).unwrap();
    assert!(stored_rows.as_array().unwrap().iter().any(|r| r["record"]["job_title"] == "SRE"), "a fixed set of fields per result: {stored_rows}");
    let all_stored = serde_json::to_string(&stored_rows).unwrap();
    assert!(!all_stored.contains("jobs@acme.com") && !all_stored.contains("Build things"), "no description, no contact details");
    assert_eq!(records::stored(&owner, now).unwrap()[0]["record"]["job_title"], "SRE");
    // 6. the allowance: the run's tokens count for the member only, the admin sets the member's own cap
    let (ao, am) = (Allowance::new(owner.clone(), scratch().0.clone()), Allowance::new(member.clone(), scratch().0.clone()));
    let mut read = am.refresh(crate::core::now_ms());
    for _ in 0..10 { if read.is_ok() { break; } settle(); read = am.refresh(crate::core::now_ms()); }
    read.unwrap();
    am.add(Agent::Company, &toks(4_000), crate::core::now_ms());
    am.flush(crate::core::now_ms());
    ao.refresh(crate::core::now_ms()).unwrap();
    assert_eq!(ao.view(Agent::Company, crate::core::now_ms()).unwrap().counted, 0, "the owner's count is the owner's");
    ao.set_user(&member_email, 5_000, 60, crate::core::now_ms()).unwrap();
    am.refresh(crate::core::now_ms()).unwrap();
    let v = am.view(Agent::Company, crate::core::now_ms()).unwrap();
    assert_eq!((v.cap, v.window_minutes, v.counted, v.locked), (5_000, 60, 4_000, false));
    assert_eq!(am.add(Agent::Candidate, &toks(1_500), crate::core::now_ms()), Some(false), "4,000 + 1,500 passes the member's own 5,000: locked for every assistant");
    // 7. the logs of the run reach the admin, and only the admin
    let ld = scratch();
    logs::add(&ld, "run", "info", Some(Agent::Company), "✓ Step 6/6 · 2 jobs written", json!({}), crate::core::now_ms());
    logs::add(&ld, "run", "info", Some(Agent::Company), "Run finished", json!({"counted_tokens": 4000}), crate::core::now_ms());
    let mut lr = LogSync::new().sync(&member, &ld, now);
    for _ in 0..10 { if lr.state == "sent" { break; } settle(); lr = LogSync::new().sync(&member, &ld, now); }
    assert_eq!(lr.sent, 2, "{lr:?}");
    assert_eq!(logs::list(&member, None, None, None, now).unwrap_err(), "Only an admin can read the logs.");
    assert!(logs::list(&owner, Some("run"), None, Some(&member_email), now).unwrap().as_array().unwrap().iter().any(|l| l["message"] == "Run finished"));
    // 8. the Excel results are not in Neon at all: there is no table to put them in
    let gone = owner.data_request("GET", "workbook_rows?select=id&limit=1", None, None, now).unwrap();
    assert!(matches!(gone.status, 404 | 400), "no workbook table: {} {}", gone.status, gone.body);
    // 9. the file never left: still exactly what the assistant wrote, readable by the window
    assert_eq!(crate::excel::read_sheet(&wb, Some("Acme")).unwrap().rows.len(), 2);
}
