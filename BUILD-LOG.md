
## 0.4.1: Exa key optional (2026-10-15, god)

- Human request: keep the Exa search key optional.
- Setup screen (src/main.ts): the "Exa search key" check is optional. When it is unset, the screen shows it with a neutral marker and a key box. It no longer counts as "needs attention", no longer forces the setup screen at start-up, and no longer lights the Setup badge.
- Installer (src-tauri/windows/exa-page.nsh): the header and text say "optional". An empty box now continues without the confirmation pop-up.
- README install step 3 updated.
- Tests: npm run build OK, with the excluded-files check passing; cargo test --lib 40/40. The Windows installer and the .deb were rebuilt. Installer copy: AI-AGENTS/Recruit-Desk-0.4.1-Setup.exe.
