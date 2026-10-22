---
name: verify-company
description: Step 1 of /account-scan. Confirm the company's official website before any research. Use when only a name is given or the domain is unclear.
---

# Verify company

1. `uv run python scripts/memory.py show <domain>` if a domain is known: an earlier row may exist.
2. Only a name: Exa search `"<Company> official website"` (`mcp__exa__web_search_exa`). Prefer the company's own
   domain over directories, news and aggregators.
3. Confirm from the site itself: `uv run python scripts/lookup.py page https://<domain>/` and check that the
   company name appears in the title, description or JSON-LD Organization. If it redirects, use the final domain.
4. Two companies with one name and no domain given: pick the one that matches the user's words; if still unsure,
   stop and ask (in batch mode mark it `failed --note "ambiguous name"`).

`progress.py sub "<what you checked>"` per check; close with `progress.py done 1 "<domain> confirmed from <how>"`.
