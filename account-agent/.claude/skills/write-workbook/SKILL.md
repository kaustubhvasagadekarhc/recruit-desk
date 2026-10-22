---
name: write-workbook
description: Steps 5 and 7 of /account-scan. Write the draft row, run assemble.py (roles cell, team.csv match, validation), then excel_upsert.py.
---

# Draft → rows → workbook

## Step 5: the draft

`start 5`, then Write `runs/<slug>.draft.json` (the hook validates it on Write):

```json
{
  "meta": {"company": "Razorpay", "domain": "razorpay.com", "list": "Accounts"},
  "rows": [{
    "Company": "Razorpay",
    "Domain": "razorpay.com",
    "Product Type": "Fintech; Platform / API",
    "What They Build (brief)": "... [1]",
    "Company Brief (founded, size, customers, milestones)": "Founded 2014 [2]; ...",
    "HQ / US Office": "...",
    "India Office": "...",
    "Product?": "Yes",
    "Funding": "...",
    "India Connection (US-based leader)": "No evidence found",
    "Who to Contact": "Name, Title [n]",
    "Leadership Education & Background": "...",
    "Sources": "[1] https://razorpay.com/about/\n[2] https://www.wikidata.org/wiki/Q109985545",
    "Confidence": "Medium",
    "_leaders": [
      {"name": "...", "title": "CTO", "location": "Bengaluru, India", "employers": ["..."], "colleges": ["..."],
       "cities": [], "communities": [], "conferences": ["SaaStr 2026"], "industries": [], "cite": "[3]"}
    ],
    "_contacts": [
      {"name": "...", "title": "CTO", "location": "Bengaluru, India", "source": "https://razorpay.com/about/",
       "verified": "2026-10-16", "email": "Not published (checked: about page, 2 Exa searches)",
       "phone": {"value": "+91 ...", "kind": "company line", "quote": "<words containing it>", "source": "https://razorpay.com/contact/"},
       "linkedin": "https://www.linkedin.com/in/...", "x": "https://x.com/...",
       "channels_checked": "about page, 3 Exa searches"}
    ],
    "_channels": {"press_email": {"value": "press@...", "quote": "<words containing it>", "source": "https://..."},
                  "contact_form": {"value": "https://.../contact", "source": "https://.../contact"},
                  "offices": [{"label": "HQ", "address": "...", "phone": "...", "source": "...", "quote": "..."}],
                  "checked": "contact, press, investors pages"}
  }]
}
```

- **No empty research cell.** Product Type, What They Build, Company Brief, HQ / US Office, India Office,
  Product?, Funding, India Connection, Who to Contact and Leadership hold a cited fact or, after the field's
  ladder was tried, `Not found (checked: <sources tried>)` (India Connection: `No evidence found (checked: ...)`).
  The hook refuses an empty one, a bare `No evidence found`, a Company Brief with no size or
  `size not found (checked: ...)`, a Funding round 2+ years old without a newer round or `No newer round found
  (checked: ...)`, and a CTO conflict without a `(conflict: ...)` note.
- Do not write Development Requirement, Warm Connection, Best Person, Conversation Starter or Last Checked: the
  scripts fill them.
- Leave out the typed columns (Founded Year, Employees, ... Ticker) and the H-1B columns: excel_upsert.py fills them
  from your cited prose and the LCA index. If you do set a typed column it must be a number / ISO date / allowed value
  that a cited clause of the row states, or the hook refuses the row.
- **Confidence**: High = most fields from official pages or filings; Medium = a mix with news / snippets;
  Low = mostly snippets or many blanks.
- `_leaders` holds only evidenced facts (each leader's `cite` points at the source of those facts). It feeds the
  team.csv matcher and is never written to the sheet.
- `_contacts` and `_channels` come from the `contacts` skill and go to the `Contacts` and `Company channels`
  sheets (linked by Domain). The hook refuses an email or phone without a source URL and a quote that contains it.

Then: `uv run python scripts/assemble.py --draft runs/<slug>.draft.json`. It adds the roles cell, the team.csv
match (or `add team.csv`), Last Checked, validates, writes `runs/<slug>.rows.json`, prints the row and its ✓ line.
On a validation error: fix the draft (Write it again whole) and rerun.

## Step 7: Excel

`start 7`, then `uv run python scripts/excel_upsert.py --rows runs/<slug>.rows.json [--list "<sheet>"] [--contacts]`
(`--contacts` only when the human passed it to /account-scan). One row
per company per sheet, matched by Domain, updated in place on reruns.
