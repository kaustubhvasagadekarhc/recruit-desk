# UI screenshot harness

Runs the real front end (`dist/`) in Chromium with a fake `window.__TAURI__` backend (`mock.js`), so every screen and state can be captured without running an agent or touching `~/.claude.json`. It was used for `shots/ui26-before-*` and `shots/ui26-after-*`.

1. Copy the two workbooks somewhere temporary, then export them:
   `uv run --with openpyxl python -I export-fixtures.py <folder-with-xlsx> <work>/fixtures.json`.
   `fixtures.json` holds candidate data, so keep it out of the repo.
2. `npm run build`, then copy `dist/*.js` and `dist/styles.css` into `<work>/`, together with `mock.js`, `shoot.mjs` and `fixtures.json`. Write `dist/index.html` into `<work>/` with `<script src="mock.js"></script>` added before the stylesheet link.
3. `cd <work> && python3 -m http.server 8765 &`, then `node shoot.mjs ui26-after <repo>/desktop-app/shots 8765`. This needs the global `@playwright/test` install.

Scenarios are chosen with `?s=` (`setup-bad`, `idle`, `near`, `locked`, `empty`) plus `view`, `sheet` and `theme`. Running and finished states replay real `progress.log` lines through `window.__mock.line()` and `window.__mock.end()`.
