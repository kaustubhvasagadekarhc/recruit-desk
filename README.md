# Recruit Desk

Three AI research assistants for recruiters, and a desktop app that runs them:

- **Find candidates** (`candidate-agent/`): finds and scores candidates for a job description from public sources.
- **Company jobs** (`company-agent/`): checks a company's open jobs.
- **Accounts** (`account-agent/`): researches a company as an account (what it builds, offices, funding, leaders and open engineering roles, each with a source).
- **Recruit Desk** (`desktop-app/`): the Tauri 2 app (TypeScript, Rust) that runs the assistants through Claude Code, shows their Excel results, and handles sign-in, people and each person's token allowance.
- **Neon backend** (`neon-backend/`): the SQL and operator tools for the company database on Neon (accounts through Neon Auth, a Data API with row-level security). `auth-server/` is an earlier FastAPI version, kept for reference.

The assistants are independent Claude Code projects. Each keeps its own memory database and keys on the person's computer. The Excel results stay on the computer too (`Documents/RecruitDesk-Data`, one folder per assistant). The company database holds only people, the company, each person's allowance, the search history, the last record and admin-only logs.

## Run and build

- Each assistant: `cd <assistant> && claude`, then follow its `CLAUDE.md` and `README.md`. Copy `.env.example` to `.env` and add your own keys (Exa, and Apollo for accounts). Keys are never committed.
- The app: see `desktop-app/README.md` (prerequisites, build, install, sign-in, releasing an update).
- The database: see `neon-backend/README.md`. The app needs a connection code made with `neon-backend/operator.py connection-code`, set as `RECRUIT_DESK_AUTH_URL` when building. None is included here.
- Some `.claude/settings.json` files hold absolute paths from the author's machine; change them to yours.

## Licence

MIT, see `LICENSE`.
