"""Key factors (2026-10-15): Open to Work, Stack Match, Experience Match, Location Match. Temp copies only."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from unittest import mock

from openpyxl import Workbook, load_workbook

from helpers import JD, TempTestCase, candidate

import common
import excel_upsert
import memory
import score

W, CRIT = JD["weights"], JD["criteria"]


def scored(**over) -> dict:
    """A fixture candidate without precomputed factors, scored by score.py."""
    c = candidate(**over)
    for f in ("open_to_work", "open_to_work_quote", *common.FACTOR_FIELDS):
        c.pop(f, None)
    if "open_to_work_quote" in over:
        c["open_to_work_quote"] = over["open_to_work_quote"]
    score.score(c, W, CRIT)
    return c


class OpenToWorkDetector(TempTestCase):
    def test_every_phrase(self):
        cases = {
            "Senior FE | #OpenToWork | React": ("Yes", "#OpenToWork"),
            "Open to work · React, TypeScript": ("Yes", "Open to work"),
            "Currently actively looking for a frontend role": ("Yes", "actively looking"),
            "Looking for new opportunities in Pune": ("Yes", "Looking for new opportunities"),
            "React developer, immediate joiner": ("Yes", "immediate joiner"),
            "Frontend Performance Expert | Open to Opportunities": ("Yes", "Open to Opportunities"),
            "Currently open to Senior Frontend Engineer roles in Pune": ("Yes", "open to Senior Frontend Engineer roles"),
            "Senior Engineer Lead Open to frontend leadership roles": ("Yes", "Open to frontend leadership roles"),
            "Serving notice period, LWD 30 Nov": ("Likely", "Serving notice period"),
            "Notice period: 30 days": ("Likely", "Notice period: 30 days"),
            "notice period 60 days": ("Likely", "notice period 60 days"),
        }
        for text, want in cases.items():
            self.assertEqual(common.detect_open_to_work(text), want, text)

    def test_negations_and_absence_are_not_signals(self):
        for text in ("I am not open to work right now", "No longer looking for new opportunities",
                     "Isn't actively looking", "Not open to new roles", "open to feedback and learning", "Senior Frontend Engineer | React | 7+ YOE",
                     "8 years at Acme, promoted twice", ""):
            self.assertIsNone(common.detect_open_to_work(text), text)

    def test_explicit_signal_beats_notice_period(self):
        self.assertEqual(common.detect_open_to_work("Notice period: 30 days. #OpenToWork")[0], "Yes")

    def test_score_adds_points_and_keeps_cap_100(self):
        mid = {"scores": {"core_skills": 20, "domain_projects": 8, "seniority": 6, "preferred_skills": 4}}   # 68, under the caps
        base = scored(**mid)
        yes = scored(evidence_notes="Headline: 'Senior Frontend Engineer | React | TypeScript | 7+ YOE | #OpenToWork'.", **mid)
        likely = scored(evidence_notes="Headline: 'Senior Frontend Engineer | React | 7+ YOE'. Notice period: 30 days.", **mid)
        self.assertEqual((base["open_to_work"], base["open_to_work_match"]), ("Unknown", "Unknown"))
        self.assertIn("Open to work Unknown (no signal, no penalty)", base["score_breakdown"])
        self.assertEqual(yes["match_score"], base["match_score"] + 5)
        self.assertEqual(likely["match_score"], base["match_score"] + 2)
        self.assertEqual(yes["open_to_work_match"], 'Yes: "#OpenToWork"')
        self.assertEqual(likely["open_to_work_match"], 'Likely: "Notice period: 30 days"')
        top = scored(evidence_notes="Lead React engineer #OpenToWork",
                     scores={"core_skills": 35, "domain_projects": 15, "seniority": 10, "preferred_skills": 10},
                     facts=["x"], corroboration={"url": "https://github.com/tp", "source_type": "GitHub", "snippet": "React apps",
                                                 "confirms": [{"fact": "React work", "quote": "React apps"}]})
        self.assertEqual(top["match_score"], 100)

    def test_confidence_caps_still_apply(self):
        c = scored(evidence_confidence="Low", evidence_notes="Senior Frontend Engineer #OpenToWork")
        self.assertEqual(c["open_to_work"], "Yes")
        self.assertLessEqual(c["match_score"], 60)

    def test_unknown_is_never_a_penalty_even_with_tenure(self):
        c = scored(evidence_notes="Senior Frontend Engineer | React | 9 years at the same company")
        self.assertEqual(c["open_to_work"], "Unknown")
        self.assertEqual(c["match_score"], scored()["match_score"])

    def test_supplied_quote_is_verified(self):
        c = scored(evidence_notes="Senior FE, React. Immediate joiner.", open_to_work_quote="Immediate joiner")
        self.assertEqual(c["open_to_work"], "Yes")
        with self.assertRaises(SystemExit):   # not in the evidence: fabricated
            scored(open_to_work_quote="#OpenToWork")
        with self.assertRaises(SystemExit):   # in the evidence but not a signal
            scored(open_to_work_quote="Senior Frontend Engineer")

    def test_validator_checks_the_quote(self):
        c = scored(evidence_notes="Headline: 'React dev | #OpenToWork'.")
        self.assertEqual(common.validate_factors(c), [])
        self.assertTrue(common.validate_factors(dict(c, open_to_work_quote="#OpenToWork now")))
        self.assertTrue(common.validate_factors(dict(c, open_to_work_quote="")))
        self.assertTrue(common.validate_factors(dict(c, open_to_work="Likely")))
        self.assertTrue(common.validate_factors(dict(c, open_to_work="Maybe")))
        self.assertTrue(common.validate_factors(dict(c, stack_match="")))
        self.assertTrue(common.validate_factors(dict(c, location_match="Near · Pune")))

    def test_open_to_work_query_is_optional_and_nameless(self):
        q = common.open_to_work_query(CRIT)
        self.assertEqual(q, '"open to work" React developer Pune site:linkedin.com/in')
        self.assertNotIn(q, common.plan_queries(JD["title"], CRIT))


class FactorStrings(TempTestCase):
    def test_stack_match(self):
        c = scored(evidence_notes="Senior FE | React | TypeScript | Next.js | Redux Toolkit | Jest")
        self.assertEqual(c["stack_match"],
                         "3/3 must-have: React, TypeScript, JavaScript; missing: none · preferred 1/1: Next.js · core 30/35, preferred 6/10")
        g = scored(mandatory_missing=["JavaScript"])
        self.assertTrue(g["stack_match"].startswith("2/3 must-have: React, TypeScript; missing: JavaScript"))

    def test_stack_match_tolerates_spelling(self):
        self.assertTrue(score.skill_named("Next.js", "nextjs, react"))
        self.assertTrue(score.skill_named("Jest / React Testing Library", "react testing library"))
        self.assertFalse(score.skill_named("Java", "javascript"))

    def test_experience_match(self):
        r = {"min": 5, "max": 9}
        self.assertEqual(score.experience_match("6+ yrs", r), "6 yrs vs 5-9 required: in range")
        self.assertEqual(score.experience_match("~3 yrs (estimated)", {"min": 5}), "3 yrs (estimate) vs 5+ required: 2 below")
        self.assertEqual(score.experience_match("14 yrs", r), "14 yrs vs 5-9 required: 5 above")
        self.assertEqual(score.experience_match("", r), "not stated vs 5-9 required")
        self.assertEqual(score.experience_match("6 yrs", {}), "6 yrs (JD sets no range)")

    def test_location_match(self):
        self.assertEqual(scored()["location_match"], "Same city · Pune, Maharashtra, India")
        self.assertEqual(scored(location="Mumbai, India")["location_match"], "Same region · Mumbai, Maharashtra, India")
        self.assertEqual(scored(location="Bengaluru")["location_match"], "Other · Bengaluru, Karnataka, India")
        self.assertEqual(scored(location="")["location_match"], "Unknown")
        c = candidate(location="Bengaluru")
        score.score(c, W, dict(CRIT, location_rules={"remote_ok": True}))
        self.assertEqual(c["location_match"], "Remote OK · Bengaluru, Karnataka, India")


class Migration(TempTestCase):
    def setUp(self):
        super().setUp()
        self.db, self.wb = self.tmp / "memory.db", self.tmp / "wb.xlsx"
        con = sqlite3.connect(self.db)
        con.executescript(common.SCHEMA_PATH.read_text())
        con.close()
        self._p = [mock.patch.object(excel_upsert, "DB_PATH", self.db), mock.patch.object(excel_upsert, "WORKBOOK_PATH", self.wb),
                   mock.patch.object(memory, "DB_PATH", self.db), mock.patch.object(memory, "WORKBOOK_PATH", self.wb),
                   mock.patch.object(common, "DB_PATH", self.db)]
        for p in self._p:
            p.start()
        jd = self.write_json("jd.json", JD)
        with mock.patch.object(sys, "argv", ["memory.py", "add-jd", "--file", str(jd)]):
            memory.main()

    def tearDown(self):
        for p in self._p:
            p.stop()
        super().tearDown()

    def upsert(self, cands):
        path = self.write_json("b.json", {"jd_id": JD["id"], "candidates": cands})
        with mock.patch.object(sys, "argv", ["excel_upsert.py", "--input", str(path)]):
            excel_upsert.main()

    def old_sheet(self, n: int):
        wb = Workbook()
        ws = wb.active
        ws.title = JD["title"]
        ws.append(common.HEADERS[:n])
        old = candidate(candidate_name="Old Row", evidence_sources=1)
        ws.append([old.get(f, "") or None for f, _, _ in common.COLUMNS[:n]])
        wb.save(self.wb)

    def test_15_and_14_column_sheets_get_the_four_columns_at_the_end(self):
        for n in (15, 14):
            self.old_sheet(n)
            new = scored(candidate_name="New Row", profile_url="https://github.com/new",
                         evidence_notes="React dev #OpenToWork")
            self.upsert([new])
            ws = load_workbook(self.wb)[JD["title"]]
            hdr = [c.value for c in ws[1]]
            self.assertEqual(hdr, common.HEADERS)
            self.assertEqual(hdr[15:19], ["Open to Work", "Stack Match", "Experience Match", "Location Match"])
            self.assertEqual(ws.max_row, 3)
            self.assertIsNone(ws.cell(2, hdr.index("Location Match") + 1).value)   # old row: blank until rescored
            self.assertEqual(ws.cell(3, hdr.index("Open to Work") + 1).value, 'Yes: "#OpenToWork"')
            self.assertEqual(ws.cell(3, hdr.index("Location Match") + 1).value, "Same city · Pune, Maharashtra, India")

    def test_unknown_header_is_refused(self):
        wb = Workbook()
        ws = wb.active
        ws.title = JD["title"]
        ws.append(common.HEADERS[:15] + ["Something else"])
        wb.save(self.wb)
        with self.assertRaises(SystemExit):
            self.upsert([scored()])

    def test_batch_without_factors_is_rejected(self):
        c = candidate()
        c.pop("stack_match")
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit):
            self.upsert([c])
        self.assertFalse(self.wb.exists())

    def test_run_finish_reports_open_to_work_counts(self):
        self.upsert([scored(evidence_notes="React #OpenToWork"),
                     scored(candidate_name="B", profile_url="https://github.com/b", evidence_notes="React. Notice period: 30 days"),
                     scored(candidate_name="C", profile_url="https://github.com/c")])
        self.assertEqual(memory.open_to_work_counts(JD["title"], self.wb), {"Yes": 1, "Likely": 1, "rows": 3})
        buf = io.StringIO()
        con = sqlite3.connect(self.db)
        rid = con.execute("INSERT INTO runs (jd_id, started_at, queries_json) VALUES (?,?,?)", (JD["id"], "now", "[]")).lastrowid
        con.commit()
        con.close()
        with mock.patch.object(sys, "argv", ["memory.py", "run-finish", "--run", str(rid)]), redirect_stdout(buf):
            memory.main()
        self.assertIn("open to work: 1 Yes, 1 Likely of 3 rows in the sheet", buf.getvalue())
        out = json.loads(buf.getvalue()[buf.getvalue().index("\n{") + 1:])
        self.assertEqual(out["open_to_work"]["Yes"], 1)
