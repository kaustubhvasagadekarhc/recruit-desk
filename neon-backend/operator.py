"""Operator tools for the Recruit Desk database on Neon. Run from this folder with the DIRECT (unpooled) connection string:

    DATABASE_URL_UNPOOLED=postgresql://... uv run --with psycopg[binary] python operator.py setup
    ... operator.py create-company "Acme Corp" boss@acme.com     (prints an invitation code for the first admin)
    ... operator.py list-companies
    ... operator.py list-people
    ... operator.py purge-history 180                              (deletes stored searches, result rows and usage lines older than 180 days)
    python operator.py connection-code <neon auth url> <data api url> [origin]    (no database needed; prints the code people paste into the app)

The connection string can also be read from ../auth-server/.env.local (written by `neon deploy`). Nothing here takes a password:
the first admin makes their own account in the app and uses the invitation code.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path


HERE = Path(__file__).parent


def connection_string() -> str:
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        for env in (HERE.parent / "auth-server" / ".env.local", HERE / ".env.local"):
            if env.exists():
                for line in env.read_text().splitlines():
                    if line.startswith("DATABASE_URL_UNPOOLED="):
                        url = line.split("=", 1)[1].strip().strip('"')
    if not url:
        sys.exit("Set DATABASE_URL_UNPOOLED (the direct Neon connection string).")
    return url


def rows(conn, sql: str, params=()):
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    print(" | ".join(cols))
    for r in cur.fetchall():
        print(" | ".join("" if x is None else str(x) for x in r))


DEFAULT_ORIGIN = "https://recruitdesk.example"   # a reserved name nobody can own; it must be added to Neon Auth's trusted domains


def connection_code(auth: str, api: str, origin: str) -> str:
    """The code an admin gives people: two Neon addresses and the app's origin name. It holds no secret."""
    for label, url in (("sign-in", auth), ("data", api), ("origin", origin)):
        if not url.startswith("https://") or " " in url or "?" in url:
            sys.exit(f"The {label} address must be a plain https:// address.")
    if origin.count("/") != 2:
        sys.exit("The origin is only a scheme and a host, like https://recruitdesk.example")
    body = json.dumps({"a": auth.rstrip("/"), "r": api.rstrip("/"), "o": origin.rstrip("/")}, separators=(",", ":"))
    return "rd1." + base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "connection-code":
        if len(args) not in (3, 4):
            sys.exit("usage: connection-code <neon auth url ending /auth> <data api url ending /rest/v1> [origin]")
        print(connection_code(args[1], args[2], args[3] if len(args) == 4 else DEFAULT_ORIGIN))
        return
    if not args or args[0] not in {"setup", "create-company", "list-companies", "list-people", "purge-history"}:
        sys.exit(__doc__)
    import psycopg   # only the database commands need it

    with psycopg.connect(connection_string(), autocommit=True) as conn:
        if args[0] == "setup":
            conn.execute((HERE / "setup.sql").read_text())
            print("setup applied")
        elif args[0] == "create-company":
            if len(args) != 3:
                sys.exit('usage: create-company "<company name>" <first admin email>')
            try:
                code = conn.execute("select public.operator_create_company(%s, %s)", (args[1], args[2])).fetchone()[0]
            except psycopg.errors.UniqueViolation:
                sys.exit("A company with that name already exists (capitals, spacing, punctuation and accents do not make a new name).")
            except psycopg.Error as e:
                sys.exit(str(e).splitlines()[0])
            print(f"Company created. Invitation code for {args[2].strip().lower()} (valid 14 days, only that address can use it):\n{code}")
        elif args[0] == "purge-history":
            try:
                days = int(args[1])
            except (IndexError, ValueError):
                sys.exit("usage: purge-history <days>   (stored searches and result rows older than that are deleted)")
            if days < 1:
                sys.exit("The number of days must be at least 1.")
            n_rows = conn.execute("delete from public.shared_rows where created_at < now() - make_interval(days => %s)", (days,)).rowcount
            searches = conn.execute("delete from public.searches s where s.started_at < now() - make_interval(days => %s)", (days,)).rowcount
            n_log = conn.execute("delete from public.allowance_log where at < now() - make_interval(days => %s)", (days,)).rowcount
            n_app = conn.execute("delete from public.app_logs where at < now() - make_interval(days => %s)", (days,)).rowcount
            print(f"Deleted {n_rows} result row(s), {searches} search(es), {n_log} usage line(s) and {n_app} log line(s) older than {days} days.")
        elif args[0] == "list-companies":
            rows(conn, 'select o.name, (select count(*) from neon_auth.member m where m."organizationId" = o.id) as people, o."createdAt" from neon_auth.organization o order by o.name')
        else:
            rows(conn, 'select u.email, o.name as company, m.role from neon_auth.member m join neon_auth."user" u on u.id = m."userId" join neon_auth.organization o on o.id = m."organizationId" order by o.name, m.role, u.email')


if __name__ == "__main__":
    main()
