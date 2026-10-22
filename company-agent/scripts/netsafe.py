"""Network safety for the crawler and the ATS readers (security review step 6, 2026-10-14).

- SafeSession: a requests.Session that follows redirects itself, one hop at a time, and before
  every request (the first one and each redirect target) refuses non-http(s) URLs and any host
  that resolves to a private, loopback, link-local, multicast, reserved or otherwise non-global
  address. After connecting it also checks the socket's peer address, which catches a DNS answer
  that changed between the check and the connection. A per-request `redirect_check(url) -> bool`
  lets the crawler re-check robots.txt for every redirect target.
- Bodies of non-streamed responses are capped at MAX_BODY bytes after decoding (urllib3 >= 2.6
  bounds each decompression step, so a Content-Encoding gzip bomb cannot blow up in one read).
- gunzip_capped(): decompresses a .gz file (sitemaps) with zlib.decompressobj and a hard output cap.
- with_retries(): retries a request on HTTP 429 / 5xx, a timeout or a dropped connection, with a 2 s
  then 5 s backoff, honouring Retry-After (capped at 30 s). Refusals (private address, robots,
  size) and unresolvable hosts are never retried.
"""
from __future__ import annotations

import ipaddress
import email.utils
import socket
import time
import zlib
from urllib.parse import urljoin, urlsplit

import requests

MAX_REDIRECTS = 8
MAX_BODY = 20_000_000  # bytes, decoded; ATS feeds with full descriptions stay well below this
MAX_GUNZIP = 10_000_000  # bytes of decompressed sitemap


RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_BACKOFF = (2.0, 5.0)  # seconds before the 2nd and 3rd attempt
RETRY_AFTER_MAX = 30.0


class Unresolvable(requests.ConnectionError):
    """DNS has no answer for the host; retrying soon will not help."""


class BlockedAddress(requests.RequestException):
    """The URL points at a private, loopback, link-local or otherwise non-public address."""


class RedirectRefused(requests.RequestException):
    """A redirect target failed the caller's check (for example robots.txt)."""


class TooLarge(requests.RequestException):
    """The body (or its decompressed form) is over the cap."""


def bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not a.is_global or a.is_multicast or a.is_loopback or a.is_link_local or a.is_private or a.is_reserved or a.is_unspecified


def check_url(url: str) -> None:
    """Raise BlockedAddress unless url is http(s) and every address its host resolves to is public."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise BlockedAddress(f"refused {url}: only http and https are fetched")
    host = p.hostname
    if not host:
        raise BlockedAddress(f"refused {url}: no host")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise Unresolvable(f"cannot resolve {host}: {e}") from e
    for info in infos:
        ip = info[4][0]
        if bad_ip(ip):
            raise BlockedAddress(f"refused {url}: {host} resolves to non-public address {ip}")


def _peer_ip(r: requests.Response) -> str | None:
    try:
        sock = r.raw._connection.sock  # urllib3 2.x, while a streamed body is unread
        return sock.getpeername()[0]
    except (AttributeError, OSError, TypeError):
        return None


def read_capped(r: requests.Response, limit: int = MAX_BODY) -> bytes:
    body = bytearray()
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) > limit:
            r.close()
            raise TooLarge(f"{r.url}: body over {limit} bytes")
    return bytes(body)


def retry_after(r: requests.Response) -> float | None:
    """Seconds asked for by a Retry-After header (delta-seconds or HTTP date), else None."""
    v = (r.headers.get("retry-after") or "").strip()
    if not v:
        return None
    if v.isdigit():
        return float(v)
    try:
        when = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def with_retries(send, backoff=RETRY_BACKOFF, sleep=time.sleep, on_retry=None):
    """Call send() (one HTTP request, returns a Response) up to len(backoff) + 1 times.

    Retried: HTTP 429 and 5xx answers, timeouts and dropped connections. The wait is the
    server's Retry-After when given (capped at RETRY_AFTER_MAX), else the backoff step. The last
    answer is returned as is (the caller decides what a 503 means); the last error is raised.
    """
    for attempt in range(len(backoff) + 1):
        last = attempt == len(backoff)
        try:
            r = send()
        except (BlockedAddress, RedirectRefused, TooLarge, Unresolvable):
            raise
        except (requests.Timeout, requests.ConnectionError) as e:
            if last:
                raise
            wait, why = backoff[attempt], type(e).__name__
        else:
            if r.status_code not in RETRY_STATUS or last:
                return r
            ra = retry_after(r)
            wait = min(RETRY_AFTER_MAX, ra) if ra is not None else backoff[attempt]
            why = f"HTTP {r.status_code}"
            r.close()
        if on_retry:
            on_retry(why, wait)
        sleep(wait)
    raise AssertionError("unreachable")


def gunzip_capped(data: bytes, limit: int = MAX_GUNZIP) -> bytes:
    """Decompress gzip data, never producing more than `limit` bytes (gzip-bomb safe)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    buf = data
    while buf:
        out += d.decompress(buf, limit + 1 - len(out))
        if len(out) > limit:
            raise TooLarge(f"gzip output over {limit} bytes")
        buf = d.unconsumed_tail
        if d.eof:
            break
    return bytes(out)


class SafeSession(requests.Session):
    def __init__(self, max_body: int = MAX_BODY):
        super().__init__()
        self.max_body = max_body
        self.trust_env = False  # no proxies or .netrc from the environment (CVE-2024-47081 class)

    def request(self, method, url, *args, redirect_check=None, **kw):
        follow = kw.pop("allow_redirects", True)
        stream = kw.pop("stream", False)
        history = []
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            r = super().request(method, url, *args, allow_redirects=False, stream=True, **kw)
            peer = _peer_ip(r)
            if peer and bad_ip(peer):
                r.close()
                raise BlockedAddress(f"refused {url}: connected to non-public address {peer}")
            if not (follow and r.is_redirect):
                break
            nxt = urljoin(r.url, r.headers.get("location", ""))
            r.close()
            history.append(r)
            if redirect_check and not redirect_check(nxt):
                raise RedirectRefused(f"redirect from {url} to {nxt} refused (robots.txt)")
            if r.status_code == 303 or (r.status_code in (301, 302) and method.upper() == "POST"):
                method, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json", "files")}
            url = nxt
        else:
            raise requests.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects from {url}")
        r.history = history
        if not stream:
            r._content = read_capped(r, self.max_body)
            r._content_consumed = True
        return r
