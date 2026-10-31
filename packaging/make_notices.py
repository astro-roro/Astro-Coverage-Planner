"""Writes THIRD-PARTY-NOTICES.txt for a built package.

    python packaging/make_notices.py --site <site-packages> --python-license <LICENSE.txt> --out <file>

Reads the licence files each installed wheel carries in its .dist-info folder, plus
the licence that ships inside the python.org embeddable zip, and writes them out
in full under a short header. A text that two packages share is written once.
Standard library only, so it runs on the build machine before anything else is
installed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from email.parser import Parser
from pathlib import Path

SOURCE_URL = "https://github.com/astro-roro/Astro-Coverage-Planner"

HEADER = f"""\
THIRD-PARTY NOTICES

Astro Coverage Planner (ACP) is free software, licensed under the GNU General Public License version 3 or later. The licence is in the file LICENSE. The source code is at {SOURCE_URL}

ACP ships with the software listed below. Each entry gives the package, its version, the licence it declares, and the licence files it carries. A licence text that several packages share is printed once and the later entries point back to it.

Three of these packages are licensed under the GNU Lesser General Public License: paramiko, pystray and WCSLIB (code inside astropy). Their files sit unchanged in the site-packages folder, so you can replace any of them with another version of the same library. The MPL 2.0 covers certifi. Packages that other people ask you to credit are listed in CREDITS.md.
"""

# Data that ships in the zip as data, not as a package. No wheel carries a
# notice for it, so it gets its own. It is licensed apart from ACP's GPL code.
BUNDLED_DATA_NOTICE = """\
BUNDLED SKY MAP (app/static/offline-sky)

A low-resolution colour map of the sky, shown when the survey servers cannot be reached. It is made from ESA's picture "The colour of the sky from Gaia's Early Data Release 3".

Credit: ESA/Gaia/DPAC; CC BY-SA 3.0 IGO. Acknowledgement: A. Moitinho
Licence: Creative Commons Attribution-ShareAlike 3.0 IGO (CC BY-SA 3.0 IGO), https://creativecommons.org/licenses/by-sa/3.0/igo/
Changes: resampled into HiPS tiles in equatorial coordinates and saved as JPEG.

This licence covers the static/offline-sky folder only, which is separate from ACP's own code under the GPL. The same text is in static/offline-sky/LICENSE.txt.
"""

# A package whose licence we must reproduce; the build fails if no text is found.
MUST_HAVE_TEXT = ("paramiko", "pystray", "astropy", "certifi")

LICENCE_NAME = re.compile(r"^(licen[cs]e|copying|notice|copyright)", re.IGNORECASE)


def normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip("\n")


def licence_files(dist_info: Path) -> list[Path]:
    """Licence-like files in a .dist-info folder, including its licenses/ subfolders."""
    found = []
    for path in sorted(dist_info.rglob("*")):
        if not path.is_file() or path.name in ("METADATA", "RECORD", "WHEEL", "INSTALLER",
                                              "REQUESTED", "top_level.txt", "entry_points.txt"):
            continue
        in_licences_folder = "licenses" in path.relative_to(dist_info).parts[:-1] or \
            "license_files" in path.relative_to(dist_info).parts[:-1]
        if in_licences_folder or LICENCE_NAME.match(path.name):
            found.append(path)
    return found


def describe(dist_info: Path) -> tuple[str, str, str]:
    meta = Parser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8", errors="replace"))
    name = meta.get("Name", dist_info.name.split("-")[0])
    declared = meta.get("License-Expression") or meta.get("License") or ""
    if not declared or "\n" in declared or len(declared) > 80:
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier", [])
                       if c.startswith("License ::")]
        declared = "; ".join(classifiers) or ("see the licence files below" if declared else "not stated")
    return name, meta.get("Version", "?"), declared


def build(site: Path, python_license: Path | None) -> tuple[str, list[str]]:
    """Returns the notices text and a list of problems."""
    problems = []
    seen: dict[str, str] = {}       # text hash -> where it was printed first
    parts = [HEADER, "\n" + BUNDLED_DATA_NOTICE.rstrip("\n")]

    def add_entry(title: str, declared: str, files: list[tuple[str, str]]):
        lines = ["", "=" * 78, title, f"Licence: {declared}", "=" * 78]
        for label, text in files:
            digest = hashlib.sha256(re.sub(r"\s+", " ", text).encode("utf-8")).hexdigest()
            lines.append("")
            if digest in seen:
                lines.append(f"[{label}: the same text as {seen[digest]}]")
                continue
            seen[digest] = f"{title} ({label})"
            lines.append(f"[{label}]")
            lines.append("")
            lines.append(text)
        parts.append("\n".join(lines))

    if python_license is not None and python_license.is_file():
        add_entry("Python (embeddable package from python.org)", "Python Software Foundation License",
                  [("LICENSE.txt", read_text(python_license))])
    else:
        problems.append("the Python LICENSE.txt was not found")

    entries = sorted(site.glob("*.dist-info"), key=lambda p: normalise_name(p.name))
    names = set()
    for dist_info in entries:
        if not (dist_info / "METADATA").is_file():
            continue
        name, version, declared = describe(dist_info)
        names.add(normalise_name(name))
        files = [(str(p.relative_to(dist_info)).replace("\\", "/"), read_text(p))
                 for p in licence_files(dist_info)]
        files = [(label, text) for label, text in files if text.strip()]
        if not files:
            files = [("no licence file in the package",
                      "This package does not carry a licence file. The licence it declares in its "
                      "metadata is shown above.")]
            if normalise_name(name) in MUST_HAVE_TEXT:
                problems.append(f"{name} has no licence file in its .dist-info folder")
        add_entry(f"{name} {version}", declared, files)
    for required in MUST_HAVE_TEXT:
        if required not in names:
            problems.append(f"{required} is not installed in {site}")
    return "\n".join(parts).rstrip("\n") + "\n", problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--python-license", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    text, problems = build(args.site, args.python_license)
    for line in problems:
        print("FAIL:", line)
    if problems:
        return 1
    with open(args.out, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)
    print(f"wrote {args.out} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
