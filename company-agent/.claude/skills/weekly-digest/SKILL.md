---
name: weekly-digest
description: Keep a watchlist of companies, re-scan them and write the weekly digest of new and closed roles, each new role with a ready /scout line. Use for "watch this company", "what is new this week" or "weekly hiring digest".
---

# Weekly digest

```bash
uv run python scripts/memory.py watch add <domain> [--name "Name"] [--keywords "engineer,designer"]
uv run python scripts/memory.py watch remove <domain>
uv run python scripts/memory.py watch list
uv run python scripts/rescan_watchlist.py [--domain <d> ...] [--since 7d] [--no-digest]
uv run python scripts/digest.py [--since 7d|YYYY-MM-DD] [--all]
```

- `watch add` takes the name from a company already scanned; a company never scanned needs `--name`.
  Keywords are optional: with them the digest lists only new roles whose title contains one, and counts the rest.
- `rescan_watchlist.py` runs `run_company.py` for each watched company in turn (feed first, polite, robots.txt obeyed),
  keeps going when one fails, then writes the digest. It uses no LLM and no Exa search, so step 3 (public posts)
  is skipped. It does not schedule itself.
- `digest.py` reads memory.db only and writes `runs/digest-<date>.md`; it prints `DIGEST_FILE=...`.
  A company's first scan is its baseline: counted, not listed as new. A company not scanned in the period is flagged.
- `--since 7d` is the 7 days ending today, so two weekly runs a week apart share no day; an ISO date is the first day itself.
  `rescan_watchlist.py` exits 0 when all scanned, 2 when some failed (the others are saved), 1 when none worked.
- Tell the user the path and the totals line. Do not paste the whole digest unless asked.
- Each new role carries `/scout <job URL>` for the candidate agent. The URL is the only link between the two agents;
  never open or write the sibling folder. LinkedIn and Indeed listings get no `/scout` line (they cannot be fetched).
