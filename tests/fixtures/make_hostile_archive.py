"""Build a throwaway archive of awkward inputs for the scanner.

Used by the security and cross-platform run (notes/security-run-plan.md, parts
B5 and E3). Writes a small tree of real FITS files: a couple of clean targets
so the manifest has something correct to compare against, then one case per
awkward input the scanner might meet on a NAS or on Windows.

Cases the running OS refuses are skipped and reported rather than raising, so
the same script produces a comparable tree on macOS, Linux and Windows and the
report says which cases that platform could not create.

    python tests/fixtures/make_hostile_archive.py /tmp/acp-sec/archive

Never point this at a real archive directory: it writes into the target and the
caller is expected to delete the whole tree afterwards.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import Angle
from astropy.io import fits


def _deg_to_hms(ra_deg: float) -> str:
    return Angle(ra_deg, unit=u.deg).to_string(
        unit=u.hourangle, sep=" ", precision=2, pad=True)


def _deg_to_dms(dec_deg: float) -> str:
    return Angle(dec_deg, unit=u.deg).to_string(
        unit=u.deg, sep=" ", precision=2, pad=True, alwayssign=True)


def write_light(path: Path, *, obj: str, filt: str, exptime: float = 300.0,
                imagetyp: str = "Light", ra: float = 311.4, dec: float = 30.7,
                naxis: int = 32, date_obs: str = "2021-07-30T10:53:29") -> None:
    """One synthetic light frame. Header fields match tests/test_scan_cache.py."""
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis
    hdr["NAXIS2"] = naxis
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = imagetyp
    hdr["EXPTIME"] = exptime
    hdr["FILTER"] = filt
    hdr["OBJECT"] = obj
    hdr["DATE-OBS"] = date_obs
    hdr["OBJCTRA"] = _deg_to_hms(ra)
    hdr["OBJCTDEC"] = _deg_to_dms(dec)
    hdr["FOCALLEN"] = 530.0
    hdr["XPIXSZ"] = 3.76
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(data=np.zeros((naxis, naxis), dtype=np.int16),
                    header=hdr).writeto(path, overwrite=True)


class Builder:
    """Creates cases, recording which succeeded and which the platform refused."""

    def __init__(self, root: Path):
        self.root = root
        self.made: list[str] = []
        self.skipped: list[dict] = []

    def case(self, name: str, fn) -> None:
        try:
            fn()
        except (OSError, ValueError, UnicodeEncodeError) as exc:
            self.skipped.append({"case": name, "reason": f"{type(exc).__name__}: {exc}"})
        else:
            self.made.append(name)

    # Baseline: two correct targets, so a scan of this tree has a right answer
    # to be wrong about.

    def clean_targets(self) -> None:
        def build():
            for i in range(3):
                write_light(self.root / "M42" / f"Light_Ha_300s_{i:03d}.fit",
                            obj="M42", filt="Ha", ra=83.8, dec=-5.4)
            for i in range(2):
                write_light(self.root / "M42" / f"Light_OIII_300s_{i:03d}.fit",
                            obj="M42", filt="OIII", ra=83.8, dec=-5.4)
            for i in range(4):
                write_light(self.root / "NGC7000" / f"Light_SII_600s_{i:03d}.fit",
                            obj="NGC7000", filt="SII", exptime=600.0,
                            ra=314.7, dec=44.5)
        self.case("clean_targets", build)

    # Awkward names. Each is a real light frame under a name the scanner has to
    # carry through the summary markdown, the manifest and the API.

    def awkward_names(self) -> None:
        cases = {
            "name_non_ascii": "Light_Hα_300s_ünïcode_日本語.fit",
            "name_single_quote": "Light_Ha_300s_o'brien.fit",
            "name_double_quote": 'Light_Ha_300s_say"what".fit',
            "name_shell_metachars": "Light_Ha_300s_$(whoami)`id`;rm -rf.fit",
            "name_semicolon_pipe": "Light_Ha_300s_a;b|c&d.fit",
            "name_newline": "Light_Ha_300s_line1\nline2.fit",
            "name_trailing_dot": "Light_Ha_300s_trailing..fit",
            "name_leading_space": " Light_Ha_300s_leading.fit",
            "name_html_ish": "Light_Ha_300s_<img src=x onerror=alert(1)>.fit",
        }
        for label, filename in cases.items():
            def build(filename=filename):
                write_light(self.root / "Awkward" / filename,
                            obj="Awkward Target", filt="Ha", ra=200.0, dec=10.0)
            self.case(label, build)

        # Windows reserved device stems. Legal on POSIX, refused by Windows.
        for stem in ("con", "nul", "aux", "com1"):
            def build(stem=stem):
                write_light(self.root / "Awkward" / f"{stem}.fit",
                            obj="Awkward Target", filt="Ha", ra=200.0, dec=10.0)
            self.case(f"name_reserved_{stem}", build)

        # Trailing space on a directory. Windows silently strips it, so one
        # archive can present two names for the same folder.
        def trailing_space_dir():
            write_light(self.root / "TrailingSpaceDir " / "Light_Ha_300s.fit",
                        obj="Trailing Space", filt="Ha", ra=210.0, dec=12.0)
        self.case("dir_trailing_space", trailing_space_dir)

    # Path length. Windows caps at 260 characters unless long paths are enabled.

    def deep_path(self) -> None:
        def build():
            deep = self.root / "Deep"
            for i in range(12):
                deep = deep / f"level_{i}_{'x' * 18}"
            write_light(deep / "Light_Ha_300s.fit", obj="Deep Target",
                        filt="Ha", ra=220.0, dec=15.0)
        self.case("deep_path_over_260", build)

    # Symlinks. Needs a privilege on Windows, so a skip there is expected and
    # is itself the finding: the case cannot be tested on that platform.

    def symlinks(self) -> None:
        outside = self.root.parent / "outside-the-archive"

        def build_escape():
            outside.mkdir(parents=True, exist_ok=True)
            write_light(outside / "Light_Ha_300s_outside.fit", obj="Outside",
                        filt="Ha", ra=230.0, dec=20.0)
            (self.root / "Links").mkdir(parents=True, exist_ok=True)
            link = self.root / "Links" / "escape"
            if not link.exists():
                link.symlink_to(outside, target_is_directory=True)
        self.case("symlink_escapes_root", build_escape)

        def build_loop():
            links = self.root / "Links"
            links.mkdir(parents=True, exist_ok=True)
            loop = links / "loop"
            if not loop.exists():
                loop.symlink_to(links, target_is_directory=True)
        self.case("symlink_loop", build_loop)

        def build_dangling():
            links = self.root / "Links"
            links.mkdir(parents=True, exist_ok=True)
            dangling = links / "dangling.fit"
            if not dangling.exists():
                dangling.symlink_to(self.root / "does-not-exist.fit")
        self.case("symlink_dangling", build_dangling)

    # Malformed payloads. The scanner should flag these and finish, not crash.

    def malformed(self) -> None:
        bad = self.root / "Malformed"

        def truncated_fits():
            good = bad / "_full.fit"
            write_light(good, obj="Truncated", filt="Ha", ra=240.0, dec=25.0)
            raw = good.read_bytes()
            (bad / "Light_Ha_300s_truncated.fit").write_bytes(raw[: len(raw) // 3])
            good.unlink()
        self.case("fits_truncated", truncated_fits)

        def empty_fits():
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "Light_Ha_300s_empty.fit").write_bytes(b"")
        self.case("fits_empty", empty_fits)

        def not_fits():
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "Light_Ha_300s_notfits.fit").write_bytes(b"this is not a FITS file\n" * 100)
        self.case("fits_wrong_magic", not_fits)

        def malformed_xisf():
            bad.mkdir(parents=True, exist_ok=True)
            # Correct XISF signature, then a header that never closes.
            (bad / "master_Ha_malformed.xisf").write_bytes(
                b"XISF0100" + b"\x00" * 8 + b"<xisf version='1.0'><Image geometry=")
        self.case("xisf_malformed_header", malformed_xisf)

        def huge_header_fits():
            bad.mkdir(parents=True, exist_ok=True)
            hdr = fits.Header()
            hdr["IMAGETYP"] = "Light"
            hdr["EXPTIME"] = 300.0
            hdr["FILTER"] = "Ha"
            hdr["OBJECT"] = "Huge Header"
            for i in range(2000):
                hdr[f"PAD{i:04d}"] = "x" * 60
            fits.PrimaryHDU(data=np.zeros((32, 32), dtype=np.int16),
                            header=hdr).writeto(
                bad / "Light_Ha_300s_hugeheader.fit", overwrite=True)
        self.case("fits_huge_header", huge_header_fits)

    # A directory the scanning process cannot read, which is the local stand-in
    # for an SMB share that blips mid-walk (part E1).

    def unreadable_dir(self) -> None:
        def build():
            if os.name == "nt":
                raise OSError("chmod 000 does not deny access on Windows")
            d = self.root / "Unreadable"
            write_light(d / "Light_Ha_300s.fit", obj="Unreadable", filt="Ha",
                        ra=250.0, dec=30.0)
            d.chmod(0o000)
        self.case("dir_unreadable", build)


def build_archive(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    b = Builder(root)
    b.clean_targets()
    b.awkward_names()
    b.deep_path()
    b.symlinks()
    b.malformed()
    b.unreadable_dir()
    return {
        "root": str(root),
        "platform": sys.platform,
        "os_name": os.name,
        "made": b.made,
        "skipped": b.skipped,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser()
    report = build_archive(root)
    report_path = root.parent / "hostile-archive-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"built {len(report['made'])} cases under {root}")
    for s in report["skipped"]:
        print(f"  skipped {s['case']}: {s['reason']}")
    print(f"report -> {report_path}")
    print("\nWhat a correct scan of this tree looks like:")
    print("  M42     Ha 3x300s and OIII 2x300s")
    print("  NGC7000 SII 4x600s")
    print("  Awkward every awkwardly-named light, each a real 300s Ha frame")
    print("  Deep    the one frame at the end of the long path")
    print("  the malformed files excluded and reported, the scan still finishing")
    print("A file that is silently absent, or a crash, is the finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
