---
name: score-candidates
description: Score each discovered candidate against the JD's criteria and weights with per-dimension points, gates, evidence and a confidence rating, then compute totals with scripts/score.py. Use as step 3 of /scout.
---

# Score candidates

Step 3 of /scout (`uv run scripts/progress.py start 3` first).

Delegate to the `candidate-scorer` subagent with the JD file and the batch file. It prints
`… scoring i/n · <name>` as it judges candidates, then runs
`uv run scripts/score.py --input runs/<file>.json` (computes `match_score` and `score_breakdown`), which prints
its own `… scoring i/n · <name> <score>` lines and closes the step:
`✓ Step 3/6 · n scored, top S, g capped by a mandatory-skill gate, l Low confidence`. A bad dimension value makes
score.py print `✗ Step 3/6 · <name>: <dim>=<p> is outside 0-<w>`; fix the points and rerun it. Relay the
subagent's lines to the user.

What is judged where (calibrated scoring, 2026-10-15):
- The subagent judges core_skills, domain_projects, seniority and preferred_skills, and for each one it gives
  points to, the exact phrases it relied on (`quotes`). score.py stops with `✗` if a quote is not in the
  evidence or a dimension has points without a quote.
- score.py computes experience (parsed years against `criteria.experience_years`: full inside the range,
  -25% of the weight per year below, -10% per year above, half for an estimate; 1+ year below the minimum
  caps the total at 65) and location (same city full, same region half, other 0, remote allowed full), and
  rewrites Location to one canonical form per city (original kept in `location_raw` and the breakdown).
- The ✓ line is preceded by `… spread p10 · p50 · p90`; if p50 > 75 it prints a `⚠ … not discriminating`
  warning. Relay it; it means the batch's points are too generous or the evidence too thin.

Key factors (2026-10-15), filled by score.py for every row and shown as the last four sheet columns:
- **Open to Work**: Yes (explicit self-published signal: '#OpenToWork', 'open to work', 'actively looking',
  'looking for new opportunities', 'immediate joiner'), Likely (a notice period: 'serving notice period',
  'notice period: 30 days') or Unknown, with the exact quote. Yes +5, Likely +2, Unknown never a penalty; total
  capped at 100, gates and confidence caps still apply. Snippets only; never inferred from tenure or job changes.
- **Stack Match**: `3/3 must-have: React, TypeScript, JavaScript; missing: none · preferred 2/6: Next.js, Redux · core 30/35, preferred 6/10`.
- **Experience Match**: `6 yrs vs 5-9 required: in range`, `3 yrs (estimate) vs 5+ required: 2 below`.
- **Location Match**: `Same city`, `Same region`, `Other`, `Remote OK` or `Unknown`, with the canonical location.
The ✓ line ends with `open to work N Yes / M Likely`.

Points per dimension never exceed its weight. Guide (scale to the weight):
- full: explicit, specific evidence (named skill in a role, years stated, matching city)
- ~half: indirect or partial (adjacent skill, years estimated from a timeline, nearby city)
- 0: no evidence. Absence of evidence is 0, not a guess.

`mandatory_missing` lists mandatory skills with **no** evidence (the script caps the total at 40).
Confidence: High = several specific facts from a full profile or page; Medium = a rich snippet;
Low = a headline only. 70+ with Low is rejected by validation — lower the score or find more evidence.
score.py enforces the evidence rule (2026-10-15): High needs 2+ independent sources (one corroboration
fetched via `scripts/evidence.py plan`, or GitHub / Stack Exchange API data from `scripts/dev_evidence.py fetch` for a
profile the candidate links); Medium caps at 85, Low at 60; the breakdown shows `Evidence Sources n`.
