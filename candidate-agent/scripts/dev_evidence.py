"""GitHub and Stack Exchange skill evidence (data-structure plan item 9, 2026-10-16).

uv run scripts/dev_evidence.py fetch --input runs/<file>.json [--top 10] [--max-repos 5] [--dry-run]

For the top N candidates by Match Score, reads the public GitHub REST API and the Stack Exchange API, but ONLY for
a GitHub or Stack Exchange profile URL the candidate's own batch entry already links (profile_url, "public_urls" or
a URL inside the evidence notes). It never searches by name: no profile link in hand means no call.

  GitHub: /users/<login>, /users/<login>/repos, /repos/<login>/<repo>/languages for the most recently pushed own repos
  Stack Exchange: /users/<id> and /users/<id>/top-answer-tags on the site the link points to

It writes into that candidate:
  "dev_signals": {"github": {"url", "login", "fetched_at", "public_repos", "languages": {lang: bytes},
                             "repos": [{"name", "url", "stars", "language", "pushed_at"}], "last_active", "facts"},
                  "stackexchange": {"url", "site", "user_id", "fetched_at", "reputation",
                                    "top_tags": [{"tag", "answer_score", "answer_count"}], "facts"}}
Each "facts" item is {"fact", "quote"} where the quote is the API value the fact rests on. Rerun score.py afterwards:
it counts each API as an independent source (unless it is the same site as the primary source or the fetched
corroboration), so a Medium row can reach High. excel_upsert fills GitHub Languages, GitHub Last Active and
SO Top Tags and writes the facts to the Evidence sheet.

Never stored: emails or any other contact field. Only whitelisted fields are copied from the API responses.
GitHub's Acceptable Use Policy bars using its user data for recruiter outreach, so this is skill evidence only.

Rate limits: GitHub allows 60 requests an hour without a token (5,000 with GITHUB_TOKEN, read from the environment
or from .env by this script; never printed). Stack Exchange allows 300 a day without a key (STACKEXCHANGE_KEY
optional) and may ask for a "backoff". 403/429/5xx are retried with backoff; a limit that resets later than
--max-wait seconds stops the run cleanly and the candidates done so far are kept.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, normalise_url  # noqa: E402
import progress  # noqa: E402

DEFAULT_TOP = 10
DEFAULT_MAX_REPOS = 5
DEFAULT_MAX_WAIT = 90
GITHUB_API = "https://api.github.com"
SE_API = "https://api.stackexchange.com/2.3"
UA = "candidate-agent-skill-evidence"
_URL = re.compile(r"https?://[^\s'\"<>)\]|,]+", re.I)
# github.com/<first segment> paths that are not user profiles
_GH_RESERVED = {"about", "orgs", "topics", "features", "marketplace", "sponsors", "settings", "login", "join", "search",
                "explore", "collections", "trending", "events", "issues", "pulls", "notifications", "apps", "enterprise",
                "pricing", "readme", "site", "security", "customer-stories", "team", "contact", "new", "codespaces"}
_GH_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_SE_SITES = {"stackoverflow.com": "stackoverflow", "superuser.com": "superuser", "serverfault.com": "serverfault",
             "askubuntu.com": "askubuntu", "mathoverflow.net": "mathoverflow.net"}


class RateLimited(Exception):
    """A limit that does not reset within --max-wait; the run stops and keeps what it has."""


# ---------- which profile URLs a candidate links ----------

def github_login(url: str) -> str | None:
    """'https://github.com/octocat' -> 'octocat'. Only a bare profile link counts: a repo link may be someone else's."""
    n = normalise_url(url)
    host, _, path = n.split("://", 1)[-1].partition("/")
    parts = [p for p in path.split("/") if p]
    if host != "github.com" or len(parts) != 1 or parts[0].lower() in _GH_RESERVED or not _GH_LOGIN.match(parts[0]):
        return None
    return parts[0]


def se_profile(url: str) -> tuple[str, int] | None:
    """'https://stackoverflow.com/users/22656/jon-skeet' -> ('stackoverflow', 22656); other SE sites too, no meta."""
    n = normalise_url(url)
    host, _, path = n.split("://", 1)[-1].partition("/")
    m = re.match(r"users/(\d+)(?:/|$)", path)
    if not m:
        return None
    if host in _SE_SITES:
        return _SE_SITES[host], int(m.group(1))
    sm = re.match(r"^([a-z0-9-]+)\.stackexchange\.com$", host)
    if sm and sm.group(1) not in ("meta", "api", "data", "chat"):
        return sm.group(1), int(m.group(1))
    return None


def linked_profiles(c: dict) -> dict:
    """{'github': (url, login), 'stackexchange': (url, site, id)} for the first such link in the candidate's own entry."""
    raw = list(c.get("public_urls") or []) + [c.get("profile_url") or ""]
    raw += _URL.findall(" ".join(str(c.get(f) or "") for f in ("evidence_notes_raw", "evidence_notes", "source")))
    out = {}
    for u in raw:
        u = (u or "").strip().rstrip(".;")
        if not u:
            continue
        if "github" not in out and (login := github_login(u)):
            out["github"] = (f"https://github.com/{login}", login)
        if "stackexchange" not in out and (se := se_profile(u)):
            out["stackexchange"] = (normalise_url(u), se[0], se[1])
    return out


# ---------- HTTP with backoff ----------

def _env_value(name: str) -> str:
    """From the environment, else from .env (read here, inside the script; the value is never printed)."""
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    try:
        for line in env.read_text().splitlines():
            k, _, v = line.strip().partition("=")
            if k.strip() == name:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def urllib_transport(url: str, headers: dict) -> tuple[int, dict, object]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, hdrs, body = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, body = e.code, dict(e.headers or {}), e.read() or b""
    if body[:2] == b"\x1f\x8b":   # Stack Exchange always gzips
        body = gzip.decompress(body)
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except ValueError:
        data = None
    return status, {k.lower(): v for k, v in hdrs.items()}, data


class Client:
    def __init__(self, transport=urllib_transport, sleep=time.sleep, clock=time.time, max_wait: int = DEFAULT_MAX_WAIT,
                 tries: int = 4, gh_token: str = "", se_key: str = ""):
        self.transport, self.sleep, self.clock = transport, sleep, clock
        self.max_wait, self.tries = max_wait, tries
        self.gh_token, self.se_key = gh_token, se_key
        self.se_backoff_until = 0.0
        self.calls = 0

    def _wait(self, seconds: float, why: str) -> None:
        if seconds > self.max_wait:
            raise RateLimited(f"{why}: next window in {int(seconds)}s, more than --max-wait {self.max_wait}s")
        if seconds > 0:
            progress.sub(f"{why}: waiting {int(seconds)}s")
            self.sleep(seconds)

    def get(self, url: str, headers: dict, kind: str):
        """JSON body for 200, None for 404 / 410; retries 403/429/5xx with backoff, honouring the API's own hints."""
        for attempt in range(self.tries):
            self.calls += 1
            status, h, data = self.transport(url, headers)
            if status == 200:
                return data
            if status in (404, 410):
                return None
            limited = status in (403, 429) or (status == 400 and isinstance(data, dict) and data.get("error_id") == 502)
            if not limited and status < 500:
                raise RuntimeError(f"{kind} API answered {status} for {url}")
            if h.get("x-ratelimit-remaining") == "0" and h.get("x-ratelimit-reset"):
                wait = float(h["x-ratelimit-reset"]) - self.clock() + 1
            elif h.get("retry-after"):
                wait = float(h["retry-after"])
            elif isinstance(data, dict) and data.get("backoff"):
                wait = float(data["backoff"])
            else:
                wait = 2 ** attempt * 2
            self._wait(wait, f"{kind} API {status}")
        raise RateLimited(f"{kind} API still refusing after {self.tries} tries")

    def github(self, path: str):
        h = {"User-Agent": UA, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.gh_token:
            h["Authorization"] = f"Bearer {self.gh_token}"
        return self.get(GITHUB_API + path, h, "GitHub")

    def stackexchange(self, path: str, site: str, **params):
        wait = self.se_backoff_until - self.clock()
        if wait > 0:
            self._wait(wait, "Stack Exchange backoff")
        q = {"site": site, **params}
        if self.se_key:
            q["key"] = self.se_key
        data = self.get(f"{SE_API}{path}?{urllib.parse.urlencode(q)}", {"User-Agent": UA, "Accept-Encoding": "gzip"},
                        "Stack Exchange")
        if isinstance(data, dict):
            if data.get("backoff"):
                self.se_backoff_until = self.clock() + float(data["backoff"])
            if data.get("quota_remaining") == 0:
                self.se_backoff_until = float("inf")
        return data


# ---------- turning API answers into facts (whitelisted fields only) ----------

def _day(ts) -> str:
    return str(ts or "")[:10]


def github_signals(client: Client, url: str, login: str, max_repos: int, fetched_at: str) -> dict | None:
    user = client.github(f"/users/{urllib.parse.quote(login)}")
    if not isinstance(user, dict) or user.get("type", "User") != "User":
        return None
    repos = client.github(f"/users/{urllib.parse.quote(login)}/repos?per_page=100&type=owner&sort=pushed") or []
    own = [r for r in repos if isinstance(r, dict) and not r.get("fork")]
    langs: dict[str, int] = {}
    for r in sorted(own, key=lambda r: r.get("pushed_at") or "", reverse=True)[:max_repos]:
        for lang, n in (client.github(f"/repos/{urllib.parse.quote(login)}/{urllib.parse.quote(r['name'])}/languages") or {}).items():
            if isinstance(n, int):
                langs[lang] = langs.get(lang, 0) + n
    langs = dict(sorted(langs.items(), key=lambda kv: -kv[1]))
    notable = sorted(own, key=lambda r: (-(r.get("stargazers_count") or 0), r.get("pushed_at") or ""))[:3]
    keep = [{"name": r.get("name"), "url": r.get("html_url") or f"{url}/{r.get('name')}",
             "stars": r.get("stargazers_count") or 0, "language": r.get("language") or "", "pushed_at": _day(r.get("pushed_at"))}
            for r in notable]
    last = max((_day(r.get("pushed_at")) for r in repos if isinstance(r, dict) and r.get("pushed_at")), default="")
    total = sum(langs.values())
    facts = [{"fact": f"GitHub language: {lang} ({n * 100 // total if total else 0}% of code in {min(len(own), max_repos)} recent repos)",
              "quote": f"{lang}: {n} bytes"} for lang, n in list(langs.items())[:5]]
    facts += [{"fact": f"GitHub repo {r['name']}" + (f" ({r['language']})" if r["language"] else "") + f", {r['stars']} stars",
               "quote": f"{r['name']}: stargazers_count {r['stars']}" + (f", language {r['language']}" if r["language"] else "")}
              for r in keep]
    if last:
        facts.append({"fact": f"GitHub last push {last}", "quote": f"pushed_at {last}"})
    if isinstance(user.get("public_repos"), int):
        facts.append({"fact": f"{user['public_repos']} public GitHub repositories", "quote": f"public_repos {user['public_repos']}"})
    return {"url": url, "login": user.get("login") or login, "fetched_at": fetched_at,
            "public_repos": user.get("public_repos"), "languages": langs, "repos": keep, "last_active": last, "facts": facts}


def se_signals(client: Client, url: str, site: str, user_id: int, fetched_at: str) -> dict | None:
    users = client.stackexchange(f"/users/{user_id}", site) or {}
    items = users.get("items") or []
    if not items:
        return None
    u = items[0]
    tags = (client.stackexchange(f"/users/{user_id}/top-answer-tags", site, pagesize=10) or {}).get("items") or []
    top = [{"tag": t.get("tag_name"), "answer_score": t.get("answer_score") or 0, "answer_count": t.get("answer_count") or 0}
           for t in tags if t.get("tag_name")][:5]
    label = "Stack Overflow" if site == "stackoverflow" else f"Stack Exchange ({site})"
    facts = [{"fact": f"{label} top answer tag: {t['tag']} ({t['answer_count']} answers, score {t['answer_score']})",
              "quote": f"{t['tag']}: answer_score {t['answer_score']}, answer_count {t['answer_count']}"} for t in top]
    if isinstance(u.get("reputation"), int):
        facts.append({"fact": f"{label} reputation {u['reputation']}", "quote": f"reputation {u['reputation']}"})
    return {"url": u.get("link") or url, "site": site, "user_id": user_id, "fetched_at": fetched_at,
            "reputation": u.get("reputation"), "top_tags": top, "facts": facts}


def fetch(cands: list[dict], client: Client, top: int = DEFAULT_TOP, max_repos: int = DEFAULT_MAX_REPOS,
          now: str | None = None) -> dict:
    """Fill dev_signals for the top candidates that link a GitHub or Stack Exchange profile. Returns counts."""
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    order = sorted(range(len(cands)), key=lambda i: -(cands[i].get("match_score") or 0))[:top]
    stats = {"top": len(order), "github": 0, "stackexchange": 0, "no_link": 0, "not_found": 0, "stopped": ""}
    for i in order:
        c = cands[i]
        links = linked_profiles(c)
        if not links:
            stats["no_link"] += 1
            continue
        sig = dict(c.get("dev_signals") or {})
        try:
            if "github" in links:
                g = github_signals(client, *links["github"], max_repos, now)
                if g:
                    sig["github"] = g
                    stats["github"] += 1
                else:
                    stats["not_found"] += 1
            if "stackexchange" in links:
                s = se_signals(client, *links["stackexchange"], now)
                if s:
                    sig["stackexchange"] = s
                    stats["stackexchange"] += 1
                else:
                    stats["not_found"] += 1
        except RateLimited as e:
            stats["stopped"] = f"{c.get('candidate_name')}: {e}"
            if sig:
                c["dev_signals"] = sig
            break
        if sig:
            c["dev_signals"] = sig
            got = ", ".join(k for k in ("github", "stackexchange") if k in sig)
            progress.sub(f"#{i} {c.get('candidate_name')} · {got}")
    stats["calls"] = client.calls
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--input", required=True)
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    p.add_argument("--max-repos", type=int, default=DEFAULT_MAX_REPOS)
    p.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    p.add_argument("--dry-run", action="store_true", help="list the linked profiles, call nothing, write nothing")
    a = ap.parse_args()
    path = Path(a.input)
    payload = json.loads(path.read_text())
    cands = payload["candidates"]
    if a.dry_run:
        order = sorted(range(len(cands)), key=lambda i: -(cands[i].get("match_score") or 0))[: a.top]
        plan = [{"index": i, "candidate": cands[i].get("candidate_name"),
                 "links": {k: v[0] for k, v in linked_profiles(cands[i]).items()}} for i in order]
        progress.result(f"top {len(plan)}: {sum(bool(p['links']) for p in plan)} link a GitHub or Stack Exchange profile")
        print(json.dumps({"plan": plan}, indent=2, ensure_ascii=False))
        return
    client = Client(max_wait=a.max_wait, gh_token=_env_value("GITHUB_TOKEN"), se_key=_env_value("STACKEXCHANGE_KEY"))
    stats = fetch(cands, client, a.top, a.max_repos)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    if stats["stopped"]:
        progress.sub(f"stopped early (rate limit): {stats['stopped']}")
    progress.result(f"top {stats['top']}: {stats['github']} GitHub, {stats['stackexchange']} Stack Exchange, "
                    f"{stats['no_link']} without a linked profile, {stats['calls']} API calls; rerun score.py next")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
