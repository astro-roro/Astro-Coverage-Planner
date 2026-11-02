#!/bin/bash
# Builds ACP-macos-arm64-UNSIGNED.zip. Runs on macos-14 (Apple Silicon); see
# .github/workflows/release-macos.yml. Spec: docs/specs/windows-download-and-setup.md,
# sections 4.1 and 4.2.
#
#   packaging/macos/build.sh [--out dist] [--tag v1.0.0] [--work <folder>]
#
# Needs on PATH: curl, tar, clang, git, ditto, codesign, shasum, iconutil. Nothing else:
# the bundled Python installs its own dependencies, so the runner's Python is not used.
#
# Writes into --out: ACP-macos-arm64-UNSIGNED.zip and SHA256SUMS-macos-unsigned.txt.
# The app inside is signed ad hoc, which an arm64 Mac needs to run it at all. Signing
# with a Developer ID and notarising happen later, in packaging/macos/sign.sh.
set -euo pipefail

OUT=dist
TAG=
WORK=
MAX_ZIP_MB=400
MAX_PATH_CHARS=400
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT=$2; shift 2 ;;
        --tag) TAG=$2; shift 2 ;;
        --work) WORK=$2; shift 2 ;;
        --max-zip-mb) MAX_ZIP_MB=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }

REPO=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
if [ -z "$WORK" ]; then
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/acp-build.XXXXXXXX")
else
    mkdir -p "$WORK"
    WORK=$(cd "$WORK" && pwd)
fi
STAGE="$WORK/ACP"
APP="$STAGE/ACP.app"
CONTENTS="$APP/Contents"
RES="$CONTENTS/Resources"
PYDIR="$RES/python"
APPDIR="$RES/app"
PY="$PYDIR/bin/python3"
mkdir -p "$CONTENTS/MacOS" "$RES"

pin() { sed -n "s/^$1=\(.*\)$/\1/p" "$REPO/packaging/pins.txt" | head -n 1 | tr -d '[:space:]'; }
PY_VERSION=$(pin PYTHON_MACOS_VERSION)
PBS_TAG=$(pin PYTHON_MACOS_STANDALONE_TAG)
PBS_SHA=$(pin PYTHON_MACOS_ARM64_SHA256)
[ -n "$PY_VERSION" ] && [ -n "$PBS_TAG" ] && [ -n "$PBS_SHA" ] || { echo "packaging/pins.txt is missing a macOS pin" >&2; exit 1; }

VERSION=$(sed -n 's/^VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO/app.py" | head -n 1)
[ -n "$VERSION" ] || { echo "could not read VERSION from app.py" >&2; exit 1; }

# --- 1. The relocatable Python ----------------------------------------------------
step "Downloading Python $PY_VERSION (python-build-standalone $PBS_TAG, arm64)"
ARCHIVE="$WORK/python.tar.gz"
# The + in the file name is %2B in the URL.
URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-${PY_VERSION}%2B${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"
curl -fsSL --retry 3 --retry-delay 5 -o "$ARCHIVE" "$URL"
ACTUAL=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
if [ "$ACTUAL" != "$PBS_SHA" ]; then
    echo "The Python download does not match packaging/pins.txt. Expected $PBS_SHA, got $ACTUAL." >&2
    exit 1
fi
tar -xzf "$ARCHIVE" -C "$RES"          # creates Resources/python
[ -x "$PY" ] || { echo "the Python archive has no bin/python3" >&2; exit 1; }
rm "$ARCHIVE"

# --- 2. Dependencies, into the bundled Python's own site-packages ----------------------
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1

step "Building wheels for packages that publish only a source package"
# xisf is pure Python with no wheel on PyPI. Build it with the bundled Python so the
# install below can stay wheels-only for everything else.
WHEELS="$WORK/wheels"
mkdir -p "$WHEELS"
while IFS= read -r line; do
    req=$(printf '%s' "${line%%#*}" | tr -d '[:space:]')
    [ -n "$req" ] || continue
    "$PY" -m pip wheel --no-deps -c "$REPO/packaging/constraints.txt" -w "$WHEELS" "$req"
done < "$REPO/packaging/sdist-only.txt"
ls "$WHEELS"/*.whl >/dev/null

step "Installing dependencies (wheels only)"
"$PY" -m pip install --only-binary=:all: --find-links "$WHEELS" \
    -c "$REPO/packaging/constraints.txt" \
    -r "$REPO/requirements.txt" -r "$REPO/requirements-packaged.txt"
SITE=$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

# --- 3. Trim ----------------------------------------------------------------------------
step "Trimming scripts, headers, test folders and bytecode"
# pip leaves launcher scripts in bin/ whose first line is the build machine's absolute
# path to Python. They work nowhere else, and a signed bundle should not carry them.
find "$PYDIR/bin" -mindepth 1 -maxdepth 1 ! -name 'python' ! -name 'python3' ! -name 'python3.12' -exec rm -rf {} +
rm -rf "$PYDIR/include" "$PYDIR/share" "$PYDIR/lib/python3.12/test"
# astropy imports astropy.tests at start-up, so its folder stays.
find "$SITE" -type d \( -name tests -o -name test \) ! -path "$SITE/astropy/tests" -prune -exec rm -rf {} +
find "$PYDIR" -type d -name __pycache__ -prune -exec rm -rf {} +

# --- 4. The app, icons and the stub ---------------------------------------------------
step "Copying the app"
FILES=()
while IFS= read -r -d '' rel; do
    case "$rel" in
        tests/*|docs/*|.github/*|notes/*|packaging/*) continue ;;
    esac
    FILES+=("$rel")
done < <(cd "$REPO" && git -c core.quotepath=off ls-files -z)
mkdir -p "$APPDIR"
(cd "$REPO" && printf '%s\0' "${FILES[@]}" | tar -cf - --null -T -) | tar -xf - -C "$APPDIR"

step "Drawing the icons"
"$PY" "$REPO/packaging/icons/make_icons.py" "$APPDIR/static" --icns
mv "$APPDIR/static/ACP.icns" "$RES/ACP.icns"

step "Compiling the launcher stub"
clang -O2 -Wall -arch arm64 -mmacosx-version-min=11.0 -o "$CONTENTS/MacOS/ACP" "$REPO/packaging/macos/stub.c"

step "Writing Info.plist, README.txt, VERSION.txt and the licence files"
# LSMinimumSystemVersion is the highest version any Mach-O file in the bundle needs, and
# never less than the Python's own 11.0. A wheel built for a newer macOS would otherwise
# fail with a dyld error on an older Mac instead of the system saying it is too old.
MIN_MACOS=$("$PY" -I "$REPO/packaging/macos/macho.py" minos "$APP")
case "$MIN_MACOS" in
    ""|10.*|11|11.0|11.0.*) MIN_MACOS=11.0 ;;
esac
echo "Minimum macOS: $MIN_MACOS"
sed -e "s/{{VERSION}}/$VERSION/g" -e "s/{{MIN_MACOS}}/$MIN_MACOS/g" \
    "$REPO/packaging/macos/Info.plist.in" > "$CONTENTS/Info.plist"
plutil -lint "$CONTENTS/Info.plist"
sed "s/{{VERSION}}/$VERSION/g" "$REPO/packaging/macos/README.txt" > "$STAGE/README.txt"
printf '%s\n' "$VERSION" > "$STAGE/VERSION.txt"
cp "$REPO/LICENSE" "$STAGE/LICENSE"
cp "$REPO/docs/credits.md" "$STAGE/CREDITS.md"
"$PY" -I "$REPO/packaging/make_notices.py" --site "$SITE" \
    --python-license "$PYDIR/lib/python3.12/LICENSE.txt" \
    --out "$STAGE/THIRD-PARTY-NOTICES.txt"

# --- 5. Bytecode ----------------------------------------------------------------------------
# checked-hash, as on Windows: a timestamp-based .pyc goes stale when zip rounds the
# source time, and a hash stays valid. The standard library is compiled again for the
# same reason. -B stops Python writing new ones, because a signed bundle must not change.
step "Compiling bytecode"
"$PY" -m compileall -q -f -j 0 --invalidation-mode checked-hash -s "$WORK" \
    "$PYDIR/lib/python3.12" "$APPDIR"

# --- 6. Ad hoc signature ---------------------------------------------------------------------
step "Signing ad hoc (an arm64 Mac will not run unsigned code)"
while IFS= read -r file; do
    codesign --force --sign - "$file"
done < <("$PY" -I "$REPO/packaging/macos/macho.py" list "$APP")
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"

# --- 7. Check, zip, checksum -------------------------------------------------------------------
step "Checking the tree"
"$PY" -I "$REPO/packaging/check_tree.py" --tree "$STAGE" --platform macos --max-path "$MAX_PATH_CHARS"

step "Zipping"
# ditto keeps symlinks, permissions and extended attributes. Python's zipfile, which the
# Windows build uses, keeps none of them, and that would break the app.
ZIP="$OUT/ACP-macos-arm64-UNSIGNED.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$ZIP"
HASH=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)
printf '%s  %s\n' "$HASH" "ACP-macos-arm64-UNSIGNED.zip" > "$OUT/SHA256SUMS-macos-unsigned.txt"

step "Checking the archive and the tag"
CHECK=("$REPO/packaging/check_tree.py" --tree "$STAGE" --zip "$ZIP" --platform macos
       --max-zip-mb "$MAX_ZIP_MB" --max-path "$MAX_PATH_CHARS")
[ -n "$TAG" ] && CHECK+=(--tag "$TAG")
"$PY" -I "${CHECK[@]}"

SIZE_MB=$(( $(stat -f%z "$ZIP") / 1048576 ))
UNPACKED_MB=$(( $(du -sk "$STAGE" | cut -f1) / 1024 ))
COUNT=$(find "$STAGE" -type f | wc -l | tr -d ' ')
echo
echo "Built $ZIP"
echo "Size: zip ${SIZE_MB} MB, unpacked ${UNPACKED_MB} MB, ${COUNT} files, minimum macOS $MIN_MACOS"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### macOS package (unsigned)\n\nVersion %s. Zip %s MB, unpacked %s MB, %s files. Minimum macOS %s.\n' \
        "$VERSION" "$SIZE_MB" "$UNPACKED_MB" "$COUNT" "$MIN_MACOS" >> "$GITHUB_STEP_SUMMARY"
fi
if [ -n "${ACP_KEEP_STAGE:-}" ]; then
    echo "Stage kept at $STAGE"
else
    rm -rf "$WORK"
fi
