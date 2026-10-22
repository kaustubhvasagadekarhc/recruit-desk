// Author: kaustubh.s.v
//! Token cap per agent in a 4-hour window that starts with the first token used.
//!
//! The window opens when an agent first uses tokens. When it closes, usage resets to zero.
//! Reaching the cap inside the window locks the section until the window closes.
//!
//! Time is measured with a "high-water mark": the latest wall-clock time the app has seen.
//! Window time only accrues when the clock moves forward past that mark, so setting the
//! clock backwards never shortens a lock (it is flagged and the lock is kept), and time
//! spent with the app closed still counts on the next start.

use serde::{Deserialize, Serialize};

/// Length of the usage window, counted from the first token used in it.
pub const LOCK_WINDOW_MS: i64 = 4 * 60 * 60 * 1000;
/// A backwards jump smaller than this is treated as normal clock jitter (NTP adjustments).
pub const BACKWARD_TOLERANCE_MS: i64 = 2 * 60 * 1000;
/// Default cap per agent.
pub const DEFAULT_CAP: u64 = 3_000_000;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tokens {
    pub input: u64,
    pub output: u64,
    pub cache_creation: u64,
    pub cache_read: u64,
}

impl Tokens {
    /// Tokens that count toward the cap: input + output + cache creation (not cache reads).
    pub fn counted(&self) -> u64 {
        self.input + self.output + self.cache_creation
    }
    pub fn is_zero(&self) -> bool {
        self.counted() == 0 && self.cache_read == 0
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentUsage {
    /// Tokens counted toward the cap in the current window.
    pub counted: u64,
    /// Cache-read tokens in the current window (shown, not counted).
    pub cache_read: u64,
    /// Wall-clock time (ms since epoch) when the cap was hit; None while unlocked.
    pub locked_at: Option<i64>,
    /// Time accrued in the current window; usage resets (and any lock lifts) at LOCK_WINDOW_MS.
    pub lock_elapsed_ms: i64,
    /// Wall-clock time (ms since epoch) the current window opened; None before any usage.
    #[serde(default)]
    pub window_started: Option<i64>,
    /// Latest wall-clock time seen (ms since epoch); 0 before the first tick.
    pub high_water: i64,
    /// True while the clock reads earlier than the high-water mark.
    pub clock_backwards: bool,
}

impl AgentUsage {
    /// Advance time to `now`. Returns true if this call lifted the lock.
    pub fn tick(&mut self, now: i64) -> bool {
        if self.high_water == 0 {
            self.high_water = now;
        }
        if now + BACKWARD_TOLERANCE_MS < self.high_water {
            self.clock_backwards = true;
            return false;
        }
        self.clock_backwards = false;
        // Files saved before windows existed: a lock opened its window when the cap was hit;
        // unlocked partial usage opens its window now.
        if self.window_started.is_none() {
            if let Some(at) = self.locked_at {
                self.window_started = Some(at);
            } else if self.counted > 0 || self.cache_read > 0 {
                self.window_started = Some(self.high_water);
                self.lock_elapsed_ms = 0;
            }
        }
        if now > self.high_water {
            if self.window_started.is_some() {
                self.lock_elapsed_ms += now - self.high_water;
            }
            self.high_water = now;
        }
        if self.window_started.is_some() && self.lock_elapsed_ms >= LOCK_WINDOW_MS {
            let was_locked = self.is_locked();
            self.reset();
            return was_locked;
        }
        false
    }

    pub fn is_locked(&self) -> bool {
        self.locked_at.is_some()
    }

    /// The wall-clock time the current window closes and usage resets, read on the clock as
    /// the app last trusted it. None before any usage.
    pub fn resets_at(&self) -> Option<i64> {
        self.window_started
            .map(|_| self.high_water + (LOCK_WINDOW_MS - self.lock_elapsed_ms).max(0))
    }

    /// The wall-clock time the lock ends (the window's end); None while unlocked.
    pub fn unlock_at(&self) -> Option<i64> {
        self.locked_at.and(self.resets_at())
    }

    /// Add tokens. Returns true when the agent is at or over its cap (now locked).
    pub fn add(&mut self, delta: &Tokens, cap: u64, now: i64) -> bool {
        self.tick(now);
        if self.window_started.is_none() && (delta.counted() > 0 || delta.cache_read > 0) {
            self.window_started = Some(self.high_water.max(now));
            self.lock_elapsed_ms = 0;
        }
        self.counted += delta.counted();
        self.cache_read += delta.cache_read;
        if self.locked_at.is_none() && self.counted >= cap {
            self.locked_at = Some(self.high_water.max(now));
        }
        self.is_locked()
    }

    /// Lock immediately if the counter already reached a (possibly lowered) cap.
    pub fn enforce(&mut self, cap: u64, now: i64) {
        self.tick(now);
        if self.locked_at.is_none() && self.counted >= cap && self.counted > 0 {
            self.locked_at = Some(self.high_water.max(now));
        }
    }

    fn reset(&mut self) {
        self.counted = 0;
        self.cache_read = 0;
        self.locked_at = None;
        self.lock_elapsed_ms = 0;
        self.window_started = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_760_000_000_000;
    const MIN: i64 = 60_000;
    const HOUR: i64 = 60 * MIN;

    fn toks(n: u64) -> Tokens {
        Tokens { input: n, ..Default::default() }
    }

    #[test]
    fn counts_input_output_and_cache_creation_but_not_cache_read() {
        let t = Tokens { input: 10, output: 20, cache_creation: 30, cache_read: 1000 };
        assert_eq!(t.counted(), 60);
        let mut u = AgentUsage::default();
        u.add(&t, 3_000_000, T0);
        assert_eq!(u.counted, 60);
        assert_eq!(u.cache_read, 1000);
        assert!(!u.is_locked());
    }

    #[test]
    fn cap_hit_locks_until_four_hours_after_first_use() {
        let mut u = AgentUsage::default();
        assert!(!u.add(&toks(2_999_999), 3_000_000, T0));
        assert_eq!(u.resets_at(), Some(T0 + 4 * HOUR));
        assert!(u.add(&toks(1), 3_000_000, T0 + MIN));
        assert_eq!(u.locked_at, Some(T0 + MIN));
        assert_eq!(u.unlock_at(), Some(T0 + 4 * HOUR));
    }

    #[test]
    fn window_starts_with_first_use_and_resets_partial_usage() {
        let mut u = AgentUsage::default();
        u.tick(T0);
        assert_eq!(u.resets_at(), None);
        u.add(&toks(1_000), 3_000_000, T0 + HOUR);
        assert_eq!(u.resets_at(), Some(T0 + 5 * HOUR));
        u.add(&toks(1_000), 3_000_000, T0 + 2 * HOUR);
        assert!(!u.tick(T0 + 5 * HOUR));
        assert_eq!(u.counted, 0);
        assert_eq!(u.resets_at(), None);
        u.add(&toks(5), 3_000_000, T0 + 6 * HOUR);
        assert_eq!(u.resets_at(), Some(T0 + 10 * HOUR));
    }

    #[test]
    fn old_saved_usage_migrates_to_a_window() {
        let json = r#"{"counted":10,"cache_read":0,"locked_at":null,"lock_elapsed_ms":0,"high_water":1760000000000,"clock_backwards":false}"#;
        let mut u: AgentUsage = serde_json::from_str(json).unwrap();
        u.tick(T0 + HOUR);
        assert_eq!(u.resets_at(), Some(T0 + 4 * HOUR));
    }

    #[test]
    fn crossing_the_cap_mid_run_locks() {
        let mut u = AgentUsage::default();
        assert!(u.add(&toks(5_000_000), 3_000_000, T0));
        assert!(u.is_locked());
    }

    #[test]
    fn stays_locked_until_four_hours_then_resets_to_zero() {
        let mut u = AgentUsage::default();
        u.add(&Tokens { input: 3_000_000, cache_read: 50, ..Default::default() }, 3_000_000, T0);
        assert!(!u.tick(T0 + 4 * HOUR - 1));
        assert!(u.is_locked());
        assert!(u.tick(T0 + 4 * HOUR));
        assert!(!u.is_locked());
        assert_eq!(u.counted, 0);
        assert_eq!(u.cache_read, 0);
        assert_eq!(u.unlock_at(), None);
    }

    #[test]
    fn restart_persistence_keeps_lock_and_counts_time_while_closed() {
        let mut u = AgentUsage::default();
        u.add(&toks(3_000_000), 3_000_000, T0);
        u.tick(T0 + HOUR);
        // Save, "restart", load.
        let saved = serde_json::to_string(&u).unwrap();
        let mut back: AgentUsage = serde_json::from_str(&saved).unwrap();
        assert_eq!(back, u);
        // Two hours later the app starts again: still locked, unlock time unchanged.
        back.tick(T0 + 3 * HOUR);
        assert!(back.is_locked());
        assert_eq!(back.unlock_at(), Some(T0 + 4 * HOUR));
        // After the window it opens.
        assert!(back.tick(T0 + 4 * HOUR + 1));
        assert_eq!(back.counted, 0);
    }

    #[test]
    fn restart_persistence_keeps_partial_usage() {
        let mut u = AgentUsage::default();
        u.add(&toks(1_234_567), 3_000_000, T0);
        let back: AgentUsage = serde_json::from_str(&serde_json::to_string(&u).unwrap()).unwrap();
        assert_eq!(back.counted, 1_234_567);
        assert!(!back.is_locked());
    }

    #[test]
    fn clock_set_backwards_keeps_the_lock() {
        let mut u = AgentUsage::default();
        u.add(&toks(3_000_000), 3_000_000, T0);
        u.tick(T0 + HOUR);
        // Clock set back by a day: flagged, still locked, no time accrues.
        assert!(!u.tick(T0 - 24 * HOUR));
        assert!(u.clock_backwards);
        assert!(u.is_locked());
        assert_eq!(u.lock_elapsed_ms, HOUR);
        // Clock creeping forward but still before the mark: no accrual.
        u.tick(T0 - 20 * HOUR);
        assert!(u.is_locked());
        assert_eq!(u.lock_elapsed_ms, HOUR);
        // The lock only ends once 4 trusted hours have passed.
        assert!(!u.tick(T0 + 4 * HOUR - 1));
        assert!(u.tick(T0 + 4 * HOUR));
    }

    #[test]
    fn clock_back_then_forward_jump_does_not_count_backwards_time_twice() {
        let mut u = AgentUsage::default();
        u.add(&toks(3_000_000), 3_000_000, T0);
        u.tick(T0 - 10 * HOUR); // back
        u.tick(T0 + 30 * MIN); // returns to normal
        assert!(!u.clock_backwards);
        assert_eq!(u.lock_elapsed_ms, 30 * MIN);
        assert!(u.is_locked());
    }

    #[test]
    fn small_backward_jitter_is_not_flagged() {
        let mut u = AgentUsage::default();
        u.tick(T0);
        u.tick(T0 - 30_000);
        assert!(!u.clock_backwards);
    }

    #[test]
    fn lowering_the_cap_below_usage_locks_on_enforce() {
        let mut u = AgentUsage::default();
        u.add(&toks(1_000), 3_000_000, T0);
        u.enforce(500, T0 + 1);
        assert!(u.is_locked());
    }

    #[test]
    fn tokens_added_while_locked_are_still_counted() {
        let mut u = AgentUsage::default();
        u.add(&toks(3_000_000), 3_000_000, T0);
        assert!(u.add(&toks(10), 3_000_000, T0 + 1));
        assert_eq!(u.counted, 3_000_010);
        assert_eq!(u.locked_at, Some(T0));
    }
}
