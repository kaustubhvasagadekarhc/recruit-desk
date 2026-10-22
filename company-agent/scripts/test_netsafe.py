"""Negative tests for scripts/netsafe.py (security review step 6). Run:
  uv run python scripts/test_netsafe.py [--live]

Offline cases use a fake transport, so no request leaves the machine; --live adds one real
redirect through httpbin.org to 127.0.0.1.
"""
from __future__ import annotations

import http.server
import io
import socket
import sys
import threading
import zlib
from pathlib import Path

import requests
import urllib3
from requests.adapters import BaseAdapter, HTTPAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netsafe  # noqa: E402

RESULTS = []


def case(name, fn, expect):
    try:
        fn()
        got = "no error"
    except Exception as e:  # noqa: BLE001
        got = type(e).__name__ + ": " + str(e)[:110]
    ok = got.startswith(expect)
    RESULTS.append(ok)
    print(("PASS" if ok else "FAIL"), name, "->", got)


def gzip_bomb(n_bytes: int) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    zero = b"\0" * 1_000_000
    out = bytearray()
    for _ in range(n_bytes // len(zero)):
        out += c.compress(zero)
    return bytes(out + c.flush())


class Fake(BaseAdapter):
    """Answers every request from a table: url -> (status, headers, body bytes)."""

    def __init__(self, table):
        super().__init__()
        self.table, self.seen = table, []

    def send(self, request, **kw):
        self.seen.append(request.url)
        status, headers, body = self.table[request.url]
        raw = urllib3.HTTPResponse(body=io.BytesIO(body), headers=headers, status=status,
                                   preload_content=False, decode_content=True)
        return HTTPAdapter().build_response(request, raw)

    def close(self):
        pass


def session(table):
    s = netsafe.SafeSession()
    fake = Fake(table)
    s.mount("http://", fake)
    s.mount("https://", fake)
    return s, fake


# public.test resolves to a public address; everything else resolves normally.
_real = socket.getaddrinfo


def _fake_getaddrinfo(host, *a, **k):
    if host in ("public.test", "public2.test"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80))]
    return _real(host, *a, **k)


netsafe.socket.getaddrinfo = _fake_getaddrinfo


def main() -> None:
    # A real local server: if any test reaches it, it records the hit.
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"secret")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{port}/secret"

    plain = netsafe.SafeSession()
    case("direct fetch of 127.0.0.1 refused", lambda: plain.get(local, timeout=5), "BlockedAddress")
    case("direct fetch of localhost refused", lambda: plain.get(f"http://localhost:{port}/", timeout=5), "BlockedAddress")
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "[::1]", "[::ffff:127.0.0.1]", "[fe80::1]"):
        case(f"fetch of {ip} refused", lambda ip=ip: plain.get(f"http://{ip}/", timeout=5), "BlockedAddress")
    case("file:// refused", lambda: netsafe.check_url("file:///etc/passwd"), "BlockedAddress")

    s, fake = session({"http://public.test/": (302, {"Location": local}, b"")})
    case("redirect public.test -> 127.0.0.1 refused", lambda: s.get("http://public.test/", timeout=5), "BlockedAddress")
    s, fake = session({"http://public.test/a": (301, {"Location": "http://public2.test/b"}, b""),
                       "http://public2.test/b": (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")})
    case("2-hop redirect to cloud metadata 169.254.169.254 refused", lambda: s.get("http://public.test/a", timeout=5), "BlockedAddress")
    s, fake = session({"http://public.test/a": (302, {"Location": "http://public2.test/private"}, b"")})
    case("redirect refused by robots check", lambda: s.get("http://public.test/a", redirect_check=lambda u: "private" not in u), "RedirectRefused")
    s, fake = session({"http://public.test/a": (302, {"Location": "http://public2.test/b"}, b""), "http://public2.test/b": (200, {}, b"ok")})
    case("redirect between public hosts allowed", lambda: s.get("http://public.test/a").raise_for_status(), "no error")

    s, _ = session({"http://public.test/big": (200, {}, b"x" * (netsafe.MAX_BODY + 10))})
    case("body over 20 MB capped", lambda: s.get("http://public.test/big"), "TooLarge")
    bomb = gzip_bomb(200_000_000)
    print(f"     gzip bomb: {len(bomb):,} bytes compressed -> 200,000,000 bytes")
    s, _ = session({"http://public.test/bomb": (200, {"Content-Encoding": "gzip"}, bomb)})
    case("Content-Encoding gzip bomb capped", lambda: s.get("http://public.test/bomb"), "TooLarge")
    case("sitemap .gz bomb capped (gunzip_capped)", lambda: netsafe.gunzip_capped(bomb), "TooLarge")
    case("normal .gz sitemap decompressed", lambda: netsafe.gunzip_capped(gzip_bomb(1_000_000)), "no error")

    if "--live" in sys.argv:
        live = netsafe.SafeSession()
        case("LIVE httpbin.org/redirect-to -> 127.0.0.1 refused",
             lambda: live.get(f"https://httpbin.org/redirect-to?url=http://127.0.0.1:{port}/secret", timeout=20), "BlockedAddress")

    srv.shutdown()
    ok_hits = not hits
    RESULTS.append(ok_hits)
    print(("PASS" if ok_hits else "FAIL"), f"local server received {len(hits)} requests (must be 0)")
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
