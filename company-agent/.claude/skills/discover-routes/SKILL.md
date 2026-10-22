---
name: discover-routes
description: Crawl a company's own website politely (robots.txt, sitemap first, same domain, 150-page cap, rate limit) to find the careers page, job pages and ATS links. Use inside /company-scan or to refresh routes.
---

# Discover routes

Run from the project folder:

```bash
uv run python scripts/scan.py --company "<Name>" --domain <domain> [--careers <url>] [--max-pages 150] [--full-crawl]
# normal runs use scripts/run_company.py (same flags), which also dedupes and writes Excel
```

What it does (scripts/crawl.py):
- reads `robots.txt` first and obeys it (Disallow, Crawl-delay); 401/403/5xx on robots.txt means
  "do not crawl"; at least 1 second between requests to one host;
- reads the sitemap(s) listed in robots.txt or at /sitemap.xml, career-looking URLs first;
- crawls same-domain links only (subdomains allowed), skips files, stops at `--max-pages`;
- extracts jobs from JSON-LD `JobPosting`, from careers child pages (h1 + nearby location line),
  and from listing cards on the careers page (this catches roles whose own page is broken);
- collects links to ATS boards and reads their public feeds (see `ats-feeds`).

Read the printed summary: `careers_page`, `complete_sources`, `notes`. If `careers_page` is empty,
the site may render jobs with JavaScript or host them on an unlinked ATS: try `verify-company`
step 4, then Exa `web_fetch_exa` of the careers URL as a last resort (those rows use source
`Careers Page` and status `Unverified`). Firecrawl can render JavaScript pages if the user
enables it; it stays off until `FIRECRAWL_API_KEY` exists.

Every route lands in `memory.db` (`routes` table) for the next scan. Never raise the page cap
above 300 or lower the delay.

## Checkpoints

Step 2 of /company-scan (`uv run python scripts/progress.py start 2` first). `scan.py` prints the sub-progress itself (`… robots.txt read`, `… sitemap: n URLs`, `… crawled 40/150 pages`, one line per ATS feed) and closes the step with `✓ Step 2/6 · <pages, careers page, boards, roles>`, or `✗ Step 2/6 · <reason>`.
