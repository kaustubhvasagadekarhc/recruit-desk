// Author: kaustubh.s.v
//! Spawns the Claude Code CLI in an agent's folder and streams its events.
//!
//! The prompt goes in on stdin, so a pasted job description with quotes or newlines
//! needs no shell escaping. Claude Code keeps its normal permission mode: the agent's
//! own .claude/settings.json allow list decides what runs, and anything else is denied.

use crate::cap::Tokens;
use crate::settings::quiet_command;
use crate::stream::{Event, StreamParser};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, Stdio};
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, PartialEq)]
pub enum EndReason {
    /// The CLI finished; `ok` is the result event's success flag.
    Finished { ok: bool },
    /// The user pressed Stop.
    Stopped,
    /// The agent reached its token cap mid-run.
    Cap,
    /// The process ended without a result event.
    Failed(String),
}

pub trait Sink: Send + Sync + 'static {
    fn event(&self, ev: &Event);
    /// Called for every token delta. Return false to stop the run (cap reached).
    fn usage(&self, delta: &Tokens) -> bool;
    fn stderr(&self, line: &str);
    fn ended(&self, reason: &EndReason, total: &Tokens);
}

pub struct RunHandle {
    pid: u32,
    reason: Arc<Mutex<Option<EndReason>>>,
}

impl RunHandle {
    pub fn pid(&self) -> u32 {
        self.pid
    }
    /// Kill the run and its children; the reader thread reports `reason` when the pipe closes.
    pub fn stop(&self, reason: EndReason) {
        let mut r = self.reason.lock().unwrap();
        if r.is_none() {
            *r = Some(reason);
        }
        drop(r);
        kill_tree(self.pid);
    }
}

#[cfg(unix)]
fn kill_tree(pid: u32) {
    // The child leads its own process group, so this reaches uv, python and MCP servers too.
    unsafe {
        libc::killpg(pid as i32, libc::SIGTERM);
    }
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(3));
        unsafe {
            libc::killpg(pid as i32, libc::SIGKILL);
        }
    });
}

#[cfg(windows)]
fn kill_tree(pid: u32) {
    let _ = quiet_command(Path::new("taskkill"))
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .output();
}

fn run_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{}-{}", std::process::id(), nanos)
}

pub fn start(claude: &Path, dir: &Path, workbook_dir: Option<&Path>, data_root: Option<&Path>, prompt: &str, sink: Arc<dyn Sink>) -> Result<RunHandle, String> {
    let mut cmd = quiet_command(claude);
    cmd.args(["-p", "--output-format", "stream-json", "--verbose"])
        .current_dir(dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // If this app was itself started from inside Claude Code, do not look like a nested session.
        .env_remove("CLAUDECODE")
        .env_remove("CLAUDE_CODE_ENTRYPOINT")
        // One id per run: the agents' progress.py keeps its step state per run with it.
        .env("RD_RUN_ID", run_id());
    // Where the assistant keeps its Excel file (the data folder); without it the assistant uses its own folder.
    if let Some(w) = workbook_dir {
        cmd.env("RECRUIT_DESK_WORKBOOK_DIR", w);
    }
    // The small hand-off files the app reads (the history rows, the last record) stay in the assistant's own folder.
    cmd.env("RECRUIT_DESK_HANDOFF_DIR", dir);
    // The accounts assistant reads the company assistant's file (and starts its scan) and needs to know where that one lives.
    if let Some(d) = data_root {
        cmd.env("RECRUIT_DESK_DATA_ROOT", d);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    let mut child: Child = cmd
        .spawn()
        .map_err(|e| format!("Could not start {}: {e}", claude.display()))?;
    let pid = child.id();
    {
        let mut stdin = child.stdin.take().ok_or("no stdin")?;
        stdin.write_all(prompt.as_bytes()).map_err(|e| e.to_string())?;
    } // dropped: closes stdin so claude reads the whole prompt
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;
    let reason: Arc<Mutex<Option<EndReason>>> = Arc::new(Mutex::new(None));

    let s2 = sink.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                s2.stderr(&line);
            }
        }
    });

    let r2 = reason.clone();
    std::thread::spawn(move || {
        let mut parser = StreamParser::new();
        let mut ok = None;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            for ev in parser.feed(&line) {
                if let Event::Usage(d) = &ev {
                    if !sink.usage(d) {
                        let mut r = r2.lock().unwrap();
                        if r.is_none() {
                            *r = Some(EndReason::Cap);
                        }
                        drop(r);
                        kill_tree(pid);
                    }
                }
                if let Event::Result { ok: o, .. } = &ev {
                    ok = Some(*o);
                }
                sink.event(&ev);
            }
        }
        let status = child.wait();
        let reason = r2.lock().unwrap().clone().unwrap_or_else(|| match (ok, &status) {
            (Some(o), _) => EndReason::Finished { ok: o },
            (None, Ok(s)) => EndReason::Failed(format!("claude exited ({s}) without a result")),
            (None, Err(e)) => EndReason::Failed(e.to_string()),
        });
        sink.ended(&reason, &parser.total());
    });

    Ok(RunHandle { pid, reason })
}
