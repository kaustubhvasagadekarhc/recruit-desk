# Real-window UI test

Drives the **real app window** (WebKitGTK, the Linux build) with its real Rust backend and a throwaway Neon branch. No window system tricks: the
app is started with `WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9334`, which makes WebKit serve its remote inspector, and `wk.py` talks to that
(Target.sendMessageToTarget, then Runtime.evaluate) to click, type and read the page. Nothing is typed into the desktop, so it cannot hit
another window.

1. A throwaway Neon branch of the production project with Neon Auth and the Data API on, a trusted origin `https://recruitdesk.example`,
   `operator.py setup`, `operator.py create-company "<unique name>" <owner email>` (the name must be new each run) and
   `operator.py connection-code <auth url> <api url> https://recruitdesk.example` (see neon-backend/README.md).
2. An isolated home: `env -i HOME=<tmp>/home XDG_DATA_HOME=<tmp>/data XDG_CONFIG_HOME=<tmp>/config XDG_CACHE_HOME=<tmp>/cache
   CLAUDE_CONFIG_DIR=<tmp>/config/claude ...` run under `dbus-run-session`, with `<tmp>/data/in.humancloud.recruitdesk/settings.json`
   pointing `agents_root` at a copy of `src-tauri/windows/agents-bundle`, and `RECRUIT_DESK_AUTH_URL=<connection code>`.
3. `uv run --with websockets python ui_e2e.py <owner invite code> <owner email> <member email> <password> <tmp>/home/Documents/RecruitDesk-Data
   <tmp>/agents/company-agent`

It signs the owner up with the invitation code, checks Settings (data folder, allowance card, live line, update check), has the company
assistant's own script write results and checks the table shows them from the data folder, invites a member, reads the Logs screen, signs the
member up and checks what a user can and cannot see, signs the owner back in, saves the member's own cap and window, and checks the logs and
History. A window opens briefly on the desktop while it runs. It does not run an assistant through Claude Code (tokens, Exa key).
