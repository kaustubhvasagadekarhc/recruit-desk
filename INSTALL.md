# Installing Recruit Desk

Recruit Desk is a desktop app that runs three AI research assistants (Find candidates, Company jobs and Accounts) through Claude Code. This guide covers installing it on Windows and Linux, what happens the first time you start it, and the one-time setup a company does for the sign-in database.

## What you need

- A **Claude account** that can use Claude Code. The assistants run through it and use your Claude allowance.
- **Node.js and npm**, **Claude Code** and **uv** (a Python tool runner). The Windows installer checks for these and installs them for you. On Linux you install them yourself.
- An **invitation code** from your company's admin, or, if you are the first admin, the code printed by the company setup (see "Setting up the company" below).
- Optional: an **Exa key** (web search; Exa gives a free key at https://dashboard.exa.ai) and, for account research only, an **Apollo key** (paid). Each person uses their own keys, and they stay on that person's computer.

## Windows

Run `Recruit-Desk-<version>-Setup.exe`. It installs for the current user only and needs no administrator rights. The installer is not code-signed, so Windows SmartScreen may warn the first time: choose "More info", then "Run anyway".

**If your browser or Windows warns about the file.** The installer is not code-signed yet, so you may see "... isn't commonly downloaded" (the browser) and then "Windows protected your PC" (SmartScreen). This is a reputation warning for new, unsigned files, not a finding that the file is harmful. To continue:
1. In the browser's download list choose **Keep** (in Edge, "..." then Keep, then "Keep anyway"; in Chrome, the arrow then Keep).
2. Open the file. On the blue SmartScreen screen choose **More info**, then **Run anyway**.
3. If you want to check the file first, compare its SHA-256 with the one in the release notes of that version (`Get-FileHash .\Recruit-Desk_<version>_x64-setup.exe -Algorithm SHA256` in PowerShell), and download only from this repository's Releases page.

The wizard goes through these pages:

1. **Welcome** and the licence.
2. **Prerequisite checks**, one page each. Nothing is installed until all four pass. Each page has a button that installs what is missing, then you press "Check again":
   - Node.js and npm.
   - Claude Code.
   - Your Claude sign-in. If you are not signed in, "Log in to Claude" opens a window running `claude`; sign in in the browser.
   - uv.
3. **Exa key** (optional). Paste a key or tick "Skip, add later". It is written only to the assistants' `.env` files and is never logged.
4. **Apollo key** (optional, paid, account research only). Paste a key or skip.
5. **Install folder** and Start Menu folder, then the install.

What the installer puts on your computer:

| What | Where |
|---|---|
| The app | The folder you chose on the install page |
| The three assistants (code and settings only) | `Documents\Recruit Desk\AI-AGENTS` |
| Your results folder | `Documents\RecruitDesk-Data`, with one folder per assistant: `Find candidates`, `Company jobs`, `Accounts`. Each assistant's Excel file is kept in its own folder. |

It then runs `uv sync` in each assistant so the first run is fast. If that fails it is skipped, and the app's setup screen offers "Prepare now".

No keys, databases, Excel files or job descriptions are ever packed into the installer.

## Linux

The `.deb` contains only the app. The assistants come from this repository, and you install the prerequisites yourself.

1. Install **Node.js and npm**, **Claude Code** (`npm install -g @anthropic-ai/claude-code`, then run `claude` once and sign in) and **uv** (https://docs.astral.sh/uv/).
2. Put the assistants where the app looks for them by default:
   ```
   mkdir -p "$HOME/Documents/Recruit Desk"
   git clone https://github.com/kaustubhvasagadekarhc/recruit-desk.git "$HOME/Documents/Recruit Desk/AI-AGENTS"
   ```
   If you keep them somewhere else, set that folder in the app under Settings.
3. In each assistant folder copy `.env.example` to `.env` and add your own keys (Exa, and Apollo for `account-agent`).
4. Install the app: `sudo apt install ./Recruit-Desk-<version>-linux-amd64.deb`. Using `apt` installs the libraries it depends on.
5. Start **Recruit Desk** from the application menu, or run `recruit-desk`. On first start it creates `~/Documents/RecruitDesk-Data` with the three assistant folders.

On Linux there is no in-app update: install the newer `.deb` over the old one.

## The first time you start it

1. **Setup check.** The app finds the assistants folder and checks Claude Code, folder trust, your keys and the prepared packages. Use the buttons on the setup screen to fix anything it lists ("Trust the agent folders" starts `claude` in each assistant folder so you can answer its trust question; close any open Claude Code windows first).
2. **Sign in.** Enter your email and password. If this is your first time, choose "I have an invitation code" and make your account with the code your admin gave you. There is nothing to configure: the company connection is built into the app.
3. **Allowance.** Every person has their own token allowance, counted over all three assistants together. A new person starts at 3,000,000 tokens per 4 hours. Admins change each person's cap and window in Settings.
4. **Run.** Pick an assistant, paste a job description or enter a company, and start. Results appear in the table and are saved to that assistant's Excel file in `RecruitDesk-Data`.

Admins also see **History** (everyone's searches), **People** (invite people, change roles, remove people) and **Logs** (what the app wrote about itself). A normal user sees only their own History.

## Where things are kept

- **On your computer only:** the Excel results (`Documents\RecruitDesk-Data`), each assistant's `memory.db` and `.env` (in the assistants folder), and the app's own settings. Your API keys never leave your computer.
- **In the company database (Neon):** accounts, the company, invitations, each person's allowance, the search history (what was searched, with contact details removed, and a few fields per result), the last record of each assistant, and the admin-only logs. The Excel files are never sent anywhere.

## Updating, repairing and uninstalling

- **Windows update:** the app checks for a newer signed release at start-up and every hour. At a quiet moment (no assistant running, nothing typed into a run form) it shows "Version X is available. Installing in 15 seconds", then installs and restarts by itself. Press "Later" to cancel, or switch it off in About ("Install updates automatically"). You can also press "Update and restart" at once, or run a newer installer by hand. It replaces only the app and the assistants' code, never your keys, databases, workbooks or runs.
- **Uninstall:** it removes the app and leaves the assistants folder and `RecruitDesk-Data` in place, and tells you where they are. Delete them yourself if you no longer want them.
- **If something looks stuck:** the app waits at most 8 seconds for the system keychain and then keeps the sign-in in memory for that session. If the sign-in ended, sign in again. If "Prepare now" is offered, press it. The setup screen shows what is missing.

## Setting up the company (done once, by whoever runs the database)

Sign-in, people and allowances need a Neon project. Full details are in [`neon-backend/README.md`](neon-backend/README.md); in short:

1. Create a Neon project, turn on **Neon Auth** and the **Data API** for the branch, and add `https://recruitdesk.example` as a trusted origin (the app sends that origin; it is a label, not a website).
2. Apply the rules to the database: `uv run --with "psycopg[binary]" python neon-backend/operator.py setup` with `DATABASE_URL_UNPOOLED` set to the direct connection string.
3. Create the company and its first admin: `operator.py create-company "Company name" admin@company.com`. It prints an invitation code for that address. The admin makes their account with it in the app and then invites everyone else from People.
4. Make the connection code people's apps use: `operator.py connection-code <auth url> <data api url>`.
5. Build the installers with that code inside: set `RECRUIT_DESK_AUTH_URL` to the code when you run `cargo tauri build` (see [`desktop-app/README.md`](desktop-app/README.md), "Build" and "Releasing an update"). A build without it starts without a sign-in screen, which is only meant for development.

Sign-up is invitation only: only the operator script can create a company, one person can belong to one company, and a company's data is invisible to other companies.

## Building it yourself

See [`desktop-app/README.md`](desktop-app/README.md): the app is Tauri 2 (TypeScript and Rust). The Windows installer is built from Linux with `cargo tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc`, and the Linux package with `cargo tauri build --bundles deb`. Signed updates need your own update key (see "Releasing an update" there).
