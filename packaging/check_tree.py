"""Checks a built package against the rules in spec section 9, test K5.

    python packaging/check_tree.py --tree <ACP folder> --zip <file> --platform windows \
        [--tag v1.0.0] [--max-zip-mb 400] [--max-path 150]

Prints one line per failed rule and exits 1 if any failed. Standard library only,
so it runs on the build machine before anything else is installed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The folders the spec removes from app/ (section 3.2). A trailing slash marks a folder.
EXCLUDED_FROM_APP = ("tests/", "docs/", ".github/", "notes/", "packaging/")

REQUIRED = {
    "windows": ("Start ACP.bat", "README.txt", "VERSION.txt", "python/pythonw.exe",
                "python/python.exe", "app/launcher.py", "app/app.py",
                "app/bundled_extensions/nina_ts_sync"),
}


def app_version(repo_root: Path = REPO_ROOT) -> str:
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', (repo_root / "app.py").read_text(encoding="utf-8"),
                      re.MULTILINE)
    if not match:
        raise SystemExit("could not find VERSION in app.py")
    return match.group(1)


def longest_paths(tree: Path, top: str = "ACP", count: int = 3) -> list[tuple[int, str]]:
    """The longest relative paths, measured from the folder that holds ``tree``
    (so the top folder name counts, as it does once the zip is extracted)."""
    found = []
    for folder, dirs, files in os.walk(tree):
        for name in dirs + files:
            rel = os.path.relpath(os.path.join(folder, name), tree).replace(os.sep, "/")
            found.append((len(top) + 1 + len(rel), f"{top}/{rel}"))
    return sorted(found, reverse=True)[:count]


def check(tree: Path, zip_path: Path | None, platform: str, tag: str | None = None,
          max_zip_mb: int = 400, max_path: int = 150, version: str | None = None) -> list[str]:
    problems = []
    version = version or app_version()
    if tag is not None and tag != f"v{version}":
        problems.append(f"the tag {tag} does not match VERSION {version} in app.py")
    if zip_path is not None:
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        if size_mb > max_zip_mb:
            problems.append(f"the archive is {size_mb:.0f} MB, over the {max_zip_mb} MB limit")
    longest = longest_paths(tree, tree.name)
    if longest and longest[0][0] >= max_path:
        problems.append(f"the longest path is {longest[0][0]} characters, limit {max_path}: "
                        f"{longest[0][1]}")
    for required in REQUIRED[platform]:
        if not (tree / required).exists():
            problems.append(f"missing from the package: {required}")
    for folder in EXCLUDED_FROM_APP:
        if (tree / "app" / folder.rstrip("/")).exists():
            problems.append(f"app/{folder} should not be in the package")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--platform", choices=sorted(REQUIRED), required=True)
    parser.add_argument("--tag", help="the git tag, on a tag run")
    parser.add_argument("--max-zip-mb", type=int, default=400)
    parser.add_argument("--max-path", type=int, default=150)
    args = parser.parse_args(argv)
    problems = check(args.tree, args.zip, args.platform, args.tag, args.max_zip_mb, args.max_path)
    for line in problems:
        print("FAIL:", line)
    if not problems:
        print("package checks passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
