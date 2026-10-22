> **Superseded (2026-10-19).** Recruit Desk no longer uses this service. Sign-in and company data now run on Neon Auth and the Neon Data API with the rules kept in the database; see `../neon-backend/README.md`. This folder is kept for reference and its tests still pass, but nothing deploys or calls it.

# Recruit Desk auth service

Invite-only accounts with two roles, `admin` and `user`. It decides who may use Recruit Desk. It stores accounts and sessions only:
candidate, company and account data stay on each person's machine, and so do their own API keys.

**Rule: no user key ever reaches this server.** Each person sets their own keys (Exa, Apollo, others) in the app. They stay in that
person's local files and are sent only to the service they belong to. This service has no table, field or endpoint for them, and a
test (`test_the_server_has_no_place_for_a_users_own_api_keys`) fails if one is added.

## Run it locally

```bash
cd AI-AGENTS/auth-server
uv sync
cp .env.example .env                              # optional; defaults work for development
uv run python cli.py gen-keys                     # once: keys/jwt_private.pem (secret) and keys/jwt_public.pem
uv run python cli.py create-tenant "Acme Corp" you@example.com   # a company and its first admin; asks for the password, never taken from the command line
uv run uvicorn app.main:create_app --factory --port 8000
uv run pytest                                      # 89 tests on SQLite
TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/db uv run pytest   # the same tests on a throwaway Postgres (it drops its tables)
```

Interactive docs: `http://localhost:8000/docs`.

## Endpoints

| Endpoint | Who | What |
|---|---|---|
| `POST /auth/login` | anyone | email and password → access token, refresh token, `offline_grace_until` |
| `POST /auth/refresh` | anyone with a refresh token | rotates it; a token that was already exchanged ends all of that user's sessions |
| `POST /auth/logout` | anyone with a refresh token | revokes it |
| `POST /auth/accept-invite` | invited person | sets a password from the invite link, creates the account |
| `POST /auth/forgot-password`, `/auth/reset-password` | anyone | reset by emailed link (needs SMTP) |
| `POST /auth/change-password` | signed in | signs out every other device |
| `GET /auth/me`, `GET /auth/public-key` | signed in / anyone | the user; the key the app checks tokens with |
| `POST /admin/invites`, `GET /admin/invites`, `DELETE /admin/invites/{id}` | admin | invite by email as `user` or `admin`; list open invites; withdraw one (its link stops working) |
| `GET /admin/users`, `PATCH /admin/users/{id}` | admin | list; change role or disable (ends their sessions at once) |
| `POST /admin/users/{id}/reset-link` | admin | a reset link to pass on |
| `PUT /records/last/{agent}` | signed in | store the last record an assistant created (`candidate`, `company` or `account`): one row per assistant, the newer record wins (409 when an older one arrives), a fixed list of fields per assistant, no contact details |
| `GET /records/last` | signed in | the stored records (the admin screen shows them) |
| `DELETE /records/last/{agent}?subject_hash=...` | signed in | remove the stored record, only if it is the one with that hash (used when the person it is about is erased on the computer that created it) |

## Tenants (companies)

Every account belongs to exactly one tenant, and the structure is: the server operator creates a tenant together with its first admin (`cli.py create-tenant "<name>" <email>`), that admin signs in and invites the rest of the company.

- **The name is required and unique.** Two names are the same when they differ only in capitals, spacing, punctuation, accents or full-width letters ("Acme Corp", "ACME, corp." and "Café" / "Cafe" collide). The folded key has a unique index in the database, so two requests racing each other cannot both succeed. Letters that merely look alike from another alphabet are not folded.
- **No public sign-up.** A tenant exists only through the command line, which needs access to the server's database. `cli.py create-admin <email> --tenant "<name>"` adds another admin to an existing tenant; `list-tenants` and `list-users` show what exists.
- **An admin works inside their own tenant only.** Invitations, the user list, role changes, disabling, reset links and the stored last records are all scoped to the tenant; another tenant's user or invitation answers 404. The rule that a tenant keeps at least one active admin is counted per tenant.
- **One email, one tenant.** An address cannot be invited if it already has an account; if two tenants invite the same new address, the first invitation accepted gets it and the other then fails.
- The tenant name and id travel in the access token and in `/auth/me`, so the app can show which company a person is signed in to.
- A database created before tenants existed is refused at start with a message (there is no migration yet): use a new database, or add Alembic first.

There is no public sign-up. The invitation and reset links carry a one-time token. No web page on the server accepts them: the Recruit Desk app does (sign-in screen > "I have an invitation" or "I have a reset link"), taking only the token from the pasted link and ignoring its address. Without SMTP settings nothing is emailed: the admin gets the invite or reset link in the response and
passes it on. Links hold secrets, so they are never logged.

## How it protects accounts

- Passwords: argon2id, at least 10 characters. A wrong email and a wrong password give the same answer and take the same time.
- Access tokens: Ed25519-signed, 15 minutes. The app checks them offline with the public key. Each carries `grace`, the time until
  which the app may keep working without reaching the server (7 days after the last sign-in or refresh); it is signed, so editing a file does not extend it.
- Refresh tokens: random, stored only as a SHA-256 hash, 30 days, single use (rotated on every refresh), revocable.
- Invite and reset tokens: random, stored hashed, single use, expiring (7 days, 60 minutes).
- The database decides on every request: disabling an account or changing a role works at once, not when the token expires.
- 5 failed logins per email and 20 per address in 15 minutes lock that email or address for the rest of the window (counted in the database, so it holds across serverless instances).
- An admin cannot demote or disable themselves, and the last active admin cannot be removed.

## Hosting on Vercel (no domain needed: `https://<project>.vercel.app`)

Vercel runs FastAPI as a serverless function (entrypoint `vercel_app.py`, set in `pyproject.toml`; `vercel.json` sets the duration).
Serverless means no lasting disk and no memory shared between requests, so this service is built for it: Postgres instead of a
SQLite file, a failed-login counter kept in the database, the signing key supplied as an environment variable, and no connection pool.

Steps (you do 1, 2 and 4 yourself: they involve secrets, and a key should not pass through a chat):

1. **A Neon database.** Either `vercel install neon` from this folder (the Vercel Marketplace creates the database and connects it to
   your project; run `vercel link` first), or create a project at neon.tech and copy its connection string. Use the **pooled** string
   (the host name contains `-pooler`) for the running service, and the **direct** (unpooled) string for `init-db` and `create-tenant`.
   `neon deploy` and `neon env pull` write `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` (direct) to `.env.local`, which this service reads (`.env.local` wins over `.env`). The CLI commands use the direct address automatically when `DATABASE_URL_UNPOOLED` is set. If you use the Vercel Marketplace integration instead, check the variable names it adds with `vercel env ls` and set `DATABASE_URL` to the pooled string. Keep `?sslmode=require` on the URL. A free Neon database sleeps
   after a few minutes idle and wakes on the next login (a short delay; the connection timeout is 10 seconds). Check Neon's current
   free-plan limits and how long it keeps backups before relying on it.
2. **A signing key**, made on your own machine: `uv run python cli.py gen-keys`, then put the private key in Vercel without printing it:
   `vercel env add JWT_PRIVATE_KEY production < keys/jwt_private.pem`. Keep `keys/jwt_private.pem` backed up somewhere safe: losing it
   signs everyone out, and anyone who gets it can forge logins.
3. **Environment variables** in the Vercel project: `DATABASE_URL`, `JWT_PRIVATE_KEY`, `BASE_URL=https://<project>.vercel.app`,
   `USE_VERCEL_IP=true` (rate limiting needs each caller's real address), `AUTO_CREATE_TABLES=false`, and optionally `SMTP_*`.
   `VERCEL` is set by the platform and switches on serverless mode.
4. **Create the tables and the first admin** once, from your computer, against the production database (the direct Neon string):
   `DATABASE_URL='<the direct connection string>' uv run python cli.py init-db`, then the same with `create-tenant "Your Company" you@example.com` (it asks for the password).
5. **Deploy** from this folder with `vercel --prod` (or connect the folder to a Git repository), then check `GET /health` and `GET /auth/public-key`.

Things to know:

- Vercel's Hobby plan may be limited to personal, non-commercial use; this is for a company tool, so read Vercel's current terms and pricing before launch.
- A function that has been idle starts slowly (a second or two for the first login); argon2 hashing needs memory and CPU, so keep an eye on login time.
- Tokens and links are never logged by this service. Do not add request-body logging.
- The app takes the server address from a setting, so moving to your own domain later (for example behind a Cloudflare Tunnel) needs no rebuild.

## Not done yet

- No Alembic migrations (tables are created on start). Add Alembic before the first schema change.
- Email is optional and untested against a real SMTP server.

## Neon project files

`neon init` linked this folder to the Neon project (`.neon`, git-ignored) and wrote `neon.ts` (the services Neon should provide on the branch) plus a
small `package.json` for its typed config. They are tooling only: this service is Python and does not use Node. Never run the test suite with
`TEST_DATABASE_URL` pointing at the production branch: it drops the tables. Create a development branch first (`neon checkout dev --create`).

## The last record (data stays local)

Each person's assistants process everything on their own computer. The only business data this server holds is **one row per assistant per tenant**: the newest record that assistant created, in the `last_records` table (Postgres, so on Neon when deployed).

- A fixed list of fields per assistant (candidate: name, title, company, location, score, profile link, JD title, date found; company: job title, company, location, status, source, posted and discovered dates, listing link; account: company, domain, type, HQ, funding, confidence, last checked). Any other field is refused (422), and so is a value that looks like an email address or a phone number. No email, phone, evidence text or notes.
- The record's key is stored only as a sha256 hash. The app uses it to remove exactly this record again if the person is erased locally; a newer record from another computer is never removed by an older computer's erasure.
- The newer record wins by its creation time: an older one is refused with 409, the same record again changes nothing.
