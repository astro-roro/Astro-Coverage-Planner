#!/bin/bash
# Signs ACP.app with a Developer ID Application certificate, notarises it with
# Apple, staples the ticket and writes the release zip. Spec section 4.3.
#
#   packaging/macos/sign.sh --zip dist/ACP-macos-arm64-UNSIGNED.zip --out dist
#
# Reads these from the environment and nothing else (GitHub secrets in CI):
#   MACOS_CERT_P12_BASE64   the Developer ID Application certificate and its private key (.p12), base64
#   MACOS_CERT_PASSWORD     the password chosen when the .p12 was exported
#   APPLE_TEAM_ID           the 10-character team id
#   APPLE_ID                the Apple ID that owns the developer account
#   APPLE_APP_PASSWORD      an app-specific password for that Apple ID
#
# If any of the five is empty, this script prints one clear line, writes nothing and
# exits 0, so a fork or a repository that has not added the secrets still passes. It
# sets "signed=false" in $GITHUB_OUTPUT then; after a full run it sets "signed=true".
#
# No command here prints a secret and none runs with trace mode on. Decoded files go
# under a private folder (mode 700) that an EXIT trap deletes, with the keychain.
set -euo pipefail
set +x

ZIP=
OUT=dist
while [ $# -gt 0 ]; do
    case "$1" in
        --zip) ZIP=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$ZIP" ] || { echo "--zip is required" >&2; exit 2; }

REPO=$(cd "$(dirname "$0")/../.." && pwd)
HERE="$REPO/packaging/macos"
emit() { [ -n "${GITHUB_OUTPUT:-}" ] && echo "$1" >> "$GITHUB_OUTPUT" || true; }
step() { printf '\n==> %s\n' "$1"; }

missing=()
for name in MACOS_CERT_P12_BASE64 MACOS_CERT_PASSWORD APPLE_TEAM_ID APPLE_ID APPLE_APP_PASSWORD; do
    [ -n "${!name:-}" ] || missing+=("$name")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "SKIPPING signing and notarising: these secrets are not set: ${missing[*]}. The build stays unsigned and will not be released. See packaging/README.md, 'Mac signing secrets'."
    emit "signed=false"
    exit 0
fi

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
PRIVATE=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/acp-sign.XXXXXXXX")
chmod 700 "$PRIVATE"
KEYCHAIN="$PRIVATE/acp-signing.keychain-db"
ORIGINAL_KEYCHAINS=$(security list-keychains -d user | tr -d '"' | tr '\n' ' ')

cleanup() {
    # shellcheck disable=SC2086
    security list-keychains -d user -s $ORIGINAL_KEYCHAINS >/dev/null 2>&1 || true
    security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
    rm -rf "$PRIVATE"
}
trap cleanup EXIT

# --- 1. Unpack and import the certificate --------------------------------------------
step "Unpacking the unsigned build"
WORK="$PRIVATE/work"
mkdir -p "$WORK"
ditto -x -k "$ZIP" "$WORK"
STAGE="$WORK/ACP"
APP="$STAGE/ACP.app"
[ -d "$APP" ] || { echo "$ZIP has no ACP/ACP.app" >&2; exit 1; }
PY="$APP/Contents/Resources/python/bin/python3"

step "Importing the Developer ID certificate into a temporary keychain"
KEYCHAIN_PASSWORD=$(uuidgen)
P12="$PRIVATE/cert.p12"
( umask 077; printf '%s' "$MACOS_CERT_P12_BASE64" | base64 --decode > "$P12" )
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security import "$P12" -k "$KEYCHAIN" -P "$MACOS_CERT_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security
rm -f "$P12"
# Without the partition list codesign can wait on an access prompt nobody answers.
security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
# shellcheck disable=SC2086
security list-keychains -d user -s "$KEYCHAIN" $ORIGINAL_KEYCHAINS

# The identity is found by team id, so no separate secret holds its name. The hash
# names exactly one certificate even if two share a display name.
IDENTITY=$(security find-identity -v -p codesigning "$KEYCHAIN" \
    | grep "Developer ID Application" | grep "($APPLE_TEAM_ID)" | head -n 1 \
    | sed -n 's/^ *[0-9]*) *\([0-9A-F]\{40\}\) .*/\1/p')
if [ -z "$IDENTITY" ]; then
    echo "No 'Developer ID Application' identity for team $APPLE_TEAM_ID in the imported certificate. Check that the .p12 holds that certificate and its private key, and that APPLE_TEAM_ID matches." >&2
    exit 1
fi
echo "Signing identity found (team $APPLE_TEAM_ID)."

# --- 2. Sign every Mach-O file, deepest first, then the app ------------------------------
step "Signing every Mach-O file inside the app"
COUNT=0
while IFS= read -r file; do
    # The Python executable and the stub take the entitlements. Libraries ignore them.
    case "$file" in
        */Contents/Resources/python/bin/python3.12|*/Contents/MacOS/ACP)
            codesign --force --options runtime --timestamp --entitlements "$HERE/entitlements.plist" \
                --sign "$IDENTITY" --keychain "$KEYCHAIN" "$file" ;;
        *)
            codesign --force --options runtime --timestamp --sign "$IDENTITY" --keychain "$KEYCHAIN" "$file" ;;
    esac
    COUNT=$((COUNT + 1))
done < <("$PY" -I "$HERE/macho.py" list "$APP")
echo "Signed $COUNT files."

step "Signing ACP.app"
codesign --force --options runtime --timestamp --entitlements "$HERE/entitlements.plist" \
    --sign "$IDENTITY" --keychain "$KEYCHAIN" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# --- 3. Notarise ------------------------------------------------------------------------------
step "Notarising (this waits for Apple, usually a few minutes)"
SUBMIT_ZIP="$PRIVATE/ACP-notarise.zip"
ditto -c -k --keepParent "$APP" "$SUBMIT_ZIP"
RESULT="$PRIVATE/notary-result.json"
if ! xcrun notarytool submit "$SUBMIT_ZIP" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait --timeout 30m --output-format json > "$RESULT"; then
    echo "notarytool submit failed:" >&2
    cat "$RESULT" >&2 || true
fi
STATUS=$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$RESULT" 2>/dev/null || true)
SUBMISSION=$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "$RESULT" 2>/dev/null || true)
echo "Notarisation status: ${STATUS:-unknown}"
if [ "$STATUS" != "Accepted" ]; then
    if [ -n "$SUBMISSION" ]; then
        echo "Apple's log for submission $SUBMISSION (it holds no secrets):" >&2
        xcrun notarytool log "$SUBMISSION" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
            --password "$APPLE_APP_PASSWORD" >&2 || true
    fi
    exit 1
fi

# --- 4. Staple and zip ---------------------------------------------------------------------------
step "Stapling the ticket"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
codesign --verify --deep --strict "$APP"

step "Writing the release zip"
FINAL="$OUT/ACP-macos-arm64.zip"
rm -f "$FINAL"
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$FINAL"
HASH=$(shasum -a 256 "$FINAL" | cut -d' ' -f1)
printf '%s  %s\n' "$HASH" "ACP-macos-arm64.zip" > "$OUT/SHA256SUMS-macos.txt"
echo "Wrote $FINAL"
emit "signed=true"
