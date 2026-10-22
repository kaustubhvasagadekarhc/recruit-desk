"""Strict schema checks for declarative reader specs (schema/readers/<name>.json) and the ATS
fingerprint registry (schema/ats_fingerprints.json). Standard library only, so the PreToolUse hook
can import it and refuse a bad spec before it is even written.

A reader spec is data, never code: request URL templates, pagination, and paths that map a JSON
answer (or CSS selectors that map an HTML page) to job fields. scripts/spec_reader.py is the one
trusted interpreter. The rules here keep a spec from reaching anything but the public job board it
was written for:

- https only; no user:password@, no port, no file:, data:, ftp: or plain http URLs;
- every request host must fully match the spec's host_pattern, which must be anchored and end in a
  literal registrable domain (e.g. `\\.keka\\.com$`), and must not match shared hosting suffixes;
- GET, or POST with a JSON body only; no headers, cookies, auth or anything that looks like a key,
  token, password or session, in keys, query parameters or values;
- placeholders: {slug} (checked against slug_pattern and a safe charset), {offset}/{page}/{size}
  for pagination, and item paths in field templates only;
- caps: at most 50 pages, 200 items a page, 5000 items in all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
SAFE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")  # no dots: a slug can never add a host label
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&-]{1,39}$")
PATH_RE = re.compile(r"^(?:\$|[A-Za-z_][\w-]*(?:\[(?:\d+|\*)\])*(?:\.[A-Za-z_][\w-]*(?:\[(?:\d+|\*)\])*)*)$")
PATH_TOKEN = re.compile(r"\.?([A-Za-z_][\w-]*)|\[(\d+|\*)\]")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
PAGE_VARS = {"offset", "page", "size"}

MAX_PAGES = 50
MAX_PAGE_SIZE = 200
MAX_ITEMS = 5000

TOP_KEYS = {"name", "ats", "label", "version", "description", "host_pattern", "slug_pattern", "board_url",
            "request", "pagination", "response", "fields", "provenance"}
REQUIRED_TOP = {"name", "ats", "label", "host_pattern", "slug_pattern", "board_url", "request", "response", "fields"}
FIELD_KEYS = {"title", "url", "location", "posted_date", "department", "description", "apply_url"}
REQUIRED_FIELDS = {"title", "url"}

# Anything that smells of a credential, in a key or a query parameter name.
SECRET_KEY = re.compile(r"(auth|token|secret|passw|cookie|api[_-]?key|apikey|session|bearer|signature|credential|private|x-api)", re.I)
SECRET_VALUE = re.compile(r"(?i)(\bbearer\s+\S|\bbasic\s+[A-Za-z0-9+/=]{8,}|://[^/\s]*@|\bsk-[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{10,}\.)")
# Suffixes where anyone can host anything: a host_pattern must not cover them.
SHARED_PROBES = ["x.com", "x.co.in", "x.co.uk", "x.github.io", "x.herokuapp.com", "x.vercel.app", "x.netlify.app",
                 "x.cloudfront.net", "x.s3.amazonaws.com", "x.appspot.com", "x.blogspot.com", "x.azurewebsites.net",
                 "x.pages.dev", "x.workers.dev", "x.firebaseapp.com", "x.web.app", "localhost", "127.0.0.1",
                 "169.254.169.254", "x.ngrok.io", "x.ngrok-free.app", "example.com"]

# CSS subset for HTML specs: compound selectors joined by descendant (space) or child (>).
CSS_COMPOUND = r"(?:[a-zA-Z][a-zA-Z0-9]*|\*)?(?:#[\w-]+)?(?:\.[\w-]+)*(?:\[[\w-]+(?:[\^*$]?=\"[^\"]{0,80}\")?\])*"
CSS_RE = re.compile(rf"^\s*{CSS_COMPOUND}(?:\s*>\s*{CSS_COMPOUND}|\s+{CSS_COMPOUND})*\s*$")


def parse_path(path: str) -> list:
    """'a.b[0].c[*]' -> ['a', 'b', 0, 'c', '*'];  '$' -> []."""
    if path == "$":
        return []
    out: list = []
    for m in PATH_TOKEN.finditer(path):
        if m.group(1) is not None:
            out.append(m.group(1))
        else:
            out.append("*" if m.group(2) == "*" else int(m.group(2)))
    return out


def _walk_strings(obj, where: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{where}.{k}", str(k), True
            yield from _walk_strings(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        yield where, obj, False


def check_host_pattern(pat: str) -> list[str]:
    errs = []
    if not isinstance(pat, str) or not pat.startswith("^") or not pat.endswith("$") or len(pat) > 200:
        return ["host_pattern must be an anchored regex ^...$ of at most 200 characters"]
    try:
        rx = re.compile(pat)
    except re.error as e:
        return [f"host_pattern does not compile: {e}"]
    # It must end in a literal registrable domain such as \.keka\.com$ (two literal labels at least).
    # (a final group of literal TLDs such as \.zohorecruit\.(?:com|in|eu)$ is accepted too)
    m = re.search(r"[a-z0-9-]{2,}\\\.(?:[a-z0-9-]+(?:\\\.[a-z0-9-]+)*|\(\?:[a-z0-9|\\.]+\))\$$", pat)
    if not m:
        errs.append("host_pattern must end in a literal domain, e.g. ^[a-z0-9-]+\\.keka\\.com$")
    if re.match(r"\^\(?\??:?\.[*+]", pat) or ".*" in pat or ".+" in pat:
        errs.append("host_pattern may not use .* or .+ (use [a-z0-9-]+ for one label)")
    hits = [p for p in SHARED_PROBES if rx.fullmatch(p)]
    if hits:
        errs.append(f"host_pattern is too broad: it matches {', '.join(hits[:3])}")
    return errs


def _check_url_template(tpl, where: str, host_rx, extra_vars: set[str]) -> list[str]:
    if not isinstance(tpl, str) or len(tpl) > 400:
        return [f"{where}: must be a string of at most 400 characters"]
    errs = []
    if not tpl.startswith("https://"):
        errs.append(f"{where}: only https:// URLs are allowed (got {tpl[:40]!r})")
    for ph in PLACEHOLDER.findall(tpl):
        if ph != "slug" and ph not in extra_vars:
            errs.append(f"{where}: unknown placeholder {{{ph}}} (allowed: slug{', ' + ', '.join(sorted(extra_vars)) if extra_vars else ''})")
    sample = PLACEHOLDER.sub(lambda m: "examplecorp" if m.group(1) == "slug" else "1", tpl)
    p = urlsplit(sample)
    if p.scheme != "https":
        errs.append(f"{where}: scheme must be https")
    if "@" in p.netloc or p.port not in (None, 443):
        errs.append(f"{where}: no user:password@ and no port in the host")
    if host_rx is not None and p.hostname and not host_rx.fullmatch(p.hostname):
        errs.append(f"{where}: host {p.hostname} (with slug 'examplecorp') does not match host_pattern")
    hostpart = tpl.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0] if "://" in tpl else ""
    # A placeholder in the host is allowed only as {slug}, and only as the whole first label.
    if "{" in hostpart and not re.fullmatch(r"\{slug\}(?:\.[a-z0-9-]+)+", hostpart):
        errs.append(f"{where}: only {{slug}} may appear in the host, as its first label ({{slug}}.vendor.com)")
    return errs


def _check_field(name: str, spec, fmt: str) -> list[str]:
    where = f"fields.{name}"
    if fmt == "json":
        if isinstance(spec, str):
            return [] if PATH_RE.fullmatch(spec) and len(spec) <= 120 else [f"{where}: bad path {spec!r} (use a.b[0].c or a[*].name)"]
        if not isinstance(spec, dict):
            return [f"{where}: a path string or an object"]
        if set(spec) - {"path", "join", "template"}:
            return [f"{where}: unknown keys {sorted(set(spec) - {'path', 'join', 'template'})}"]
        if ("path" in spec) == ("template" in spec):
            return [f"{where}: give exactly one of path or template"]
        if "path" in spec and not (isinstance(spec["path"], str) and PATH_RE.fullmatch(spec["path"])):
            return [f"{where}: bad path {spec.get('path')!r}"]
        if "join" in spec and not (isinstance(spec["join"], str) and len(spec["join"]) <= 5):
            return [f"{where}: join must be a short string"]
        if "template" in spec:
            tpl = spec["template"]
            if not isinstance(tpl, str) or len(tpl) > 400:
                return [f"{where}: template must be a string"]
            for ph in PLACEHOLDER.findall(tpl):
                if ph != "slug" and not PATH_RE.fullmatch(ph):
                    return [f"{where}: template placeholder {{{ph}}} is not slug or an item path"]
            if name in ("url", "apply_url") and not tpl.startswith("https://"):
                return [f"{where}: URL templates must start with https://"]
        return []
    # html
    if not isinstance(spec, dict) or set(spec) - {"css", "attr"} or "css" not in spec:
        return [f"{where}: an object {{\"css\": \"<selector>\", \"attr\": \"text|href|<attribute>\"}}"]
    if spec["css"] and not (isinstance(spec["css"], str) and len(spec["css"]) <= 120 and CSS_RE.fullmatch(spec["css"])):
        return [f"{where}: unsupported CSS selector {spec['css']!r} (tag, #id, .class, [attr], [attr=\"v\"], descendant and >)"]
    attr = spec.get("attr", "text")
    if not (isinstance(attr, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", attr)):
        return [f"{where}: attr must be text, href or an attribute name"]
    return []


def validate_spec(spec, file_name: str | None = None) -> list[str]:
    """Every problem found, or [] when the spec is acceptable."""
    if not isinstance(spec, dict):
        return ["a spec is a JSON object"]
    errs: list[str] = []
    unknown = set(spec) - TOP_KEYS
    if unknown:
        errs.append(f"unknown top-level keys {sorted(unknown)} (allowed: {sorted(TOP_KEYS)})")
    missing = REQUIRED_TOP - set(spec)
    if missing:
        errs.append(f"missing keys {sorted(missing)}")
        return errs
    # Credentials: no key or value anywhere may look like one.
    for where, s, is_key in _walk_strings(spec, "spec"):
        if is_key and SECRET_KEY.search(s) and not where.startswith("spec.provenance"):
            errs.append(f"{where}: keys that look like credentials (auth, token, cookie, session, api key ...) are not allowed")
        if not is_key and SECRET_VALUE.search(s):
            errs.append(f"{where}: value looks like a credential or user:password@ URL")
        if not is_key and re.match(r"(?i)\s*(file|data|ftp|javascript|gopher|http):", s):
            errs.append(f"{where}: only https URLs are allowed")
    if not (isinstance(spec["name"], str) and NAME_RE.fullmatch(spec["name"])):
        errs.append("name: lower-case letters, digits, - or _, 2-40 characters")
    elif file_name and Path(file_name).stem != spec["name"]:
        errs.append(f"name {spec['name']!r} must equal the file name ({Path(file_name).stem!r})")
    if not (isinstance(spec["ats"], str) and NAME_RE.fullmatch(spec["ats"])):
        errs.append("ats: lower-case letters, digits, - or _")
    if not (isinstance(spec["label"], str) and LABEL_RE.fullmatch(spec["label"])):
        errs.append("label: 2-40 characters, letters, digits, space . & -")
    errs += check_host_pattern(spec["host_pattern"])
    host_rx = None
    try:
        host_rx = re.compile(spec["host_pattern"])
    except (re.error, TypeError):
        pass
    sp = spec["slug_pattern"]
    if not (isinstance(sp, str) and sp.startswith("^") and sp.endswith("$") and len(sp) <= 100):
        errs.append("slug_pattern must be an anchored regex ^...$")
    else:
        try:
            re.compile(sp)
        except re.error as e:
            errs.append(f"slug_pattern does not compile: {e}")
    errs += _check_url_template(spec["board_url"], "board_url", host_rx, set())

    req = spec["request"]
    if not isinstance(req, dict) or set(req) - {"method", "url", "query", "json"}:
        errs.append("request: an object with method, url and optional query / json (no headers, cookies or auth)")
        req = {}
    method = req.get("method", "GET")
    if method not in ("GET", "POST"):
        errs.append("request.method: GET or POST only")
    pg = spec.get("pagination") or {"type": "none"}
    page_vars = set()
    if not isinstance(pg, dict) or pg.get("type") not in ("none", "offset", "page"):
        errs.append("pagination.type: none, offset or page")
        pg = {"type": "none"}
    elif pg["type"] != "none":
        allowed = {"type", "param", "start", "size", "size_param", "in", "max_pages"}
        if set(pg) - allowed:
            errs.append(f"pagination: unknown keys {sorted(set(pg) - allowed)}")
        if not (isinstance(pg.get("param"), str) and re.fullmatch(r"[A-Za-z_][\w.-]{0,40}", pg["param"])):
            errs.append("pagination.param: the name of the offset / page parameter")
        if pg.get("size_param") is not None and not (isinstance(pg["size_param"], str) and re.fullmatch(r"[A-Za-z_][\w.-]{0,40}", pg["size_param"])):
            errs.append("pagination.size_param: a parameter name")
        size = pg.get("size", 50)
        if not (isinstance(size, int) and 1 <= size <= MAX_PAGE_SIZE):
            errs.append(f"pagination.size: 1..{MAX_PAGE_SIZE}")
        mp = pg.get("max_pages", 10)
        if not (isinstance(mp, int) and 1 <= mp <= MAX_PAGES):
            errs.append(f"pagination.max_pages: 1..{MAX_PAGES}")
        if pg.get("in", "query") not in ("query", "json"):
            errs.append("pagination.in: query or json")
        if pg.get("in") == "json" and method != "POST":
            errs.append("pagination.in json needs a POST request")
        if not isinstance(pg.get("start", 0), int):
            errs.append("pagination.start: an integer")
        page_vars = PAGE_VARS
    errs += _check_url_template(req.get("url"), "request.url", host_rx, page_vars)
    q = req.get("query") or {}
    if not isinstance(q, dict) or len(q) > 20:
        errs.append("request.query: an object of at most 20 parameters")
    else:
        for k, v in q.items():
            if not re.fullmatch(r"[A-Za-z_][\w.\[\]-]{0,40}", str(k)):
                errs.append(f"request.query: bad parameter name {k!r}")
            if not isinstance(v, (str, int, bool)) or (isinstance(v, str) and (len(v) > 200 or any(ph not in page_vars | {"slug"} for ph in PLACEHOLDER.findall(v)))):
                errs.append(f"request.query.{k}: a short string or number (placeholders: slug, offset, page, size)")
    if method == "POST":
        if not isinstance(req.get("json"), (dict, list)):
            errs.append("request.json: a POST must send a JSON object (form, file and raw bodies are not allowed)")
        elif len(json.dumps(req["json"])) > 4000:
            errs.append("request.json: at most 4000 characters")
    elif "json" in req:
        errs.append("request.json: only a POST sends a body")

    resp = spec["response"]
    fmt = resp.get("format") if isinstance(resp, dict) else None
    if fmt not in ("json", "html") or set(resp) - {"format", "items", "total"}:
        errs.append("response: {\"format\": \"json\"|\"html\", \"items\": <path or CSS>, \"total\": <path, optional>}")
        fmt = "json"
    else:
        items = resp.get("items")
        if fmt == "json" and not (isinstance(items, str) and PATH_RE.fullmatch(items)):
            errs.append("response.items: a JSON path to the list of jobs ($ for a top-level list)")
        if fmt == "html" and not (isinstance(items, str) and items and CSS_RE.fullmatch(items)):
            errs.append("response.items: a CSS selector for one job card")
        if "total" in resp and not (fmt == "json" and isinstance(resp["total"], str) and PATH_RE.fullmatch(resp["total"])):
            errs.append("response.total: a JSON path (json format only)")
        if fmt == "html" and method != "GET":
            errs.append("an html reader reads pages with GET only")

    fields = spec["fields"]
    if not isinstance(fields, dict):
        errs.append("fields: an object")
    else:
        if set(fields) - FIELD_KEYS:
            errs.append(f"fields: unknown {sorted(set(fields) - FIELD_KEYS)} (allowed: {sorted(FIELD_KEYS)})")
        if REQUIRED_FIELDS - set(fields):
            errs.append(f"fields: {sorted(REQUIRED_FIELDS - set(fields))} required")
        for k, v in fields.items():
            if k in FIELD_KEYS:
                errs += _check_field(k, v, fmt)
    prov = spec.get("provenance", {})
    if not isinstance(prov, dict) or len(json.dumps(prov)) > 2000:
        errs.append("provenance: a small object (who, when, blocker id, evidence URL)")
    if len(json.dumps(spec)) > 20000:
        errs.append("a spec is at most 20 KB")
    return errs


# ---- Fingerprint registry ------------------------------------------------------------------------
FP_KEYS = {"ats", "label", "host", "slug", "builtin", "added_by", "added", "note"}


def validate_fingerprints(doc) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("fingerprints"), list):
        return ["the registry is {\"version\": 1, \"fingerprints\": [...]}"]
    if set(doc) - {"version", "about", "fingerprints"}:
        return [f"unknown top-level keys {sorted(set(doc) - {'version', 'about', 'fingerprints'})}"]
    errs, seen = [], set()
    if len(doc["fingerprints"]) > 300:
        errs.append("at most 300 fingerprints")
    for i, fp in enumerate(doc["fingerprints"]):
        w = f"fingerprints[{i}]"
        if not isinstance(fp, dict) or set(fp) - FP_KEYS or not {"ats", "host"} <= set(fp):
            errs.append(f"{w}: an object with ats, host and optional label, slug, builtin, added_by, added, note")
            continue
        if not NAME_RE.fullmatch(str(fp["ats"])):
            errs.append(f"{w}.ats: lower-case name")
        if fp["ats"] in seen:
            errs.append(f"{w}.ats: {fp['ats']} listed twice")
        seen.add(fp["ats"])
        errs += [f"{w}.{e}" for e in check_host_pattern(fp["host"])]
        if "slug" in fp:
            s = fp["slug"]
            try:
                ok = isinstance(s, str) and s.startswith("^https?://") and len(s) <= 200 and "slug" in re.compile(s).groupindex
            except re.error:
                ok = False
            if not ok:
                errs.append(f"{w}.slug: an anchored URL regex ^https?://... with a (?P<slug>...) group")
        if "builtin" in fp and fp["builtin"] is not True:
            errs.append(f"{w}.builtin: true or absent")
    return errs


if __name__ == "__main__":
    import sys

    bad = 0
    for arg in sys.argv[1:]:
        doc = json.loads(Path(arg).read_text())
        errs = validate_fingerprints(doc) if "fingerprints" in doc else validate_spec(doc, arg)
        print(f"{arg}: " + ("ok" if not errs else "\n  " + "\n  ".join(errs)))
        bad += bool(errs)
    sys.exit(1 if bad else 0)
