"""Zip a folder with the folder itself as the top entry.

    python packaging/zip_tree.py <folder> <output.zip>

Compress-Archive is slow and trips over long paths with tens of thousands of
files, so the build uses this. Entries are sorted so two builds of the same
tree give the same file order.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path


def zip_tree(folder: Path, out: Path) -> int:
    folder = Path(folder)
    top = folder.name
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for current, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                path = Path(current) / name
                arcname = f"{top}/{path.relative_to(folder).as_posix()}"
                archive.write(path, arcname)
                count += 1
    return count


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(f"{zip_tree(Path(sys.argv[1]), Path(sys.argv[2]))} files")
