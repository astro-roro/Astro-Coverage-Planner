#!/bin/sh
# Builds ACP-linux-<arch>.tar.gz and SHA256SUMS-linux-<arch>.txt for the machine it runs on.
# Runs on ubuntu-22.04 (x86_64) and ubuntu-22.04-arm (aarch64); see
# .github/workflows/release-linux.yml. Spec: docs/specs/windows-download-and-setup.md, section 5.
#
#     sh packaging/linux/build.sh [--out-dir dist] [--tag v1.0.0] [--max-mb 400]
#
# Needs on PATH: curl, tar, gzip, git, and sha256sum (or shasum). Everything that goes
# into the package is installed with the bundled Python itself, so the wheels and the
# bytecode match the interpreter that ships.
set -eu

out_dir=dist
tag=
max_mb=400
while [ $# -gt 0 ]; do
    case "$1" in
        --out-dir) out_dir=$2; shift 2 ;;
        --tag) tag=$2; shift 2 ;;
        --max-mb) max_mb=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
die() { echo "build failed: $1" >&2; exit 1; }

repo=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$out_dir"
out=$(cd "$out_dir" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/acp-build.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
stage="$work/ACP"
mkdir -p "$stage"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
pin() { sed -n "s/^$1=//p" "$repo/packaging/pins.txt" | head -n 1 | tr -d '[:space:]'; }

# --- Which machine ----------------------------------------------------------------
[ "$(uname -s)" = Linux ] || die "the bundled Python is a Linux build, so this must run on Linux"
case "$(uname -m)" in
    x86_64|amd64) arch=x86_64; expected=$(pin PBS_LINUX_X86_64_SHA256) ;;
    aarch64|arm64) arch=aarch64; expected=$(pin PBS_LINUX_AARCH64_SHA256) ;;
    *) die "this builds x86_64 and aarch64 only, not $(uname -m)" ;;
esac
release=$(pin PBS_RELEASE)
pyver=$(pin PBS_PYTHON_VERSION)

# --- 1. The Python -----------------------------------------------------------------
step "Downloading Python $pyver (python-build-standalone $release, $arch, gnu)"
url="https://github.com/astral-sh/python-build-standalone/releases/download/$release/cpython-$pyver%2B$release-$arch-unknown-linux-gnu-install_only_stripped.tar.gz"
curl -fsSL --retry 3 -o "$work/python.tar.gz" "$url"
actual=$(sha256 "$work/python.tar.gz")
[ -n "$expected" ] || die "packaging/pins.txt has no SHA-256 for $arch"
[ "$actual" = "$expected" ] || die "the Python download does not match packaging/pins.txt. Expected $expected, got $actual"
tar -xzf "$work/python.tar.gz" -C "$stage"      # unpacks to ACP/python
py="$stage/python/bin/python3"
[ -x "$py" ] || die "the Python archive has no bin/python3"
"$py" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version' \
    || die "the bundled Python is not 3.12, and the requirements are pinned for 3.12"
site=$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

# --- 2. Dependencies -----------------------------------------------------------------
step 'Building wheels for packages that publish only a source package'
wheels="$work/wheels"
mkdir -p "$wheels"
while IFS= read -r line; do
    req=$(printf '%s' "$line" | sed 's/#.*$//' | tr -d '[:space:]')
    [ -n "$req" ] || continue
    "$py" -m pip wheel --disable-pip-version-check --no-deps \
        -c "$repo/packaging/constraints.txt" -w "$wheels" "$req"
done < "$repo/packaging/sdist-only.txt"
ls "$wheels"/*.whl >/dev/null 2>&1 || die 'no wheels were built from packaging/sdist-only.txt'

step 'Installing dependencies (wheels only)'
# If a wheel is missing for this architecture, this step fails. The spec's answer is to
# drop that dependency from the packaged set, not to compile it.
"$py" -m pip install --disable-pip-version-check --target "$site" \
    --only-binary=:all: --find-links "$wheels" -c "$repo/packaging/constraints.txt" \
    -r "$repo/requirements.txt" -r "$repo/requirements-packaged.txt"

# --- 3. Trim ---------------------------------------------------------------------------
step 'Trimming test folders and bytecode'
# astropy imports astropy.tests at start-up, so its folder stays.
find "$site" -type d \( -name tests -o -name test -o -name __pycache__ \) \
    ! -path "$site/astropy/tests" ! -path "$site/astropy/tests/*" -prune -exec rm -rf {} +

# --- 4. The app, icons and start files -------------------------------------------------
step 'Copying the app'
app="$stage/app"
mkdir -p "$app"
(cd "$repo" && git -c core.quotepath=off ls-files -z | tar --null -T - -cf -) | tar -xf - -C "$app"
for dir in tests docs .github notes packaging; do rm -rf "$app/$dir"; done

step 'Drawing the icons'
PYTHONPATH="$site" "$py" "$repo/packaging/icons/make_icons.py" "$app/static"

step 'Writing start-acp, README.txt and VERSION.txt'
version=$(sed -n 's/^VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$repo/app.py" | head -n 1)
[ -n "$version" ] || die 'could not read VERSION from app.py'
cp "$repo/packaging/linux/start-acp" "$stage/start-acp"
chmod 755 "$stage/start-acp"
sed "s/{{VERSION}}/$version/g" "$repo/packaging/linux/README.txt" > "$stage/README.txt"
printf '%s\n' "$version" > "$stage/VERSION.txt"

step 'Writing the licence files'
cp "$repo/LICENSE" "$stage/LICENSE"
cp "$repo/docs/credits.md" "$stage/CREDITS.md"
"$py" -I "$repo/packaging/make_notices.py" --site "$site" \
    --python-license "$stage/python/lib/python3.12/LICENSE.txt" \
    --out "$stage/THIRD-PARTY-NOTICES.txt"

# --- 5. Bytecode ------------------------------------------------------------------------
# checked-hash, as on Windows: a hash-based .pyc survives tar's timestamps, and checking
# it on import keeps "edit a .py file to fix it" working.
step 'Compiling bytecode'
"$py" -m compileall -q -j 0 --invalidation-mode checked-hash -s "$work" "$site" "$app"

# --- 6. Archive and checksums ------------------------------------------------------------
step 'Checking the tree'
"$py" -I "$repo/packaging/check_tree.py" --tree "$stage" --platform linux

step 'Archiving'
name="ACP-linux-$arch.tar.gz"
tarball="$out/$name"
rm -f "$tarball"
# Owner 0 so the files do not carry the build user's id. The mtimes stay as they are,
# since the .pyc files are hash-based and do not depend on them.
(cd "$work" && tar --owner=0 --group=0 --numeric-owner -cf - ACP) | gzip -6 > "$tarball"
printf '%s  %s\n' "$(sha256 "$tarball")" "$name" > "$out/SHA256SUMS-linux-$arch.txt"

step 'Checking the archive, the permissions and the tag'
set -- "$py" -I "$repo/packaging/check_tree.py" --tree "$stage" --zip "$tarball" \
    --platform linux --max-zip-mb "$max_mb"
[ -z "$tag" ] || set -- "$@" --tag "$tag"
"$@"
# The executable bits have to survive the archive, because the user runs it straight away.
listing=$(tar -tvzf "$tarball" ACP/start-acp ACP/python/bin/python3.12)
printf '%s\n' "$listing"
printf '%s\n' "$listing" | grep -E '^-rwx.* ACP/start-acp$' >/dev/null || die 'start-acp lost its executable bit'
printf '%s\n' "$listing" | grep -E '^-rwx.* ACP/python/bin/python3.12$' >/dev/null || die 'python3.12 lost its executable bit'

size_mb=$(du -m "$tarball" | cut -f1)
unpacked_mb=$(du -sm "$stage" | cut -f1)
count=$(find "$stage" -type f | wc -l | tr -d ' ')
echo
echo "Built $tarball"
echo "Size: tar.gz $size_mb MB, unpacked $unpacked_mb MB, $count files"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### Linux package (%s)\n\nVersion %s. Archive %s MB, unpacked %s MB, %s files.\n' \
        "$arch" "$version" "$size_mb" "$unpacked_mb" "$count" >> "$GITHUB_STEP_SUMMARY"
fi
