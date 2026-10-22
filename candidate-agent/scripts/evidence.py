"""Evidence depth (plan item 6): which top candidates get one extra public source fetched.

uv run scripts/evidence.py plan --input runs/<file>.json [--top 10]

After score.py, picks the top N candidates by Match Score and, for each, at most ONE non-LinkedIn,
non-Indeed public URL already present in its batch entry (profile_url, "public_urls", or a URL inside
the evidence notes): a GitHub profile, a personal site or a talk page. It never searches for a person:
no URL in hand means no fetch (Exa queries never carry a candidate's name).

The agent then fetches each planned URL once with web_fetch_exa and writes into that candidate:
  "corroboration": {"url": "<planned url>", "source_type": "GitHub|Personal site|Talk page|Web page",
                    "fetched_at": "<ISO time>", "snippet": "<the fetched text the facts rest on, <= 1500 chars>",
                    "confirms": [{"fact": "40 public repositories", "quote": "<exact phrase from the snippet>"}]}
and reruns score.py, which checks every quote against the snippet, counts the sources, marks each fact
claimed or corroborated and sets the confidence (High needs 2+ independent sources).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import normalise_url  # noqa: E402
import progress  # noqa: E402

DEFAULT_TOP = 10
_URL = re.compile(r"https?://[^\s'\"<>)\]|,]+", re.I)
_NO_FETCH = re.compile(r"(^|\.)(linkedin\.[a-z.]+|lnkd\.in|indeed\.[a-z.]+)$", re.I)
_SKIP_HOSTS = re.compile(r"(^|\.)(google\.[a-z.]+|exa\.ai|bing\.com|facebook\.com|instagram\.com|x\.com|twitter\.com)$", re.I)


def source_type(url: str) -> str:
    host = normalise_url(url).split("://", 1)[-1].split("/", 1)[0]
    if host in ("github.com", "gitlab.com") or host.endswith(".github.io"):
        return "GitHub" if "github" in host else "GitLab"
    if re.search(r"(speakerdeck|slideshare|sessionize|youtube|confengine|hasgeek|meetup|papercall)\.", host):
        return "Talk page"
    if re.search(r"(medium|dev\.to|hashnode|substack)\.", host + "."):
        return "Blog"
    return "Personal site"


def fetchable(url: str) -> bool:
    host = normalise_url(url).split("://", 1)[-1].split("/", 1)[0]
    return bool(host) and "." in host and not _NO_FETCH.search(host) and not _SKIP_HOSTS.search(host)


def candidate_urls(c: dict) -> list[str]:
    """Non-LinkedIn public URLs already in the candidate's entry, best first, de-duplicated."""
    raw = list(c.get("public_urls") or []) + [c.get("profile_url") or ""]
    raw += _URL.findall(" ".join(str(c.get(f) or "") for f in ("evidence_notes_raw", "evidence_notes", "source")))
    out, seen = [], set()
    for u in raw:
        u = (u or "").strip().rstrip(".;")
        n = normalise_url(u)
        if u and n not in seen and fetchable(u):
            seen.add(n)
            out.append(u)
    rank = {"GitHub": 0, "GitLab": 0, "Personal site": 1, "Talk page": 2, "Blog": 3}
    return sorted(out, key=lambda u: rank.get(source_type(u), 9))


def plan(cands: list[dict], top: int = DEFAULT_TOP) -> list[dict]:
    order = sorted(range(len(cands)), key=lambda i: -(cands[i].get("match_score") or 0))[:top]
    rows = []
    for i in order:
        c = cands[i]
        urls = candidate_urls(c)
        rows.append({"index": i, "candidate": c.get("candidate_name"), "match_score": c.get("match_score"),
                     "fetch": urls[0] if urls else None, "source_type": source_type(urls[0]) if urls else None,
                     "already_corroborated": bool(c.get("corroboration"))})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--input", required=True)
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    a = ap.parse_args()
    payload = json.loads(Path(a.input).read_text())
    rows = plan(payload["candidates"], a.top)
    todo = [r for r in rows if r["fetch"] and not r["already_corroborated"]]
    for r in rows:
        what = f"fetch {r['source_type']} {r['fetch']}" if r["fetch"] else "no public non-LinkedIn URL in hand (no fetch)"
        progress.sub(f"#{r['index']} {r['candidate']} {r['match_score']} · {what}")
    progress.result(f"top {len(rows)}: {len(todo)} to fetch (one source each), "
                    f"{sum(not r['fetch'] for r in rows)} without a public URL, "
                    f"{sum(r['already_corroborated'] for r in rows)} already corroborated")
    print(json.dumps({"top": len(rows), "to_fetch": len(todo), "plan": rows}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
