"""Shared fixtures for the pipeline unit tests: every test runs on temp copies, never on
memory.db, Candidate_Research.xlsx or runs/.current.json."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import progress  # noqa: E402

JD = {
    "id": "test-react-pune",
    "title": "Senior React Frontend Developer",
    "criteria": {
        "mandatory_skills": ["React", "TypeScript", "JavaScript"],
        "preferred_skills": ["Next.js"],
        "experience_years": {"min": 5, "max": 9},
        "location": "Pune, India",
        "work_mode": "Hybrid, 3 days in office",
        "role_synonyms": ["Senior Frontend Engineer", "Senior UI Developer"],
    },
    "weights": {"core_skills": 35, "experience": 20, "domain_projects": 15, "seniority": 10, "location": 10, "preferred_skills": 10},
}


def candidate(**over) -> dict:
    c = {
        "candidate_name": "Test Person",
        "current_role_company": "Senior Frontend Engineer @ Acme",
        "location": "Pune, Maharashtra, India",
        "experience": "7+ yrs (self-stated)",
        "key_skills": "React, TypeScript, Next.js",
        "scores": {"core_skills": 30, "domain_projects": 10, "seniority": 8, "preferred_skills": 6},
        "mandatory_missing": [],
        "evidence_confidence": "Medium",
        "profile_url": "https://www.linkedin.com/in/test-person-1",
        "email_mobile": "",
        "contact_source_url": "",
        "source": "fixture",
        "evidence_notes": "Headline: 'Senior Frontend Engineer | React | TypeScript | 7+ YOE'.",
        "date_found": "2026-10-15",
        "recruiter_status": "New",
        # key factors (2026-10-15): score.py fills these; upsert fixtures need them to pass validation
        "open_to_work": "Unknown",
        "open_to_work_quote": "",
        "open_to_work_match": "Unknown",
        "stack_match": "3/3 must-have: React, TypeScript, JavaScript; missing: none",
        "experience_match": "7 yrs vs 5-9 required: in range",
        "location_match": "Same city · Pune, Maharashtra, India",
    }
    c.update(over)
    return c


class TempTestCase(unittest.TestCase):
    """Points progress state at a temp folder and silences stdout."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cand-test-"))
        self._state = progress.STATE
        progress.STATE = self.tmp / ".current.json"
        self._out = contextlib.redirect_stdout(io.StringIO())
        self._out.__enter__()

    def tearDown(self) -> None:
        self._out.__exit__(None, None, None)
        progress.STATE = self._state

    def write_json(self, name: str, data: dict) -> Path:
        p = self.tmp / name
        p.write_text(json.dumps(data))
        return p
