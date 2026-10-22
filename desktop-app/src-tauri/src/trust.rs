// Author: kaustubh.s.v
//! Marks the agent folders as trusted in Claude Code, the same as answering "Yes, proceed"
//! to its "Do you trust the files in this folder?" question (and as AI-AGENTS/trust-folders.py).
//!
//! It sets projects[<folder>].hasTrustDialogAccepted = true in ~/.claude.json (or
//! $CLAUDE_CONFIG_DIR/.claude.json). That file belongs to Claude Code, so: the file is never
//! created, a backup is written first, the write is atomic, and nothing else in it changes
//! (key order is kept through serde_json's preserve_order feature).

use serde::Serialize;
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

/// Claude Code's config file: $CLAUDE_CONFIG_DIR/.claude.json, else ~/.claude.json.
pub fn config_path() -> Option<PathBuf> {
    if let Some(d) = std::env::var_os("CLAUDE_CONFIG_DIR") {
        return Some(PathBuf::from(d).join(".claude.json"));
    }
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(|h| PathBuf::from(h).join(".claude.json"))
}

/// Claude Code keys projects by absolute path with forward slashes, on Windows too.
pub fn config_key(dir: &Path) -> String {
    let p = std::fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    let s = p.display().to_string();
    // Windows canonical paths carry a \\?\ prefix that Claude Code does not use.
    let s = if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else {
        s.strip_prefix(r"\\?\").map(str::to_string).unwrap_or(s)
    };
    normalize(&s)
}

fn normalize(s: &str) -> String {
    let s = s.replace('\\', "/");
    let t = s.trim_end_matches('/');
    if t.is_empty() { s } else { t.to_string() }
}

/// Whether a projects key names the same folder, ignoring slash direction and case.
pub fn key_matches(existing: &str, key: &str) -> bool {
    normalize(existing).eq_ignore_ascii_case(&normalize(key))
}

fn is_trusted(v: &Value) -> bool {
    v.get("hasTrustDialogAccepted").and_then(Value::as_bool) == Some(true)
}

/// Whether `projects` in the parsed config marks `key` as trusted.
pub fn trusted_in(cfg: &Value, key: &str) -> bool {
    cfg.get("projects")
        .and_then(Value::as_object)
        .map(|p| p.iter().any(|(k, v)| key_matches(k, key) && is_trusted(v)))
        .unwrap_or(false)
}

#[derive(Debug, Default, Serialize, PartialEq)]
pub struct TrustOutcome {
    /// Folders newly marked as trusted.
    pub changed: Vec<String>,
    /// Folders that were already trusted.
    pub already: Vec<String>,
    /// The backup of the previous file, when the file was written.
    pub backup: Option<String>,
}

/// Mark each folder in `keys` (already in config_key form) as trusted in the config at `cfg`.
pub fn trust_keys(cfg: &Path, keys: &[String]) -> Result<TrustOutcome, String> {
    if !cfg.is_file() {
        return Err(format!(
            "Claude Code's settings file ({}) does not exist yet. Sign in to Claude Code first, then try again.",
            cfg.display()
        ));
    }
    let text = std::fs::read_to_string(cfg).map_err(|e| format!("Could not read {}: {e}. Nothing was changed.", cfg.display()))?;
    let mut data: Value =
        serde_json::from_str(&text).map_err(|e| format!("Could not read {}: {e}. Nothing was changed.", cfg.display()))?;
    let root = data
        .as_object_mut()
        .ok_or_else(|| format!("{} is not in the expected format. Nothing was changed.", cfg.display()))?;
    let projects = root
        .entry("projects")
        .or_insert_with(|| Value::Object(Map::new()))
        .as_object_mut()
        .ok_or_else(|| format!("The projects list in {} is not in the expected format. Nothing was changed.", cfg.display()))?;

    let mut out = TrustOutcome::default();
    for key in keys {
        let name = projects.keys().find(|k| key_matches(k, key)).cloned().unwrap_or_else(|| key.clone());
        let entry = projects.entry(name).or_insert_with(|| Value::Object(Map::new()));
        if is_trusted(entry) {
            out.already.push(key.clone());
            continue;
        }
        entry
            .as_object_mut()
            .ok_or_else(|| format!("The entry for {key} in {} is not in the expected format. Nothing was changed.", cfg.display()))?
            .insert("hasTrustDialogAccepted".into(), Value::Bool(true));
        out.changed.push(key.clone());
    }
    if out.changed.is_empty() {
        return Ok(out);
    }

    let mut body = serde_json::to_string_pretty(&data).map_err(|e| e.to_string())?;
    if text.ends_with('\n') {
        body.push('\n');
    }
    let backup = backup_path(cfg);
    std::fs::copy(cfg, &backup).map_err(|e| format!("Could not back up {}: {e}. Nothing was changed.", cfg.display()))?;
    let tmp = cfg.with_file_name(format!(".claude.json.trust-{}.tmp", std::process::id()));
    let write = || -> std::io::Result<()> {
        std::fs::write(&tmp, body.as_bytes())?;
        // Keep the original file's permissions (it is private on Linux and macOS).
        std::fs::set_permissions(&tmp, std::fs::metadata(cfg)?.permissions())?;
        std::fs::rename(&tmp, cfg)
    };
    if let Err(e) = write() {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("Could not save {}: {e}. Nothing was changed.", cfg.display()));
    }
    out.backup = Some(backup.display().to_string());
    Ok(out)
}

/// Trust these folders in Claude Code's own config file.
pub fn trust_folders(dirs: &[PathBuf]) -> Result<TrustOutcome, String> {
    let cfg = config_path().ok_or("Could not find your home folder.")?;
    let keys: Vec<String> = dirs.iter().map(|d| config_key(d)).collect();
    trust_keys(&cfg, &keys)
}

/// <file>.bak-trust-<YYYYMMDD-HHMMSS> (UTC), with a number added if that name is taken.
fn backup_path(cfg: &Path) -> PathBuf {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (y, m, d) = civil_from_days(secs.div_euclid(86_400));
    let t = secs.rem_euclid(86_400);
    let stamp = format!("{y:04}{m:02}{d:02}-{:02}{:02}{:02}", t / 3600, t % 3600 / 60, t % 60);
    let name = cfg.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| ".claude.json".into());
    let mut p = cfg.with_file_name(format!("{name}.bak-trust-{stamp}"));
    let mut n = 1;
    while p.exists() {
        p = cfg.with_file_name(format!("{name}.bak-trust-{stamp}-{n}"));
        n += 1;
    }
    p
}

/// Days since 1970-01-01 to a calendar date (Howard Hinnant's algorithm).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("recruit-desk-trust-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn backups(dir: &Path) -> usize {
        std::fs::read_dir(dir).unwrap().filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().contains(".bak-trust-")).count()
    }

    const A: &str = "/home/u/AI-AGENTS/candidate-agent";
    const B: &str = "/home/u/AI-AGENTS/company-agent";

    #[test]
    fn adds_new_entries_and_keeps_everything_else() {
        let d = tmpdir("new");
        let cfg = d.join(".claude.json");
        let orig = "{\n  \"zeta\": 1,\n  \"numStartups\": 42,\n  \"projects\": {\n    \"/other\": {\n      \"allowedTools\": [],\n      \"hasTrustDialogAccepted\": false\n    }\n  },\n  \"alpha\": \"é\"\n}";
        std::fs::write(&cfg, orig).unwrap();
        let out = trust_keys(&cfg, &[A.into(), B.into()]).unwrap();
        assert_eq!(out.changed, vec![A.to_string(), B.to_string()]);
        assert!(out.backup.is_some());
        assert_eq!(std::fs::read_to_string(out.backup.unwrap()).unwrap(), orig);
        let text = std::fs::read_to_string(&cfg).unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        assert!(trusted_in(&v, A) && trusted_in(&v, B));
        assert!(!trusted_in(&v, "/other"));
        // Top-level key order and other values are unchanged.
        let keys: Vec<&String> = v.as_object().unwrap().keys().collect();
        assert_eq!(keys, ["zeta", "numStartups", "projects", "alpha"]);
        assert_eq!(v["alpha"], "é");
        assert_eq!(v["projects"]["/other"]["allowedTools"], Value::Array(vec![]));
        assert!(!text.ends_with('\n'));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn reuses_an_existing_entry_and_its_fields() {
        let d = tmpdir("existing");
        let cfg = d.join(".claude.json");
        std::fs::write(&cfg, format!("{{\"projects\":{{\"{A}\":{{\"allowedTools\":[\"Bash\"],\"lastCost\":1.5}}}}}}\n")).unwrap();
        let out = trust_keys(&cfg, &[A.into()]).unwrap();
        assert_eq!(out.changed, vec![A.to_string()]);
        let text = std::fs::read_to_string(&cfg).unwrap();
        assert!(text.ends_with('\n'));
        let v: Value = serde_json::from_str(&text).unwrap();
        let p = v["projects"].as_object().unwrap();
        assert_eq!(p.len(), 1);
        let e = &p[A];
        assert_eq!(e["allowedTools"][0], "Bash");
        assert_eq!(e["lastCost"], 1.5);
        assert_eq!(e["hasTrustDialogAccepted"], true);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn already_trusted_means_no_write_and_no_backup() {
        let d = tmpdir("already");
        let cfg = d.join(".claude.json");
        let orig = format!("{{\"projects\": {{\"{A}\": {{\"hasTrustDialogAccepted\": true}}, \"{B}\": {{\"hasTrustDialogAccepted\": true}}}}}}");
        std::fs::write(&cfg, &orig).unwrap();
        let before = std::fs::metadata(&cfg).unwrap().modified().unwrap();
        let out = trust_keys(&cfg, &[A.into(), B.into()]).unwrap();
        assert!(out.changed.is_empty());
        assert_eq!(out.already.len(), 2);
        assert_eq!(out.backup, None);
        assert_eq!(std::fs::read_to_string(&cfg).unwrap(), orig);
        assert_eq!(std::fs::metadata(&cfg).unwrap().modified().unwrap(), before);
        assert_eq!(backups(&d), 0);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn missing_file_is_never_created() {
        let d = tmpdir("missing");
        let cfg = d.join(".claude.json");
        let err = trust_keys(&cfg, &[A.into()]).unwrap_err();
        assert!(err.contains("Sign in to Claude Code first"), "{err}");
        assert!(!cfg.exists());
        assert_eq!(std::fs::read_dir(&d).unwrap().count(), 0);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn windows_backslash_and_case_variants_match_the_existing_entry() {
        let d = tmpdir("windows");
        let cfg = d.join(".claude.json");
        std::fs::write(
            &cfg,
            r#"{"projects":{"c:\\Users\\Ana\\AI-AGENTS\\Candidate-Agent":{"allowedTools":[]},"C:/USERS/ana/ai-agents/company-agent/":{"hasTrustDialogAccepted":true}}}"#,
        )
        .unwrap();
        let ka = "C:/Users/Ana/AI-AGENTS/candidate-agent".to_string();
        let kb = "C:/Users/Ana/AI-AGENTS/company-agent".to_string();
        let out = trust_keys(&cfg, &[ka.clone(), kb.clone()]).unwrap();
        assert_eq!(out.changed, vec![ka.clone()]);
        assert_eq!(out.already, vec![kb.clone()]);
        let v: Value = serde_json::from_str(&std::fs::read_to_string(&cfg).unwrap()).unwrap();
        let p = v["projects"].as_object().unwrap();
        assert_eq!(p.len(), 2, "no duplicate entry was added");
        assert_eq!(p[r"c:\Users\Ana\AI-AGENTS\Candidate-Agent"]["hasTrustDialogAccepted"], true);
        assert!(trusted_in(&v, &ka) && trusted_in(&v, &kb));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn invalid_json_is_left_alone() {
        let d = tmpdir("invalid");
        let cfg = d.join(".claude.json");
        std::fs::write(&cfg, "{not json").unwrap();
        assert!(trust_keys(&cfg, &[A.into()]).is_err());
        assert_eq!(std::fs::read_to_string(&cfg).unwrap(), "{not json");
        assert_eq!(backups(&d), 0);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn config_key_uses_forward_slashes() {
        assert_eq!(normalize(r"C:\Users\Ana\AI-AGENTS\company-agent\"), "C:/Users/Ana/AI-AGENTS/company-agent");
        let d = tmpdir("key");
        let k = config_key(&d);
        assert!(!k.contains('\\') && !k.ends_with('/'));
        assert!(key_matches(&k.to_uppercase(), &k));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn backup_name_has_a_date_stamp() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(20_741), (2026, 10, 15));
        let n = backup_path(Path::new("/x/.claude.json")).file_name().unwrap().to_string_lossy().to_string();
        assert!(n.starts_with(".claude.json.bak-trust-20") && n.len() == ".claude.json.bak-trust-YYYYMMDD-HHMMSS".len(), "{n}");
    }
}
