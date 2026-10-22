"""Deterministic HumanCloud team matching (no LLM): team.csv x the leaders' evidenced facts.

team.csv (UTF-8, one row per HumanCloud person who may do outreach; git-ignored; see team.example.csv):
  name, role, email, seniority, regions, past_employers, colleges, cities_lived, communities,
  conferences, industries_known, linkedin_url, consent, default_for
List cells are separated by ";". Only rows with consent = yes are used. email and linkedin_url are
never read into the account sheet. default_for names the regions (e.g. "US; India") this person owns
when nothing matches.

Leaders (from the draft row's "_leaders"):
  {"name", "title", "location", "employers": [], "colleges": [], "cities": [], "communities": [],
   "conferences": [], "industries": [], "cite": "[n]"}
Only facts the leader published (with a source in the row) belong in these lists.

Scores: shared employer 3, shared college 3, same community or same conference and year 2, same
conference another year 1, same city 1, same industry 1. Warm Connection lists the best one or two
matches as common ground (never "they know each other"). Best Person is the highest total; ties go
to seniority fit (CXO or founder contact -> CXO/VP sender; VP/Director contact -> Director/VP sender)
then region fit (US contact -> US sender), then name order. No match -> the default owner for the
contact's region, else blank.

CLI (for a quick look): uv run python scripts/match_team.py check
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, TEAM_CSV  # noqa: E402

TEAM_COLUMNS = ["name", "role", "email", "seniority", "regions", "past_employers", "colleges", "cities_lived",
                "communities", "conferences", "industries_known", "linkedin_url", "consent", "default_for"]
REQUIRED = ["name", "role", "seniority", "regions", "consent"]
SENIORITY = ["CXO", "VP", "Director", "Manager", "IC"]
ALIASES_FILE = ROOT / "schema" / "aliases.json"
LEGAL_SUFFIX = re.compile(r"\b(inc|incorporated|ltd|limited|pvt|private|llc|llp|corp|corporation|co|company|plc|gmbh|sa|ag)\b\.?")
US_WORDS = re.compile(r"(?i)\b(us|usa|united states|u\.s\.|america|new york|san francisco|bay area|seattle|austin|boston|"
                      r"california|texas|washington|chicago|ny|ca|nj|new jersey|atlanta|denver|silicon valley)\b")
INDIA_WORDS = re.compile(r"(?i)\b(india|bengaluru|bangalore|pune|mumbai|hyderabad|chennai|delhi|gurugram|gurgaon|noida|kolkata|ahmedabad)\b")


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n]", v or "") if x.strip()]


def _aliases() -> dict[str, str]:
    try:
        raw = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for group in ("colleges", "employers"):
        for canon, names in (raw.get(group) or {}).items():
            for n in [canon, *names]:
                out[_basic(n)] = _basic(canon)
    return out


def _basic(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("&", " and "))
    s = LEGAL_SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


_ALIAS: dict[str, str] | None = None


def norm(s: str) -> str:
    global _ALIAS
    if _ALIAS is None:
        _ALIAS = _aliases()
    b = _basic(s)
    return _ALIAS.get(b, b)


def _event(s: str) -> tuple[str, str]:
    m = re.search(r"\b(19|20)\d{2}\b", s or "")
    year = m.group(0) if m else ""
    return norm(re.sub(r"\b(19|20)\d{2}\b", " ", s or "")), year


def _event_label(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\b(19|20)\d{2}\b", " ", s or "")).strip()


def load_team(path: Path | None = None) -> tuple[list[dict], list[str]]:
    """Consented team members and the problems found (missing columns, rows skipped)."""
    path = path or TEAM_CSV
    if not path.exists():
        return [], [f"{path.name} not found"]
    problems: list[str] = []
    team: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = [c.strip() for c in (rd.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in cols]
        if missing:
            return [], [f"{path.name} lacks columns {missing}"]
        for i, raw in enumerate(rd, 2):
            r = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if not any(r.values()):
                continue
            if r.get("consent", "").lower() not in ("yes", "y", "true"):
                problems.append(f"line {i}: skipped, no consent")
                continue
            if not all(r.get(c) for c in REQUIRED):
                problems.append(f"line {i}: skipped, a required column is empty")
                continue
            team.append({
                "name": r["name"], "role": r["role"], "seniority": r["seniority"],
                "regions": _split(r.get("regions", "")),
                "employers": _split(r.get("past_employers", "")), "colleges": _split(r.get("colleges", "")),
                "cities": _split(r.get("cities_lived", "")), "communities": _split(r.get("communities", "")),
                "conferences": _split(r.get("conferences", "")), "industries": _split(r.get("industries_known", "")),
                "default_for": _split(r.get("default_for", "")),
            })
    return team, problems


def _region(text: str) -> str:
    if US_WORDS.search(text or ""):
        return "US"
    if INDIA_WORDS.search(text or ""):
        return "India"
    return ""


def _seniority_fit(member: dict, leader_title: str) -> int:
    t = (leader_title or "").lower()
    s = member["seniority"].strip().lower()
    top = re.search(r"\b(ceo|cto|coo|cfo|cpo|chief|founder|co-founder|president)\b", t)
    if top:
        return 2 if s in ("cxo", "vp") else 0
    if re.search(r"\b(vp|vice president|director|head)\b", t):
        return 2 if s in ("director", "vp") else 1 if s == "cxo" else 0
    return 1 if s in ("manager", "director") else 0


def _region_fit(member: dict, leader: dict) -> int:
    lr = _region(leader.get("location", ""))
    return 1 if lr and any(_region(r) == lr or r.strip().lower().startswith(lr.lower()) for r in member["regions"]) else 0


def match_pairs(team: list[dict], leaders: list[dict], industries: list[str] | None = None) -> list[dict]:
    """Every (member, leader) pair with a score > 0, best first, each with its reasons."""
    out = []
    ind = {norm(x) for x in (industries or [])}
    for m in team:
        for ld in leaders:
            reasons: list[tuple[int, str]] = []
            for kind, mk, lk, pts, verb in (("employer", "employers", "employers", 3, "worked at"),
                                            ("college", "colleges", "colleges", 3, "studied at"),
                                            ("community", "communities", "communities", 2, "are part of"),
                                            ("city", "cities", "cities", 1, "lived in")):
                mine = {norm(x): x for x in m[mk]}
                for x in ld.get(lk) or []:
                    if norm(x) and norm(x) in mine:
                        reasons.append((pts, f"{verb} {x}"))
            for a in m["conferences"]:
                an, ay = _event(a)
                for b in ld.get("conferences") or []:
                    bn, by = _event(b)
                    if an and an == bn:
                        same = ay and ay == by
                        reasons.append((2 if same else 1, f"attended {b}" if same else f"attend {_event_label(b)} (in different years)"))
            for x in m["industries"]:
                if norm(x) in ind or norm(x) in {norm(i) for i in ld.get("industries") or []}:
                    reasons.append((1, f"know {x}"))
            if reasons:
                reasons.sort(key=lambda r: -r[0])
                out.append({"member": m, "leader": ld, "score": sum(r[0] for r in reasons), "reasons": reasons,
                            "fit": (_seniority_fit(m, ld.get("title", "")), _region_fit(m, ld))})
    out.sort(key=lambda p: (-p["score"], -p["fit"][0], -p["fit"][1], p["member"]["name"], p["leader"].get("name", "")))
    return out


def warm_and_best(team: list[dict], leaders: list[dict], industries: list[str] | None = None) -> tuple[str, str]:
    """The two cells: (Warm Connection / Common Ground, Best Person to Reach Out)."""
    pairs = match_pairs(team, leaders, industries)
    warm_bits = []
    for p in pairs[:2]:
        ld, m = p["leader"], p["member"]
        cite = (" " + ld["cite"]) if ld.get("cite") else ""
        who = f"{ld.get('title') + ' ' if ld.get('title') else ''}{ld.get('name', '')}".strip()
        warm_bits.append(f"{m['name']} and {who} both {p['reasons'][0][1]}{cite} (common ground, score {p['score']})")
    warm = "; ".join(warm_bits)
    if pairs:
        totals: dict[str, list] = {}
        for p in pairs:
            t = totals.setdefault(p["member"]["name"], [0, (0, 0), p["member"], p])
            t[0] += p["score"]
            t[1] = max(t[1], p["fit"])
        name, (score, fit, m, top) = sorted(totals.items(), key=lambda kv: (-kv[1][0], -kv[1][1][0], -kv[1][1][1], kv[0]))[0]
        best = f"{m['name']} ({m['role']}): score {score}, {top['reasons'][0][1]} with {top['leader'].get('name', 'the contact')}"
        return warm, best
    region = next((_region(ld.get("location", "")) for ld in leaders if _region(ld.get("location", ""))), "")
    owner = next((m for m in team if region and any(r.lower() == region.lower() for r in m["default_for"])), None)
    owner = owner or next((m for m in team if any(r.lower() in ("all", "*", "default") for r in m["default_for"])), None)
    if owner:
        return "", f"{owner['name']} ({owner['role']}): default owner for {region or 'all regions'}; no common ground found"
    return "", ""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "check":
        print(__doc__)
        return
    team, problems = load_team()
    print(f"team.csv: {len(team)} consented member(s)" + (f"; {len(problems)} note(s)" if problems else ""))
    for p in problems[:10]:
        print("  …", p)


if __name__ == "__main__":
    main()
