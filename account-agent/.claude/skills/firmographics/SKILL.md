---
name: firmographics
description: Step 2 of /account-scan. Fill Product Type, What They Build, Company Brief (with employee size), HQ / US Office, India Office, Product? and Funding from free sources, every fact cited; a field is left "Not found (checked: ...)" only after its whole source ladder was tried.
---

# Company facts

Keep a running Sources list (`[n] url`) as you go; every fact gets the `[n]` of the page that states it. Keep a
running **checked list per field** too (which sources you tried): it becomes the cell text when a field stays
unanswered, for example `Not found (checked: site about/careers, Wikidata, Wikipedia, EDGAR, 3 Exa searches)`.

## Structured sources first (no Exa cost)

1. `uv run python scripts/lookup.py site <domain>`: home page plus about / team / leadership / press / careers /
   investors pages (linked ones, then common paths the menu hides), as capped outlines with `size mention:` lines.
   Read a specific page with `lookup.py page <url>`. Exa `web_fetch_exa` is the fallback for a page the script
   cannot read (never linkedin.com / indeed.com).
2. `uv run python scripts/lookup.py wikidata "<Company>" --domain <domain>`: founded, HQ, founders, CEO, employees
   (P1128), ticker.
3. `uv run python scripts/lookup.py wikipedia "<Company>" --domain <domain>`: infobox founded, HQ, key people,
   num_employees, revenue. If it warns the website does not match, use it only when the article clearly is the
   same company.
4. `uv run python scripts/lookup.py edgar "<Company>"`: listed company (ticker, address, **headcount from the latest
   10-K / 20-F**) and Form D private offerings (US entity, raise dates, newest first).
5. `uv run python scripts/lookup.py size "<Company>" --domain <domain>`: every headcount the free sources state
   (Wikidata P1128, Wikipedia, site text and JSON-LD, SEC annual report), plus the `checked:` line.
6. `uv run python scripts/lookup.py news "<Company> <keyword>" [--months 24]`: recent English headlines (GDELT,
   keyless). Add a qualifier for common-word names (`"Postman API"`). Headlines only: cite a fact only from the
   article page or an Exa snippet that states it.
7. `uv run python scripts/lookup.py github <org>` when the site links a GitHub org (engineering signal).
8. `uv run python scripts/lookup.py formd "<Company>"` (or `formd <CIK>` from the `edgar` line): every SEC Form D of a
   US entity, newest first, with the **amount sold**, amount offered, **first sale date**, investor count, issuer
   city and the filing URL. This is the dated, legal source for a US raise: write it as `$250M sold per Form D filed
   2020-07-08 (first sale 2019-12-16) [n]`, citing the filing URL. Form D covers the US offering, not always the whole
   round; a line marked "pooled fund or SPV" is an investor vehicle, never the company's own raise. "Indefinite" or
   a missing amount is left out, never estimated.
9. `uv run python scripts/lookup.py gleif "<Company>"`: GLEIF LEI records (legal name, legal and HQ address, status),
   cite the `search.gleif.org` record URL. Use it to confirm the legal entity and HQ city / country; several entities
   (a US Inc. and an Indian Pvt Ltd) are normal and can prove an India Office legal entity.
10. `uv run python scripts/lookup.py dns <domain>`: MX and TXT records, giving the email provider (Google Workspace,
   Microsoft 365, ...) and SaaS verification tokens (Atlassian, HubSpot, Salesforce, ...). Cite the printed
   `dns.google` link. A token shows the domain was verified with a tool at some time, so write "DNS shows" not "uses".
11. `uv run python scripts/lookup.py h1b "<Company>" [--alias "<US legal name>"]`: US H-1B LCA filings in the last 4
   quarters from the local DOL index (count, top job titles, US worksites, wage ranges), each quarter file cited. Use
   the US entity name from `edgar` or `gleif` as `--alias` when the brand name does not match. It is a company-level
   hiring signal: never a claim about a person, and the files carry no nationality. "no LCA files indexed" means
   `uv run python scripts/h1b.py fetch` has not been run (a one-time ~500 MB download); carry on without it.

## Typed columns (written by excel_upsert.py, never by you)

Founded Year, Employees, Employees As Of, Last Round, Last Round Amount USD, Last Round Date, Total Raised USD, HQ
City, HQ Country, India Office City, Public/Private and Ticker are read in code from your cited prose. Write the
facts plainly so they are picked up, each clause with its own [n]: `Founded 2014 [3]`, `approx. 5,200 employees
(Revelio Labs, March 2026) [16]`, `Series F, about $375 Mn, December 2021, ... [4]`, `about $740 Mn raised in total
[4]`, `HQ: Bengaluru, India, <address> [2]`, `Public (FRSH) [9]` or `privately held [4]`. A value no cited clause
states stays blank; never add a number to make a column fill. The H-1B columns come from `lookup.py h1b`'s index.

## Field ladders (try each rung until the field is answered)

| Field | Ladder |
| --- | --- |
| Employee size (in Company Brief) | `size` → Exa `"<Company> employees"`, `"<Company> headcount"`, `"<Company> workforce"`, `"<Company> layoffs"` → careers / about page text |
| Funding | site press / newsroom → `formd` (US entity: dated amounts) → Wikipedia → Exa `"<Company> raises"`, `"<Company> Series"`, `"<Company> funding round <this year>"` → `lookup.py news "<Company> funding"` |
| HQ / US Office | site contact / offices / legal pages → Wikidata / Wikipedia HQ → GLEIF HQ address → EDGAR business address / Form D issuer address → Exa `"<Company> US headquarters"` |
| India Office | site offices / contact / careers locations → Exa `"<Company> India office"`, `"<Company> Bengaluru OR Pune OR Hyderabad office"` |
| What They Build, Product Type, Product? | home, product and pricing pages → Wikipedia → Exa `"<Company> product"` |

Fields:
- **What They Build (brief)**: 1–2 sentences from the company's own pages, cited.
- **Product Type** from the fixed list; **Product?** Yes / No / Both (evidence: pricing or product pages vs
  services-only pages).
- **Company Brief**: `Founded <year> [n]; approx. <size> employees (<basis, year>) [n]; customers: <names> [n];
  milestones: <dated> [n]`. Size always "approx." with its basis and year. When two sources disagree, give the
  newest dated one and name its year. No size after the ladder: `size not found (checked: <what you tried>)`.
- **HQ / US Office**, **India Office**: city and the evidence (address, office page, filing); job locations only
  → "hiring in <city>".
- **Funding**: latest rounds with amount, date, lead investors, each cited; "Public (TICKER) [n]" if listed.
  **Freshness:** if the newest round you found is 2 or more years old, search for a newer one (the ladder above)
  before writing; then either add the newer round or append `No newer round found (checked: <what you tried>)`.
  The hook refuses an old round without that note. Undisclosed amounts are never estimated.

Close with `progress.py done 2 "<n> facts from <m> sources, <k> Exa searches, not found: <fields or none>"`.
