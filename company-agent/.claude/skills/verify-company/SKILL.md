---
name: verify-company
description: Confirm a company's official website, careers page and ATS board before scanning. Use when only a company name is given, the domain is unclear, or memory has no careers page.
---

# Verify company

1. If only a name is given, search Exa (`web_search_exa`, from the claude.ai Exa connector or the
   `exa` server in .mcp.json): `"<Company> official website careers"`. Prefer the company's own
   domain over directories, news and job aggregators. Indian companies often use `.com`, `.in`
   or `.co.in`.
2. Confirm the domain from the site itself: fetch the home page with Exa `web_fetch_exa` (or let
   `scan.py` do it) and check the company name appears in the title, footer or JSON-LD
   Organization. If the domain redirects (e.g. `humancloud.co.in` → `humancloud.dev`), scan the
   final domain.
3. Careers page: usually linked in the header or footer as Careers, Jobs, Join us, Work with us.
   `scan.py` finds it on its own; pass `--careers <url>` when you already know it.
4. ATS: a link from the official site to `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`,
   `apply.workable.com/<slug>`, `jobs.ashbyhq.com/<slug>`, `careers.smartrecruiters.com/<slug>`
   or `<slug>.zohorecruit.com` is confirmation, and `scan.py` reads it automatically. If you
   suspect a board the site does not link, run `uv run python scripts/ats.py guess "<Company>"`
   and accept a candidate only when the feed's company name matches (`name_match: true`) or a
   public page of the company links it; then pass `--ats type:slug`. Workable answers 200 for
   every slug, so never accept a Workable guess.
5. Name clash (two companies with one name): ask the user which one, unless the domain was given.

## Checkpoints

Step 1 of /company-scan (`uv run python scripts/progress.py start 1` first). Print a `progress.py sub "<what you checked>"` line per check (search, home page, careers link, ATS), and close the step with `progress.py done 1 "<domain>, careers page <url>, ATS <board or none>"`.
