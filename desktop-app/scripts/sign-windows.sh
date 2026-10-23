#!/usr/bin/env bash
# Author: kaustubh.s.v
# Signs one Windows file for the Tauri build (tauri.conf.json: bundle.windows.signCommand calls this with the file). With nothing configured it
# does nothing, so a build without a certificate is unsigned exactly as before. Two ways to sign, set in the environment of the build:
#
#   A certificate file (.pfx or .p12):
#     RECRUIT_DESK_SIGN_PFX=/path/to/cert.pfx   RECRUIT_DESK_SIGN_PFX_PASSWORD=...   [RECRUIT_DESK_SIGN_TIMESTAMP=http://timestamp.digicert.com]
#   Any other signer (a cloud signing service, a hardware token, jsign ...): a command that signs the file it is given as "$1" in place:
#     RECRUIT_DESK_SIGN_COMMAND='jsign --storetype ... "$1"'
#
# If signing is configured and fails, the build fails: a release is never half signed. See SIGNING.md.
set -euo pipefail
file="${1:?usage: sign-windows.sh <file>}"

if [[ -n "${RECRUIT_DESK_SIGN_COMMAND:-}" ]]; then
  echo "sign-windows: signing $(basename "$file") with RECRUIT_DESK_SIGN_COMMAND"
  bash -c "$RECRUIT_DESK_SIGN_COMMAND" _ "$file"
  exit 0
fi

if [[ -z "${RECRUIT_DESK_SIGN_PFX:-}" ]]; then
  exit 0   # nothing configured: unsigned, as before
fi

command -v osslsigncode >/dev/null || { echo "sign-windows: osslsigncode is not installed (brew install osslsigncode, or apt install osslsigncode)" >&2; exit 1; }
[[ -f "$RECRUIT_DESK_SIGN_PFX" ]] || { echo "sign-windows: the certificate file $RECRUIT_DESK_SIGN_PFX does not exist" >&2; exit 1; }
stamp="${RECRUIT_DESK_SIGN_TIMESTAMP:-http://timestamp.digicert.com}"
tmp="${file}.signing.$$"   # a name that does not exist yet: osslsigncode will not overwrite a file
rm -f "$tmp"
trap 'rm -f "$tmp"' EXIT
echo "sign-windows: signing $(basename "$file") with the certificate file, timestamp $stamp"
osslsigncode sign -pkcs12 "$RECRUIT_DESK_SIGN_PFX" -pass "${RECRUIT_DESK_SIGN_PFX_PASSWORD:-}" \
  -n "Recruit Desk" -i "https://github.com/kaustubhvasagadekarhc/recruit-desk" \
  -h sha256 -ts "$stamp" -in "$file" -out "$tmp"
mv -f "$tmp" "$file"
trap - EXIT
