---
name: parse-jd
description: Parse a job description into structured criteria and scoring weights, save jds/<jd_id>.json and register it in memory.db. Use as step 1 of /scout or alone.
---

# Parse a JD

1. Get the text: pasted as is; a file with Read; a URL with Exa `web_fetch_exa` (public pages only).
2. Write `jds/<jd_id>.json` (`jd_id`: lowercase slug of role + location, e.g. `senior-react-frontend-pune`):

```json
{
  "id": "senior-react-frontend-pune",
  "title": "Senior React Frontend Developer",
  "source": "pasted | <path> | <url>",
  "raw_text": "<the JD>",
  "criteria": {
    "mandatory_skills": ["React", "TypeScript"],
    "preferred_skills": ["Next.js", "testing"],
    "experience_years": {"min": 5, "max": 9},
    "seniority": "Senior IC",
    "location": "Pune, India", "work_mode": "Hybrid",
    "industry": "", "exclusions": [],
    "role_synonyms": ["Senior Frontend Engineer", "Senior UI Developer", "React Lead"]
  },
  "weights": {"core_skills": 35, "experience": 20, "domain_projects": 15, "seniority": 10, "location": 10, "preferred_skills": 10}
}
```

3. Adjust weights to the JD (they must sum to 100; keep the six keys). Only put in criteria what the JD
   says; leave the rest empty.
4. `uv run scripts/memory.py add-jd --file jds/<jd_id>.json` — prints the worksheet name (31 chars max,
   safe characters, unique). Inside /scout it also prints `✓ Step 1/6 · JD '<id>' registered · sheet '<name>' · n mandatory, m preferred skills`.
   If the JD cannot be read or parsed: `uv run scripts/progress.py fail 1 "<reason>"` and ask the user.
