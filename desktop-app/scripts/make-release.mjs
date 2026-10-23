// Author: kaustubh.s.v
// Collect what a GitHub release needs after a signed build:
//   node scripts/make-release.mjs [--notes "text"] [--repo owner/name]
// Reads the version from package.json, finds the signed Windows installer (and its .sig) and the Linux package, copies them under
// desktop-app/release/ with names that survive GitHub's file naming (no spaces) and writes latest.json, which installed copies read to
// learn about the update. Nothing is uploaded here.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
const conf = JSON.parse(fs.readFileSync(path.join(root, "src-tauri", "tauri.conf.json"), "utf8"));
const endpoint = conf.plugins?.updater?.endpoints?.[0] ?? "";
const repo = arg("--repo", (/github\.com\/([^/]+\/[^/]+)\//.exec(endpoint) ?? [])[1]);
if (!repo) throw new Error("No repository: pass --repo owner/name or set the updater endpoint in tauri.conf.json");
const notes = arg("--notes", "");
// Every release needs a real description (what changed, how to install, known limits); latest.json carries it to the updater.
if (notes.trim().length < 80) throw new Error("Give the release a real description: --notes \"$(cat release-notes/<version>.md)\" (at least 80 characters).");
const requireSigned = process.argv.includes("--require-signed");

const nsis = path.join(root, "src-tauri", "target", "x86_64-pc-windows-msvc", "release", "bundle", "nsis");
const exeSrc = path.join(nsis, `Recruit Desk_${version}_x64-setup.exe`);
for (const f of [exeSrc, `${exeSrc}.sig`]) if (!fs.existsSync(f)) throw new Error(`Missing ${f}. Build with TAURI_SIGNING_PRIVATE_KEY set (see README, "Releasing an update").`);

// --require-signed: refuse to publish an installer that carries no Windows (Authenticode) signature. It checks that a signature is there; whether
// Windows trusts the certificate is up to the certificate (see SIGNING.md).
if (requireSigned) {
  const tmp = path.join(fs.mkdtempSync(path.join(root, ".sigcheck-")), "x.sig");
  try { execFileSync("osslsigncode", ["extract-signature", "-in", exeSrc, "-out", tmp], { stdio: "ignore" }); }
  catch { fs.rmSync(path.dirname(tmp), { recursive: true, force: true }); throw new Error("The installer has no Windows signature (or osslsigncode is missing). Sign it first, see SIGNING.md."); }
  fs.rmSync(path.dirname(tmp), { recursive: true, force: true });
  console.log("installer carries a Windows signature");
}

const out = path.join(root, "release");
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
const exeName = `Recruit-Desk_${version}_x64-setup.exe`;
fs.copyFileSync(exeSrc, path.join(out, exeName));
fs.copyFileSync(`${exeSrc}.sig`, path.join(out, `${exeName}.sig`));
const deb = path.join(root, "src-tauri", "target", "release", "bundle", "deb", `Recruit Desk_${version}_amd64.deb`);
if (fs.existsSync(deb)) fs.copyFileSync(deb, path.join(out, `Recruit-Desk_${version}_amd64.deb`));

const latest = {
  version,
  notes,
  pub_date: new Date().toISOString(),
  platforms: {
    "windows-x86_64": {
      signature: fs.readFileSync(`${exeSrc}.sig`, "utf8").trim(),
      url: `https://github.com/${repo}/releases/download/v${version}/${exeName}`,
    },
  },
};
fs.writeFileSync(path.join(out, "latest.json"), JSON.stringify(latest, null, 2) + "\n");
console.log(`release/ ready for v${version} (${repo}):`);
for (const f of fs.readdirSync(out)) console.log("  ", f, fs.statSync(path.join(out, f)).size, "bytes");
console.log(`Upload with: gh release create v${version} release/* --repo ${repo} --title "Recruit Desk ${version}" --notes "..."`);
