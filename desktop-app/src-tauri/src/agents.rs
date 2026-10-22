// Author: kaustubh.s.v
//! The installed assistant folders: where the Windows installer puts them, the
//! Exa key in their .env files, and preparing their Python packages.
//! Key values are never logged or returned; only "set" or "not set" is.

use crate::settings::{find_program, is_agents_root, Agent};
use std::path::{Path, PathBuf};

/// The file the installer writes next to the exe, holding the assistants folder.
pub const MARKER: &str = "agents-root.txt";
pub const EXA_KEY: &str = "EXA_API_KEY";
/// Optional paid Apollo.io key; only account-agent uses it.
pub const APOLLO_KEY: &str = "APOLLO_API_KEY";

/// The installer's default: %USERPROFILE%\Documents\Recruit Desk\AI-AGENTS.
pub fn default_root(home: &Path) -> PathBuf {
    home.join("Documents").join("Recruit Desk").join("AI-AGENTS")
}

/// The folder named in `<exe dir>/agents-root.txt`, if it holds every assistant.
pub fn marker_root(exe_dir: &Path) -> Option<PathBuf> {
    let raw = decode(&std::fs::read(exe_dir.join(MARKER)).ok()?);
    let p = PathBuf::from(raw.trim().trim_start_matches('\u{feff}'));
    is_agents_root(&p).then_some(p)
}

/// The installer writes UTF-16LE with a BOM (any user name works); accept UTF-8 too.
fn decode(b: &[u8]) -> String {
    if b.len() >= 2 && b[0] == 0xFF && b[1] == 0xFE {
        let u: Vec<u16> = b[2..].chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        String::from_utf16_lossy(&u)
    } else {
        String::from_utf8_lossy(b).into_owned()
    }
}

/// The installed folder: the installer's marker first, then the default path.
pub fn installed_root(exe_dir: Option<&Path>, home: Option<&Path>) -> Option<PathBuf> {
    exe_dir
        .and_then(marker_root)
        .or_else(|| home.map(default_root).filter(|p| is_agents_root(p)))
}

/// Whether `key` has a non-empty value in `dir/.env`.
pub fn env_has(dir: &Path, key: &str) -> bool {
    std::fs::read_to_string(dir.join(".env"))
        .map(|s| {
            s.lines().any(|l| {
                l.trim_start()
                    .strip_prefix(key)
                    .and_then(|r| r.trim_start().strip_prefix('='))
                    .map(|v| !v.trim().trim_matches(|c| c == '"' || c == '\'').is_empty())
                    .unwrap_or(false)
            })
        })
        .unwrap_or(false)
}

/// The text of `base` with `key=value` set: an existing `key=` line is replaced,
/// otherwise the line is appended. Every other line is kept unchanged.
pub fn with_key(base: &str, key: &str, value: &str) -> String {
    let nl = if base.contains("\r\n") { "\r\n" } else { "\n" };
    let mut done = false;
    let mut out: Vec<String> = base
        .lines()
        .map(|l| {
            let is_key = l.trim_start().strip_prefix(key).map(|r| r.trim_start().starts_with('=')).unwrap_or(false);
            if is_key && !done {
                done = true;
                format!("{key}={value}")
            } else {
                l.to_string()
            }
        })
        .collect();
    if !done {
        out.push(format!("{key}={value}"));
    }
    out.join(nl) + nl
}

/// Set `key` in `dir/.env`, starting from .env.example when there is no .env.
/// Written through a temp file and a rename, so .env is never left half-written.
pub fn set_env_key(dir: &Path, key: &str, value: &str) -> Result<(), String> {
    let value = value.trim();
    if value.is_empty() || value.chars().any(|c| c.is_whitespace() || c == '"' || c == '\'' || c == '#') {
        return Err(if key == APOLLO_KEY {
            "That does not look like an Apollo API key. Copy it again from Apollo (Settings, Integrations, API).".into()
        } else {
            "That does not look like an Exa key. Copy it again from the Exa dashboard.".into()
        });
    }
    let env = dir.join(".env");
    let base = std::fs::read_to_string(&env)
        .or_else(|_| std::fs::read_to_string(dir.join(".env.example")))
        .unwrap_or_default();
    let tmp = dir.join(".env.rdnew");
    std::fs::write(&tmp, with_key(&base, key, value)).map_err(|e| format!("Could not write in {}: {e}", dir.display()))?;
    std::fs::rename(&tmp, &env).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("Could not save {}: {e}", env.display())
    })
}

/// Save the Exa key for every assistant under `root`.
pub fn save_exa_key(root: &Path, value: &str) -> Result<(), String> {
    for a in Agent::ALL {
        set_env_key(&root.join(a.folder()), EXA_KEY, value)?;
    }
    Ok(())
}

/// Save the Apollo key in account-agent's .env only (the other assistants never use Apollo).
pub fn save_apollo_key(root: &Path, value: &str) -> Result<(), String> {
    set_env_key(&root.join(Agent::Account.folder()), APOLLO_KEY, value)
}

/// Whether an assistant's Python packages are prepared (uv has made its .venv).
pub fn prepared(dir: &Path) -> bool {
    dir.join(".venv").join("pyvenv.cfg").is_file()
}

/// Run `uv sync` in every assistant folder. Returns a plain-language result.
pub fn prepare(root: &Path, uv: &Path) -> Result<String, String> {
    for a in Agent::ALL {
        let dir = root.join(a.folder());
        let out = crate::settings::quiet_command(uv)
            .args(["sync", "--quiet"])
            .current_dir(&dir)
            .output()
            .map_err(|e| format!("Could not start uv: {e}"))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(format!("Preparing {} failed: {}", a.folder(), err.lines().last().unwrap_or("unknown error")));
        }
    }
    Ok("All three assistants are prepared.".into())
}

/// The CRM formats both assistants' export_crm.py accept.
pub const CRM_FORMATS: [&str; 2] = ["hubspot", "salesforce"];

/// The fixed argument list for `uv` that runs an assistant's CRM export. Only the account and candidate
/// assistants have one, and the format must be one of CRM_FORMATS: nothing typed by a person reaches the command.
pub fn crm_args(agent: Agent, format: &str) -> Result<[&'static str; 5], String> {
    if !matches!(agent, Agent::Account | Agent::Candidate) {
        return Err("Only Accounts and Find candidates can export for a CRM.".into());
    }
    let fmt = CRM_FORMATS.iter().find(|f| **f == format).ok_or_else(|| format!("Unknown CRM format '{format}'."))?;
    Ok(["run", "python", "scripts/export_crm.py", "--format", fmt])
}

/// Run the assistant's export_crm.py in its folder (read-only on the workbook) and return the exports folder
/// with the CSV files the run wrote or rewrote, newest first.
pub fn export_crm(dir: &Path, agent: Agent, format: &str, uv: &Path) -> Result<(PathBuf, Vec<String>), String> {
    let args = crm_args(agent, format)?;
    let started = std::time::SystemTime::now() - std::time::Duration::from_secs(2);
    let out = crate::settings::quiet_command(uv)
        .args(args)
        .current_dir(dir)
        .output()
        .map_err(|e| format!("Could not start uv: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let last = err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("unknown error");
        return Err(format!("The CRM export did not work: {last}"));
    }
    let folder = dir.join("exports");
    let mut files: Vec<(std::time::SystemTime, String)> = std::fs::read_dir(&folder)
        .map(|rd| {
            rd.flatten()
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().to_string();
                    let t = e.metadata().and_then(|m| m.modified()).ok()?;
                    (name.ends_with(".csv") && t >= started).then_some((t, name))
                })
                .collect()
        })
        .unwrap_or_default();
    files.sort_by(|a, b| b.0.cmp(&a.0));
    if files.is_empty() {
        return Err("The CRM export finished but wrote no file. Check that the results file has rows.".into());
    }
    Ok((folder, files.into_iter().map(|f| f.1).collect()))
}

/// Find uv for `prepare`.
pub fn uv_path() -> Option<PathBuf> {
    crate::settings::detect_uv().or_else(|| find_program(&["uv"], &[]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("rd-agents-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn fake_root(root: &Path) {
        for a in Agent::ALL {
            fs::create_dir_all(root.join(a.folder())).unwrap();
            fs::write(root.join(a.folder()).join("CLAUDE.md"), "x").unwrap();
        }
    }

    #[test]
    fn crm_args_are_fixed_and_checked() {
        assert_eq!(crm_args(Agent::Account, "hubspot").unwrap(), ["run", "python", "scripts/export_crm.py", "--format", "hubspot"]);
        assert_eq!(crm_args(Agent::Candidate, "salesforce").unwrap()[4], "salesforce");
        assert!(crm_args(Agent::Company, "hubspot").is_err(), "company-agent has no CRM export");
        assert!(crm_args(Agent::Account, "hubspot; rm -rf /").is_err());
        assert!(crm_args(Agent::Account, "HubSpot").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn export_crm_lists_the_new_files() {
        use std::os::unix::fs::PermissionsExt;
        let d = scratch("crm");
        let uv = d.join("fake-uv");
        // Stands in for uv: checks the fixed arguments, then writes a CSV the way export_crm.py does.
        fs::write(&uv, "#!/bin/sh\n[ \"$*\" = \"run python scripts/export_crm.py --format hubspot\" ] || { echo bad args >&2; exit 2; }\nmkdir -p exports && echo a > exports/hubspot-companies.csv\n").unwrap();
        fs::set_permissions(&uv, fs::Permissions::from_mode(0o755)).unwrap();
        let (folder, files) = export_crm(&d, Agent::Account, "hubspot", &uv).unwrap();
        assert_eq!(folder, d.join("exports"));
        assert_eq!(files, vec!["hubspot-companies.csv".to_string()]);
        let err = export_crm(&d, Agent::Account, "salesforce", &uv).unwrap_err();
        assert!(err.contains("bad args"), "{err}");
    }

    #[test]
    fn key_line_is_replaced_and_other_lines_kept() {
        let base = "# comment EXA_API_KEY=no\nEXA_API_KEY=\nHASDATA_API_KEY=\n";
        assert_eq!(with_key(base, EXA_KEY, "k1"), "# comment EXA_API_KEY=no\nEXA_API_KEY=k1\nHASDATA_API_KEY=\n");
        assert_eq!(with_key("A=1\r\nB=2", EXA_KEY, "k"), "A=1\r\nB=2\r\nEXA_API_KEY=k\r\n");
        assert_eq!(with_key("", EXA_KEY, "k"), "EXA_API_KEY=k\n");
    }

    #[test]
    fn set_key_keeps_user_env_and_starts_from_example() {
        let d = scratch("env");
        fs::write(d.join(".env.example"), "EXA_API_KEY=\nFIRECRAWL_API_KEY=\nCRAWLER_CONTACT=\n").unwrap();
        assert!(!env_has(&d, EXA_KEY));
        set_env_key(&d, EXA_KEY, " abc-123 ").unwrap();
        assert_eq!(fs::read_to_string(d.join(".env")).unwrap(), "EXA_API_KEY=abc-123\nFIRECRAWL_API_KEY=\nCRAWLER_CONTACT=\n");
        assert!(env_has(&d, EXA_KEY) && !env_has(&d, "FIRECRAWL_API_KEY"));
        // An existing .env wins over the example, and its other values survive.
        fs::write(d.join(".env"), "MY=keep\nEXA_API_KEY=old\n").unwrap();
        set_env_key(&d, EXA_KEY, "new").unwrap();
        assert_eq!(fs::read_to_string(d.join(".env")).unwrap(), "MY=keep\nEXA_API_KEY=new\n");
        assert!(!d.join(".env.rdnew").exists());
        assert!(set_env_key(&d, EXA_KEY, "has space").is_err());
        assert!(set_env_key(&d, EXA_KEY, "  ").is_err());
        assert_eq!(fs::read_to_string(d.join(".env")).unwrap(), "MY=keep\nEXA_API_KEY=new\n");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn save_apollo_key_writes_only_the_account_agent() {
        let root = scratch("apollo");
        for a in Agent::ALL {
            fs::create_dir_all(root.join(a.folder())).unwrap();
        }
        save_apollo_key(&root, "ap_key_123").unwrap();
        assert!(env_has(&root.join(Agent::Account.folder()), APOLLO_KEY));
        assert!(!env_has(&root.join(Agent::Candidate.folder()), APOLLO_KEY));
        assert!(!env_has(&root.join(Agent::Company.folder()), APOLLO_KEY));
        let err = save_apollo_key(&root, "has space").unwrap_err();
        assert!(err.contains("Apollo") && !err.contains("has space"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn save_exa_key_writes_both_agents() {
        let root = scratch("both");
        fake_root(&root);
        save_exa_key(&root, "k").unwrap();
        for a in Agent::ALL {
            assert!(env_has(&root.join(a.folder()), EXA_KEY));
        }
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn installed_root_uses_marker_then_default() {
        let home = scratch("home");
        let exe = home.join("app");
        fs::create_dir_all(&exe).unwrap();
        assert_eq!(installed_root(Some(&exe), Some(&home)), None);
        let def = default_root(&home);
        fake_root(&def);
        assert_eq!(installed_root(Some(&exe), Some(&home)), Some(def.clone()));
        let other = home.join("Elsewhere").join("AI-AGENTS");
        fake_root(&other);
        fs::write(exe.join(MARKER), format!("\u{feff}{}\r\n", other.display())).unwrap();
        assert_eq!(installed_root(Some(&exe), Some(&home)), Some(other.clone()));
        let mut utf16 = vec![0xFF, 0xFE];
        format!("{}", other.display()).encode_utf16().for_each(|u| utf16.extend(u.to_le_bytes()));
        fs::write(exe.join(MARKER), utf16).unwrap();
        assert_eq!(installed_root(Some(&exe), Some(&home)), Some(other.clone()));
        // A marker pointing at a folder without the assistants is ignored.
        fs::write(exe.join(MARKER), home.join("nope").display().to_string()).unwrap();
        assert_eq!(installed_root(Some(&exe), Some(&home)), Some(def));
        fs::remove_dir_all(&home).unwrap();
    }
}
