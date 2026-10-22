// Author: kaustubh.s.v
// Copies the static front-end files (HTML, CSS) next to the compiled TypeScript in dist/.
import { cpSync, mkdirSync } from "node:fs";
mkdirSync("dist", { recursive: true });
for (const f of ["index.html", "styles.css"]) cpSync(`src/${f}`, `dist/${f}`);
