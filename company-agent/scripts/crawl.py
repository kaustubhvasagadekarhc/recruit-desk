"""Polite same-domain crawler: robots.txt obeyed, sitemap first, page cap, rate limit.

It finds the careers page, links to ATS boards, and job pages on the company's own site
(JSON-LD JobPosting first, then careers child pages).

CLI: uv run python scripts/crawl.py --domain humancloud.dev [--max-pages 150] [--out runs/x.crawl.json]
"""
from __future__ import annotations

import argparse
import heapq
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.robotparser
import zlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blockers  # noqa: E402
import netsafe  # noqa: E402
import progress  # noqa: E402
from common import bare_domain, clean_text, looks_like_job, normalize_url, user_agent  # noqa: E402

MAX_BYTES = 2_000_000
MIN_DELAY = 1.0  # seconds between requests to one host, raised by robots Crawl-delay
MAX_DELAY = 10.0
MAX_SITEMAP_FILES = 20
MAX_SITEMAP_URLS = 5000

CAREER_WORDS = re.compile(
    r"(?:^|[/_.-])(careers?|jobs?|join-?us|work-?with-?us|opportunit(?:y|ies)|openings?|vacanc(?:y|ies)|hiring|positions?|life-?at)(?:$|[/_.-])",
    re.I,
)
JOB_DETAIL_HINT = re.compile(r"/(careers?|jobs?|openings?|positions?|vacanc(?:y|ies)|opportunit(?:y|ies))/[^/?#]+", re.I)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|ico|css|js|json|xml|zip|gz|mp4|mp3|woff2?|ttf|docx?|xlsx?|pptx?)$", re.I)
GENERIC_H1 = re.compile(r"^(careers?|jobs?|open (positions|roles)|current openings|join (us|our team)|work with us)$", re.I)
LOCATION = re.compile(
    r"\b(remote|hybrid|on-?site|in-?office|india|usa|united states|united kingdom|uk|canada|germany|singapore|uae|"
    r"bengaluru|bangalore|pune|mumbai|hyderabad|chennai|delhi|new delhi|noida|gurgaon|gurugram|kolkata|ahmedabad|"
    r"kochi|coimbatore|indore|jaipur|chandigarh|nagpur|trivandrum|thiruvananthapuram|mysore|mysuru|london|"
    r"new york|san francisco|mountain view|seattle|austin|toronto|dubai)\b|,\s*[A-Z]{2}\b",
    re.I,
)
EMPLOYMENT = re.compile(r"^(full[- ]?time|part[- ]?time|internship|contract|temporary|freelance|permanent)$", re.I)


class Page(HTMLParser):
    """Collects title, first h1, meta description, links, script/iframe sources, text lines, JSON-LD."""

    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "td", "section", "article", "span", "a"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.meta_description = ""
        self.links: list[tuple[str, str]] = []
        self.link_pos: list[int] = []  # len(self.lines) when each link closed: text before it is its "card"
        self.embeds: list[str] = []
        self.lines: list[str] = []
        self.jsonld: list[str] = []
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._a_href: str | None = None
        self._a_text: list[str] = []
        self._in_h1 = False
        self._h1_buf: list[str] = []
        self._ld = False
        self._ld_buf: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
                self._ld = True
                self._ld_buf = []
            else:
                self._skip += 1
            if tag == "script" and a.get("src"):
                self.embeds.append(a["src"])
            return
        if tag == "iframe" and a.get("src"):
            self.embeds.append(a["src"])
        if tag == "title":
            self._in_title = True
        if tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta_description = a.get("content") or ""
        if tag == "h1" and not self.h1:
            self._in_h1 = True
            self._h1_buf = []
        if tag == "a" and a.get("href"):
            self._a_href = a["href"]
            self._a_text = []
        if tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            if self._ld and tag == "script":
                self.jsonld.append("".join(self._ld_buf))
                self._ld = False
            elif self._skip:
                self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == "h1" and self._in_h1:
            self.h1 = clean_text("".join(self._h1_buf))
            self._in_h1 = False
        if tag == "a" and self._a_href is not None:
            self._flush()
            self.links.append((self._a_href, clean_text("".join(self._a_text))))
            self.link_pos.append(len(self.lines))
            self._a_href = None
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._ld:
            self._ld_buf.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data
        if self._in_h1:
            self._h1_buf.append(data)
        if self._a_href is not None:
            self._a_text.append(data)
        self._buf.append(data)

    def _flush(self):
        t = clean_text("".join(self._buf))
        if t:
            self.lines.append(t)
        self._buf = []

    def close(self):
        super().close()
        self._flush()
        self.title = clean_text(self.title)


def robots_allows(rp: urllib.robotparser.RobotFileParser, agent: str, url: str) -> bool:
    """RFC 9309 matching: the longest matching rule of the group for this agent wins, and Allow wins
    a tie. (urllib.robotparser applies the FIRST matching rule, so "Disallow: /" followed by
    "Allow: /careers" would wrongly refuse /careers/..., which the site explicitly allows.)"""
    if not rp.last_checked:  # robots.txt not read yet
        return False
    entry = next((e for e in rp.entries if e.applies_to(agent)), None) or rp.default_entry
    if entry is None:
        return True
    p = urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(p.path or "/"))
    if p.query:
        path += "?" + p.query
    best = None
    for line in entry.rulelines:
        if line.path == "" or path.startswith(line.path):
            if line.path == "" and line.allowance is False:
                continue  # "Disallow:" with an empty path allows everything
            if best is None or len(line.path) > len(best.path) or (len(line.path) == len(best.path) and line.allowance):
                best = line
    return True if best is None else bool(best.allowance)


class Fetcher:
    """HTTP client that obeys robots.txt (also for every redirect target), spaces requests per
    host, and refuses private / loopback / link-local addresses on every hop (netsafe)."""

    def __init__(self):
        self.s = netsafe.SafeSession()
        self.s.headers.update({"User-Agent": user_agent(), "Accept-Language": "en"})
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.delay: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.sitemaps: dict[str, list[str]] = {}
        self.requests = 0

    def _robots(self, scheme: str, host: str):
        if host in self.robots:
            return self.robots[host]
        rp = urllib.robotparser.RobotFileParser()
        url = f"{scheme}://{host}/robots.txt"
        try:
            self._wait(host)
            r = netsafe.with_retries(lambda: self.s.get(url, timeout=15), on_retry=self._retrying(url))
            self.requests += 1
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 500:
                rp.disallow_all = True  # server trouble: treat as "do not crawl" like major crawlers
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
                self.sitemaps[host] = rp.site_maps() or []
        except requests.RequestException:
            rp.disallow_all = True
        cd = rp.crawl_delay(self.s.headers["User-Agent"]) if not (rp.allow_all or rp.disallow_all) else None
        self.delay[host] = min(MAX_DELAY, max(MIN_DELAY, float(cd or 0)))
        self.robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        rp = self._robots(p.scheme or "https", p.netloc)
        if rp.allow_all:
            return True
        if rp.disallow_all:
            return False
        return robots_allows(rp, self.s.headers["User-Agent"], url)

    @staticmethod
    def _retrying(url: str):
        return lambda why, wait: progress.sub(f"{why} from {url[:80]}; retrying in {wait:.0f}s")

    def _wait(self, host: str):
        gap = self.delay.get(host, MIN_DELAY)
        elapsed = time.monotonic() - self.last.get(host, 0)
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self.last[host] = time.monotonic()

    def get(self, url: str, check_robots: bool = True):
        """Return (status, final_url, content_type, body_bytes) or None if robots forbids."""
        if check_robots and not self.allowed(url):
            return None
        host = urlsplit(url).netloc
        self._wait(host)
        try:
            # 429 / 5xx / timeouts are retried twice (2 s, 5 s, or the server's Retry-After).
            r = netsafe.with_retries(
                lambda: self.s.get(url, timeout=20, stream=True, allow_redirects=True,
                                   redirect_check=self.allowed if check_robots else None),
                on_retry=self._retrying(url))
            self.requests += 1
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    break
            r.close()
            return r.status_code, r.url, r.headers.get("content-type", ""), body
        except netsafe.RedirectRefused:
            return None
        except requests.RequestException as e:
            return 0, url, "", str(e).encode()


def same_site(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def parse_html(body: bytes) -> Page:
    p = Page()
    try:
        p.feed(body.decode("utf-8", errors="replace"))
        p.close()
    except Exception:  # malformed HTML: keep what was parsed
        pass
    return p


def read_sitemaps(f: Fetcher, base: str, domain: str) -> list[str]:
    host = urlsplit(base).netloc
    f.allowed(base)  # loads robots.txt and its Sitemap: lines
    queue = list(f.sitemaps.get(host, [])) or [urljoin(base, "/sitemap.xml"), urljoin(base, "/sitemap_index.xml")]
    seen, urls = set(), []
    while queue and len(seen) < MAX_SITEMAP_FILES and len(urls) < MAX_SITEMAP_URLS:
        sm = queue.pop(0)
        if sm in seen or not same_site(sm, domain):
            continue
        seen.add(sm)
        got = f.get(sm)
        if not got or got[0] != 200:
            continue
        body = got[3]
        if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
            try:
                body = netsafe.gunzip_capped(body)
            except (zlib.error, netsafe.TooLarge) as e:
                progress.sub(f"sitemap {sm} skipped: {e}")
                continue
        text = body.decode("utf-8", errors="replace")
        locs = [html.unescape(x.strip()) for x in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)]
        if "<sitemapindex" in text:
            # Career-related child sitemaps first.
            queue.extend(sorted(locs, key=lambda u: 0 if CAREER_WORDS.search(u) else 1))
        else:
            urls.extend(u for u in locs if same_site(u, domain))
    return urls[:MAX_SITEMAP_URLS]


def priority(url: str) -> int:
    path = urlsplit(url).path
    if CAREER_WORDS.search(path):
        return 0 if path.count("/") <= 2 else 1
    return 3


def jsonld_location(it: dict) -> str:
    locs = it.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    parts = []
    for loc in locs:
        addr = (loc or {}).get("address") or {} if isinstance(loc, dict) else {}
        if isinstance(addr, dict):
            country = addr.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            parts.append(", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion"), country if isinstance(country, str) else None) if x))
    if it.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return "; ".join(dict.fromkeys(p for p in parts if p))


def jsonld_places(it: dict) -> list:
    """[[city, country], ...] from JobPosting jobLocation addresses."""
    locs = it.get("jobLocation") or []
    out = []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            country = addr.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            if addr.get("addressLocality") or country:
                out.append([addr.get("addressLocality"), country if isinstance(country, str) else ""])
    return out


def jsonld_salary(it: dict) -> dict | None:
    """JobPosting baseSalary (MonetaryAmount with a QuantitativeValue) -> salary hint; None when absent."""
    bs = it.get("baseSalary")
    bs = bs[0] if isinstance(bs, list) and bs else bs
    if not isinstance(bs, dict):
        return None
    val = bs.get("value")
    if isinstance(val, dict):
        lo, hi, unit = val.get("minValue", val.get("value")), val.get("maxValue", val.get("value")), val.get("unitText")
    else:
        lo = hi = val
        unit = bs.get("unitText")
    return {"min": lo, "max": hi, "currency": bs.get("currency"), "interval": unit}


def jsonld_jobs(page: Page, url: str) -> list[dict]:
    out = []
    for raw in page.jsonld:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t != "JobPosting" and not (isinstance(t, list) and "JobPosting" in t):
                continue
            if not looks_like_job(clean_text(it.get("title")), it.get("url") or url):
                continue
            emp = it.get("employmentType")
            emp = ", ".join(emp) if isinstance(emp, list) else (emp or "")
            loc_text = jsonld_location(it)
            desc = clean_text(re.sub(r"<[^>]+>", " ", html.unescape(it.get("description") or "")), 300)
            valid = str(it.get("validThrough") or "")[:10]
            out.append({
                "Job Title": clean_text(it.get("title")),
                "Location / Work Mode": " · ".join(x for x in (loc_text, emp.replace("_", " ").title() if emp else "") if x),
                "Job / Post URL": it.get("url") or url,
                "Posted Date": str(it.get("datePosted") or "")[:10],
                "Description Summary": desc,
                "_valid_through": valid,
                "_evidence": "JSON-LD JobPosting",
                "_work_mode": "remote" if str(it.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None,
                "_places": jsonld_places(it),
                "_salary": jsonld_salary(it),
            })
    return out


def page_location(page: Page) -> str:
    """Location of a job detail page: JSON-LD JobPosting jobLocation first, then a short
    location-looking line near the h1."""
    for raw in page.jsonld:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            t = it.get("@type") if isinstance(it, dict) else None
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                loc = jsonld_location(it)
                if loc:
                    return loc
    try:
        start = page.lines.index(page.h1) if page.h1 else 0
    except ValueError:
        start = 0
    return next((l for l in page.lines[start + 1 : start + 15] if len(l) <= 80 and LOCATION.search(l)), "")


def heuristic_job(page: Page, url: str) -> dict | None:
    title = page.h1 or re.split(r"\s[-|–]\s", page.title)[0]
    title = clean_text(title)
    if not title or GENERIC_H1.match(title) or len(title) > 120 or not looks_like_job(title, url):
        return None
    try:
        start = page.lines.index(next(l for l in page.lines if l == page.h1))
    except StopIteration:
        start = 0
    window = page.lines[start + 1 : start + 15]
    loc = next((l for l in window if len(l) <= 80 and LOCATION.search(l)), "")
    emp = next((l for l in window if EMPLOYMENT.match(l)), "")
    desc = page.meta_description or next((l for l in window if len(l) > 60), "")
    return {
        "Job Title": title,
        "Location / Work Mode": " · ".join(x for x in (loc, emp) if x),
        "Job / Post URL": url,
        "Posted Date": "",
        "Description Summary": clean_text(desc, 300),
        "_evidence": "careers child page (h1 + nearby location line)",
    }


GENERIC_LINK = re.compile(r"^(view|see|read|learn|apply|details?|more|view details|apply now|read more|learn more|know more)\b", re.I)


def listing_cards(page: Page, url: str, domain: str) -> dict[str, dict]:
    """Title and location of each job card on a careers listing page, keyed by the card's job link.
    The title is the line just before the card's first short location line."""
    cards, prev = {}, 0
    for (href, text), pos in zip(page.links, page.link_pos):
        absu = urljoin(url, html.unescape(href))
        path = urlsplit(absu).path
        if not (same_site(absu, domain) and JOB_DETAIL_HINT.search(path) and path.rstrip("/").count("/") >= 2):
            continue
        window = page.lines[max(prev, pos - 20) : pos]
        prev = pos
        k = normalize_url(absu)
        if k in cards:
            continue
        title, loc = "", ""
        if text and not GENERIC_LINK.match(text) and len(text) <= 100:
            title = text
        for i, line in enumerate(window):
            if len(line) <= 50 and LOCATION.search(line) and i > 0:
                loc = line
                if not title:
                    title = window[i - 1]
                break
        emp = next((l for l in window if EMPLOYMENT.match(l)), "")
        if title and not GENERIC_H1.match(title) and looks_like_job(title, absu):
            cards[k] = {"url": absu, "title": clean_text(title), "location": " · ".join(x for x in (loc, emp) if x), "listing": url}
    return cards


def _merge_external(items: list[dict]) -> list[dict]:
    """One entry per URL; an URL seen as a <script src> / <iframe> anywhere stays marked as an embed."""
    out: dict[str, dict] = {}
    for a in items:
        if a["url"] in out:
            out[a["url"]]["embed"] = out[a["url"]]["embed"] or a["embed"]
        else:
            out[a["url"]] = dict(a)
    return list(out.values())


def crawl(domain: str, max_pages: int = 150, careers_hint: str | None = None,
          sitemaps: bool = True, stop_on=None) -> dict:
    """Crawl one site. `sitemaps=False` skips the sitemap (a light re-check of known pages).
    `stop_on(ats_urls) -> bool` is asked after each careers page that links an ATS board; when it
    says yes the crawl stops early (result "stopped_early": True), because the board's feed is the
    full job list and the rest of the site adds nothing."""
    domain = bare_domain(domain)
    f = Fetcher()
    base = f"https://{domain}/"
    home = f.get(base)
    if home is None:
        return {"domain": domain, "error": "robots.txt disallows the home page", "pages": []}
    if home[0] == 0 or home[0] >= 400:
        base = f"https://www.{domain}/"
        home = f.get(base) or (0, base, "", b"")
    official = home[1] if home and home[0] and home[0] < 400 else ""
    if official and not same_site(official, domain):
        domain = bare_domain(official)  # site redirects to a new domain (e.g. humancloud.co.in -> humancloud.dev)
    base = official or base

    progress.sub(f"robots.txt read for {urlsplit(base).netloc}; reading sitemaps")
    sitemap_urls = read_sitemaps(f, base, domain) if sitemaps else []
    progress.sub(f"sitemap: {len(sitemap_urls)} URLs; crawling up to {max_pages} pages")
    heap: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    n = 0

    def push(u: str, prio: int | None = None):
        nonlocal n
        u = u.split("#")[0]
        if not u.startswith("http") or not same_site(u, domain) or SKIP_EXT.search(urlsplit(u).path):
            return
        k = normalize_url(u)
        if k in seen:
            return
        seen.add(k)
        n += 1
        heapq.heappush(heap, (priority(u) if prio is None else prio, n, u))

    if careers_hint:
        push(careers_hint, -1)
    for path in ("/careers", "/jobs", "/careers/", "/join-us", "/work-with-us"):
        push(urljoin(base, path), 2)
    for u in sitemap_urls:
        push(u)
    push(base, 2)

    pages, ats_links, job_rows = [], [], []
    careers_external: list[dict] = []
    linked_from_careers: dict[str, str] = {}
    cards: dict[str, dict] = {}
    blocked = 0
    stopped_early = False

    def blank_cards() -> list[str]:
        done = {normalize_url(p["url"]) for p in pages}
        return [k for k, c in cards.items() if not c["location"] and k not in done]

    # Part of the page cap is kept for the detail pages of listing cards without a location.
    while heap and len(pages) < max_pages - min(len(blank_cards()), max_pages // 3):
        _, _, url = heapq.heappop(heap)
        got = f.get(url)
        if got is None:
            blocked += 1
            continue
        status, final, ctype, body = got
        if len(pages) + 1 == max_pages or (len(pages) + 1) % 20 == 0:
            progress.sub(f"crawled {len(pages) + 1}/{max_pages} pages, {len(job_rows)} job pages so far")
        rec = {"url": url, "final_url": final, "status": status, "careers": bool(CAREER_WORDS.search(urlsplit(final).path))}
        pages.append(rec)
        if status != 200 or "html" not in ctype.lower() or not same_site(final, domain):
            continue
        pg = parse_html(body)
        rec["title"] = pg.title
        rec["h1"] = pg.h1
        n_ats = len(ats_links)
        if rec["careers"]:
            # Third-party URLs a careers page links, embeds or names in its scripts, for the blocker
            # detector (an embedded job widget often appears only in an inline script).
            for href in pg.embeds + [h for h, _ in pg.links]:
                absu = urljoin(final, html.unescape(href))
                if absu.startswith("http") and not same_site(absu, domain):
                    careers_external.append({"url": absu, "found_on": final, "embed": href in pg.embeds})
            for absu in blockers.urls_in_html(body, final):
                if not same_site(absu, domain):
                    careers_external.append({"url": absu, "found_on": final, "embed": False})
            rec["text_lines"] = len(pg.lines)
        for href in pg.embeds + [h for h, _ in pg.links]:
            absu = urljoin(final, html.unescape(href))
            if re.search(r"greenhouse\.io|lever\.co|workable\.com|ashbyhq\.com|smartrecruiters\.com|zohorecruit\.|darwinbox|keka\.com|myworkdayjobs|successfactors|taleo|icims", absu, re.I):
                ats_links.append({"url": absu, "found_on": final})
        if stop_on and rec["careers"] and (len(ats_links) > n_ats or careers_external) and \
                stop_on([x["url"] for x in ats_links] + [x["url"] for x in careers_external]):
            progress.sub(f"ATS board linked from {final}; its feed is the job list, so the crawl stops at {len(pages)} pages")
            stopped_early = True
            break
        for href, text in pg.links:
            absu = urljoin(final, html.unescape(href))
            push(absu)
            if rec["careers"] and same_site(absu, domain):
                linked_from_careers.setdefault(normalize_url(absu), text)
        if rec["careers"]:
            for k, card in listing_cards(pg, final, domain).items():
                cards.setdefault(k, card)
        found = jsonld_jobs(pg, final)
        if found:
            for j in found:
                j["_found_on"] = final
            job_rows.extend(found)
        elif JOB_DETAIL_HINT.search(urlsplit(final).path) and urlsplit(final).path.rstrip("/").count("/") >= 2:
            j = heuristic_job(pg, final)
            if j:
                j["_found_on"] = final
                job_rows.append(j)

    # Listing cards without a location: read it from the role's own page, within the page cap.
    detail_loc: dict[str, str] = {}
    todo = [] if stopped_early else blank_cards()
    if todo and len(pages) < max_pages:
        progress.sub(f"reading {min(len(todo), max_pages - len(pages))} job detail page(s) for missing locations")
    for k in todo:
        if len(pages) >= max_pages:
            break
        got = f.get(cards[k]["url"])
        if got is None:
            blocked += 1
            continue
        status, final, ctype, body = got
        pages.append({"url": cards[k]["url"], "final_url": final, "status": status, "careers": False, "detail_for_location": True})
        if status == 200 and "html" in ctype.lower():
            detail_loc[k] = page_location(parse_html(body))

    if len(pages) < max_pages and not stopped_early:
        progress.sub(f"crawled {len(pages)}/{max_pages} pages (no more same-site links), {len(job_rows)} job pages, {blocked} blocked by robots")
    careers_pages = sorted(
        (p for p in pages if p["careers"] and p["status"] == 200 and same_site(p["final_url"], domain)),
        key=lambda p: (urlsplit(p["final_url"]).path.rstrip("/").count("/"), len(p["final_url"])),
    )
    careers_page = careers_hint or (careers_pages[0]["final_url"] if careers_pages else "")

    # Status from evidence: linked from the live careers listing (or JSON-LD still valid) = Open;
    # reachable only through the sitemap = Unverified (sitemaps often keep closed roles).
    today = time.strftime("%Y-%m-%d")
    for j in job_rows:
        k = normalize_url(j["Job / Post URL"])
        if j.get("_valid_through") and j["_valid_through"] < today:
            j["Status"] = "Closed"
        elif k in linked_from_careers or j.get("_evidence") == "JSON-LD JobPosting":
            j["Status"] = "Open"
        else:
            j["Status"] = "Unverified"
            j["_evidence"] += "; not linked from the live careers listing"

    uniq = {}
    for j in job_rows:
        uniq.setdefault(normalize_url(j["Job / Post URL"]), j)
    # Roles listed on the careers page whose detail page is missing, broken or not yet fetched.
    status_of = {normalize_url(p["url"]): p["status"] for p in pages}
    for k, card in cards.items():
        if not card["location"] and detail_loc.get(k):
            card["location"] = detail_loc[k]
        if k in uniq:
            if not uniq[k]["Location / Work Mode"]:
                uniq[k]["Location / Work Mode"] = card["location"]
            continue
        st = status_of.get(k)
        ok = st is None or st == 200
        uniq[k] = {
            "Job Title": card["title"],
            "Location / Work Mode": card["location"],
            "Job / Post URL": card["url"],
            "Application URL": card["listing"],
            "Posted Date": "",
            "Description Summary": "" if ok else f"Listed on the careers page, but the role's own page returned HTTP {st} on {today}.",
            "Status": "Open" if ok else "Unverified",
            "_evidence": "careers listing card" + ("" if ok else f"; detail page HTTP {st}"),
            "_found_on": card["listing"],
        }
    return {
        "domain": domain,
        "official_website": official,
        "careers_page": careers_page,
        "sitemap_urls": len(sitemap_urls),
        "pages_fetched": len(pages),
        "blocked_by_robots": blocked,
        # Career-looking URLs still queued when the page cap hit: the careers listing may be incomplete.
        "career_urls_unvisited": sum(1 for prio, _, _ in heap if prio <= 1),
        "stopped_early": stopped_early,
        "requests": f.requests,
        "ats_links": list({a["url"]: a for a in ats_links}.values()),
        "careers_external": _merge_external(careers_external)[:400],
        "jobs": list(uniq.values()),
        "pages": pages,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--careers", help="known careers page URL")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = crawl(a.domain, a.max_pages, a.careers)
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}: {res['pages_fetched']} pages, {len(res['jobs'])} job pages, {len(res['ats_links'])} ATS links")
    else:
        print(text)
