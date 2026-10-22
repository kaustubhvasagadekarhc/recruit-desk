"""score.py: deterministic experience and location, gates, quotes, range errors, spread warning."""
from __future__ import annotations

import json
import sys
from unittest import mock

from helpers import JD, TempTestCase, candidate

import score

W, CRIT = JD["weights"], JD["criteria"]


class ParseYears(TempTestCase):
    def test_variants(self):
        self.assertEqual(score.parse_years("7+ yrs (self-stated)"), (7, False))
        self.assertEqual(score.parse_years("~6+ yrs (estimated from timeline)"), (6, True))
        self.assertEqual(score.parse_years("~4 yrs (estimated from timeline: Nov 2022 onward)"), (4, True))
        self.assertEqual(score.parse_years("14+ yrs (self-stated; 9+ in React/Next.js)"), (14, False))
        self.assertEqual(score.parse_years("5.5 years"), (5.5, False))
        self.assertEqual(score.parse_years(""), (None, False))
        self.assertEqual(score.parse_years("senior"), (None, False))


class Experience(TempTestCase):
    rng = {"min": 5, "max": 9}

    def pts(self, text):
        return score.experience_points(text, self.rng, 20)

    def test_inside_range_full(self):
        for t in ("5+ yrs", "7 yrs", "9 yrs"):
            self.assertEqual(self.pts(t)[0], 20, t)

    def test_below_minimum_falls_off_and_soft_gates(self):
        p, _, soft = self.pts("4+ yrs")
        self.assertEqual((p, soft), (15, True))
        p, _, soft = self.pts("4.5 yrs")
        self.assertEqual(soft, False)
        self.assertEqual(self.pts("1 yr")[0], 0)

    def test_above_maximum_falls_off_gently(self):
        self.assertEqual(self.pts("10+ yrs")[0], 18)
        self.assertEqual(self.pts("14 yrs")[0], 10)
        self.assertFalse(self.pts("14 yrs")[2])

    def test_estimate_gets_half(self):
        self.assertEqual(self.pts("~6 yrs (estimated)")[0], 10)

    def test_no_years_is_zero(self):
        self.assertEqual(self.pts("")[:2], (0, "no years stated"))


class Location(TempTestCase):
    def test_same_city_variants(self):
        for t in ("Pune, Maharashtra, India", "Pune District, Maharashtra, India", "Pune/Pimpri-Chinchwad Area, India", "Hinjewadi"):
            self.assertEqual(score.location_points(t, CRIT, 10), (10, "same city"), t)

    def test_same_region_half_other_zero(self):
        self.assertEqual(score.location_points("Mumbai, Maharashtra", CRIT, 10), (5, "same region"))
        self.assertEqual(score.location_points("Bangalore, Karnataka", CRIT, 10), (0, "other city"))
        self.assertEqual(score.location_points("", CRIT, 10), (0, "no location"))

    def test_remote_allowed_full(self):
        crit = dict(CRIT, work_mode="Fully remote")
        self.assertEqual(score.location_points("Bangalore", crit, 10)[0], 10)
        crit = dict(CRIT, location_rules={"remote_ok": True})
        self.assertEqual(score.location_points("Kolkata", crit, 10)[0], 10)

    def test_unknown_city_matches_on_first_part(self):
        crit = dict(CRIT, location="Coimbatore, India")
        self.assertEqual(score.location_points("Coimbatore, Tamil Nadu", crit, 10)[0], 10)


class Score(TempTestCase):
    def test_total_and_breakdown(self):
        c = candidate()
        score.score(c, W, CRIT)
        self.assertEqual(c["match_score"], 30 + 20 + 10 + 8 + 10 + 6)
        self.assertIn("Experience 20/20 (7 yrs, JD 5-9)", c["score_breakdown"])
        self.assertIn("Location 10/10 (same city)", c["score_breakdown"])

    def test_llm_experience_and_location_points_are_replaced(self):
        c = candidate(experience="4+ yrs", scores={"core_skills": 35, "experience": 20, "location": 0,
                                                   "domain_projects": 15, "seniority": 10, "preferred_skills": 10})
        score.score(c, W, CRIT)
        self.assertEqual(c["scores"]["experience"], 15)
        self.assertEqual(c["scores"]["location"], 10)
        self.assertEqual(c["match_score"], 65)
        self.assertIn("SOFT GATE", c["score_breakdown"])

    def test_mandatory_gate_caps_at_40(self):
        c = candidate(mandatory_missing=["TypeScript"])
        score.score(c, W, CRIT)
        self.assertEqual(c["match_score"], 40)
        self.assertIn("GATE: missing mandatory TypeScript", c["score_breakdown"])

    def test_low_confidence_capped_below_70(self):
        c = candidate(evidence_confidence="Low")
        score.score(c, W, CRIT)
        self.assertEqual(c["match_score"], 60)  # 69 until 2026-10-15; plan item 6 caps Low at 60

    def test_location_normalised_and_original_kept(self):
        c = candidate(location="Pune/Pimpri-Chinchwad Area, India")
        score.score(c, W, CRIT)
        self.assertEqual(c["location"], "Pune, Maharashtra, India")
        self.assertEqual(c["location_raw"], "Pune/Pimpri-Chinchwad Area, India")
        self.assertIn("listed as 'Pune/Pimpri-Chinchwad Area, India'", c["score_breakdown"])
        score.score(c, W, CRIT)  # idempotent on a rerun
        self.assertEqual(c["location_raw"], "Pune/Pimpri-Chinchwad Area, India")

    def test_range_errors_exit(self):
        with self.assertRaises(SystemExit):
            score.score(candidate(scores={"core_skills": 99}), W, CRIT)
        with self.assertRaises(SystemExit):
            score.score(candidate(scores={"core_skills": -1}), W, CRIT)
        with self.assertRaises(SystemExit):
            score.score(candidate(scores={"charisma": 5}), W, CRIT)

    def test_quotes_must_exist_in_evidence(self):
        ok = candidate(quotes={"core_skills": ["React | TypeScript"], "domain_projects": ["Acme"],
                               "seniority": ["Senior Frontend Engineer"], "preferred_skills": ["Next.js"]})
        score.score(ok, W, CRIT)
        bad = candidate(quotes={"core_skills": ["10 years of Angular"]})
        with self.assertRaises(SystemExit):
            score.score(bad, W, CRIT)
        missing = candidate(quotes={"core_skills": ["React"]})  # seniority has points but no quote
        with self.assertRaises(SystemExit):
            score.score(missing, W, CRIT)


class Spread(TempTestCase):
    def test_percentiles(self):
        sp = score.spread([50, 74, 76, 77, 81, 81, 81, 83, 85, 87, 87, 88, 89, 93, 94])
        self.assertEqual((sp["p10"], sp["p50"], sp["p90"]), (74, 83, 93))
        self.assertEqual(score.spread([]), {})

    def test_warning_only_when_p50_above_75(self):
        self.assertIsNotNone(score.spread_warning(score.spread([76, 80, 90])))
        self.assertIsNone(score.spread_warning(score.spread([40, 75, 90])))

    def test_main_prints_spread_and_warning(self):
        batch = {"jd_id": "no-such-jd", "candidates": [candidate(profile_url=f"https://x.dev/{i}") for i in range(3)]}
        p = self.write_json("batch.json", batch)
        with mock.patch.object(score, "jd_for", return_value=(W, CRIT)), \
             mock.patch.object(sys, "argv", ["score.py", "--input", str(p)]):
            score.main()
        out = self._out._new_target.getvalue()
        self.assertIn("spread p10 84", out)
        self.assertIn("not discriminating", out)
        self.assertEqual({c["match_score"] for c in json.loads(p.read_text())["candidates"]}, {84})
