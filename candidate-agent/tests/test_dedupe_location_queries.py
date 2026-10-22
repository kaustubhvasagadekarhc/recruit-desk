"""dedupe.py (URL variants, merge), location normalisation and the discovery query plan."""
from __future__ import annotations

import json
import sys
from unittest import mock

from helpers import JD, TempTestCase, candidate

import common
import dedupe


class UrlVariants(TempTestCase):
    def test_linkedin_variants_share_a_key(self):
        a = common.dedupe_key("https://www.linkedin.com/in/Vikranti-Jadhav-54510b220/")
        for v in ("https://in.linkedin.com/in/vikranti-jadhav-54510b220/?trk=public_profile",
                  "http://linkedin.com/in/Vikranti-Jadhav-54510b220#about",
                  "www.linkedin.com/in/vikranti-jadhav-54510b220"):
            self.assertEqual(common.dedupe_key(v), a, v)

    def test_no_url_falls_back_to_name_and_company(self):
        self.assertEqual(common.row_key("", "A. Person", "Dev @ Acme Ltd"), common.row_key("", "a person", "Lead at ACME LTD"))
        self.assertNotEqual(common.row_key("", "A Person", "Dev @ Acme"), common.row_key("", "A Person", "Dev @ Other"))


class Dedupe(TempTestCase):
    def run_dedupe(self, cands):
        p = self.write_json("batch.json", {"jd_id": "x", "candidates": cands})
        with mock.patch.object(dedupe, "DB_PATH", self.tmp / "none.db"), \
             mock.patch.object(sys, "argv", ["dedupe.py", "--input", str(p)]):
            dedupe.main()
        return json.loads(p.read_text())["candidates"]

    def test_variant_url_merged_into_first(self):
        first = candidate(profile_url="https://www.linkedin.com/in/test-person-1", email_mobile="", evidence_notes="first")
        dup = candidate(profile_url="https://in.linkedin.com/in/Test-Person-1/?trk=x", evidence_notes="second",
                        key_skills="ignored because first has skills")
        other = candidate(profile_url="https://github.com/someone-else")
        out = self.run_dedupe([first, dup, other])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["evidence_notes"], "first | second")
        self.assertEqual(out[0]["key_skills"], "React, TypeScript, Next.js")
        self.assertFalse(out[0]["_known"])


class LocationNormalise(TempTestCase):
    def test_one_canonical_form(self):
        for t in ("Pune District, Maharashtra, India", "Pune/Pimpri-Chinchwad Area, India", "Pune, Maharashtra, India",
                  "pune", "Hinjawadi, Pune"):
            self.assertEqual(common.normalise_location(t), "Pune, Maharashtra, India", t)
        self.assertEqual(common.normalise_location("Bangalore Urban, Karnataka"), "Bengaluru, Karnataka, India")
        self.assertEqual(common.normalise_location("  Coimbatore,  India "), "Coimbatore, India")
        self.assertEqual(common.canonical_city("Navi Mumbai, Maharashtra"), "mumbai")
        self.assertEqual(common.region_of("Thane"), "maharashtra")


class QueryPlan(TempTestCase):
    def test_uses_synonyms_city_and_skills(self):
        qs = common.plan_queries(JD["title"], JD["criteria"], ["Frontend Lead", "senior ui developer"])
        self.assertEqual(qs[0], "category:people Senior React Frontend Developer Pune React TypeScript JavaScript")
        self.assertIn("site:linkedin.com/in Senior Frontend Engineer Pune React", qs)
        self.assertIn("site:linkedin.com/in Frontend Lead Pune React", qs)
        self.assertEqual(sum("Senior UI Developer".lower() in q.lower() for q in qs), 1)  # case-insensitive dedupe
        self.assertLessEqual(len(qs), 6)
        self.assertFalse(any("linkedin.com/in/" in q for q in qs))

    def test_limit(self):
        self.assertEqual(len(common.plan_queries("T", {"location": "Pune"}, [], limit=2)), 2)
