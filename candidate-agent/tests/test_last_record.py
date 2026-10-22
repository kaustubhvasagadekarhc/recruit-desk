"""last_record.json: the last NEW record, minimal fields, no network (2026-10-19). Temp copies only."""
from __future__ import annotations

import hashlib
import json
import sys
from unittest import mock

from helpers import JD, TempTestCase, candidate

import common
import entities
import excel_upsert
import last_record
import memory
from test_structured_crm import scored


class LastRecord(TempTestCase):
    def setUp(self):
        super().setUp()
        self.db, self.wb = self.tmp / "memory.db", self.tmp / "wb.xlsx"
        self.file = self.tmp / "last_record.json"
        self._p = [mock.patch.object(m, n, v) for m, n, v in (
            (excel_upsert, "DB_PATH", self.db), (excel_upsert, "WORKBOOK_PATH", self.wb), (memory, "DB_PATH", self.db),
            (memory, "WORKBOOK_PATH", self.wb), (common, "DB_PATH", self.db), (entities, "DB_PATH", self.db), (entities, "WORKBOOK_PATH", self.wb))]
        for p in self._p:
            p.start()
        self.con = memory.connect()
        memory.add_jd(self.con, JD)
        self.con.commit()

    def tearDown(self):
        self.con.close()
        for p in self._p:
            p.stop()
        super().tearDown()

    def upsert(self, cands, *extra):
        p = self.write_json("b.json", {"jd_id": JD["id"], "candidates": cands})
        with mock.patch.object(sys, "argv", ["excel_upsert.py", "--input", str(p), *extra]):
            excel_upsert.main()

    def doc(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_the_last_new_candidate_of_a_batch_is_written_with_minimal_fields(self):
        self.upsert([scored(), scored(candidate_name="Other One", profile_url="https://www.linkedin.com/in/other-one")])
        d = self.doc()
        assert d["agent"] == "candidate"
        assert d["record"]["name"] == "Other One" and d["record"]["jd"] == JD["title"]
        assert set(d["record"]) <= set(last_record.FIELDS)
        assert d["subject_hash"] == hashlib.sha256(b"candidate:url:https://www.linkedin.com/in/other-one").hexdigest()
        assert d["created_at"].endswith("+00:00")

    def test_no_contact_details_evidence_or_raw_key_in_the_file(self):
        self.upsert([scored(email_mobile="a@b.com", contact_source_url="https://x.test", evidence_notes="Headline: senior React engineer")], "--contacts")
        text = self.file.read_text(encoding="utf-8")
        assert "a@b.com" not in text and "Headline" not in text and "x.test" not in text
        assert "url:https" not in text, "the dedupe key is only in hashed form"
        assert {"email_mobile", "evidence_notes", "key_skills", "score_breakdown", "contact_source_url"}.isdisjoint(self.doc()["record"])

    def test_a_rerun_that_only_updates_leaves_the_file_alone(self):
        self.upsert([scored()])
        first = self.file.read_text(encoding="utf-8")
        self.upsert([scored()])   # same person: an update, not a new record
        assert self.file.read_text(encoding="utf-8") == first

    def test_a_later_new_candidate_replaces_it(self):
        self.upsert([scored()])
        self.upsert([scored(candidate_name="Second Person", profile_url="https://www.linkedin.com/in/second")])
        assert self.doc()["record"]["name"] == "Second Person"

    def test_a_dry_run_writes_nothing(self):
        self.upsert([scored()], "--dry-run")
        assert not self.file.exists()

    def test_forgetting_that_person_removes_the_file_but_forgetting_someone_else_does_not(self):
        self.upsert([scored(), scored(candidate_name="Other One", profile_url="https://www.linkedin.com/in/other-one")])
        out = memory.forget(self.con, "https://www.linkedin.com/in/test-person-1")
        assert out["last_record_removed"] is False and self.file.exists()
        out = memory.forget(self.con, "https://www.linkedin.com/in/other-one")
        assert out["last_record_removed"] is True and not self.file.exists()

    def test_clean_drops_what_is_not_listed_or_looks_like_contact_details(self):
        c = last_record.clean({"name": "Ann", "email": "a@b.co", "title": "Mail a@b.co", "company": "Call +91 98765 43210",
                               "profile_url": "https://x.test/jobs/4739199005", "match_score": 80, "location": "x" * 500, "jd": True})
        assert c == {"name": "Ann", "profile_url": "https://x.test/jobs/4739199005", "match_score": 80, "location": "x" * 300}

    def test_read_of_a_missing_or_damaged_file_is_none(self):
        assert last_record.read(self.file) is None
        self.file.write_text("{not json")
        assert last_record.read(self.file) is None
