"""Typed company fields for the account agent: one fact per cell, read only from cited facts.

The prose cells (Company Brief, HQ / US Office, India Office, Funding) stay as they are. This module reads them and
fills the typed columns appended after the derived Evidence columns (schema/fields.json typed_columns):

  Founded Year, Employees, Employees As Of, Last Round, Last Round Amount USD, Last Round Date, Total Raised USD,
  HQ City, HQ Country, India Office City, Public/Private, Ticker

Rules:
  - A value is set only when a clause of a cited cell states it and that clause carries an [n] marker whose URL is
    known. The citation is the first marker after the match at the same bracket depth, so a parenthetical
    "(Wikipedia shows 5,300 [4])" never lends its source to the main clause.
  - Numbers are ints (USD amounts in whole dollars); dates are ISO strings (YYYY, YYYY-MM or YYYY-MM-DD, as precise
    as the source); Last Round is one of ROUNDS; HQ Country is an ISO 3166 alpha-2 code.
  - Every typed value gets its own Evidence row (Field = the typed column, Fact = the clause it was read from). A
    typed value is valid only if re-reading that clause gives the same value (check_typed); validate_rows applies the
    same rule to typed values a rows file sets itself.
  - Nothing is inferred: "Series D" never makes a company Private, and a draft IPO filing is not an IPO.
"""
from __future__ import annotations

import json
import re
from datetime import date

from common import CITE_RE, F_BRIEF, F_CHECKED, F_DOMAIN, F_FUNDING, F_HQ, F_INDIA_OFFICE, _FIELDS, bare_domain, \
    is_not_found, parse_sources, today, urls_in
from evidence import TYPE_CONFIDENCE, age_days, source_type

TYPED_COLUMNS: list[str] = _FIELDS["typed_columns"]
(T_FOUNDED, T_EMP, T_EMP_ASOF, T_ROUND, T_ROUND_USD, T_ROUND_DATE, T_TOTAL, T_HQ_CITY, T_HQ_COUNTRY, T_INDIA_CITY,
 T_PUBLIC, T_TICKER) = TYPED_COLUMNS
INT_COLUMNS = {T_FOUNDED, T_EMP, T_ROUND_USD, T_TOTAL}
DATE_COLUMNS = {T_EMP_ASOF, T_ROUND_DATE}
ROUNDS = ["Pre-Seed", "Seed"] + [f"Series {c}" for c in "ABCDEFGHIJK"] + ["IPO"]
PUBLIC_VALUES = ("Public", "Private")
# The prose cell each typed column is read from, in order of preference.
SOURCE_FIELDS = {T_FOUNDED: [F_BRIEF], T_EMP: [F_BRIEF], T_EMP_ASOF: [F_BRIEF], T_ROUND: [F_FUNDING],
                 T_ROUND_USD: [F_FUNDING], T_ROUND_DATE: [F_FUNDING], T_TOTAL: [F_FUNDING], T_HQ_CITY: [F_HQ],
                 T_HQ_COUNTRY: [F_HQ], T_INDIA_CITY: [F_INDIA_OFFICE], T_PUBLIC: [F_FUNDING, F_BRIEF],
                 T_TICKER: [F_FUNDING, F_BRIEF]}
ISO_DATE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")

MONTHS = {m: i for i, ms in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], 1) for m in ms}
_MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
DATE_PATTERNS = [
    (re.compile(r"\b((?:19|20)\d\d)-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?\b"), "iso"),
    (re.compile(_MON + r"\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b", re.I), "mdy"),
    (re.compile(r"\b(\d{1,2})\s+" + _MON + r",?\s+((?:19|20)\d\d)\b", re.I), "dmy"),
    (re.compile(r"\b" + _MON + r",?\s+((?:19|20)\d\d)\b", re.I), "my"),
]
FOUNDED_RE = re.compile(r"(?i)\b(?:founded|established|incorporated|inception)\b[^;.\d]{0,40}?\b((?:18|19|20)\d\d)\b")
EMP_RE = re.compile(r"(?i)(?<![\w$.,])(?:(over|more than|at least|nearly|under|fewer than|less than)\s+)?"
                    r"(?:approx\.?|approximately|about|around|roughly|~)?\s*(\d{1,3}(?:,\d{3})+|\d{1,6})(\+?)\s+"
                    r"(?:full[- ]time\s+)?(?:employees|staff|team members)\b")
MONEY_RE = re.compile(r"(?i)(?:US\s?)?\$\s?(\d+(?:,\d{3})*(?:\.\d+)?)\s*(billion|bn|b|million|mn|mm|m|thousand|k)?\b")
ROUND_RE = re.compile(r"(?i)(?<![\w-])(pre-seed|seed|series [a-k]|ipo)\b")
NOT_A_RAISE = re.compile(r"(?i)\b(draft|drhp|confidential\w*|plans?|planned|planning|preparing|prospectus|could|"
                         r"rumou?r\w*|expected|considering|no newer|not found|checked)\b")
TOTAL_AFTER = re.compile(r"(?i)^\s*(?:raised\s+)?(?:in total|to date|total(?: funding| raised)?|raised in total)\b")
TOTAL_BEFORE = re.compile(r"(?i)\b(?:total(?: funding| raised)?(?: of)?|raised a total of|in total)\s*$")
LOWER_BOUND = re.compile(r"(?i)\b(more than|over|at least|nearly)\s*$")
PUBLIC_RE = re.compile(r"(?i)\b(publicly (?:traded|listed)|public company|listed on (?:the )?(?:nasdaq|nyse|nse|bse|lse)|"
                       r"^public\b)|(?<![\w.])Public \(")
PRIVATE_RE = re.compile(r"(?i)\b(privately held|private company|privately owned)\b")
EXCHANGE = r"(?:Nasdaq|NASDAQ|NYSE|NSE|BSE|LSE)"
TICKER_RES = [re.compile(EXCHANGE + r"\s*[:(]\s*([A-Z][A-Z.]{0,5})\b"),
              re.compile(r"\(([A-Z]{1,6}),\s*" + EXCHANGE + r"\)"),
              re.compile(r"(?<![\w.])Public \(([A-Z]{1,6})\b")]
HQ_KEY = re.compile(r"(?i)\b(HQ|head ?quarters?|headquartered)\b")
CITY_RE = re.compile(r"\b([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3}),\s*([A-Z]{2}|[A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})\b")

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
US_STATE_NAMES = {v.lower() for v in US_STATES.values()}
COUNTRIES = {"india": "IN", "united states": "US", "usa": "US", "us": "US", "united kingdom": "GB", "uk": "GB",
             "england": "GB", "canada": "CA", "germany": "DE", "france": "FR", "singapore": "SG", "israel": "IL",
             "netherlands": "NL", "ireland": "IE", "australia": "AU", "japan": "JP", "sweden": "SE",
             "switzerland": "CH", "spain": "ES", "brazil": "BR", "united arab emirates": "AE", "uae": "AE"}
INDIA_CITIES = {"bengaluru": "Bengaluru", "bangalore": "Bengaluru", "pune": "Pune", "hyderabad": "Hyderabad",
                "chennai": "Chennai", "madras": "Chennai", "mumbai": "Mumbai", "bombay": "Mumbai",
                "new delhi": "New Delhi", "delhi": "Delhi", "gurugram": "Gurugram", "gurgaon": "Gurugram",
                "noida": "Noida", "kolkata": "Kolkata", "ahmedabad": "Ahmedabad", "kochi": "Kochi",
                "jaipur": "Jaipur", "chandigarh": "Chandigarh", "coimbatore": "Coimbatore", "indore": "Indore",
                "thiruvananthapuram": "Thiruvananthapuram", "trivandrum": "Thiruvananthapuram", "mysuru": "Mysuru",
                "mysore": "Mysuru", "nagpur": "Nagpur", "vadodara": "Vadodara", "bhubaneswar": "Bhubaneswar"}
INDIA_CITY_RE = re.compile(r"(?i)\b(" + "|".join(sorted(INDIA_CITIES, key=len, reverse=True)) + r")\b")


# ---- small parsers -------------------------------------------------------------------------------------------------

def parse_date(text: str) -> tuple[str, int, int] | None:
    """The first date in text as (ISO string, start, end), at the source's precision."""
    best = None
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m or (best and m.start() >= best[1]):
            continue
        g = m.groups()
        if kind == "iso":
            iso = "-".join(x for x in g if x)
        elif kind == "mdy":
            iso = f"{g[2]}-{MONTHS[g[0].lower().rstrip('.')]:02d}-{int(g[1]):02d}"
        elif kind == "dmy":
            iso = f"{g[2]}-{MONTHS[g[1].lower().rstrip('.')]:02d}-{int(g[0]):02d}"
        else:
            iso = f"{g[1]}-{MONTHS[g[0].lower().rstrip('.')]:02d}"
        if ISO_DATE.match(iso) and _real_date(iso):
            best = (iso, m.start(), m.end())
    return best


def _real_date(iso: str) -> bool:
    if len(iso) < 10:
        return True
    try:
        date.fromisoformat(iso)
        return True
    except ValueError:
        return False


def money(m: re.Match) -> int:
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    mult = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
            "thousand": 1e3, "k": 1e3}.get(unit, 1)
    return int(round(n * mult))


def _round_name(s: str) -> str:
    s = s.lower()
    return {"pre-seed": "Pre-Seed", "seed": "Seed", "ipo": "IPO"}.get(s) or "Series " + s[-1].upper()


def _round_rank(name: str) -> int:
    return ROUNDS.index(name)


def clause_bounds(text: str, pos: int) -> tuple[int, int]:
    """The clause around pos: from the previous ';' or sentence end to the next one (or the text's ends)."""
    start = 0
    for m in re.finditer(r";|\.\s+(?=[A-Z])", text[:pos]):
        start = m.end()
    m = re.compile(r";|\.\s+(?=[A-Z])").search(text, pos)
    return start, (m.start() if m else len(text))


def _depths(text: str) -> list[int]:
    out, d = [], 0
    for ch in text:
        if ch == ")":
            d = max(0, d - 1)
        out.append(d)
        if ch == "(":
            d += 1
    return out


def cite_after(text: str, pos: int, srcmap: dict[int, str]) -> str:
    """URL of the first [n] marker at or after pos at the same bracket depth (an inline URL counts too)."""
    depth = _depths(text)
    want = depth[pos] if pos < len(depth) else 0
    marks = [(m.start(), m) for m in CITE_RE.finditer(text) if m.start() >= pos and depth[m.start()] <= want]
    for _, m in marks:
        for x in re.split(r"\s*[,;]\s*", m.group(1)):
            if int(x) in srcmap:
                return srcmap[int(x)]
    for u in urls_in(text[pos:]):
        return u
    return ""


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", CITE_RE.sub("", text)).strip(" ;,.:\n\t")[:300]


# ---- extractors: text -> [(value, match start, match end)] -----------------------------------------------------------

def x_founded(t: str) -> list:
    return [(int(m.group(1)), m.start(), m.end()) for m in FOUNDED_RE.finditer(t)]


def x_employees(t: str) -> list:
    return [(int(m.group(2).replace(",", "")), m.start(2), m.end()) for m in EMP_RE.finditer(t)
            if not m.group(1) and not m.group(3)]


def x_emp_asof(t: str) -> list:
    out = []
    for m in EMP_RE.finditer(t):
        if m.group(1) or m.group(3):
            continue
        _, end = clause_bounds(t, m.end())
        nxt = EMP_RE.search(t, m.end())
        stop = min(end, nxt.start() if nxt else end)
        d = parse_date(t[m.end():stop])
        if d:
            out.append((d[0], m.start(2), m.end()))
    return out


def _checked_spans(t: str) -> list[tuple[int, int]]:
    """'(checked: ... )' notes, which name searches, never rounds."""
    out = []
    for m in re.finditer(r"(?i)\(checked:", t):
        depth, i = 0, m.start()
        while i < len(t):
            depth += {"(": 1, ")": -1}.get(t[i], 0)
            if depth == 0:
                break
            i += 1
        out.append((m.start(), i + 1))
    return out


def _is_raise(text: str, mm: re.Match) -> bool:
    after = text[mm.end():mm.end() + 14].lower()
    before = text[max(0, mm.start() - 12):mm.start()].lower()
    return not ("per share" in after or "valuation" in after or "valuation" in before or "secondary" in after)


def _raise_amount(text: str) -> int | None:
    for mm in MONEY_RE.finditer(text):
        if _is_raise(text, mm):
            return money(mm)
    return None


def rounds(t: str) -> list[dict]:
    """Every round named in t with the amount and date stated in its clause."""
    out = []
    skip = _checked_spans(t)
    for m in ROUND_RE.finditer(t):
        if any(a <= m.start() < b for a, b in skip):
            continue
        s, e = clause_bounds(t, m.start())
        clause = t[m.start():e]
        if NOT_A_RAISE.search(t[s:e]):
            continue
        amt = _raise_amount(clause)
        if amt is None:  # "$75M Series D": the amount just before the round name, in the same clause
            amts = [mm for mm in MONEY_RE.finditer(t[s:m.start()]) if _is_raise(t[s:m.start()], mm)]
            amt = money(amts[-1]) if amts else None
        d = parse_date(clause)
        out.append({"round": _round_name(m.group(1)), "amount": amt, "date": d[0] if d else None,
                    "start": m.start(), "end": m.end()})
    return out


def last_round(t: str) -> dict | None:
    rs = rounds(t)
    if not rs:
        return None
    top = max(_round_rank(r["round"]) for r in rs)
    best = [r for r in rs if _round_rank(r["round"]) == top]
    return next((r for r in best if r["amount"]), best[0])


def x_round(key: str):
    def f(t: str) -> list:
        return [(r[key], r["start"], r["end"]) for r in rounds(t) if r[key]]
    return f


def x_total(t: str) -> list:
    out = []
    for m in MONEY_RE.finditer(t):
        pre = t[max(0, m.start() - 30):m.start()]
        pre = re.sub(r"(?i)(about|approx\.?|approximately|around|roughly|~)\s*$", "", pre.rstrip()).rstrip() + " "
        if LOWER_BOUND.search(pre.rstrip()):
            continue
        if TOTAL_AFTER.search(t[m.end():m.end() + 30]) or TOTAL_BEFORE.search(pre.rstrip()):
            out.append((money(m), m.start(), m.end()))
    return out


def x_public(t: str) -> list:
    out = [("Public", m.start(), m.end()) for m in PUBLIC_RE.finditer(t)]
    out += [("Private", m.start(), m.end()) for m in PRIVATE_RE.finditer(t)]
    return sorted(out, key=lambda x: x[1])


def x_ticker(t: str) -> list:
    out = {}
    for rx in TICKER_RES:
        for m in rx.finditer(t):
            out.setdefault(m.start(1), (m.group(1).rstrip("."), m.start(1), m.end(1)))
    return sorted(out.values(), key=lambda x: x[1])


def _place(t: str) -> list[tuple[str, str, int, int]]:
    """(city, country code, start, end) for every 'City, ST|State|Country' in t."""
    out = []
    for m in CITY_RE.finditer(t):
        city, region = m.group(1), m.group(2)
        cc = ""
        if region in US_STATES or region.lower() in US_STATE_NAMES:
            cc = "US"
        elif region.lower() in COUNTRIES:
            cc = COUNTRIES[region.lower()]
        if cc and city.lower() not in COUNTRIES and city.lower() not in US_STATE_NAMES:
            out.append((city, cc, m.start(), m.end()))
    return out


def _hq_places(t: str) -> list[tuple[str, str, int, int]]:
    out = []
    for k in HQ_KEY.finditer(t):
        s, e = clause_bounds(t, k.start())
        places = _place(t[s:e])
        after = [p for p in places if p[2] + s >= k.end()]
        p = (after or places or [None])[0] if t[k.end():k.end() + 3].strip().startswith(":") else (places or [None])[0]
        if p:
            out.append((p[0], p[1], p[2] + s, p[3] + s))
    return out


def x_hq_city(t: str) -> list:
    return [(c, s, e) for c, _, s, e in _hq_places(t)]


def x_hq_country(t: str) -> list:
    return [(cc, s, e) for _, cc, s, e in _hq_places(t)]


def x_india_city(t: str) -> list:
    return [(INDIA_CITIES[m.group(1).lower()], m.start(), m.end()) for m in INDIA_CITY_RE.finditer(t)]


EXTRACT = {T_FOUNDED: x_founded, T_EMP: x_employees, T_EMP_ASOF: x_emp_asof, T_ROUND: x_round("round"),
           T_ROUND_USD: x_round("amount"), T_ROUND_DATE: x_round("date"), T_TOTAL: x_total, T_HQ_CITY: x_hq_city,
           T_HQ_COUNTRY: x_hq_country, T_INDIA_CITY: x_india_city, T_PUBLIC: x_public, T_TICKER: x_ticker}


def candidates(col: str, text: str) -> list:
    """Every value of a typed column that text states (citation markers ignored)."""
    return [v for v, _, _ in EXTRACT[col](CITE_RE.sub("", text or ""))]


# ---- deriving the typed values of one company ------------------------------------------------------------------------

def _pick(col: str, text: str):
    """The value for col in one cell and its match position, or None."""
    if col in (T_ROUND, T_ROUND_USD, T_ROUND_DATE):
        r = last_round(text)
        key = {T_ROUND: "round", T_ROUND_USD: "amount", T_ROUND_DATE: "date"}[col]
        return (r[key], r["start"], r["end"]) if r and r[key] else None
    found = EXTRACT[col](text)
    return found[0] if found else None


def derive(cells: dict[str, str], srcmaps: dict[str, dict[int, str]], domain: str, checked: str,
           on: date | None = None) -> tuple[dict[str, object], list[list]]:
    """Typed values and their Evidence rows (EVIDENCE_COLUMNS order) for one company.

    cells: prose field -> cell text; srcmaps: prose field -> {n: url} for that cell's markers.
    """
    d = bare_domain(domain)
    age = age_days(checked, on)
    values: dict[str, object] = {}
    ev: list[list] = []
    for col in TYPED_COLUMNS:
        for field in SOURCE_FIELDS[col]:
            text = str(cells.get(field) or "").strip()
            if not text or is_not_found(text):
                continue
            hit = _pick(col, text)
            if not hit:
                continue
            value, start, _ = hit
            url = cite_after(text, start, srcmaps.get(field, {}))
            if not url:
                continue
            s, e = clause_bounds(text, start)
            quote = _plain(text[s:e])
            if value not in candidates(col, quote):
                continue
            st = source_type(url, d)
            values[col] = value
            ev.append([d, col, quote, url, st, checked, age, TYPE_CONFIDENCE[st]])
            break
    return values, ev


def derive_row(row: dict, on: date | None = None) -> tuple[dict[str, object], list[list]]:
    """derive() for a rows-file row: markers resolve through its Sources cell."""
    sources, _ = parse_sources(str(row.get("Sources") or ""))
    fields = {f for fs in SOURCE_FIELDS.values() for f in fs}
    return derive({f: str(row.get(f) or "") for f in fields}, {f: sources for f in fields},
                  str(row.get(F_DOMAIN) or ""), str(row.get(F_CHECKED) or today())[:10], on)


def derive_facts(domain: str, facts: list[dict], on: date | None = None) -> tuple[dict[str, object], list[list]]:
    """derive() from memory.db facts rows: each cell's markers pair with its stored URL list by position."""
    cells, maps, checked = {}, {}, ""
    for f in facts:
        v = str(f["value"] or "")
        try:
            urls = [u for u in json.loads(f["sources"] or "[]") if isinstance(u, str)]
        except ValueError:
            urls = []
        nums = [int(x) for m in CITE_RE.finditer(v) for x in re.split(r"\s*[,;]\s*", m.group(1))]
        if len(urls) < len(nums):
            continue
        cells[f["field"]] = v
        maps[f["field"]] = {}
        for n, u in zip(nums, urls):
            maps[f["field"]].setdefault(n, u)
        checked = max(checked, str(f["checked"] or "")[:10])
    return derive(cells, maps, domain, checked or date.today().isoformat(), on)


def apply_explicit(row: dict, values: dict[str, object], ev: list[list]) -> tuple[dict[str, object], list[list]]:
    """A rows file may set a typed column itself (validated by check_explicit): use it, with its supporting clause."""
    for col in TYPED_COLUMNS:
        v = row.get(col)
        if v in (None, "") or values.get(col) == v:
            continue
        sup = _support(row, col, v)
        if sup:
            values[col] = v
            ev = [r for r in ev if r[1] != col] + [sup]
    return values, ev


def _support(row: dict, col: str, value) -> list | None:
    """An Evidence row for an explicit typed value: a cited clause of the row that states it."""
    sources, _ = parse_sources(str(row.get("Sources") or ""))
    d = bare_domain(str(row.get(F_DOMAIN) or ""))
    checked = str(row.get(F_CHECKED) or today())[:10]
    for field in SOURCE_FIELDS[col]:
        text = str(row.get(field) or "")
        for v, start, _ in EXTRACT[col](text):
            if v != value:
                continue
            url = cite_after(text, start, sources)
            if url:
                s, e = clause_bounds(text, start)
                st = source_type(url, d)
                return [d, col, _plain(text[s:e]), url, st, checked, age_days(checked), TYPE_CONFIDENCE[st]]
    return None


# ---- rules shared by the validator and the tests ---------------------------------------------------------------------

def type_error(col: str, value) -> str:
    """'' when value has the right type for col, else the reason."""
    if value in (None, ""):
        return ""
    if col in INT_COLUMNS:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"{col} must be a whole number (got {value!r})"
        if col == T_FOUNDED and not 1800 <= value <= date.today().year:
            return f"{col} must be a year between 1800 and this year (got {value})"
    elif col in DATE_COLUMNS:
        if not isinstance(value, str) or not ISO_DATE.match(value) or not _real_date(value):
            return f"{col} must be an ISO date YYYY, YYYY-MM or YYYY-MM-DD (got {value!r})"
    elif col == T_ROUND and value not in ROUNDS:
        return f"{col} must be one of {', '.join(ROUNDS)} (got {value!r})"
    elif col == T_PUBLIC and value not in PUBLIC_VALUES:
        return f"{col} must be Public or Private (got {value!r})"
    elif col == T_HQ_COUNTRY and not (isinstance(value, str) and re.fullmatch(r"[A-Z]{2}", value)):
        return f"{col} must be a two-letter country code (got {value!r})"
    elif not isinstance(value, str):
        return f"{col} must be text (got {value!r})"
    return ""


def check_explicit(row: dict) -> list[str]:
    """Typed values a rows file sets itself: right type, and stated by a cited clause of the same row."""
    errs = []
    for col in TYPED_COLUMNS:
        v = row.get(col)
        if v in (None, ""):
            continue
        t = type_error(col, v)
        if t:
            errs.append(t)
        elif not _support(row, col, v):
            errs.append(f"{col} = {v!r} is not stated by a cited clause of {' / '.join(SOURCE_FIELDS[col])}; "
                        "leave it blank (typed fields are filled only from cited facts)")
    return errs


def check_typed(values: dict[str, object], ev_rows: list[list]) -> list[str]:
    """Every typed value has the right type and an Evidence row whose Fact states it."""
    errs = []
    for col, v in values.items():
        if v in (None, ""):
            continue
        t = type_error(col, v)
        if t:
            errs.append(t)
            continue
        if not any(r[1] == col and str(r[3]).startswith(("http://", "https://")) and v in candidates(col, r[2])
                   for r in ev_rows):
            errs.append(f"{col} = {v!r} has no Evidence row that states it")
    return errs
