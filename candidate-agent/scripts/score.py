"""Turn per-dimension points into Match Score and Score Breakdown.

uv run scripts/score.py --input runs/<file>.json [--out runs/<file>.scored.json]

Calibrated, partly deterministic scoring (2026-10-15):
- The candidate-scorer subagent gives points for core_skills, domain_projects, seniority and
  preferred_skills only (never above a dimension's weight), and per dimension the exact phrases it
  relied on ("quotes": {dimension: [phrase, ...]}). Each quote must appear in the candidate's evidence.
- experience and location are computed here, in code:
  experience: years parsed from the Experience field ("7+ yrs" -> 7, "~6 yrs (estimated)" -> 6, estimate)
  against criteria.experience_years. Full points inside the range, minus 25% of the weight per year below
  the minimum and 10% per year above the maximum, half credit for an estimate. One year or more below the
  minimum is a soft gate: the total is capped at 65.
  location: same city = full, same metro or region = half, elsewhere = 0, remote allowed = full. The
  Location field is rewritten to one canonical form per city; the original text is kept in location_raw.
- A missing mandatory skill is a hard gate: the total is capped at 40.
- Evidence and honest confidence (plan item 6, 2026-10-15): the primary source (the search snippet) counts as
  one source; a "corroboration" (one fetched non-LinkedIn public page: GitHub, personal site, talk page, see
  scripts/evidence.py) whose "confirms" quotes are all in its snippet adds a second, independent one. Each fact
  is marked "claimed (<primary source>)" or "corroborated (<type>: <url>)". High needs 2+ independent sources:
  a corroborated Medium becomes High, an uncorroborated High becomes Medium. Medium caps the total at 85 and
  Low at 60 (70+ on Low is also rejected by validation). The breakdown and the Evidence Sources column carry
  the count. The scorer's own confidence is kept in "confidence_claimed".
  GitHub and Stack Exchange (plan item 9, 2026-10-16): each API answer in "dev_signals" (scripts/dev_evidence.py,
  only for a profile the candidate links) is one more independent source, unless its site is the primary source's
  or the corroboration's (the same site never counts twice).
- People in the suppressed table (`memory.py forget`) are dropped before scoring.
- The summary prints the score spread (p10/p50/p90) and warns when p50 > 75: the batch is not discriminating.
- Key factors (2026-10-15), one sheet column each, all reusing the scores above:
  Open to Work: a self-published signal in the public snippet ('#OpenToWork', 'open to work', 'actively looking',
  'looking for new opportunities', 'immediate joiner' = Yes; 'serving notice period', 'notice period: 30 days' =
  Likely), with the exact quote; none = Unknown. Never inferred from tenure or job changes. Yes adds +5, Likely +2,
  Unknown never costs points; the total stays capped at 100 and the gates and confidence caps still apply. A quote
  the batch supplies (open_to_work_quote) must be in the evidence and be such a signal, or score.py stops with ✗.
  Stack Match: must-have skills matched / missing (from mandatory_missing), preferred skills named in the evidence,
  and the core / preferred points. Experience Match: years against the JD range. Location Match: Same city, Same
  region, Other, Remote OK or Unknown, with the canonical location.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (DB_PATH, DEFAULT_WEIGHTS, DEV_SOURCE_TYPES, OTW_POINTS, ROOT, _norm_text, _squash,  # noqa: E402
                    canonical_city, detect_open_to_work, dev_columns, dev_sources, drop_suppressed, evidence_text, normalise_location, normalise_url, parse_years, region_of)
import progress  # noqa: E402

LABELS = {
    "core_skills": "Core skills",
    "experience": "Experience",
    "domain_projects": "Domain/projects",
    "seniority": "Seniority",
    "location": "Location",
    "preferred_skills": "Preferred skills",
}
CODE_DIMS = ("experience", "location")
GATE_CAP = 40
SOFT_GATE_CAP = 65
LOW_CONF_CAP = 60
MEDIUM_CONF_CAP = 85
CONF_CAPS = {"Medium": MEDIUM_CONF_CAP, "Low": LOW_CONF_CAP}
NO_FETCH_HOSTS = re.compile(r"(^|\.)(linkedin\.[a-z.]+|lnkd\.in|indeed\.[a-z.]+)$", re.I)
BELOW_STEP = 0.25   # share of the experience weight lost per year below the minimum
ABOVE_STEP = 0.10   # share lost per year above the maximum
SPREAD_WARN_P50 = 75



def jd_for(jd_id: str) -> tuple[dict, dict]:
    """(weights, criteria) from memory.db, else jds/<id>.json, else defaults."""
    if DB_PATH.exists():
        r = sqlite3.connect(DB_PATH).execute("SELECT weights_json, criteria_json FROM jds WHERE id=?", (jd_id,)).fetchone()
        if r:
            return json.loads(r[0]), json.loads(r[1])
    f = ROOT / "jds" / f"{jd_id}.json"
    if jd_id and f.exists():
        jd = json.loads(f.read_text())
        return jd.get("weights") or DEFAULT_WEIGHTS, jd.get("criteria") or {}
    return DEFAULT_WEIGHTS, {}


def experience_points(text: str, rng: dict, weight: int) -> tuple[int, str, bool]:
    """(points, note, soft_gate) for the Experience field against {"min": a, "max": b}."""
    years, est = parse_years(text)
    if years is None:
        return 0, "no years stated", False
    lo, hi = (rng or {}).get("min"), (rng or {}).get("max")
    frac = 1.0
    if lo is not None and years < lo:
        frac = max(0.0, 1 - BELOW_STEP * (lo - years))
    elif hi is not None and years > hi:
        frac = max(0.0, 1 - ABOVE_STEP * (years - hi))
    if est:
        frac *= 0.5
    y = f"{years:g} yrs" + (" est." if est else "")
    want = f"{lo}-{hi}" if lo is not None and hi is not None else (f"{lo}+" if lo is not None else f"≤{hi}")
    soft = lo is not None and years <= lo - 1
    return round(weight * frac), f"{y}, JD {want}", soft


def location_points(text: str, criteria: dict, weight: int) -> tuple[int, str]:
    """Same city = full, same metro or region = half, other = 0, remote allowed = full."""
    rules = criteria.get("location_rules") or {}
    mode = (criteria.get("work_mode") or "").lower()
    remote_ok = rules.get("remote_ok", "remote" in mode and "hybrid" not in mode)
    if remote_ok:
        return weight, "remote allowed"
    if not (text or "").strip():
        return 0, "no location"
    target = criteria.get("location") or ""
    tcity, ccity = canonical_city(target), canonical_city(text)
    if tcity and ccity:
        same = tcity == ccity
    else:
        same = _norm_text(target.split(",")[0]) == _norm_text(text.split(",")[0]) != ""
    if same:
        return weight, "same city"
    treg, creg = region_of(target), region_of(text)
    if treg and treg == creg:
        return round(weight / 2), "same region"
    return 0, "other city"


LOCATION_LABELS = {"same city": "Same city", "same region": "Same region", "other city": "Other",
                   "remote allowed": "Remote OK", "no location": "Unknown"}


def experience_match(text: str, rng: dict) -> str:
    """'6 yrs vs 5-9 required: in range', '3 yrs (estimate) vs 5+ required: 2 below', 'not stated vs 5-9 required'."""
    lo, hi = (rng or {}).get("min"), (rng or {}).get("max")
    want = f"{lo}-{hi}" if lo is not None and hi is not None else (f"{lo}+" if lo is not None else (f"up to {hi}" if hi is not None else ""))
    years, est = parse_years(text)
    if years is None:
        return f"not stated vs {want} required" if want else "not stated (JD sets no range)"
    y = f"{years:g} yrs" + (" (estimate)" if est else "")
    if not want:
        return f"{y} (JD sets no range)"
    if lo is not None and years < lo:
        return f"{y} vs {want} required: {lo - years:g} below"
    if hi is not None and years > hi:
        return f"{y} vs {want} required: {years - hi:g} above"
    return f"{y} vs {want} required: in range"


def location_match(note: str, location: str) -> str:
    """'Same city · Pune, Maharashtra, India'; 'Unknown' when no location is listed."""
    label = LOCATION_LABELS.get(note.split(";")[0].strip(), "Other")
    loc = (location or "").strip()
    return f"{label} · {loc}" if loc else label


def _skill_rx(skill: str) -> re.Pattern:
    """Whole-word match that tolerates 'Next.js' / 'NextJS' / 'next js'."""
    parts = [re.escape(p) for p in re.split(r"[\s.\-]+", skill.strip().lower()) if p]
    return re.compile(r"(?<![\w])" + r"[\s.\-]?".join(parts) + r"(?![\w])")


def skill_named(skill: str, ev: str) -> bool:
    alts = [a for a in re.split(r"\s*/\s*|\s+or\s+", skill or "") if a.strip()]
    return any(_skill_rx(a).search(ev) for a in alts)


def stack_match(c: dict, criteria: dict, weights: dict, pts: dict) -> str:
    """'3/3 must-have: React, TypeScript, JavaScript; missing: none · preferred 2/6: Next.js, Redux · core 30/35, preferred 6/10'."""
    must = [s for s in criteria.get("mandatory_skills") or [] if s]
    missing_l = {_squash(m) for m in c.get("mandatory_missing") or []}
    have = [s for s in must if _squash(s) not in missing_l]
    miss = [s for s in must if _squash(s) in missing_l] + [m for m in c.get("mandatory_missing") or []
                                                          if _squash(m) not in {_squash(s) for s in must}]
    parts = [f"{len(have)}/{len(must)} must-have: {', '.join(have) or 'none'}; missing: {', '.join(miss) or 'none'}"
             if must else "no must-have list in the JD" + (f"; missing: {', '.join(miss)}" if miss else "")]
    pref = [s for s in criteria.get("preferred_skills") or [] if s]
    if pref:
        ev = evidence_text(c)
        named = [s for s in pref if skill_named(s, ev)]
        parts.append(f"preferred {len(named)}/{len(pref)}" + (f": {', '.join(named)}" if named else ""))
    pts_txt = [f"{lbl} {round(pts.get(d, 0))}/{weights[d]}" for d, lbl in (("core_skills", "core"), ("preferred_skills", "preferred")) if d in weights]
    if pts_txt:
        parts.append(", ".join(pts_txt))
    return " · ".join(parts)


def open_to_work(c: dict) -> tuple[str, str]:
    """(Yes | Likely | Unknown, quote). Code reads the snippet; a quote the batch supplies is verified, never trusted."""
    name = c.get("candidate_name")
    src = " ".join(str(c.get(f) or "") for f in ("current_role_company", "key_skills"))
    src += " " + str(c.get("evidence_notes_raw") or c.get("evidence_notes") or "")
    hit = detect_open_to_work(src)
    given = (c.get("open_to_work_quote") or "").strip()
    if given:
        if _squash(given) not in evidence_text(c):
            progress.fail_exit(f"{name}: open-to-work quote not found in the evidence: {given!r}")
        g = detect_open_to_work(given)
        if not g:
            progress.fail_exit(f"{name}: open-to-work quote is not a self-published open-to-work signal: {given!r}")
        if hit is None or (hit[0] == "Likely" and g[0] == "Yes"):
            hit = (g[0], given)
    return hit if hit else ("Unknown", "")


def _evidence_text(c: dict) -> str:
    parts = [c.get(f) or "" for f in ("evidence_notes", "key_skills", "current_role_company", "experience", "location_raw", "location")]
    return re.sub(r"\s+", " ", " ".join(map(str, parts))).lower()


def check_quotes(c: dict, llm_dims: list[str]) -> None:
    """Each LLM dimension with points needs at least one quote, and every quote must be in the evidence."""
    quotes = c.get("quotes") or {}
    ev = _evidence_text(c)
    for dim in llm_dims:
        qs = quotes.get(dim) or []
        if (c.get("scores") or {}).get(dim, 0) > 0 and not qs:
            progress.fail_exit(f"{c.get('candidate_name')}: {dim} has points but no quote")
        for q in qs:
            if re.sub(r"\s+", " ", str(q)).strip().lower() not in ev:
                progress.fail_exit(f"{c.get('candidate_name')}: {dim} quote not found in the evidence: {q!r}")


def _host(url: str) -> str:
    return normalise_url(url).split("://", 1)[-1].split("/", 1)[0]


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def apply_evidence(c: dict) -> list[str]:
    """Count independent sources, mark facts claimed / corroborated, set the honest confidence.
    Returns breakdown parts. Stops with ✗ on a corroboration that breaks the rules."""
    name = c.get("candidate_name")
    claimed = c.setdefault("confidence_claimed", c.get("evidence_confidence"))
    primary = _clean(c.get("source")) or "primary source"
    sources = [primary]
    facts = [{"fact": _clean(f.get("fact") if isinstance(f, dict) else f), "status": "claimed",
              "source": _clean(f.get("source")) if isinstance(f, dict) and f.get("source") else primary}
             for f in (c.get("facts") or [])]
    cor = c.get("corroboration")
    corroborated = 0
    if cor:
        if not isinstance(cor, dict):
            progress.fail_exit(f"{name}: corroboration must be one object (at most one fetched source per person)")
        url, snip = _clean(cor.get("url")), _clean(cor.get("snippet"))
        host = _host(url)
        if not host or NO_FETCH_HOSTS.search(host):
            progress.fail_exit(f"{name}: corroboration url must be a non-LinkedIn, non-Indeed public page, got {url!r}")
        if host == _host(c.get("profile_url", "")):
            progress.fail_exit(f"{name}: corroboration {url!r} is the profile page itself, not an independent source")
        if not snip:
            progress.fail_exit(f"{name}: corroboration has no snippet (the fetched text the facts rest on)")
        kind = _clean(cor.get("source_type")) or "Web page"
        low = snip.lower()
        for f in cor.get("confirms") or []:
            fact, quote = _clean(f.get("fact")), _clean(f.get("quote"))
            if not fact or not quote or quote.lower() not in low:
                progress.fail_exit(f"{name}: corroboration quote not found in the fetched snippet: {quote!r}")
            src = f"{kind}: {url}"
            same = next((x for x in facts if x["fact"].lower() == fact.lower()), None)
            if same:
                same.update(status="corroborated", source=src)
            else:
                facts.append({"fact": fact, "status": "corroborated", "source": src})
            corroborated += 1
        if corroborated:
            sources.append(kind)
    dev_kinds = tuple(f"{k}: " for k in DEV_SOURCE_TYPES.values())
    facts = [x for x in facts if not x["source"].startswith(dev_kinds)]   # a rerun rebuilds them from dev_signals
    seen_hosts = {_host(c.get("profile_url", ""))} | ({_host(cor.get("url"))} if corroborated else set())
    for kind, sig in dev_sources(c):
        cols = dev_columns(c)
        summary = cols["github_languages"] if kind == "GitHub API" else cols["so_top_tags"]
        if kind == "GitHub API" and cols["github_last_active"]:
            summary = (summary + f"; last push {cols['github_last_active']}").strip("; ")
        facts.append({"fact": summary or sig["facts"][0]["fact"], "status": "corroborated", "source": f"{kind}: {sig['url']}"})
        host = _host(sig["url"])
        if host not in seen_hosts:
            seen_hosts.add(host)
            sources.append(kind)
    c["facts"] = facts
    c["evidence_sources"] = len(sources)
    conf = claimed
    if conf == "Medium" and len(sources) >= 2:
        conf = "High"
    elif conf == "High" and len(sources) < 2:
        conf = "Medium"
    c["evidence_confidence"] = conf
    raw = c.setdefault("evidence_notes_raw", c.get("evidence_notes") or "")
    if facts:
        c["evidence_notes"] = (raw + " | Facts: " + "; ".join(
            f"{x['status']} ({x['source']}): {x['fact']}" for x in facts)).strip(" |")
    part = f"Evidence Sources {len(sources)} ({'; '.join(sources)})"
    if conf != claimed:
        part += f"; confidence {claimed} → {conf}" + (" (corroborated)" if conf == "High" else " (High needs 2+ independent sources)")
    return [part]


def score(c: dict, weights: dict, criteria: dict | None = None) -> None:
    criteria = criteria or {}
    pts = dict(c.get("scores") or {})
    unknown = set(pts) - set(weights)
    if unknown:
        progress.fail_exit(f"{c.get('candidate_name')}: unknown score dimensions {sorted(unknown)}")
    if "location_raw" not in c and c.get("location"):
        c["location_raw"] = c["location"]
    raw_loc = c.get("location_raw") or ""
    notes, soft = {}, False
    if "experience" in weights:
        pts["experience"], notes["experience"], soft = experience_points(
            c.get("experience", ""), criteria.get("experience_years") or {}, weights["experience"])
    loc_note = location_points(raw_loc, criteria, weights.get("location", 0))[1]
    if "location" in weights:
        pts["location"], notes["location"] = location_points(raw_loc, criteria, weights["location"])
    if raw_loc:
        c["location"] = normalise_location(raw_loc)
        if c["location"] != raw_loc.strip():
            notes["location"] += f"; listed as '{raw_loc.strip()}'"
    llm_dims = [d for d in weights if d not in CODE_DIMS]
    if "quotes" in c:
        check_quotes(c, llm_dims)
    parts, total = [], 0
    for dim, w in weights.items():
        p = pts.get(dim, 0)
        if not isinstance(p, (int, float)) or not 0 <= p <= w:
            progress.fail_exit(f"{c.get('candidate_name')}: {dim}={p} is outside 0-{w}")
        total += p
        parts.append(f"{LABELS.get(dim, dim)} {round(p)}/{w}" + (f" ({notes[dim]})" if dim in notes else ""))
    c["scores"] = pts
    otw, otw_quote = open_to_work(c)
    c["open_to_work"], c["open_to_work_quote"] = otw, otw_quote
    c["open_to_work_match"] = f'{otw}: "{otw_quote}"' if otw_quote else otw
    total += OTW_POINTS[otw]
    parts.append(f'Open to work +{OTW_POINTS[otw]} ({otw}: "{otw_quote}")' if otw_quote else "Open to work Unknown (no signal, no penalty)")
    c["stack_match"] = stack_match(c, criteria, weights, pts)
    c["experience_match"] = experience_match(c.get("experience", ""), criteria.get("experience_years") or {})
    c["location_match"] = location_match(loc_note, c.get("location") or "")
    parts += apply_evidence(c)
    total = min(100, round(total))
    if soft and total > SOFT_GATE_CAP:
        total = SOFT_GATE_CAP
        parts.append(f"SOFT GATE: 1+ yr below the {criteria['experience_years']['min']}-yr minimum (capped at {SOFT_GATE_CAP})")
    missing = c.get("mandatory_missing") or []
    if missing:
        total = min(total, GATE_CAP)
        parts.append(f"GATE: missing mandatory {', '.join(missing)} (capped at {GATE_CAP})")
    cap = CONF_CAPS.get(c.get("evidence_confidence"))
    if cap is not None and total > cap:
        total = cap
        parts.append(f"{c['evidence_confidence']} confidence (capped at {cap})")
    c["match_score"] = total
    c["score_breakdown"] = " · ".join(parts)


def spread(scores: list[int]) -> dict:
    """Nearest-rank p10/p50/p90 of the batch's match scores."""
    s = sorted(scores)
    if not s:
        return {}
    pick = lambda q: s[max(0, math.ceil(q * len(s)) - 1)]  # noqa: E731
    return {"p10": pick(0.1), "p50": pick(0.5), "p90": pick(0.9), "min": s[0], "max": s[-1]}


def spread_warning(sp: dict) -> str | None:
    if sp and sp["p50"] > SPREAD_WARN_P50:
        return (f"⚠ score spread p50 {sp['p50']} > {SPREAD_WARN_P50}: this batch is not discriminating; "
                "check core-skill points and their quotes")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    payload = json.loads(Path(a.input).read_text())
    w, crit = jd_for(payload.get("jd_id", ""))
    cands, dropped = drop_suppressed(payload["candidates"])
    payload["candidates"] = cands
    if dropped:
        progress.sub(f"{dropped} suppressed people dropped before scoring (they asked to be forgotten)")
    for i, c in enumerate(cands, 1):
        score(c, w, crit)
        if progress.every(i, len(cands)):
            progress.sub(f"scoring {i}/{len(cands)} · {c.get('candidate_name')} {c['match_score']}")
    out = Path(a.out or a.input)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    gated = sum(bool(c.get("mandatory_missing")) for c in cands)
    soft = sum("SOFT GATE" in c["score_breakdown"] for c in cands)
    low = sum(c.get("evidence_confidence") == "Low" for c in cands)
    unquoted = sum("quotes" not in c for c in cands)
    otw = {v: sum(c.get("open_to_work") == v for c in cands) for v in ("Yes", "Likely")}
    top = max((c["match_score"] for c in cands), default=0)
    sp = spread([c["match_score"] for c in cands])
    warn = spread_warning(sp)
    if sp:
        progress.sub(f"spread p10 {sp['p10']} · p50 {sp['p50']} · p90 {sp['p90']}")
    if warn:
        progress.sub(warn)
    if unquoted:
        progress.sub(f"{unquoted} candidates have no quotes for their LLM points")
    progress.result(f"{len(cands)} scored, top {top}, {gated} capped by a mandatory-skill gate, "
                    f"{soft} by the experience soft gate, {low} Low confidence, "
                    f"{sum(c.get('evidence_sources', 1) >= 2 for c in cands)} with 2+ evidence sources, "
                    f"open to work {otw['Yes']} Yes / {otw['Likely']} Likely")
    print(json.dumps({"scored": len(cands), "out": str(out), "spread": sp, "warning": warn, "suppressed_dropped": dropped, "open_to_work": otw,
                      "scores": {c["candidate_name"]: c["match_score"] for c in cands}}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
