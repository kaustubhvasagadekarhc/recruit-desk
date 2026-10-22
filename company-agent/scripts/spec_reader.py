"""The one trusted interpreter for declarative reader specs (schema/readers/<name>.json).

A spec says where a public job board's list lives and how to map it to job rows (see
scripts/spec_schema.py for the rules). This module never runs anything from a spec: it formats URL
templates with a checked slug, sends GET or POST-JSON requests through netsafe.SafeSession, checks
robots.txt for every URL, waits one second between requests, caps pages, items and body size, and
maps fields with plain paths or a small CSS subset.

A spec is used by scans only while tools_registry marks it 'active' and the file's sha256 still
equals the one that passed `tools.py trial`. Editing an active spec therefore switches it off until
it is validated and trialled again.

CLI (read-only):
  uv run python scripts/spec_reader.py list                 # specs on disk and whether each is active
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netsafe  # noqa: E402
import progress  # noqa: E402
from common import ROOT, bare_domain, clean_text, user_agent  # noqa: E402
from spec_schema import MAX_ITEMS, PLACEHOLDER, SAFE_SLUG, parse_path, validate_spec  # noqa: E402

SPEC_DIR = ROOT / "schema" / "readers"
MAX_BODY = 5_000_000
DELAY = 1.0  # seconds between requests (same as the crawler's per-host rate)
TIMEOUT = 30


class SpecError(ValueError):
    """The spec is invalid, inactive, or refused for this host / slug."""


def spec_path(name: str) -> Path:
    return SPEC_DIR / f"{name}.json"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_spec(path: Path) -> dict:
    """Parse and validate a spec file; raise SpecError listing every problem."""
    try:
        spec = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SpecError(f"{path.name}: not readable JSON: {e}") from e
    errs = validate_spec(spec, path.name)
    if errs:
        raise SpecError(f"{path.name}: " + "; ".join(errs))
    return spec


def active_specs(con) -> dict[str, dict]:
    """ats name -> spec, for registry rows that are active and whose file is unchanged since the trial."""
    out: dict[str, dict] = {}
    try:
        rows = con.execute("SELECT name, ats, path, sha256 FROM tools_registry WHERE status='active'").fetchall()
    except Exception:  # an old database without the table
        return out
    for r in rows:
        p = spec_path(r["name"])
        if not p.exists() or sha256_file(p) != r["sha256"]:
            continue  # changed after its trial: not used until validated and trialled again
        try:
            spec = load_spec(p)
        except SpecError:
            continue
        out[spec["ats"]] = spec
    return out


# ---- JSON paths -----------------------------------------------------------------------------------
def get_path(obj, path: str):
    """Values at a path; '[*]' fans out. Returns a list of the values found (possibly empty)."""
    cur = [obj]
    for tok in parse_path(path):
        nxt = []
        for c in cur:
            if tok == "*":
                if isinstance(c, list):
                    nxt.extend(c)
            elif isinstance(tok, int):
                if isinstance(c, list) and -len(c) <= tok < len(c):
                    nxt.append(c[tok])
            elif isinstance(c, dict) and tok in c:
                nxt.append(c[tok])
        cur = nxt
    return cur


def _scalar_text(vals, join: str = "; ") -> str:
    out = []
    for v in vals:
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            t = clean_text(str(v))
            if t and t not in out:
                out.append(t)
    return join.join(out)


def _template(tpl: str, item, slug: str) -> str:
    def sub(m):
        ph = m.group(1)
        if ph == "slug":
            return quote(slug, safe="")
        return quote(_scalar_text(get_path(item, ph)), safe="")
    return PLACEHOLDER.sub(sub, tpl)


def json_field(item, fs, slug: str) -> str:
    if isinstance(fs, str):
        return _scalar_text(get_path(item, fs))
    if "template" in fs:
        return _template(fs["template"], item, slug)
    return _scalar_text(get_path(item, fs["path"]), fs.get("join", "; "))


# ---- A small DOM and CSS subset for HTML specs ------------------------------------------------------
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node] = []
        self.text: list[str] = []

    def all_text(self) -> str:
        parts = list(self.text)
        for c in self.children:
            parts.append(c.all_text())
        return " ".join(p for p in parts if p)

    def descendants(self):
        for c in self.children:
            yield c
            yield from c.descendants()


class _Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in self.VOID:
            self.cur = n

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.cur.text.append(data.strip())


def parse_dom(body: bytes | str) -> Node:
    t = _Tree()
    try:
        t.feed(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body)
        t.close()
    except Exception:  # malformed HTML: keep what was parsed
        pass
    return t.root


COMPOUND = re.compile(r"(?P<tag>[a-zA-Z][a-zA-Z0-9]*|\*)?(?P<id>#[\w-]+)?(?P<cls>(?:\.[\w-]+)*)(?P<attrs>(?:\[[^\]]+\])*)")
ATTR = re.compile(r"\[([\w-]+)(?:([\^*$]?=)\"([^\"]*)\")?\]")


def _parse_selector(sel: str) -> list[tuple[str, dict]]:
    """'div.jobs > a.title' -> [(' ', compound), ('>', compound)]"""
    out, comb = [], " "
    for tok in re.findall(r">|[^\s>]+", sel.strip()):
        if tok == ">":
            comb = ">"
            continue
        m = COMPOUND.fullmatch(tok)
        if not m:
            raise SpecError(f"unsupported CSS selector part {tok!r}")
        out.append((comb, {
            "tag": (m.group("tag") or "*").lower(),
            "id": (m.group("id") or "")[1:],
            "cls": [c for c in (m.group("cls") or "").split(".") if c],
            "attrs": ATTR.findall(m.group("attrs") or ""),
        }))
        comb = " "
    return out


def _match(n: Node, c: dict) -> bool:
    if c["tag"] != "*" and n.tag != c["tag"]:
        return False
    if c["id"] and n.attrs.get("id") != c["id"]:
        return False
    classes = n.attrs.get("class", "").split()
    if any(x not in classes for x in c["cls"]):
        return False
    for name, op, val in c["attrs"]:
        if name not in n.attrs:
            return False
        v = n.attrs[name]
        if (op == "=" and v != val) or (op == "^=" and not v.startswith(val)) or (op == "*=" and val not in v) or (op == "$=" and not v.endswith(val)):
            return False
    return True


def select(root: Node, sel: str) -> list[Node]:
    if not sel.strip():
        return [root]
    current = [root]
    for comb, c in _parse_selector(sel):
        nxt, seen = [], set()
        for base in current:
            pool = base.children if comb == ">" else base.descendants()
            for n in pool:
                if id(n) not in seen and _match(n, c):
                    seen.add(id(n))
                    nxt.append(n)
        current = nxt
    return current


def html_field(item: Node, fs: dict, page_url: str) -> str:
    nodes = select(item, fs.get("css") or "")
    if not nodes:
        return ""
    n, attr = nodes[0], fs.get("attr", "text")
    if attr == "text":
        return clean_text(n.all_text())
    v = n.attrs.get(attr, "")
    return urljoin(page_url, v) if attr in ("href", "src") and v else clean_text(v)


# ---- Reading -------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, spec: dict, slug: str, allowed_host: str | None):
        self.spec, self.slug = spec, slug
        self.host_rx = re.compile(spec["host_pattern"])
        self.allowed_host = allowed_host
        self.s = netsafe.SafeSession(max_body=MAX_BODY)
        self.s.headers.update({"User-Agent": user_agent()})
        self.last = 0.0
        self.requests = 0
        from crawl import Fetcher  # robots.txt rules, cached per host
        self.robots = Fetcher()

    def check_url(self, url: str) -> None:
        p = urlsplit(url)
        if p.scheme != "https" or "@" in p.netloc or p.port not in (None, 443):
            raise SpecError(f"refused {url}: https, no credentials, no port")
        host = (p.hostname or "").lower()
        if not self.host_rx.fullmatch(host):
            raise SpecError(f"refused {url}: host {host} does not match the spec's host_pattern")
        if self.allowed_host and host != self.allowed_host:
            raise SpecError(f"refused {url}: host {host} is not the discovered board host {self.allowed_host}")
        if not self.robots.allowed(url):
            raise SpecError(f"refused {url}: robots.txt disallows it")

    def send(self, method: str, url: str, params: dict | None, body):
        self.check_url(url)
        gap = DELAY - (time.monotonic() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()
        accept = "application/json" if self.spec["response"]["format"] == "json" else "text/html"
        kw = {"params": params or None, "timeout": TIMEOUT, "headers": {"Accept": accept},
              "redirect_check": lambda u: self._redirect_ok(u)}
        if method == "POST":
            kw["json"] = body
        r = netsafe.with_retries(lambda: self.s.request(method, url, **kw),
                                 on_retry=lambda why, wait: progress.sub(f"{why} from {url[:80]}; retrying in {wait:.0f}s"))
        self.requests += 1
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        if self.spec["response"]["format"] == "json":
            if "json" not in ctype:
                raise SpecError(f"{url}: expected JSON, got {ctype or 'no content type'}")
            return r.json(), r.url
        if "html" not in ctype:
            raise SpecError(f"{url}: expected HTML, got {ctype or 'no content type'}")
        return r.content, r.url

    def _redirect_ok(self, url: str) -> bool:
        try:
            self.check_url(url)
            return True
        except SpecError:
            return False


def _fill(obj, values: dict):
    """Placeholders in a JSON body or query values; a whole-string placeholder keeps its int type."""
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    if isinstance(obj, str):
        m = PLACEHOLDER.fullmatch(obj)
        if m and m.group(1) in values:
            return values[m.group(1)]
        return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), obj)
    return obj


def _set_dotted(d: dict, key: str, value) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def fetch(spec: dict, slug: str, allowed_host: str | None = None, site_domain: str | None = None) -> tuple[list[dict], dict]:
    """Read one board with a spec. Returns (rows, meta) shaped like ats.fetch's readers:
    rows carry Job Title, Location / Work Mode, Job / Post URL, Application URL, Posted Date and
    _desc; meta has read, total, complete, requests, note."""
    if not SAFE_SLUG.fullmatch(slug or "") or not re.fullmatch(spec["slug_pattern"], slug):
        raise SpecError(f"slug {slug!r} does not match the spec's slug_pattern")
    import ats  # _date and _text helpers (imported here: ats imports crawl lazily too)

    rd = _Reader(spec, slug, allowed_host)
    req, pg = spec["request"], spec.get("pagination") or {"type": "none"}
    method = req.get("method", "GET")
    fmt = spec["response"]["format"]
    size = pg.get("size", 50)
    pages = 1 if pg["type"] == "none" else pg.get("max_pages", 10)
    start = pg.get("start", 0 if pg["type"] == "offset" else 1)
    rows, total, natural_end, seen = [], None, False, set()
    site = bare_domain(site_domain) if site_domain else ""

    def job_host_ok(u: str) -> bool:
        # A job link must point at the board itself or at the company's own site.
        if not u or urlsplit(u).scheme != "https":
            return False
        d = bare_domain(u)
        return bool(rd.host_rx.fullmatch((urlsplit(u).hostname or "").lower())) or (bool(site) and (d == site or d.endswith("." + site)))

    for i in range(pages):
        values = {"slug": slug, "size": size,
                  "offset": start + i * size, "page": start + i}
        url = PLACEHOLDER.sub(lambda m: quote(str(values[m.group(1)]), safe=""), req["url"])
        params = _fill(dict(req.get("query") or {}), values)
        body = _fill(req.get("json"), values) if method == "POST" else None
        if pg["type"] != "none":
            target = body if pg.get("in") == "json" else params
            _set_dotted(target, pg["param"], values["offset"] if pg["type"] == "offset" else values["page"])
            if pg.get("size_param"):
                _set_dotted(target, pg["size_param"], size)
        data, final = rd.send(method, url, params, body)
        if fmt == "json":
            found = get_path(data, spec["response"]["items"])
            items = found[0] if len(found) == 1 and isinstance(found[0], list) else found
            if total is None and spec["response"].get("total"):
                t = get_path(data, spec["response"]["total"])
                total = t[0] if t and isinstance(t[0], int) else None
        else:
            items = select(parse_dom(data), spec["response"]["items"])
        new = 0
        for it in items:
            if not isinstance(it, (dict, Node)):
                continue
            f = spec["fields"]
            get = (lambda k: json_field(it, f[k], slug) if k in f else "") if fmt == "json" else \
                  (lambda k: html_field(it, f[k], final) if k in f else "")
            title, link = get("title"), get("url")
            if not title or not job_host_ok(link) or link in seen:
                continue
            seen.add(link)
            new += 1
            desc = get("description")
            dept = get("department")
            rows.append({
                "Job Title": clean_text(title, 200),
                "Location / Work Mode": get("location"),
                "Job / Post URL": link,
                "Application URL": get("apply_url") if job_host_ok(get("apply_url")) else link,
                "Posted Date": ats._date(get("posted_date")) if get("posted_date") else "",
                "_desc": " — ".join(x for x in (dept, ats._text(desc, ats.DESC_LEN)) if x),
            })
            if len(rows) >= MAX_ITEMS:
                break
        if len(rows) >= MAX_ITEMS:
            break
        if pg["type"] == "none" or not items or len(items) < size or new == 0 or (total is not None and len(rows) >= total):
            natural_end = True
            break
    complete = natural_end and (total is None or len(rows) >= total)
    meta = {"read": len(rows), "total": total, "complete": complete, "requests": rd.requests}
    if not natural_end:
        meta["note"] = f"stopped at the page cap ({pages} pages)"
    return rows, meta


def fetch_rows(spec: dict, slug: str, site_domain: str | None = None) -> tuple[list[dict], dict]:
    """fetch() for a scan: rows labelled like ats.fetch's (Source Type "ATS: <label>", Status Open,
    summaries without the shared company blurb). Only the spec's own board host is contacted."""
    import ats

    host = (urlsplit(board_url(spec, slug)).hostname or "").lower()
    rows, meta = fetch(spec, slug, allowed_host=host, site_domain=site_domain)
    ats.summarise(rows)
    src = f"ATS: {spec['label']}"
    for r in rows:
        r["Source Type"] = src
        r["Posted By"] = ""
        r["Status"] = "Open"
        r["_evidence"] = f"{src} public list via reader spec {spec['name']} ({slug})"
    tail = "" if meta["complete"] else f" (INCOMPLETE: {meta.get('note') or 'read fewer than the total'}; no job is closed from this read)"
    progress.sub(f"reader spec {spec['name']}:{slug}: {len(rows)} jobs{tail}")
    return rows, meta


def board_url(spec: dict, slug: str) -> str:
    return spec["board_url"].replace("{slug}", quote(slug, safe=""))


def _main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "list":
        sys.exit(__doc__)
    import memory

    con = memory.connect()
    active = {s["name"] for s in active_specs(con).values()}
    for p in sorted(SPEC_DIR.glob("*.json")):
        try:
            s = load_spec(p)
            print(f"{p.stem}: ats={s['ats']} {'ACTIVE' if p.stem in active else 'inactive'}")
        except SpecError as e:
            print(f"{p.stem}: INVALID {e}")


if __name__ == "__main__":
    _main()
