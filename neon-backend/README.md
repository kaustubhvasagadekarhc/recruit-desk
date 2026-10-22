# Recruit Desk on Neon: sign-in and company data with no server of our own

Recruit Desk signs people in with **Neon Auth** (managed Better Auth) and keeps the one small piece of shared data, the last record each assistant created, in **Neon Postgres** through the **Neon Data API**. The desktop app talks to Neon directly. There is no web service to host: the rules that a server used to enforce live in the database (`setup.sql`), and an operator script (`operator.py`) creates companies.

This replaces `../auth-server` (a FastAPI service). That folder is kept for reference and still works, but nothing uses it any more.

## How it works

- **Sign-up is open on Neon Auth, but a person sees nothing until they belong to a company.** Every table the app reads is protected by row-level security that looks up live membership. Someone who signs up without an invitation holds an account that can read and write nothing.
- **A company ("tenant") is a Neon Auth organization.** Only the operator can create one: a database trigger refuses `organization/create` from anyone else. The name is required and unique ignoring capitals, spacing, punctuation and accents (a unique index on `fold_name(name)`).
- **The operator makes a company with its first admin:** `operator.py create-company "Acme Corp" boss@acme.com` prints an **invitation code**. The first admin installs the app, chooses *I have an invitation code*, uses exactly that email address, the code and a password of their own. Nobody else ever handles their password.
- **Admins invite people into their own company.** Neon Auth's organization API makes an invitation and returns its id: that id is the **invitation code**. The admin hands it over (nothing is emailed by the app). Neon's own invitation email stays **off** (`organization.send_invitation_email` is false): its text and link cannot be changed, and turning it on makes Neon refuse the invitation until the invited person has verified their email address (`EMAIL_VERIFICATION_REQUIRED_BEFORE_ACCEPTING_OR_REJECTING_INVITATION`), which the sign-up flow does not do. Only the invited email address can accept it, and a code works once.
- **One person, one company.** A trigger refuses a second membership, and refuses inviting an address that already belongs to a company.
- **Roles:** the company owner and admins can invite people, change roles and remove people; everyone else cannot. Removing a person cuts their access to the company's data at once, even with a token that is still valid, because policies check live membership. A trigger clears the removed person's active-company pointer, because Neon Auth otherwise answers that session's lookups with a server error.
- **The last record:** `last_records` holds one row per assistant per company. The database checks the field list, value lengths, that nothing looks like an email address or a phone number, that an older record never replaces a newer one (HTTP 409), and fills in who stored it. A delete only matches the record with the given hash.
- **Search history (2026-10-20):** `searches` (what was searched, when, how it ended) and `shared_rows` (every NEW result row, a fixed list of fields, no contact details) belong to the person who stored them; the database sets that from the sign-in, never from what the app says. A person sees and removes only their own; the company's owner and admins see and remove everyone's in their company. A row can only be linked to the sender's own search. `operator.py purge-history <days>` deletes older history.
- **The token allowance (2026-10-20; per person since 2026-10-21):** `allowance_use` holds one row per person: their own cap and window length (new people start at 3,000,000 tokens and 240 minutes; nothing is company-wide), their count over all three assistants together, window start and lock. People never write it: `allowance_add(agent, tokens)` adds to the caller's count (up to 5,000,000 per call; the assistant only goes to the log), `allowance_state()` reads the caller's row, and only the company's owner and admins can call `allowance_set_user(email, cap, window_minutes)`, `allowance_reset(email or null)` (one person, or everyone) or `allowance_people()` (every member's cap, window and count). A person's window opens with their first tokens and closes on the database's clock, when their count and any lock return to zero. `allowance_log` records who added how much with which assistant (own lines for a person, everyone's for admins).
- **Application logs (2026-10-21):** `app_logs` holds short lines the app writes about itself: events and errors, how a run went (finished and failed step lines, how it ended, tokens), and sign-ins and sign-outs. Lines never carry search text, candidates, companies or contact details (the app strips emails and phone numbers and cuts long text). People write only through `log_write(items)` (up to 50 lines per call; the person and the company come from the sign-in); **only the company's owner and admins can read them**, nobody can change or delete a line through the API, and the next write removes lines older than 30 days. Failed sign-in attempts are not logged: they happen before there is a sign-in to write with.
- **No workbook rows (2026-10-21):** the Excel results stay on each computer (Documents\RecruitDesk-Data). Earlier versions stored every workbook row here (`workbook_rows`, with an archive and sync helpers); `setup.sql` drops all of it.
- **Everything else stays on each computer.** The assistants' workbooks and databases and each person's own API keys are never sent to Neon.

## Setting up a Neon branch (once)

All of this can be done with the `neon` CLI. Use a **development branch first** (`neon branches create --name dev`); the test script creates companies and accounts.

1. **Enable Neon Auth** on the branch: `neon api -X POST /projects/<project>/branches/<branch>/auth -d '{"auth_provider":"better_auth"}'`
2. **Enable the Data API** with Neon Auth as the provider: `neon data-api create --project-id <project> --branch <branch> --database neondb --auth-provider neon_auth --add-default-grants`
3. **Trust the app's origin name.** The app sends `Origin: https://recruitdesk.example` (a reserved name nobody can own; it is part of the connection code). Add it and turn localhost off:
   `neon api -X POST /projects/<project>/branches/<branch>/auth/domains -d '{"auth_provider":"better_auth","domain":"https://recruitdesk.example"}'`
   `neon api -X PATCH /projects/<project>/branches/<branch>/auth/allow_localhost -F allow_localhost=false`
4. **Apply the rules** with the direct (unpooled) connection string. It is idempotent, so run it again after changing `setup.sql`:
   `DATABASE_URL_UNPOOLED=postgresql://… uv run --with "psycopg[binary]" python operator.py setup`
5. **Make the connection code** the app needs (two addresses from `neon api /projects/<project>/branches/<branch>/auth` and `neon data-api get`; it holds no secret):
   `python operator.py connection-code https://<ep>.neonauth.<region>.aws.neon.tech/neondb/auth https://<ep>.apirest.<region>.aws.neon.tech/neondb/rest/v1`
   Build it into the installer: set the `RECRUIT_DESK_AUTH_URL` environment variable to it when running `cargo tauri build` (there is no screen in the app to type it, so people never see it). An installer built without it has no sign-in.
6. **Create the first company:** `python operator.py create-company "Acme Corp" boss@acme.com` and hand the printed code to that person.

`operator.py list-companies` and `list-people` show what exists.

## Checking it

`verify.py` drives the whole flow over real HTTP against a branch (122 checks: company creation and the unique name, the invitation code and who can use it, invitations and roles, isolation between companies, the last record and every rule on it, removal cutting access, sessions, and the search history: own-only visibility, admin sees all, stamping from the sign-in, linking, validation, removal rights, and the allowance: one pool shared by people, the common window, locking and lifting, admin-only changes, validation, the clock; and the workbook rows: own-or-admin visibility, stamping, one snapshot, size limit, no updates). It makes its own accounts with random names, so it can run again on the same branch. **Never point it at production.**

```
AUTH_URL=https://<ep>.neonauth.<region>.aws.neon.tech/neondb/auth API_URL=https://<ep>.apirest.<region>.aws.neon.tech/neondb/rest/v1 \
ORIGIN=https://recruitdesk.example DATABASE_URL_UNPOOLED=postgresql://… uv run --with "psycopg[binary]" python verify.py
```

The app's own live test (`cargo test --lib live_ -- --ignored`, see `desktop-app/src-tauri/src/auth/tests.rs`) runs the same flow through the app's Rust code.

## What to know

- **The rules sit on tables Neon owns** (`neon_auth.organization`, `member`, `invitation`, `session`). A Neon Auth update could change those tables; re-run `verify.py` on a branch after any Neon Auth change.
- **Open sign-up** means anyone who extracts the connection code from the installer (or finds the Neon addresses) can create an account that sees nothing. The code is not a secret in itself (two addresses and a name), but do not post it publicly. Neon throttles sign-up and sign-in per address (HTTP 429); the app says "Too many attempts" and the scripts wait and retry.
- **A short propagation delay** (a second or so) can exist between Neon Auth and the Data API right after someone joins or is removed; the app retries on its next check.
- **Invitations last 2 days** (Neon Auth's default). An expired code is not listed; invite the address again. The operator's first-admin code lasts 14 days.
- **Password reset** uses Neon Auth's emailed link (`request-password-reset`) through Neon's shared sender, which is rate limited. Delivery to real inboxes was not tested here; there is no admin-made reset link any more.
- **Offline allowance:** the app keeps working for up to 7 days after its last successful check while Neon cannot be reached, kept on that computer. It is a convenience limit, not tamper-proof; the data itself is protected by the database whatever the app says.
- **Company data of a removed company's people** is not touched on their computers.
