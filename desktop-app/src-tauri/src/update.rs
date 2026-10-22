// Author: kaustubh.s.v
//! Updates for an installed copy (2026-10-21).
//!
//! The app looks for a newer signed release on GitHub (`latest.json` of the latest release, see tauri.conf.json). Nothing is
//! downloaded without the person's say-so, and the download is installed only if it is signed with the key whose public half is built
//! into this app. Updates are for the Windows installer; a Linux package is updated by installing the newer package.

use crate::core::Core;
use serde::Serialize;
use std::sync::Arc;
use tauri::{AppHandle, State};

#[derive(Debug, Serialize)]
pub struct UpdateInfo {
    pub available: bool,
    pub version: String,
    pub notes: String,
    /// Plain words for the window; empty when there is nothing to say.
    pub message: String,
}

fn none(message: &str) -> UpdateInfo {
    UpdateInfo { available: false, version: String::new(), notes: String::new(), message: message.into() }
}

/// What a failed check means to the person.
pub fn plain_error(e: &str) -> String {
    let l = e.to_lowercase();
    if l.contains("404") || l.contains("not found") || l.contains("valid release json") || l.contains("could not fetch") {
        "No update has been published yet.".into()
    } else if l.contains("dns") || l.contains("connect") || l.contains("timed out") || l.contains("network") || l.contains("error sending request") {
        "Could not reach the update server. Check your internet connection and try again.".into()
    } else if l.contains("signature") {
        "The update could not be verified, so it was not installed.".into()
    } else {
        "Could not check for updates. Try again in a moment.".into()
    }
}

#[cfg(windows)]
#[tauri::command]
pub async fn update_check(app: AppHandle) -> Result<UpdateInfo, String> {
    use tauri_plugin_updater::UpdaterExt;
    let updater = app.updater().map_err(|e| plain_error(&e.to_string()))?;
    match updater.check().await {
        Ok(Some(u)) => Ok(UpdateInfo { available: true, version: u.version.clone(), notes: u.body.clone().unwrap_or_default(), message: String::new() }),
        Ok(None) => Ok(none("Recruit Desk is up to date.")),
        Err(e) => Err(plain_error(&e.to_string())),
    }
}

/// Download, verify and install the newer version; the installer closes the app and starts the new one.
#[cfg(windows)]
#[tauri::command]
pub async fn update_install(app: AppHandle, core: State<'_, Arc<Core>>) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    if !core.running_agents().is_empty() {
        return Err("An assistant is still running. Update when it has finished.".into());
    }
    let updater = app.updater().map_err(|e| plain_error(&e.to_string()))?;
    let Some(u) = updater.check().await.map_err(|e| plain_error(&e.to_string()))? else {
        return Err("Recruit Desk is already up to date.".into());
    };
    u.download_and_install(|_, _| {}, || {}).await.map_err(|e| plain_error(&e.to_string()))?;
    app.restart();
}

#[cfg(not(windows))]
#[tauri::command]
pub async fn update_check(_app: AppHandle) -> Result<UpdateInfo, String> {
    Ok(none("On Linux, install the newer .deb package to update."))
}

#[cfg(not(windows))]
#[tauri::command]
pub async fn update_install(_app: AppHandle, _core: State<'_, Arc<Core>>) -> Result<(), String> {
    Err("On Linux, install the newer .deb package to update.".into())
}

#[cfg(test)]
mod tests {
    use super::plain_error;

    #[test]
    fn failures_are_said_in_plain_words() {
        assert_eq!(plain_error("Could not fetch a valid release JSON from the remote"), "No update has been published yet.");
        assert_eq!(plain_error("error sending request for url (https://github.com/...)"), "Could not reach the update server. Check your internet connection and try again.");
        assert_eq!(plain_error("the signature could not be verified"), "The update could not be verified, so it was not installed.");
        assert_eq!(plain_error("something odd"), "Could not check for updates. Try again in a moment.");
    }
}
