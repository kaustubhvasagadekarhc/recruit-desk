---
name: draft-outreach
description: Draft first messages for a JD's shortlisted people, each resting on one cited public fact, with the GDPR Art. 14 notice appended by a script. Nothing is sent. Use for "draft outreach", "write messages for the shortlist", "shortlist export" or "tune the weights".
---

# Draft outreach, shortlist export, weight tuning

Nothing here contacts anyone, looks up an email address or changes a stored score. A recruiter sends the messages by hand.

## 1. Draft messages for the shortlist

```bash
uv run scripts/outreach.py plan --jd <jd_id> --company "<hiring company>" [--statuses Shortlisted,Contacted]
```
Prints each person with a matching Recruiter Status and their `facts`: rows of the Evidence sheet, each with an `id`, the fact,
the exact quote and its source. Ask the user for the hiring company if you do not know it.

For each person with a fact, write a short message (under 900 characters, plain, no flattery, no promise of a job):
- open with one thing from the cited fact, repeating its **quote exactly** (the check below compares them);
- say what the role is and ask one easy question;
- no email address or phone number, no claim the plan does not hold; a person with no fact gets no draft (say so).

Write `runs/outreach-<jd_id>.json` as `{"drafts": [{"key": "<key>", "fact_id": "<id>", "message": "..."}]}`, then:
```bash
uv run scripts/outreach.py write --jd <jd_id> --company "<hiring company>" --input runs/outreach-<jd_id>.json
```
It refuses a draft whose fact is not that person's, whose message does not repeat the cited quote, or that holds an email or
phone number, and it appends the Art. 14 notice to every draft itself. The result is `exports/<jd_id>-outreach-<date>.md`.
Report the path and how many drafts; do not paste the file.

## 2. Shortlist export

```bash
uv run scripts/export_shortlist.py --jd <jd_id> [--statuses Shortlisted,Contacted] [--format xlsx|csv]
```
Writes `exports/<jd_id>-shortlist-<date>.xlsx`: name, title, company, location, score, confidence, open to work, profile link,
up to 3 cited facts, status. No email or phone. Contacts go out only through `export_crm.py`.

## 3. Weight tuning (suggestion only)

```bash
uv run scripts/tune_weights.py --jd <jd_id>
```
Needs 5 Shortlisted/Contacted and 5 Rejected people with scores; otherwise it says how many more it needs. It prints, per
dimension, the share of its weight each group earned and a suggested shift of at most 5 points. **Never run it with `--apply`**:
the human does that from a terminal after reading the suggestion. Existing scores change only when the JD is scored again.
Report the suggestion and say that a small sample is a hint.
