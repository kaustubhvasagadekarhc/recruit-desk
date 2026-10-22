// Author: kaustubh.s.v
//! Parser for `claude -p --output-format stream-json --verbose` output.
//!
//! Turns each JSON line into events: checkpoint lines (▶ ✓ ✗ … and the plan), short
//! activity notes (which tool is running), warnings, token deltas and the final result.

use crate::cap::Tokens;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// A step line from the agent's progress helper (▶ ✓ ✗ … or the numbered plan).
    Checkpoint(String),
    /// Activity: session start, tool calls.
    Info(String),
    /// Something the user should notice: a denied permission, an error.
    Warn(String),
    /// Tokens used since the previous Usage event.
    Usage(Tokens),
    /// End of run: success flag and the agent's final message.
    Result { ok: bool, text: String },
}

#[derive(Default)]
pub struct StreamParser {
    per_message: HashMap<String, Tokens>,
    total: Tokens,
    seen: HashSet<String>,
    plan_lines_left: usize,
    finished: bool,
}

fn u(v: &Value, k: &str) -> u64 {
    v.get(k).and_then(Value::as_u64).unwrap_or(0)
}

fn tokens_from_usage(v: &Value) -> Tokens {
    Tokens {
        input: u(v, "input_tokens"),
        output: u(v, "output_tokens"),
        cache_creation: u(v, "cache_creation_input_tokens"),
        cache_read: u(v, "cache_read_input_tokens"),
    }
}

fn fmax(a: &Tokens, b: &Tokens) -> Tokens {
    Tokens {
        input: a.input.max(b.input),
        output: a.output.max(b.output),
        cache_creation: a.cache_creation.max(b.cache_creation),
        cache_read: a.cache_read.max(b.cache_read),
    }
}

fn diff(new: &Tokens, old: &Tokens) -> Tokens {
    Tokens {
        input: new.input.saturating_sub(old.input),
        output: new.output.saturating_sub(old.output),
        cache_creation: new.cache_creation.saturating_sub(old.cache_creation),
        cache_read: new.cache_read.saturating_sub(old.cache_read),
    }
}

fn add(a: &mut Tokens, d: &Tokens) {
    a.input += d.input;
    a.output += d.output;
    a.cache_creation += d.cache_creation;
    a.cache_read += d.cache_read;
}

/// Strip markdown decoration an agent may wrap around an echoed checkpoint line.
fn clean(line: &str) -> String {
    let mut s = line.trim();
    for p in ["- ", "* ", "> "] {
        if let Some(r) = s.strip_prefix(p) {
            s = r.trim_start();
        }
    }
    let s = s.trim_matches(|c| c == '`' || c == '*').trim();
    // Drop a progress.log timestamp prefix: "2026-10-15T00:05:52  ▶ Step …".
    if s.len() > 21 && s.as_bytes().get(10) == Some(&b'T') && s[..4].chars().all(|c| c.is_ascii_digit()) {
        if let Some(i) = s.find("  ") {
            return s[i..].trim().to_string();
        }
    }
    s.to_string()
}

fn short(s: &str, n: usize) -> String {
    let one: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if one.chars().count() > n {
        format!("{}…", one.chars().take(n).collect::<String>())
    } else {
        one
    }
}

impl StreamParser {
    pub fn new() -> Self {
        Self::default()
    }

    /// Total tokens seen so far in this run.
    pub fn total(&self) -> Tokens {
        self.total
    }

    pub fn finished(&self) -> bool {
        self.finished
    }

    fn text_lines(&mut self, text: &str, out: &mut Vec<Event>) {
        for raw in text.lines() {
            let line = clean(raw);
            if line.is_empty() {
                continue;
            }
            let is_plan = line.starts_with("Plan ·");
            let first = line.chars().next().unwrap_or(' ');
            let is_step = matches!(first, '▶' | '✓' | '✗' | '…');
            let is_plan_item = self.plan_lines_left > 0
                && line.split_once(". ").map_or(false, |(n, _)| n.parse::<u32>().is_ok());
            if is_plan {
                // Plan header: the next numbered lines are the steps.
                self.plan_lines_left = line
                    .split('·')
                    .nth(2)
                    .and_then(|s| s.trim().split(' ').next())
                    .and_then(|n| n.parse().ok())
                    .unwrap_or(6);
            }
            if !(is_plan || is_step || is_plan_item) {
                continue;
            }
            if is_plan_item {
                self.plan_lines_left -= 1;
            }
            if self.seen.insert(line.clone()) {
                let shown = if is_plan_item { format!("  {line}") } else { line };
                out.push(Event::Checkpoint(shown));
            }
        }
    }

    fn tool_note(name: &str, input: &Value) -> String {
        let detail = match name {
            "Bash" => input.get("command").and_then(Value::as_str).map(|s| short(s, 110)),
            "Skill" => input.get("skill").and_then(Value::as_str).map(str::to_string),
            "Task" | "Agent" => input
                .get("description")
                .or_else(|| input.get("subagent_type"))
                .and_then(Value::as_str)
                .map(str::to_string),
            "Read" | "Write" | "Edit" => input
                .get("file_path")
                .and_then(Value::as_str)
                .map(|p| p.rsplit(['/', '\\']).next().unwrap_or(p).to_string()),
            _ if name.contains("exa") => input
                .get("query")
                .or_else(|| input.get("url"))
                .and_then(Value::as_str)
                .map(|s| short(s, 90)),
            _ => None,
        };
        match detail {
            Some(d) if !d.is_empty() => format!("→ {name}: {d}"),
            _ => format!("→ {name}"),
        }
    }

    /// Feed one line of stdout. Lines that are not JSON are ignored.
    pub fn feed(&mut self, line: &str) -> Vec<Event> {
        let mut out = Vec::new();
        let v: Value = match serde_json::from_str(line.trim()) {
            Ok(v) => v,
            Err(_) => return out,
        };
        match v.get("type").and_then(Value::as_str).unwrap_or("") {
            "system" if v.get("subtype").and_then(Value::as_str) == Some("init") => {
                let model = v.get("model").and_then(Value::as_str).unwrap_or("?");
                out.push(Event::Info(format!("Session started · model {model}")));
            }
            "assistant" => {
                let msg = &v["message"];
                if let Some(usage) = msg.get("usage") {
                    let id = msg
                        .get("id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .unwrap_or_else(|| format!("anon-{}", self.per_message.len()));
                    let seen = self.per_message.get(&id).copied().unwrap_or_default();
                    let now = fmax(&seen, &tokens_from_usage(usage));
                    let d = diff(&now, &seen);
                    self.per_message.insert(id, now);
                    if !d.is_zero() {
                        add(&mut self.total, &d);
                        out.push(Event::Usage(d));
                    }
                }
                if let Some(blocks) = msg.get("content").and_then(Value::as_array) {
                    for b in blocks {
                        match b.get("type").and_then(Value::as_str) {
                            Some("text") => {
                                let t = b.get("text").and_then(Value::as_str).unwrap_or("");
                                self.text_lines(t, &mut out);
                            }
                            Some("tool_use") => {
                                let name = b.get("name").and_then(Value::as_str).unwrap_or("tool");
                                out.push(Event::Info(Self::tool_note(name, &b["input"])));
                            }
                            _ => {}
                        }
                    }
                }
            }
            "user" => {
                if let Some(blocks) = v["message"].get("content").and_then(Value::as_array) {
                    for b in blocks {
                        if b.get("type").and_then(Value::as_str) != Some("tool_result") {
                            continue;
                        }
                        let mut text = String::new();
                        match b.get("content") {
                            Some(Value::String(s)) => text.push_str(s),
                            Some(Value::Array(parts)) => {
                                for p in parts {
                                    if let Some(s) = p.get("text").and_then(Value::as_str) {
                                        text.push_str(s);
                                        text.push('\n');
                                    }
                                }
                            }
                            _ => {}
                        }
                        if b.get("is_error").and_then(Value::as_bool) == Some(true) {
                            let first = text.lines().next().unwrap_or("").to_string();
                            if first.contains("permission") || first.contains("denied") || first.contains("blocked") {
                                out.push(Event::Warn(format!("Tool refused: {}", short(&first, 160))));
                            }
                        }
                        self.text_lines(&text, &mut out);
                    }
                }
            }
            "result" => {
                // modelUsage covers every model the run used (subagents included).
                let mut sum = Tokens::default();
                if let Some(models) = v.get("modelUsage").and_then(Value::as_object) {
                    for m in models.values() {
                        add(
                            &mut sum,
                            &Tokens {
                                input: u(m, "inputTokens"),
                                output: u(m, "outputTokens"),
                                cache_creation: u(m, "cacheCreationInputTokens"),
                                cache_read: u(m, "cacheReadInputTokens"),
                            },
                        );
                    }
                } else if let Some(usage) = v.get("usage") {
                    sum = tokens_from_usage(usage);
                }
                let final_total = fmax(&self.total, &sum);
                let d = diff(&final_total, &self.total);
                if !d.is_zero() {
                    self.total = final_total;
                    out.push(Event::Usage(d));
                }
                if let Some(denials) = v.get("permission_denials").and_then(Value::as_array) {
                    for dn in denials {
                        let tool = dn.get("tool_name").and_then(Value::as_str).unwrap_or("tool");
                        let what = dn["tool_input"]
                            .get("command")
                            .and_then(Value::as_str)
                            .map(|c| format!(": {}", short(c, 100)))
                            .unwrap_or_default();
                        out.push(Event::Warn(format!(
                            "Permission denied for {tool}{what} (not in the agent's allow list)"
                        )));
                    }
                }
                let ok = v.get("subtype").and_then(Value::as_str) == Some("success")
                    && v.get("is_error").and_then(Value::as_bool) != Some(true);
                let text = v.get("result").and_then(Value::as_str).unwrap_or("").to_string();
                self.text_lines(&text, &mut out);
                self.finished = true;
                out.push(Event::Result { ok, text });
            }
            _ => {}
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn usage_of(evs: &[Event]) -> Tokens {
        let mut t = Tokens::default();
        for e in evs {
            if let Event::Usage(d) = e {
                add(&mut t, d);
            }
        }
        t
    }

    #[test]
    fn repeated_usage_for_one_message_is_counted_once() {
        let mut p = StreamParser::new();
        let a = r#"{"type":"assistant","message":{"id":"m1","usage":{"input_tokens":2,"output_tokens":1,"cache_creation_input_tokens":100,"cache_read_input_tokens":50},"content":[]}}"#;
        let b = r#"{"type":"assistant","message":{"id":"m1","usage":{"input_tokens":2,"output_tokens":9,"cache_creation_input_tokens":100,"cache_read_input_tokens":50},"content":[]}}"#;
        let mut evs = p.feed(a);
        evs.extend(p.feed(b));
        let t = usage_of(&evs);
        assert_eq!(t.counted(), 111);
        assert_eq!(t.cache_read, 50);
    }

    #[test]
    fn result_model_usage_tops_up_the_total() {
        let mut p = StreamParser::new();
        p.feed(r#"{"type":"assistant","message":{"id":"m1","usage":{"input_tokens":2,"output_tokens":1,"cache_creation_input_tokens":100,"cache_read_input_tokens":50},"content":[]}}"#);
        let evs = p.feed(r#"{"type":"result","subtype":"success","is_error":false,"result":"done","modelUsage":{"a":{"inputTokens":2,"outputTokens":4,"cacheReadInputTokens":50,"cacheCreationInputTokens":100},"b":{"inputTokens":10,"outputTokens":0,"cacheReadInputTokens":0,"cacheCreationInputTokens":0}}}"#);
        assert_eq!(usage_of(&evs).counted(), 13);
        assert_eq!(p.total().counted(), 116);
        assert!(evs.contains(&Event::Result { ok: true, text: "done".into() }));
    }

    #[test]
    fn checkpoints_from_tool_output_and_text_are_deduplicated() {
        let mut p = StreamParser::new();
        let tool = r#"{"type":"user","message":{"content":[{"type":"tool_result","content":"Plan · acme · 6 steps\n  1. Recalling\n  2. Crawling\n  3. Posts\n  4. Dedupe\n  5. Excel\n  6. Report\n  log: runs/x/progress.log\n▶ Step 1/6 · Recalling\nnoise line"}]}}"#;
        let evs = p.feed(tool);
        let cps: Vec<_> = evs.iter().filter_map(|e| if let Event::Checkpoint(s) = e { Some(s.clone()) } else { None }).collect();
        assert_eq!(cps.len(), 8, "{cps:?}");
        assert_eq!(cps[0], "Plan · acme · 6 steps");
        assert_eq!(cps[1], "  1. Recalling");
        let echo = r#"{"type":"assistant","message":{"id":"m2","content":[{"type":"text","text":"Starting.\n`▶ Step 1/6 · Recalling`\n- ✓ Step 1/6 · acme.com, careers page none"}]}}"#;
        let evs = p.feed(echo);
        let cps: Vec<_> = evs.iter().filter(|e| matches!(e, Event::Checkpoint(_))).collect();
        assert_eq!(cps.len(), 1);
        assert_eq!(cps[0], &Event::Checkpoint("✓ Step 1/6 · acme.com, careers page none".into()));
    }

    #[test]
    fn numbered_lines_outside_a_plan_are_not_checkpoints() {
        let mut p = StreamParser::new();
        let evs = p.feed(r#"{"type":"assistant","message":{"id":"m","content":[{"type":"text","text":"1. Something\n2. Else"}]}}"#);
        assert!(evs.iter().all(|e| !matches!(e, Event::Checkpoint(_))));
    }

    #[test]
    fn timestamped_log_lines_are_cleaned() {
        assert_eq!(clean("2026-10-15T00:05:52  ✓ Step 1/6 · ok"), "✓ Step 1/6 · ok");
    }

    #[test]
    fn tool_use_and_denials_are_reported() {
        let mut p = StreamParser::new();
        let evs = p.feed(r#"{"type":"assistant","message":{"id":"m","content":[{"type":"tool_use","name":"Bash","input":{"command":"uv run python scripts/scan.py --company X"}}]}}"#);
        assert_eq!(evs, vec![Event::Info("→ Bash: uv run python scripts/scan.py --company X".into())]);
        let evs = p.feed(r#"{"type":"result","subtype":"success","result":"","permission_denials":[{"tool_name":"Bash","tool_input":{"command":"rm -rf x"}}]}"#);
        assert!(matches!(&evs[0], Event::Warn(w) if w.contains("rm -rf x")));
    }

    #[test]
    fn real_sample_from_claude_2_1_300() {
        let sample = include_str!("../tests/sample-stream.jsonl");
        let mut p = StreamParser::new();
        for l in sample.lines() {
            p.feed(l);
        }
        assert!(p.finished());
        assert_eq!(p.total(), Tokens { input: 2, output: 4, cache_creation: 25903, cache_read: 11064 });
    }
}
