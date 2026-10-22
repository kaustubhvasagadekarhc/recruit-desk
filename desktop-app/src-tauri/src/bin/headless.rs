// Author: kaustubh.s.v
//! Runs the app's backend without a window, for testing:
//!   headless status
//!   headless run company "Humancloud" humancloud.co.in
//!   headless run candidate <jd-file>
//!   headless sheet company [sheet]
//! Uses a separate data dir (RECRUIT_DESK_DATA, default ./headless-data) and the same
//! settings, cap logic, runner and Excel reader as the app.

use recruit_desk_lib::core::{Core, Emit, RunInput};
use recruit_desk_lib::excel;
use recruit_desk_lib::settings::Agent;
use serde_json::Value;
use std::sync::{mpsc, Arc, Mutex};

fn main() {
    // `--contacts` (account runs) may appear anywhere; it sets RunInput.contacts.
    let all: Vec<String> = std::env::args().skip(1).collect();
    let contacts = all.iter().any(|a| a == "--contacts");
    let args: Vec<String> = all.into_iter().filter(|a| a != "--contacts").collect();
    let data = std::env::var("RECRUIT_DESK_DATA").unwrap_or_else(|_| "headless-data".into());
    let core = Core::new(data.into());
    let start = std::time::Instant::now();
    match args.first().map(String::as_str) {
        Some("status") => {
            println!("agents_root: {}", core.settings.lock().unwrap().agents_root);
            for s in core.statuses() {
                println!("{}", serde_json::to_string(&s).unwrap());
            }
        }
        Some("prereqs") => {
            let s = core.settings.lock().unwrap().clone();
            for c in recruit_desk_lib::settings::check_prereqs(&s) {
                println!("{} {}: {}{}", if c.ok { "✓" } else { "✗" }, c.name, c.detail, if c.ok { String::new() } else { format!("\n    fix: {}", c.fix) });
            }
        }
        Some("sheet") => {
            let a = Agent::parse(args.get(1).map(String::as_str).unwrap_or("company")).unwrap();
            let path = core.settings.lock().unwrap().agent_dir(a).join(a.workbook());
            let d = excel::read_sheet(&path, args.get(2).map(String::as_str)).unwrap();
            println!("workbook: {}\nsheets: {:?}\nsheet: {:?} · {} rows\nheaders: {:?}", path.display(), d.sheets, d.sheet, d.rows.len(), d.headers);
            for r in d.rows.iter().take(5) {
                println!("  {:?}", r.iter().map(|c| c.chars().take(40).collect::<String>()).collect::<Vec<_>>());
            }
        }
        Some("run") => {
            let a = Agent::parse(args.get(1).map(String::as_str).unwrap_or("")).expect("agent: candidate|company|account");
            let input = match a {
                Agent::Company | Agent::Account if args.get(2).map(String::as_str) != Some("--csv") => {
                    RunInput { company: args.get(2).cloned().unwrap_or_default(), domain: args.get(3).cloned().unwrap_or_default(), contacts, ..Default::default() }
                }
                Agent::Company | Agent::Account => RunInput { csv: std::fs::read_to_string(args.get(3).expect("csv file")).expect("read csv"), contacts, ..Default::default() },
                Agent::Candidate => RunInput { jd: std::fs::read_to_string(args.get(2).expect("jd file")).expect("read jd"), ..Default::default() },
            };
            let (tx, rx) = mpsc::channel::<()>();
            let tx = Mutex::new(Some(tx));
            let emit: Emit = Arc::new(move |name: &str, v: Value| {
                let t = start.elapsed().as_secs();
                match name {
                    "run-line" => println!("[{:>4}s] {:<10} {}", t, v["kind"].as_str().unwrap_or(""), v["text"].as_str().unwrap_or("")),
                    "usage" => println!("[{:>4}s] usage      counted {} / cap {} ({:.2}%) · cache read {} · locked {}", t, v["counted"], v["cap"], v["percent"].as_f64().unwrap_or(0.0), v["cache_read"], v["locked"]),
                    "run-ended" => {
                        println!("[{:>4}s] ENDED      {} · {} · run tokens {}", t, v["status"], v["text"], v["tokens"]);
                        if let Some(tx) = tx.lock().unwrap().take() {
                            let _ = tx.send(());
                        }
                    }
                    _ => {}
                }
            });
            match core.start_run(a, &input, emit) {
                Ok(_) => {
                    let _ = rx.recv();
                    println!("final status: {}", serde_json::to_string(&core.status(a)).unwrap());
                }
                Err(e) => {
                    eprintln!("refused: {e}");
                    std::process::exit(2);
                }
            }
        }
        _ => eprintln!("usage: headless status | prereqs | run company|account <name> [domain] | run account --csv <file> | run candidate <jd-file> | sheet <agent> [sheet]"),
    }
}
