# Recruit Desk

A small Windows desktop app (Tauri v2: Rust plus a plain HTML/TypeScript front end, no Electron) that
runs the two recruitment agents through the Claude Code CLI, caps the tokens each agent may use, and
shows each agent's Excel workbook inside the app.

- **Candidate Research**: paste a job description (or load a `.txt` / `.md` file) and press Run. The app
  runs `/scout <JD>` in `candidate-agent/`.
- **Company Intelligence**: enter a company name and an optional domain and press Run. The app runs
  `/company-scan <name> [domain]` in `company-agent/`.

## How a run works

The app starts `claude -p --output-format stream-json --verbose` with its working directory set to the
agent's folder and sends the slash command on stdin, so a pasted JD needs no quoting. It reads the
JSON stream and shows:

- the agent's checkpoint lines (`Plan · …`, `▶ Step 2/6 · …`, `✓ Step 2/6 · …`, `✗ …`, `  … sub-progress`),
  with the current step pinned above the log;
- tool activity (`→ Bash: uv run …`), which can be hidden with a checkbox;
- warnings, including every permission the agent was denied.

Claude Code keeps its normal permission mode. The app never passes `--dangerously-skip-permissions` or
any other permission flag; each agent's own `.claude/settings.json` allow list and PreToolUse hook decide
what may run. In `-p` mode a tool outside the allow list is denied (there is no one to ask), and the log
shows it as "Permission denied for …".

**Stop** kills the run and everything it started (the process group on Linux and macOS, `taskkill /T` on
Windows).

## Token cap

**Signed in to a company, the allowance is one cap per person, counted over all three assistants together (see "The allowance" under Sign-in below); the rules in this section describe the counting and the per-computer allowance used when sign-in is off.**

- One cap per agent. Default: 3,000,000 tokens each. (Signed in, an admin sets it for the whole company; with sign-in off it is the owner's, in the app's settings file.)
- Counted: input + output + cache-creation tokens, from the stream's `usage` fields (each message is
  counted once, at its final size) and the closing `result` event's `modelUsage` (all models, subagents
  included). Cache-read tokens are shown separately and are not counted.
- At 100% the section locks: Run is disabled and a banner gives the exact unlock time. The lock lasts
  4 hours from the moment the cap was hit; then the counter resets to 0 and the section unlocks.
- A run that crosses the cap mid-way is stopped as soon as the stream reports the crossing tokens. Tokens
  already spent by that last message still count, so usage can end slightly above the cap.
- Usage and the lock are saved in the app's data folder (`usage.json`; on Windows
  `%APPDATA%\in.humancloud.recruitdesk\`), so closing or restarting the app does not reset them. Time
  spent with the app closed counts toward the 4 hours.
- Setting the system clock backwards does not shorten a lock: the app keeps the latest time it has seen
  and only counts time that moves forward past it. Changing a cap does not lift a lock already running.

**The cap guards this app only.** Anyone can still open a terminal and run `claude` directly in the agent
folders; those runs are not counted and not blocked. Setting the clock forward also shortens a lock.

## Excel viewer

Each section shows that agent's workbook (`Candidate_Research.xlsx` or `Company_Hiring_Intelligence.xlsx`,
opened read-only from the agent folder and parsed in Rust with calamine): a sheet picker, a search box,
click a header to sort (numbers sort as numbers), drag a header edge (or focus it and use the arrow keys)
to resize a column, and `http(s)` links open in the system browser. Rows are virtualised, so a sheet
with 1,500 rows stays fast. Arrow keys move through the rows and Enter shows every field. The viewer
refreshes after each run and with Refresh (F5). The app never writes the workbooks.

## Keyboard

Ctrl+K quick actions · Ctrl+1/2/3 sections · Ctrl+Enter start the search · Ctrl+F search the table ·
F5 refresh the table · Esc closes dialogs and the break notice. The full tab order has visible focus,
and `prefers-reduced-motion` turns off every animation.

## Install on Windows

1. Run `Recruit-Desk-0.5.0-Setup.exe`. It is not code-signed, so Windows SmartScreen may warn: choose
   "More info", then "Run anyway".
2. After the Welcome page the installer checks four things, one page each, before it installs
   anything. On each page press the button to install what is missing, or run the command shown
   yourself, then press "Check again". Next stays disabled until the check passes.
   1. Node.js and npm (`node -v`, `npm -v`). "Install Node.js LTS" uses winget, or the official
      installer from nodejs.org when winget is missing; Windows may ask for permission for that
      installer only.
   2. Claude Code (`claude --version`). "Install Claude Code" runs
      `npm install -g @anthropic-ai/claude-code`, falling back to `irm https://claude.ai/install.ps1 | iex`.
   3. Sign in to Claude (`claude auth status`). Skipped when you are already signed in. Otherwise
      "Log in to Claude" opens a window running `claude`; sign in in the browser.
   4. uv (`uv --version`). "Install uv" runs the official installer from astral.sh.
3. **Exa search key (optional).** Leave it empty to skip; with a key the assistants can also search the web. Exa gives a free key: open
   https://dashboard.exa.ai, sign up, copy the API key and paste it. It is written to the `.env` of both
   assistants (only the `EXA_API_KEY` line; nothing else in `.env` changes) and is never logged. Or tick
   "Skip, add later": the app's setup screen then asks for it. On a reinstall, leave the box empty to
   keep the key you saved before.
4. Choose the app folder and install. Press Cancel on any page to leave without installing anything.
   The installer also copies the three assistants (candidate-agent, company-agent and account-agent, code and settings
   only) to `%USERPROFILE%\Documents\Recruit Desk\AI-AGENTS`, a folder you can write to, because the
   assistants keep their databases and keys there. It also creates `%USERPROFILE%\Documents\RecruitDesk-Data` with one folder
   per assistant (`Find candidates`, `Company jobs`, `Accounts`), where each assistant's Excel results are kept. It then tries
   `uv sync` in each assistant so the first run is fast; if that fails it is skipped and the setup screen offers "Prepare now".
5. On first start the app finds the assistants folder by itself and its setup screen checks the same
   things again, plus folder trust ("Trust both folders"), the Exa key and the prepared packages.

**Upgrades and uninstall.** Installing a newer version replaces the assistants' code files only. Your
`.env`, `memory.db`, the `.xlsx` workbooks and `runs/` are never in the installer, so they are never
overwritten or deleted. Uninstalling removes the app but leaves the assistants folder (with your databases and keys) and
the results folder `Documents\RecruitDesk-Data` in place and says so; delete them yourself if you no longer need them.

## Sign-in

Optional. When a **company connection code** is built into the installer (`RECRUIT_DESK_AUTH_URL` at compile time; the same variable at run time overrides it, for development), Recruit Desk shows a sign-in screen and refuses real work (searches, results, exports, saving keys) until someone is signed in to a company.

There is **no server of our own**. Recruit Desk talks straight to Neon: **Neon Auth** for accounts and sessions, and the **Neon Data API** for the one small piece of shared data. The rules (who may create a company, one company per person, what a stored record may hold) live in the database itself; see `../neon-backend/README.md` for how it is set up and checked.

- **The connection code** (`rd1.…`) holds two Neon web addresses and the app's origin name. It holds no secret. It is **built into the installer** and nobody types or sees it: there is no screen to change it, so a person cannot point the app at another service. The operator makes it with `operator.py connection-code` and builds with `RECRUIT_DESK_AUTH_URL=<code> cargo tauri build …`. An installer built without one has no sign-in at all.
- **Companies.** Every account belongs to one company. The operator creates a company with a required, unique name (capitals, spacing, punctuation and accents do not make a new one) and gets an **invitation code** for its first admin. That person chooses *I have an invitation code* on the sign-in screen, uses exactly that email address, the code and a password of their own. Admins then invite the rest of the company. The company name is shown under your email in the sidebar, in Settings > Sign-in and on the People screen.
- **Invitations are codes, not links.** An admin creates an invitation for an email address and hands over the code (nothing is emailed). The person makes their account with that address, the code and their own password; only that address can use the code, once. A person who signed in but has no company yet is asked for a code. The waiting-invitations list can show a code again.
- **Sessions.** The session cookie is kept in the operating system's keychain: Windows Credential Manager, or the Secret Service on Linux. `auth.json` in the app's data folder holds only non-secret facts (the connection code, your email, role and company). Without a keychain the session lasts until the app closes.
- **Offline.** If Neon cannot be reached the app keeps working for up to 7 days after its last successful check, then asks you to connect and sign in again. That allowance is stored on this computer, so it is a convenience limit and not tamper-proof; the data itself is protected by the database whatever the app says. A person removed from a company loses access to the company's data at once and the app locks within a few minutes.
- **Forgot your password?** The sign-in screen's "Forgot my password" asks Neon to email a reset link; paste the link (or the code in it) and choose a new password. Neon's shared mail sender is rate limited and delivery to real inboxes has not been tested here. There is no admin-made reset link.
- This is an access gate, not tamper-proof protection: the workbooks and databases are ordinary files on the computer.
- Errors are plain sentences chosen by the app, never Neon's own text. Both addresses in the connection code must be https (plain http only for this computer).
- Candidate, company and account data stays on the person's computer, and so does any API key they save here (keys go only to the service they belong to). The one exception is the **search history** below: what was searched and a few fields of each result are stored in the company's database, where the person and the company's admins can see them.

### Where the Excel results are kept (on this computer only)

The three Excel files are **never sent to the company database**. The installer creates `Documents\RecruitDesk-Data` (the app creates it too, on first start, for copies the installer did not make it for) with one folder per assistant: `Find candidates`, `Company jobs` and `Accounts`, each holding that assistant's Excel file. The app tells each assistant where its file lives (`RECRUIT_DESK_WORKBOOK_DIR`; the accounts assistant also gets `RECRUIT_DESK_DATA_ROOT` to find the company file), the tables in the window read from there, "Open in Excel" opens from there, and Settings > "Where your results are kept" shows the folder and opens it. A workbook an older version left in an assistant's own folder is moved into its new folder once (never over a file already there). Run by hand without Recruit Desk, an assistant still keeps its file in its own folder. The assistants' databases (`memory.db`) and `.env` stay in the assistants folder. Uninstalling leaves the data folder in place. What is kept in the company database is people (accounts, the company, invitations), each person's allowance, the search history and the last record, never the workbooks.

### The allowance (each person's own cap and window, over all the assistants)

While someone is signed in to a company, the token cap is **per person and counts all three assistants together**: what Find candidates, Company jobs and Accounts spend on a person's computers adds up in that person's own count, their window opens with their first tokens counted in it, and when their cap is reached every assistant is locked for that person (and only that person) until their window ends. **Nothing is company-wide**: each person has their own cap and their own window length. A new person starts at 3,000,000 tokens per 4 hours. (Earlier versions shared one pool per assistant by the company, then one company cap; both were dropped on 2026-10-21 before they held any data.)

- **Who sets it.** Only the company's owner and admins: Settings > Allowance (visible only to admins) has one row per person with that person's cap in tokens (1,000 to 1,000,000,000), window in hours (0.25 to 24), usage, Save and "Reset count"; and "Reset everyone's count". Everyone else sees their own total in the meters at the top of each assistant ("Your allowance used"); every assistant shows the same total.
- **Counted and clocked in the database.** Each person's cap, window, count and lock live in the company database and use the database's clock, so nobody's own computer clock matters (the old "set the clock back" protection is not needed). This computer adds its tokens to a local pending count at once, so a run is stopped on the spot when the cap is reached, and sends them every few seconds; it reads the person's row again about once a minute (and at every live update), so a change an admin makes shows within seconds. Pending tokens survive a restart (`allowance_pending.json`) and are sent when you are back online; while offline the last known count plus what you spent decides.
- **Who spent what.** The database keeps a log of each person's counted tokens with the assistant (`allowance_log`): a person can read their own lines, admins everyone's. The app does not show it; `operator.py purge-history <days>` deletes old lines.
- **Not tamper-proof, as before.** It guards this app only: anyone can still run `claude` in a terminal, and a modified app can ignore the lock. The database refuses amounts above 5,000,000 tokens per call and negative ones, and only admins can change the cap or reset.
- **Sign-in off, or the pool not read yet:** the old per-computer allowance (`usage.json`) applies, exactly as described above. The person's count takes charge only after it has been read successfully once (so a database without the allowance, or no connection at start, never locks anyone with a count made up from defaults).

### Search history (kept in the company database)

Every search a person runs is stored in the company's database, with a fixed list of fields of each result row. The database decides who sees what: **a person sees only their own searches and results; the company's owner and admins see everyone's** (the History tab, Ctrl 6, for everyone signed in; admins also get a Person column). There is no switch for it: it is how the app works while signed in. The three main views (Find candidates, Company jobs, Accounts) still read each person's own Excel workbooks on their own computer; only History reads from the database.

- **What is stored.** The search itself: a job description, a company and domain, or a list of companies (email addresses and phone numbers in that text are replaced by "[removed]" first), when it started and how it ended. And every NEW result row: candidate: name, title, company, location, match score, profile link, job title, date found; company jobs: job title, company, location, status, source, posted and discovered dates, listing link; accounts: company, domain, product type, HQ, funding, confidence, last checked. **Never** an email, phone, evidence text or notes: the database refuses a row that has them. The row's key is stored only as a hash.
- **How it gets there.** Each assistant's upsert appends every NEW row to `new_rows.jsonl` in its folder (a plain local file, never packed into the installer). The app notes each search when it starts (`searches_pending.json` in its data folder), then, while signed in, stores searches and rows, links each row to the search that was running when it was created, and removes what it stored from the queue. Offline or signed out, everything simply waits.
- **What each person sees.** History lists their searches (newest first) with what was searched, status and number of results; "Show results" lists the rows; "Remove" deletes a search and its rows from the database (a person's own, or any as an admin). Admins see the same list for the whole company.
- **Erasure.** `memory.py forget` in the candidate assistant removes that person's queued rows and leaves a tombstone; the next sync deletes every stored copy of that person's rows in the company database (by hash). An admin can also remove a whole search. Backups of workbooks on a person's own computer are not touched.
- **Retention.** The operator can delete stored history older than N days: `operator.py purge-history 180`. Nothing deletes it automatically yet.
- **What this changes about privacy.** Before this, only the single latest record per assistant left the computer. Now candidate rows (names, titles, employers, locations, profile links) are stored in Neon too, visible to the person who searched and to the company's admins. Treat it as personal data: a retention period and the erasure path above matter.

### Logs (kept in the company database, admins only)

The app writes short log lines about itself and stores them in the company database: events and errors (start-up with the app version and the operating system, sync problems), how each run went (its finished and failed step lines, how it ended, tokens used) and sign-ins and sign-outs. A line is queued in `logs_pending.jsonl` at once and sent in batches while someone is signed in, so nothing is lost offline (the newest 500 are kept). Emails and phone numbers are stripped and long text is cut; search text, candidates and companies are never written, though a step line may mention a count. **Only owners and admins can read the logs**, in the Logs screen (Ctrl 7): the newest 300 lines, filtered by kind, level and person. Lines older than 30 days are removed. Failed sign-in attempts are not logged (there is no sign-in to write with). `operator.py purge-history <days>` also removes older lines.

### The last record (kept in the company database)

Everything is processed on the person's own computer. After an upsert that creates a new row, each assistant leaves a small plain file in its own folder, `last_record.json` (the last NEW row only: a fixed list of fields, the key as a hash, when it was created; no email, phone, evidence text or notes). The app reads it, checks it again and, while someone is signed in to a company, stores it in the company's database.

- One row per assistant per company; the **newer record wins** (an older one is refused with 409), the same record again changes nothing. The database enforces the field list, value lengths and that no value looks like an email address or a phone number.
- It syncs after each run, when someone signs in or the app starts, and every 5 minutes (which also retries anything that could not be sent). Offline or signed out, the file simply waits. Settings > Sign-in lists, per assistant, whether its latest record is stored, waiting or refused.
- The app checks the file again before sending: only the listed fields leave, long values are cut, and a damaged, oversized or foreign file is ignored.
- Erasure: `memory.py forget` in the candidate agent removes `last_record.json` when it is about the forgotten person. At the next sync the app sees the file is gone and removes the stored copy, but only if the database still holds that same record (the delete matches the record's hash): a newer record from another computer stays.
- Admins see what is stored under People > "Last records in the company database", with who stored each.
- `last_record.json` and the app's `records_sent.json` are data: never packed into the installer.

### People and access (admins)

An admin sees a **People** tab (Ctrl 5). It lists everyone in their company and every invitation still waiting, and lets the admin:

- **Invite someone** by email as a user or an admin. The result is an **invitation code** with a Copy button. A waiting invitation can show its code again or be withdrawn (the code stops working at once).
- **Make admin or make user**, and **remove** a person from the company (their access to the company's data ends at once; their own files are not touched; they can be invited again).
- Nothing is offered on your own row or on the company owner, and Neon Auth refuses it anyway: the only owner cannot be removed or demoted.

An admin only sees and manages people in their own company. The window never holds a cookie or token: every call goes through the backend, which sends the session to Neon Auth and checks the role first. Invitation codes are secrets until used: they are cleared from the page when you leave the screen, close the box or lock the app.

## Settings and prerequisites

- **AI-AGENTS folder**: detected next to the app (the app's folder and its parents, directly or in an
  `AI-AGENTS` child), then where the installer put it (the path in `agents-root.txt` next to the exe,
  else `%USERPROFILE%\Documents\Recruit Desk\AI-AGENTS`). If it is not found, enter it in Settings.
- **claude executable**: leave empty to auto-detect `claude` / `claude.exe` / `claude.cmd` on PATH and in
  the usual install folders (`~/.local/bin`, `%APPDATA%\npm`).
- At startup the app checks that `claude` runs, that `claude auth status` reports a login, that `uv` is
  installed (the agents run their Python scripts with it) and that the agents folder is valid. Anything
  missing shows a banner with the command that fixes it.

## Build

Requirements: Rust (with `x86_64-pc-windows-msvc` for Windows), Node, `cargo install tauri-cli cargo-xwin`,
and for the Windows installer `makensis`, `clang`, `lld` and `llvm-rc`.

```bash
npm install                      # TypeScript only
npm run build                    # frontend + stage the assistants into src-tauri/windows/agents-bundle/
                                 # (fails if a .env, *.db, *.xlsx, backup or runs/ would be bundled)
npm run test:bundle              # self-test of the copy rules
cd src-tauri
cargo test --lib                 # cap, lock, stream parser, Excel and persistence tests
cargo tauri build                # Linux: target/release/bundle/deb/
cargo tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc
                                 # Windows NSIS installer: target/x86_64-pc-windows-msvc/release/bundle/nsis/
cd .. && npm run check-installer # list what the built installer packs; fails on any excluded file
```

`src-tauri/src/bin/headless.rs` runs the same backend without a window, for testing:

```bash
cargo run --bin headless -- status
cargo run --bin headless -- sheet company "Humancloud Technologies"
cargo run --bin headless -- run company Humancloud humancloud.co.in
```

It keeps its own usage file (`RECRUIT_DESK_DATA`, default `./headless-data`), separate from the app's.

## Live updates from the database

The company database cannot push changes to the app (the Data API has no live channel), so while the window is open and someone is signed in, the app asks for changes every 10 seconds (and at once when the window is shown or focused; a hidden window asks nothing). It refreshes the person's allowance count (so a lock, a new cap or a reset made by an admin shows within seconds) and the screen that is open: History, People, the admin's usage table. The Excel results are not part of it: they are local. Settings shows when it last asked. Each tick is a handful of small requests.

## Updates for installed copies

Windows copies check GitHub for a newer signed release at start-up and every six hours, and from About > "Check for updates". A banner offers "Update and restart"; nothing downloads without a click, nothing installs while an assistant is running, and a download is installed only if it is signed with the key whose public half is in `src-tauri/tauri.conf.json` (`plugins.updater.pubkey`). The installer runs in passive mode, closes the app and starts the new version; settings, sign-in, keys and workbooks are not touched. Linux copies are updated by installing the newer `.deb`. An installed version higher than the release (for example a private 0.15.0 against a public 0.1.0) is never "updated" downwards.

The update key pair is made once with `cargo tauri signer generate -w ~/.tauri/recruit-desk-updater.key` (the private file stays on the release machine and is never committed; losing it means copies already installed can no longer be updated and must be reinstalled by hand).

### Releasing an update

1. Raise the version in `package.json`, `src-tauri/tauri.conf.json` and `src-tauri/Cargo.toml`, and commit.
2. Build signed, with the production connection code inside (see `neon-backend/README.md` for the code):
   `export RECRUIT_DESK_AUTH_URL=<code> TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/recruit-desk-updater.key)" TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""`
   then `cargo tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc` and `cargo tauri build --bundles deb` in `src-tauri/`.
3. `node scripts/make-release.mjs --notes "What changed."` writes `release/` (installer, its `.sig`, the `.deb`, `latest.json`).
4. `git tag -a vX.Y.Z -m "..."`, push the tag, then `gh release create vX.Y.Z release/* --title "Recruit Desk X.Y.Z" --notes "What changed."`. The release must not be a draft or a pre-release: installed copies read `https://github.com/kaustubhvasagadekarhc/recruit-desk/releases/latest/download/latest.json`, which works for a public repository only.
5. Apply any new `neon-backend/setup.sql` first (`operator.py setup`), so updated copies find the database ready.

## Files

```
src/index.html, styles.css, main.ts   front end (compiled by tsc into dist/)
src/plain.ts                           plain-language step results (tests: npm run test:ui)
scripts/ui-harness/                    screenshot harness with a fake backend
UI-AUDIT.md                            UI audit, 2026 research and what 0.5.0 fixed
src-tauri/src/cap.rs                   cap and 4-hour lock (unit tests)
src-tauri/src/stream.rs                stream-json parser: checkpoints, tool notes, denials, tokens
src-tauri/src/runner.rs                spawns and stops claude
src-tauri/src/core.rs                  shared state, persistence, run lifecycle
src-tauri/src/excel.rs                 read-only workbook reader (calamine)
src-tauri/src/settings.rs              settings, path detection, prerequisite checks
src-tauri/src/lib.rs                   Tauri commands
BUILD-LOG.md                           how it was built and tested
shots/                                 screenshots
```

## Known limits

- The Windows build is cross-compiled on Linux and has not been run on a real Windows machine.
- The installer pages were only compiled and statically tested on Linux (no wine); they have not
  been run on real Windows yet.
- The installer is not code-signed, so Windows SmartScreen warns on first run.
- On Windows a run started from `claude.cmd` (npm install) goes through `cmd.exe`; the native
  `claude.exe` install is preferred.
