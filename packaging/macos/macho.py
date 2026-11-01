"""Finds the Mach-O files in a folder, by file magic and not by extension.

    python packaging/macos/macho.py list <folder>     one path per line, deepest first
    python packaging/macos/macho.py minos <folder>    the highest macOS version any file needs

Wheels carry Mach-O files under names such as ``.so``, ``.dylib`` and no extension at
all, so signing by extension would miss some, and notarisation rejects the bundle
for any one it misses (spec section 4.3, step 2). Standard library only.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from pathlib import Path

# Thin Mach-O, both byte orders, 32 and 64 bit.
THIN_MAGICS = {0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE}
# Fat (universal) files share their magic with Java class files, so the arch count
# is checked as well. A class file has a version number there, which is 44 or more.
FAT_MAGICS = {0xCAFEBABE, 0xBEBAFECA}
MAX_FAT_ARCHS = 20


def is_macho(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    if len(head) < 8:
        return False
    magic = struct.unpack(">I", head[:4])[0]
    if magic in THIN_MAGICS:
        return True
    if magic in FAT_MAGICS:
        big = magic == 0xCAFEBABE
        count = struct.unpack(">I" if big else "<I", head[4:8])[0]
        return 0 < count <= MAX_FAT_ARCHS
    return False


def find_macho(folder: Path) -> list[Path]:
    """Every Mach-O file under ``folder``, the deepest path first, so a signer that
    works down the list signs the inside of a bundle before the bundle."""
    found = []
    for current, dirs, files in os.walk(folder):
        for name in files:
            path = Path(current) / name
            if is_macho(path):
                found.append(path)
    found.sort(key=lambda p: (len(p.relative_to(folder).parts), str(p)), reverse=True)
    return found


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def min_os(path: Path) -> str | None:
    """The macOS version a Mach-O file says it needs, from ``vtool``. For a fat file,
    the highest of its slices. None where it says nothing or vtool is missing."""
    try:
        out = subprocess.run(["vtool", "-show-build", str(path)], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    versions = re.findall(r"^\s*minos\s+([0-9.]+)\s*$", out, re.MULTILINE)
    return max(versions, key=_version_key) if versions else None


def highest_min_os(folder: Path) -> str | None:
    best = None
    for path in find_macho(folder):
        found = min_os(path)
        if found and (best is None or _version_key(found) > _version_key(best)):
            best = found
    return best


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("list", "minos"):
        print(__doc__)
        return 2
    folder = Path(args[1])
    if args[0] == "list":
        for path in find_macho(folder):
            print(path)
    else:
        print(highest_min_os(folder) or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
