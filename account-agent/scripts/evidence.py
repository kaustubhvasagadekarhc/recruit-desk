"""Evidence table for the account agent: one row per cited fact, with its source, type, check date and confidence.

The Accounts sheets keep their 19 columns and their inline `[n]` markers (the app switches later). This module adds:

  - an `Evidence` sheet (EVIDENCE_COLUMNS): Domain, Field, Fact, Source URL, Source Type, Checked On, Age (days),
    Confidence. One row per (fact, source). A fact without a source URL is never written.
  - three derived columns appended after the 19 on every Accounts sheet (DERIVED_COLUMNS): Evidence Count,
    Freshness, Key Field Confidence (the weakest of the key fields, each rated by its strongest source).

excel_upsert.py writes both on every upsert. `excel_upsert.py --migrate-evidence` rebuilds them for every company
already in memory.db (the `facts` table), after backing up the workbook and memory.db.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlsplit

from common import COLUMNS, F_BEST, F_CHECKED, F_COMPANY, F_CONF, F_DOMAIN, F_SOURCES, F_STARTER, F_WARM, CITE_RE, \
    _FIELDS, bare_domain, citations, is_not_found, parse_sources, today, urls_in

EVIDENCE_SHEET = "Evidence"
EVIDENCE_COLUMNS = ["Domain", "Field", "Fact", "Source URL", "Source Type", "Checked On", "Age (days)", "Confidence"]
DERIVED_COLUMNS: list[str] = _FIELDS["derived_columns"]
KEY_FIELDS: list[str] = _FIELDS["key_fields"]
F_EVIDENCE_COUNT, F_FRESHNESS, F_KEY_CONF = DERIVED_COLUMNS
# Fields that are not facts about the company (labels, HumanCloud side, bookkeeping).
SKIP_FIELDS = {F_COMPANY, F_DOMAIN, F_SOURCES, F_CONF, F_CHECKED, F_WARM, F_BEST, F_STARTER, "Product?"}
RANK = {"High": 3, "Medium": 2, "Low": 1}
FRESH_DAYS, AGEING_DAYS = 30, 180  # 180 = the retention window

NEWS = re.compile(r"(?i)(^|\.)(reuters|bloomberg|techcrunch|economictimes\.indiatimes|livemint|moneycontrol|yourstory|"
                  r"inc42|entrackr|vccircle|business-standard|thehindu|hindustantimes|forbes|cnbc|wsj|ft|nytimes|"
                  r"theverge|venturebeat|axios|businessinsider|fortune|zdnet|siliconangle|crunchbase|"
                  r"prnewswire|businesswire|globenewswire|news\.google)\.[a-z.]+$|(^|\.)news\.")
ATS = re.compile(r"(?i)(^|\.)(greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com|workable\.com)$")
DATA_PROVIDER = re.compile(r"(?i)(^|\.)(reveliolabs\.com|apollo\.io|tracxn\.com|pitchbook\.com|craft\.co|"
                           r"growjo\.com|zoominfo\.com|owler\.com|cbinsights\.com)$")
REGISTRY = re.compile(r"(?i)(^|\.)(gleif\.org|company-information\.service\.gov\.uk|find-and-update\.company-information"
                      r"\.service\.gov\.uk|mca\.gov\.in|data\.gov\.in)$")
TYPE_CONFIDENCE = {"Filing": "High", "Registry": "High", "Company site": "High", "Careers / ATS": "High", "DNS": "High",
                   "Wikidata": "Medium", "Wikipedia": "Medium", "News": "Medium", "Code host": "Medium",
                   "Data provider": "Medium", "Web": "Low"}


def source_type(url: str, domain: str = "") -> str:
    host = (urlsplit(url).hostname or "").lower()
    d = bare_domain(domain)
    if host in ("sec.gov", "dol.gov") or host.endswith((".sec.gov", ".dol.gov")):
        return "Filing"
    if REGISTRY.search(host):
        return "Registry"
    if host == "dns.google":
        return "DNS"
    if d and (host == d or host.endswith("." + d)):
        return "Company site"
    if host.endswith("wikidata.org"):
        return "Wikidata"
    if host.endswith("wikipedia.org"):
        return "Wikipedia"
    if ATS.search(host):
        return "Careers / ATS"
    if host in ("github.com", "gitlab.com") or host.endswith(".github.io"):
        return "Code host"
    if DATA_PROVIDER.search(host):
        return "Data provider"
    if NEWS.search(host):
        return "News"
    return "Web"


def age_days(checked: str, on: date | None = None) -> int | None:
    try:
        return ((on or date.today()) - date.fromisoformat(str(checked)[:10])).days
    except ValueError:
        return None


def freshness(age: int | None) -> str:
    if age is None:
        return ""
    return "Fresh" if age <= FRESH_DAYS else "Ageing" if age <= AGEING_DAYS else "Stale"


def _clean(text: str) -> str:
    text = CITE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([)\]}])", r"\1", re.sub(r"([(\[{])\s+", r"\1", text)).strip(" ;,.:\n\t")
    return text[:300]


_OPEN, _CLOSE = "([{", ")]}"
_ABBR = {"inc", "ltd", "co", "corp", "st", "mr", "ms", "dr", "jr", "sr", "no", "vs", "approx", "est", "etc", "e.g", "i.e"}


def _scan(value: str) -> tuple[list[int], list[int], list[int]]:
    """Per character: bracket depth, position of the innermost open bracket (-1 at depth 0) and of the outermost.

    [n] citation markers are not brackets. A stray closer never takes the depth below 0; an opener that is never
    closed keeps everything after it inside, so nothing is cut there.
    """
    skip = {i for m in CITE_RE.finditer(value) for i in range(m.start(), m.end())}
    depth, inner, outer, stack = [], [], [], []
    for i, ch in enumerate(value):
        if i not in skip and ch in _CLOSE and stack:
            stack.pop()
        depth.append(len(stack))
        inner.append(stack[-1] if stack else -1)
        outer.append(stack[0] if stack else -1)
        if i not in skip and ch in _OPEN:
            stack.append(i)
    return depth, inner, outer


def _cuts(value: str, depth: list[int]) -> list[int]:
    """Clause boundaries: ';', a line break or a sentence end ('. ' then a capital), never inside brackets."""
    out = []
    for i, ch in enumerate(value):
        if ch in ";\n":
            out.append((i, depth[i]))
        elif ch == "." and re.match(r"\.\s+[A-Z]", value[i:]):
            word = re.search(r"([\w.]+)$", value[:i])
            w = word.group(1).lower() if word else ""
            if w and len(w) > 1 and w not in _ABBR and not re.fullmatch(r"(?:[a-z]\.)*[a-z]", w):
                out.append((i, depth[i]))
    return out


def split_facts(value: str) -> list[tuple[str, list[int]]]:
    """'Founded 2014 [3]; approx. 5,200 employees [16]' -> [('Founded 2014', [3]), ('approx. 5,200 employees', [16])].

    A fact is the clause (split on ';', a line break or a sentence end) that ends at its [n] marker. Markers right
    after another marker (`[3] [4]`) share the previous clause. Brackets are respected: a marker inside a
    parenthetical cites that parenthetical's clause (kept with the words before it, 'X (in SF)'), a marker after it cites
    the whole clause with the parenthetical left in, and no clause is ever cut inside an open bracket.
    """
    value = value or ""
    depth, inner, outer = _scan(value)
    cuts = _cuts(value, depth)
    out: list[tuple[str, list[int]]] = []
    ends: dict[int, int] = {}  # end of the last top-level marker
    last = ""
    for m in CITE_RE.finditer(value):
        nums = [int(x) for x in re.split(r"\s*[,;]\s*", m.group(1))]
        pos, d = m.start(), depth[m.start()] if m.start() < len(depth) else 0
        if d == 0:
            lo = max([ends.get(0, 0)] + [c + 1 for c, cd in cuts if cd == 0 and c < pos])
            fact = _clean(value[lo:pos])
        else:
            o, top = inner[pos], outer[pos]
            lo = max([o + 1] + [c + 1 for c, cd in cuts if cd == d and o < c < pos])
            close = next((j for j in range(pos, len(value)) if depth[j] < d), len(value))
            hi = min([close] + [c for c, cd in cuts if cd == d and pos < c < close])
            body = _clean(value[lo:hi])
            ctx_lo = max([0] + [c + 1 for c, cd in cuts if cd == 0 and c < top])
            ctx = _clean(value[ctx_lo:top])
            fact = f"{ctx} ({body})" if ctx and body else body
        if d == 0:
            ends[0] = m.end()
        fact = fact or last
        if fact:
            out.append((fact[:300], nums))
            last = fact
    return out


def facts_from_value(field: str, value: str, sources: dict[int, str]) -> list[tuple[str, str]]:
    """(fact, url) pairs for one cell: each [n] resolved through the Sources map, plus inline URLs."""
    pairs: list[tuple[str, str]] = []
    for fact, nums in split_facts(value):
        for n in nums:
            if n in sources:
                pairs.append((fact, sources[n]))
    for u in urls_in(CITE_RE.sub("", value)):
        pairs.append((_clean(value.replace(u, "")) or field, u))
    seen, uniq = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def evidence_for_row(row: dict, on: date | None = None) -> list[list]:
    """Evidence rows (EVIDENCE_COLUMNS order) for one account row."""
    d = bare_domain(str(row.get(F_DOMAIN) or ""))
    checked = str(row.get(F_CHECKED) or today())[:10]
    age = age_days(checked, on)
    sources, _ = parse_sources(str(row.get(F_SOURCES) or ""))
    out = []
    for field in COLUMNS:
        v = str(row.get(field) or "").strip()
        if field in SKIP_FIELDS or not v or is_not_found(v):
            continue
        for fact, url in facts_from_value(field, v, sources):
            st = source_type(url, d)
            out.append([d, field, fact, url, st, checked, age, TYPE_CONFIDENCE[st]])
    return out


def evidence_from_facts(domain: str, facts: list[dict], on: date | None = None) -> list[list]:
    """Evidence rows rebuilt from memory.db `facts` rows ({field, value, sources (JSON list), checked}).

    memory.record_row stores, per cell, the URL of every [n] marker in order, then the inline URLs. The markers are
    re-read from the value and paired with those URLs by position; a cell whose counts disagree falls back to one
    row per URL with the whole cell as the fact.
    """
    d = bare_domain(domain)
    out = []
    for f in facts:
        field, v = f["field"], str(f["value"] or "").strip()
        if field in SKIP_FIELDS or not v or is_not_found(v):
            continue
        try:
            urls = [u for u in json.loads(f["sources"] or "[]") if isinstance(u, str) and u.startswith(("http://", "https://"))]
        except ValueError:
            urls = []
        if not urls:
            continue
        checked = str(f["checked"] or "")[:10]
        age = age_days(checked, on)
        marks = [(fact, n) for fact, nums in split_facts(v) for n in nums]
        inline = urls_in(CITE_RE.sub("", v))
        pairs: list[tuple[str, str]] = []
        if len(urls) == len(marks) + len(inline):
            pairs = [(fact, urls[i]) for i, (fact, _) in enumerate(marks)]
            pairs += [(_clean(v.replace(u, "")) or field, u) for u in inline]
        else:
            pairs = [(_clean(v), u) for u in dict.fromkeys(urls)]
        for fact, url in dict.fromkeys(pairs):
            st = source_type(url, d)
            out.append([d, field, fact, url, st, checked, age, TYPE_CONFIDENCE[st]])
    return out


def derived(ev_rows: list[list]) -> dict[str, object]:
    """Evidence Count, Freshness (oldest key-field check) and Key Field Confidence (weakest key field)."""
    if not ev_rows:
        return {F_EVIDENCE_COUNT: 0, F_FRESHNESS: "", F_KEY_CONF: ""}
    best: dict[str, str] = {}
    ages: list[int] = []
    for _, field, _, _, _, _, age, conf in ev_rows:
        if field in KEY_FIELDS:
            if RANK[conf] > RANK.get(best.get(field, ""), 0):
                best[field] = conf
            if age is not None:
                ages.append(age)
    if not ages:
        ages = [r[6] for r in ev_rows if r[6] is not None]
    weakest = min(best.values(), key=lambda c: RANK[c]) if best else ""
    return {F_EVIDENCE_COUNT: len(ev_rows), F_FRESHNESS: freshness(max(ages) if ages else None), F_KEY_CONF: weakest}
