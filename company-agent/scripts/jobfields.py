"""Typed job fields: Work Mode, City, Country, Function, Seniority and salary.

Standard library only, so the hook can import it through validate_rows.

Every value comes from a field a source published, never from silence:
- Work Mode: a structured ATS field (Lever/Ashby workplaceType, Workable telecommuting, SmartRecruiters
  location.remote / hybrid, JSON-LD jobLocationType TELECOMMUTE) or the word itself in the location text
  ("Remote", "Hybrid", "On-site"). Nothing stated -> "Unknown".
- City / Country: structured address fields first, else the location text split on commas. A country is
  only written when the text names one (or its ISO code); a city is never turned into a country.
- Function: the ATS department or team field mapped onto a fixed list; a department that maps to
  nothing is "Other"; no department -> blank.
- Seniority: a level word in the job title (Intern, Junior, Senior, Staff, Lead, Manager, Director, VP,
  Chief ...); no level word -> blank.
- Salary Min / Max / Currency: only a yearly salary the posting states (Ashby compensation, JSON-LD
  baseSalary); otherwise all three stay blank.

Scripts pass the structured values in private keys: "_work_mode", "_department", "_places"
(list of [city, country]) and "_salary" ({"min", "max", "currency", "interval"}).
"""
from __future__ import annotations

import re

TYPED_COLUMNS = ["Work Mode", "City", "Country", "Function", "Seniority", "Salary Min", "Salary Max", "Currency"]

WORK_MODES = ("Remote", "Hybrid", "Onsite", "Unknown")
FUNCTIONS = ("Engineering", "Product", "Design", "Data", "Sales", "Marketing", "Customer Success",
             "Operations", "Finance", "People", "Legal", "IT", "Other")
SENIORITIES = ("Intern", "Entry", "Mid", "Senior", "Staff", "Lead", "Manager", "Director", "VP", "Exec")

_MODE_WORDS = [
    ("Hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("Remote", re.compile(r"\b(?:remote|telecommute|telecommuting|work from home|wfh)\b", re.I)),
    ("Onsite", re.compile(r"\b(?:on-?site|in-?office|in office|office-based|office based)\b", re.I)),
]


def work_mode(hint: object = None, text: str | None = None) -> str:
    """'Remote', 'Hybrid', 'Onsite' from a structured hint or from the word in the text; else 'Unknown'."""
    h = re.sub(r"[^a-z]", "", str(hint or "").lower())
    if h in ("remote", "telecommute", "fullyremote"):
        return "Remote"
    if h == "hybrid":
        return "Hybrid"
    if h in ("onsite", "inoffice", "office"):
        return "Onsite"
    t = text or ""
    found = [mode for mode, rx in _MODE_WORDS if rx.search(t)]
    if len(found) == 1:
        return found[0]
    if found:  # "Remote or Hybrid", "Remote; Pune (On-site)": more than one mode named
        return "Hybrid" if "Hybrid" in found else "Unknown"
    return "Unknown"


def mode_stated(mode: str, hint: object = None, text: str | None = None) -> bool:
    """True when `mode` is what work_mode() reads from the hint or text (used by the validator)."""
    return mode in ("", "Unknown") or work_mode(hint, text) == mode


# ---- Countries named in location text -------------------------------------------------------------
_COUNTRIES = {
    "India": ["in", "ind", "bharat"],
    "United States": ["us", "usa", "u.s.", "u.s.a.", "united states of america", "america"],
    "United Kingdom": ["uk", "gb", "gbr", "u.k.", "great britain", "england", "scotland", "wales"],
    "Canada": ["ca", "can"], "Australia": ["au", "aus"], "New Zealand": ["nz"],
    "Germany": ["de", "deu", "deutschland"], "France": ["fr", "fra"], "Netherlands": ["nl", "nld", "the netherlands", "holland"],
    "Ireland": ["ie", "irl"], "Spain": ["es", "esp"], "Portugal": ["pt", "prt"], "Italy": ["it", "ita"],
    "Poland": ["pl", "pol"], "Sweden": ["se", "swe"], "Norway": ["no", "nor"], "Denmark": ["dk", "dnk"],
    "Finland": ["fi", "fin"], "Switzerland": ["ch", "che"], "Austria": ["at", "aut"], "Belgium": ["be", "bel"],
    "Czech Republic": ["cz", "cze", "czechia"], "Romania": ["ro", "rou"], "Hungary": ["hu", "hun"],
    "Greece": ["gr", "grc"], "Ukraine": ["ua", "ukr"], "Turkey": ["tr", "tur", "türkiye", "turkiye"],
    "Israel": ["il", "isr"], "United Arab Emirates": ["ae", "are", "uae", "u.a.e."], "Saudi Arabia": ["sa", "sau", "ksa"],
    "Qatar": ["qa", "qat"], "Egypt": ["eg", "egy"], "South Africa": ["za", "zaf"], "Nigeria": ["ng", "nga"],
    "Kenya": ["ke", "ken"], "Singapore": ["sg", "sgp"], "Malaysia": ["my", "mys"], "Indonesia": ["id", "idn"],
    "Philippines": ["ph", "phl"], "Vietnam": ["vn", "vnm", "viet nam"], "Thailand": ["th", "tha"],
    "Japan": ["jp", "jpn"], "South Korea": ["kr", "kor", "korea", "republic of korea"], "China": ["cn", "chn"],
    "Hong Kong": ["hk", "hkg"], "Taiwan": ["tw", "twn"], "Sri Lanka": ["lk", "lka"], "Bangladesh": ["bd", "bgd"],
    "Pakistan": ["pk", "pak"], "Nepal": ["np", "npl"], "Brazil": ["br", "bra", "brasil"], "Mexico": ["mx", "mex"],
    "Argentina": ["ar", "arg"], "Chile": ["cl", "chl"], "Colombia": ["co", "col"], "Peru": ["pe", "per"],
    "Costa Rica": ["cr", "cri"], "Uruguay": ["uy", "ury"], "Estonia": ["ee", "est"], "Lithuania": ["lt", "ltu"],
    "Latvia": ["lv", "lva"], "Bulgaria": ["bg", "bgr"], "Serbia": ["rs", "srb"], "Croatia": ["hr", "hrv"],
    "Luxembourg": ["lu", "lux"], "Cyprus": ["cy", "cyp"], "Malta": ["mt", "mlt"],
}
COUNTRY_OF = {name.lower(): name for name in _COUNTRIES}
COUNTRY_OF.update({alias: name for name, aliases in _COUNTRIES.items() for alias in aliases})
# Two-letter codes that are also US state codes (CA, IN, DE ...) only count as a country on their own
# or as the last part after a state: "San Francisco, CA" names no country; "Pune, IN" names India.
_STATE_CODES = {"al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
                "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd",
                "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"}
# Parts of a location text that are not a place.
_NOT_PLACE = re.compile(
    r"^(?:remote|hybrid|on-?site|in-?office|anywhere|worldwide|global|multiple locations?|\d+ locations?|various|"
    r"flexible|wfh|work from home|full[- ]time|part[- ]time|contract|contractor|internship|temporary|permanent|"
    r"emea|apac|amer|americas|latam|europe|asia|asia pacific|north america|south america|middle east|africa|"
    r"eu|remote \w+|[\w ]+ \(remote\)|to be decided|tbd|n/?a|unknown)$", re.I)
_EMPLOYMENT = re.compile(r"^(?:full[- ]time|part[- ]time|contract(?:or)?|intern(?:ship)?|temporary|permanent|fixed[- ]term|freelance)$", re.I)


def country_name(text: str | None, allow_state_codes: bool = True) -> str:
    """Canonical country name for a name or ISO code, '' when the text is not a known country."""
    t = re.sub(r"\s+", " ", (text or "").strip().strip(".").lower())
    if not t:
        return ""
    if not allow_state_codes and t in _STATE_CODES:
        return ""
    return COUNTRY_OF.get(t, "")


def _clean_place(p: str) -> str:
    p = re.sub(r"\((?:remote|hybrid|on-?site|in-?office)\)", "", p, flags=re.I)
    p = re.sub(r"^(?:remote|hybrid|on-?site)\s*[-–:]\s*", "", p, flags=re.I)
    p = re.sub(r"\s*[-–:]\s*(?:remote|hybrid|on-?site)$", "", p, flags=re.I)
    return re.sub(r"\s+", " ", p).strip(" ,-–")


def places_from_text(text: str | None) -> list[tuple[str, str]]:
    """'Pune, India · Full-time' -> [('Pune', 'India')]; 'San Francisco, CA' -> [('San Francisco', '')];
    'Remote' -> []. Several locations split on ';', '|' or ' / '."""
    first = re.split(r"\s+·\s+", text or "")[0]
    out: list[tuple[str, str]] = []
    for loc in re.split(r"\s*(?:;|\||\s/\s|\bor\b)\s*", first):
        loc = _clean_place(loc)
        if not loc or _NOT_PLACE.match(loc) or _EMPLOYMENT.match(loc):
            continue
        parts = [x for x in (_clean_place(p) for p in loc.split(",")) if x and not _NOT_PLACE.match(x)]
        if not parts:
            continue
        if len(parts) == 1:
            c = country_name(parts[0], allow_state_codes=len(parts[0]) > 2)
            out.append(("", c) if c else (parts[0], ""))
            continue
        last = parts[-1]
        # "Austin, TX" (a state code last) names no country; "Pune, Maharashtra, IN" does.
        c = country_name(last, allow_state_codes=len(parts) > 2 or len(last) > 2)
        if not c and len(parts) == 2 and len(last) == 2 and last.lower() not in _STATE_CODES:
            c = country_name(last)
        city = parts[0] if not country_name(parts[0], allow_state_codes=False) else ""
        out.append((city, c))
    return [p for p in dict.fromkeys(out) if any(p)]


def _join(values) -> str:
    return "; ".join(dict.fromkeys(v for v in values if v))


def city_country(places: object = None, text: str | None = None) -> tuple[str, str]:
    """(City, Country) cells. Structured places first, else the location text. Several distinct
    values are joined with '; ' so one cell never claims a single place for a multi-city role."""
    got: list[tuple[str, str]] = []
    if isinstance(places, (list, tuple)):
        for p in places:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                city = re.sub(r"\s+", " ", str(p[0] or "")).strip()
                ctry = country_name(str(p[1] or "")) or ""
                if city and _NOT_PLACE.match(city):
                    city = ""
                if city or ctry:
                    got.append((city, ctry))
    if not got:
        got = places_from_text(text)
    return _join(c for c, _ in got), _join(k for _, k in got)


# ---- Function from an ATS department --------------------------------------------------------------
_FUNCTION_RULES = [
    ("People", r"learning (?:and|&) development|\bl ?& ?d\b"),  # before Engineering's "development"
    ("Data", r"\bdata\b|analytics|\bbi\b|business intelligence|machine learning|\bml\b|\bai\b|data science|insights"),
    ("Design", r"\bdesign|\bux\b|\bui\b|user experience|creative|brand design"),
    ("Sales", r"\bsales\b|business development|\bbd\b|\bbdr\b|\bsdr\b|revenue|account executive|partnerships|channel|\bgtm\b|go-to-market"),
    ("Engineering", r"engineer|software|\btech\b|technology|development|\bdev\b|devops|\bsre\b|infrastructure|"
                    r"platform|security|\bqa\b|quality assurance|testing|r ?& ?d|research and development|mobile|"
                    r"frontend|front-end|backend|back-end|cloud"),
    ("Product", r"\bproduct\b"),
    ("Marketing", r"marketing|growth|communications|\bpr\b|public relations|content|\bseo\b|brand"),
    ("Customer Success", r"customer|account management|\bsupport\b|success|client services|service delivery|implementation|professional services"),
    ("Finance", r"financ|accounting|\btax\b|treasury|\baudit"),
    ("People", r"\bpeople\b|\bhr\b|human resources|talent|recruit|people ops"),
    ("Legal", r"legal|compliance|\brisk\b|regulatory|privacy"),
    ("IT", r"^it$|\bit\b|information technology|internal systems|helpdesk|help desk"),
    ("Operations", r"operations|\bops\b|supply|logistics|procurement|facilities|admin|administration|business operations"),
]
_FUNCTION_RX = [(f, re.compile(rx, re.I)) for f, rx in _FUNCTION_RULES]


def function_of(department: object) -> str:
    """Function for an ATS department / team name; 'Other' when it maps to nothing; '' when none given."""
    if isinstance(department, (list, tuple)):
        found = [function_of(d) for d in department if d]
        found = [f for f in found if f]
        return next((f for f in found if f != "Other"), found[0] if found else "")
    d = re.sub(r"\s+", " ", str(department or "")).strip()
    if not d:
        return ""
    return next((f for f, rx in _FUNCTION_RX if rx.search(d)), "Other")


# ---- Seniority from a level word in the title -----------------------------------------------------
# Titles where "Manager" names the role, not a people manager.
_IC_MANAGER = re.compile(r"\b(?:product|program|programme|project|account|relationship|customer success|success|partner|"
                         r"territory|key account|community|social media|content|case|category|release|delivery|scrum)\s+manager\b", re.I)
_SENIORITY_RULES = [
    ("Exec", r"\bchief\b|\bceo\b|\bcto\b|\bcfo\b|\bcoo\b|\bcpo\b|\bcmo\b|\bciso\b|\bcro\b|"
             r"(?<!vice )\bpresident\b|\bco-?founder\b|\bfounder\b|\bgeneral manager\b|\bmanaging director\b"),
    ("VP", r"\bvp\b|\bsvp\b|\bevp\b|\bavp\b|vice president"),
    ("Director", r"\bdirector\b|\bhead of\b|\bhead,|\b\w+ head\b"),
    ("Manager", r"\bmanager\b|\bmgr\b|\bmanaging\b"),
    ("Lead", r"\blead\b|\bleader\b"),
    ("Staff", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b"),
    ("Senior", r"\bsenior\b|\bsr\b\.?|\bsnr\b"),
    ("Mid", r"\bmid[- ]?level\b|\bintermediate\b"),
    ("Entry", r"\bjunior\b|\bjr\b\.?|\bentry[- ]level\b|\bgraduate\b|\bfresher\b|\bnew grad\b|\btrainee\b|\bapprentice\b"),
    ("Intern", r"\bintern\b|\binternship\b|\bco-?op\b|\bsummer student\b"),
]
_SENIORITY_RX = [(s, re.compile(rx, re.I)) for s, rx in _SENIORITY_RULES]


def seniority_of(title: str | None) -> str:
    """Level named in the title, '' when it names none. Intern wins over everything (an
    'Engineering Manager Intern' is an intern); otherwise the highest level named counts."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if not t:
        return ""
    if re.search(r"\bintern\b|\binternship\b", t, re.I):
        return "Intern"
    for level, rx in _SENIORITY_RX:
        if level == "Manager":
            stripped = _IC_MANAGER.sub(" ", t)
            if rx.search(stripped):
                return level
            continue
        if rx.search(t):
            return level
    return ""


# ---- Salary stated by the posting ------------------------------------------------------------------
_YEARLY = re.compile(r"^(?:1\s*)?(?:year|yearly|annual|annually|per year|p\.?a\.?|yr)$", re.I)
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,\s]", "", str(v or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def salary(hint: object) -> tuple[object, object, str]:
    """(min, max, currency) of a stated yearly salary; ('', '', '') when there is none.
    A single stated value fills both min and max."""
    if not isinstance(hint, dict):
        return "", "", ""
    cur = str(hint.get("currency") or "").strip().upper()
    if not _CURRENCY.match(cur) or not _YEARLY.match(str(hint.get("interval") or "").strip()):
        return "", "", ""
    lo, hi = _num(hint.get("min")), _num(hint.get("max"))
    lo, hi = (lo if lo is not None else hi), (hi if hi is not None else lo)
    if lo is None or lo <= 0 or hi < lo:
        return "", "", ""
    as_int = lambda x: int(x) if float(x).is_integer() else round(x, 2)  # noqa: E731
    return as_int(lo), as_int(hi), cur


def fill_typed(row: dict) -> dict:
    """Set the typed columns on a row in place from its private hints and its own cells."""
    loc = str(row.get("Location / Work Mode") or "")
    row["Work Mode"] = work_mode(row.get("_work_mode"), loc)
    row["City"], row["Country"] = city_country(row.get("_places"), loc)
    row["Function"] = function_of(row.get("_department"))
    row["Seniority"] = seniority_of(row.get("Job Title"))
    row["Salary Min"], row["Salary Max"], row["Currency"] = salary(row.get("_salary"))
    return row


def validate_typed(row: dict) -> list[str]:
    """Errors for typed cells that are not on their list or not backed by the row's own fields."""
    errs = []
    mode = str(row.get("Work Mode") or "")
    if mode and mode not in WORK_MODES:
        errs.append(f"'Work Mode' must be one of {list(WORK_MODES)}, got {mode[:30]}")
    elif mode and not mode_stated(mode, row.get("_work_mode"), str(row.get("Location / Work Mode") or "")):
        errs.append(f"'Work Mode' {mode} is not stated by the location text or a structured field; use Unknown")
    fn = str(row.get("Function") or "")
    if fn and fn not in FUNCTIONS:
        errs.append(f"'Function' must be one of {list(FUNCTIONS)}, got {fn[:30]}")
    sen = str(row.get("Seniority") or "")
    if sen and sen not in SENIORITIES:
        errs.append(f"'Seniority' must be one of {list(SENIORITIES)}, got {sen[:30]}")
    elif sen and sen != seniority_of(row.get("Job Title")):
        errs.append(f"'Seniority' {sen} is not the level the title names; leave it blank")
    lo, hi, cur = row.get("Salary Min"), row.get("Salary Max"), str(row.get("Currency") or "")
    if any(v not in (None, "") for v in (lo, hi, cur)):
        if _num(lo) is None or _num(hi) is None or not _CURRENCY.match(cur):
            errs.append("'Salary Min', 'Salary Max' and 'Currency' (ISO code) go together, or all stay blank")
        elif _num(lo) > _num(hi):
            errs.append("'Salary Min' is above 'Salary Max'")
    return errs
