// Author: kaustubh.s.v
//! Static checks on the front end in src/: the credit, the lock dialog's
//! keyboard handling, the CSP-safe assets (nothing loaded from the network),
//! reduced motion, and the plain-language step results.

const HTML: &str = include_str!("../../src/index.html");
const CSS: &str = include_str!("../../src/styles.css");
const MAIN: &str = include_str!("../../src/main.ts");
const PLAIN: &str = include_str!("../../src/plain.ts");

#[test]
fn credit_in_sidebar_and_about() {
    assert_eq!(HTML.matches("Built by kaustubh.s.v").count(), 2, "sidebar and About must both carry the credit");
    for src in [HTML, CSS, MAIN, PLAIN] {
        assert!(src.contains("Author: kaustubh.s.v"));
    }
}

#[test]
fn nothing_loaded_from_the_network() {
    for src in [HTML, CSS] {
        assert!(!src.contains("http://") && !src.contains("@import") && !src.contains("fonts.googleapis"), "the CSP is 'self'");
    }
    assert!(!HTML.contains("<script src=\"http"));
}

#[test]
fn lock_dialog_and_overlays_are_keyboard_safe() {
    assert!(HTML.contains("class=\"lock-overlay hidden\" role=\"dialog\" aria-modal=\"true\""));
    assert!(MAIN.contains("e.key === \"Escape\") { e.preventDefault(); dismissLock(v); }"), "Esc must close the lock overlay");
    assert!(MAIN.contains("toggleAttribute(\"inert\""), "content behind the lock overlay must be inert");
    assert!(CSS.contains("@media (prefers-reduced-motion: reduce)"));
    assert!(CSS.contains(":focus-visible"));
}

#[test]
fn big_table_is_virtualised_and_steps_are_plain() {
    assert!(MAIN.contains("aria-rowcount") && MAIN.contains("aria-rowindex"), "virtual rows must announce the real size");
    assert!(MAIN.contains("const ROW_H = 40;") && CSS.contains("table.sheet td { height: 40px;"), "row height must match the CSS");
    assert!(MAIN.contains("plainResult(v.key, i + 1, m[4])"), "step results go through the plain-language translator");
}

#[test]
fn accounts_is_the_third_section() {
    assert!(HTML.contains("data-view=\"account\" aria-keyshortcuts=\"Control+3\""), "Accounts tab with Ctrl+3");
    assert!(HTML.contains("data-view=\"settings\" aria-keyshortcuts=\"Control+4\""), "Settings moves to Ctrl+4");
    assert!(HTML.contains("<div id=\"view-account\" class=\"view hidden\"></div>"));
    assert!(MAIN.contains("views.account = buildView(\"account\")"));
    assert!(MAIN.contains("Load a CSV of companies"), "batch mode from a CSV");
    assert!(MAIN.contains("Trust the agent folders") && !MAIN.contains("Trust both folders"), "one trust button for all three folders");
    assert!(MAIN.contains("trust-account"));
    assert!(PLAIN.contains("\"candidate\" | \"company\" | \"account\""), "plain-language results know the account agent");
    // The allowance is fixed per agent and never shown as a setting.
    assert!(!HTML.contains("set-cap") && !MAIN.contains("set-cap"));
}

#[test]
fn not_found_cells_and_no_side_stripes() {
    // 0.6.2: an account cell the agent could not answer says what it checked; the table shows it muted.
    assert!(MAIN.contains("function notFound(") && MAIN.contains("(Not found|No evidence found) \\(checked:"));
    assert!(PLAIN.contains("not found after searching the free sources"));
    // No coloured side stripe accents anywhere (a left or right border thicker than the others).
    for rule in CSS.split('}') {
        let r = rule.replace(' ', "");
        for side in ["border-left:", "border-right:", "border-inline-start:", "border-inline-end:"] {
            if let Some(i) = r.find(side) {
                let w: String = r[i + side.len()..].chars().take_while(|c| c.is_ascii_digit()).collect();
                assert!(w.parse::<u32>().unwrap_or(0) < 3, "side stripe in CSS rule: {}", rule.trim());
            }
        }
    }
}

#[test]
fn the_window_cannot_change_the_allowance_or_the_sign_in_server() {
    use crate::settings::{Agent, Settings};
    let mut stored = Settings::default();
    stored.auth_server = "https://auth.example.com".into();
    stored.caps.insert(Agent::Candidate, 123);
    // what the settings screen sends: its own fields only, the rest at their defaults
    let mut sent = Settings::default();
    sent.agents_root = "/new/root".into();
    sent.claude_path = "/new/claude".into();
    sent.auth_server = String::new();
    sent.caps.insert(Agent::Candidate, 999_999_999);
    let kept = sent.keep_owner_fields(&stored);
    assert_eq!((kept.agents_root.as_str(), kept.claude_path.as_str()), ("/new/root", "/new/claude"));
    assert_eq!(kept.auth_server, "https://auth.example.com");
    assert_eq!(kept.caps.get(&Agent::Candidate), Some(&123));
}

#[test]
fn old_settings_files_without_a_sign_in_server_still_load() {
    let s: crate::settings::Settings = serde_json::from_str(r#"{"agents_root":"/x","claude_path":""}"#).unwrap();
    assert_eq!(s.auth_server, "");
    assert_eq!(s.agents_root, "/x");
}
