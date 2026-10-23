# Signing the Windows installer

Windows shows "Unknown publisher" and SmartScreen's "Windows protected your PC" (and browsers say "isn't commonly downloaded") for an installer that has no code signature. The fix is a **code-signing certificate** issued to a person or a company by an authority Windows trusts. The name on the certificate is the publisher people see. Nothing in the code can replace that, but the build is ready for it.

This is separate from the **update signature** (`~/.tauri/recruit-desk-updater.key`): that one only lets installed apps check that an update comes from you. Windows ignores it.

## What is already in the build

`src-tauri/tauri.conf.json` has `bundle.windows.signCommand`, which runs `scripts/sign-windows.sh` on every Windows file Tauri produces (the app and the installer). With nothing configured the script does nothing, so builds are unsigned as before. Configure one of these in the environment of `cargo tauri build`:

**A certificate file** (`.pfx` or `.p12`, for example from a certificate authority):

```
export RECRUIT_DESK_SIGN_PFX=/path/to/certificate.pfx
export RECRUIT_DESK_SIGN_PFX_PASSWORD='the file password'
# optional, default http://timestamp.digicert.com
export RECRUIT_DESK_SIGN_TIMESTAMP=http://timestamp.digicert.com
```

It needs `osslsigncode` (`brew install osslsigncode` or `apt install osslsigncode`). The timestamp matters: it keeps the signature valid after the certificate expires.

**Any other signer** (a cloud signing service, a hardware token, jsign): a command that signs the file it gets as `$1` in place:

```
export RECRUIT_DESK_SIGN_COMMAND='jsign --storetype ... "$1"'
```

If signing is configured and fails, the build fails, so a release is never half signed. To refuse to publish an unsigned installer, add `--require-signed` to `node scripts/make-release.mjs`.

The pipeline was tested with a throwaway self-signed certificate: the installer and the app inside it came out signed. Windows does not trust a self-signed certificate, so that test showed nothing about the warning itself.

## Where to get a certificate

| Route | Cost | Publisher shown | Notes |
|---|---|---|---|
| **SignPath Foundation** | free for qualifying open-source projects | "SignPath Foundation" | The project must be open source with an OSI licence (this one is MIT), public, and built from CI. The signing runs on SignPath's side from a GitHub Actions build, so the Windows installer would be built in a workflow, not on a laptop. Each signing request is approved. Check their current terms. |
| **Azure Artifact Signing** (formerly Trusted Signing) | about $10 a month | the verified name of the person or organisation | Organisations in the US, Canada, EU and UK; individuals in the US and Canada. The identity check takes some days. Works with `jsign` through `RECRUIT_DESK_SIGN_COMMAND`. |
| **A certificate from a certificate authority** | about $150 to $300 a year | the verified name on it | Since 2023 the private key must live on a hardware token or a cloud key service, so it is signed through that service's tool (`RECRUIT_DESK_SIGN_COMMAND`) more often than from a `.pfx` file. |

Whichever you pick, the first downloads may still show a SmartScreen warning: Windows also wants some history for a new signature. It fades as people install. Microsoft's own notes are at https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options and https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation.

## A ready application for SignPath Foundation

Apply through the form on the SignPath Foundation website. Paste something like this (change the contact details):

> **Project:** Recruit Desk, https://github.com/kaustubhvasagadekarhc/recruit-desk
> **What it is:** a desktop app (Tauri 2, Rust and TypeScript) that runs three AI research assistants for recruiters and shows their results, with sign-in, people and token allowances through Neon.
> **Licence:** MIT (`LICENSE` in the repository root).
> **Distribution:** a Windows installer (NSIS) published on the repository's GitHub Releases page; the app updates itself from there, checking its own update signature.
> **Build:** the installer is built with `cargo tauri build` from this repository; I can build it in GitHub Actions so SignPath signs the artifact of that workflow.
> **Why signing:** the unsigned installer shows "Unknown publisher" and SmartScreen and browser warnings, which keeps people from installing it.
> **Contact:** (your name and email)

## Once you have a certificate

1. Put the settings above in the environment of the release build (never in the repository).
2. Build: `cargo tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc`.
3. `node scripts/make-release.mjs --require-signed` and `gh release create ...` as in `README.md` ("Releasing an update").
4. Check on a Windows computer: right-click the installer, Properties, Digital Signatures.

## Release from GitHub Actions (for SignPath)

`.github/workflows/release.yml` builds the Windows installer and the Linux package on GitHub, sends the installer to SignPath, makes the update signature again over the signed file, runs `make-release.mjs --require-signed` and publishes the release. It runs when code is pushed to `main` (pushes that change only Markdown files, the licence or `.gitignore` are ignored) or when started by hand (a hand start builds and signs but does not publish). It publishes only when the version in `package.json` has no GitHub release yet, so raise the version (in `package.json`, `tauri.conf.json` and `Cargo.toml`) in the push that should ship; other pushes build and sign but publish nothing. It never runs on pull requests. The workflow has not been run yet; expect to fix small things on the first run.

Set these in the repository under Settings, Secrets and variables, Actions:

| Name | Kind | Value |
|---|---|---|
| `RECRUIT_DESK_AUTH_URL` | secret | the production connection code |
| `TAURI_SIGNING_PRIVATE_KEY` | secret | contents of the update private key file |
| `SIGNPATH_API_TOKEN` | secret | API token of a SignPath CI user |
| `SIGNPATH_ORGANIZATION_ID` | variable | from the SignPath organisation page |
| `SIGNPATH_PROJECT_SLUG` | variable | the project slug in SignPath |
| `SIGNPATH_SIGNING_POLICY_SLUG` | variable | for example `release-signing` |

Putting the connection code and the update key into GitHub secrets means GitHub holds them, and anyone who can push a tag to the repository can run the workflow. Keep write access to the repository small. SignPath also needs an artifact configuration that signs the installer `.exe` with Authenticode; their onboarding explains it. Only the installer is signed this way; the programs inside it are not signed separately.
