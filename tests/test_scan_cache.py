"""Tests for the incremental scan cache (data/scan_cache.json).

A repeat scan must be able to skip the header read for every file whose path,
size and mtime are unchanged without anything downstream noticing. These tests
run the real builder as a subprocess over a small synthetic FITS tree, the way a
user runs it, and compare the manifests it produces.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import Angle
from astropy.io import fits

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILDER = REPO_ROOT / "scripts" / "build_archive_manifest.py"


def _deg_to_hms(ra_deg: float) -> str:
    return Angle(ra_deg, unit=u.deg).to_string(
        unit=u.hourangle, sep=" ", precision=2, pad=True)


def _deg_to_dms(dec_deg: float) -> str:
    return Angle(dec_deg, unit=u.deg).to_string(
        unit=u.deg, sep=" ", precision=2, pad=True, alwayssign=True)


def _write_light(path: Path, *, filt: str, exptime: float, imagetyp: str = "Light",
                 ra: float = 311.4, dec: float = 30.7, naxis: int = 64):
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis
    hdr["NAXIS2"] = naxis
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = imagetyp
    hdr["EXPTIME"] = exptime
    hdr["FILTER"] = filt
    hdr["OBJECT"] = "Test Target"
    hdr["DATE-OBS"] = "2021-07-30T10:53:29"
    hdr["OBJCTRA"] = _deg_to_hms(ra)
    hdr["OBJCTDEC"] = _deg_to_dms(dec)
    hdr["FOCALLEN"] = 530.0
    hdr["XPIXSZ"] = 3.76
    fits.PrimaryHDU(data=np.zeros((naxis, naxis), dtype=np.int16),
                    header=hdr).writeto(path, overwrite=True)


class ScanCacheCase(unittest.TestCase):
    """Shared fixture: a two-folder archive of nine synthetic lights."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.archive = self.tmp / "archive"
        self.out = self.tmp / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.manifest = self.out / "manifest.json"
        self.cache = self.out / "scan_cache.json"
        self.files = []
        for folder, filt, exp, n in (("Ha", "H", 300.0, 5), ("OIII", "O", 600.0, 4)):
            d = self.archive / "M27" / folder
            d.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                p = d / f"Light_{i:04d}.fit"
                _write_light(p, filt=filt, exptime=exp)
                self.files.append(p)

    def run_scan(self, *extra_args, expect_ok: bool = True):
        env = dict(os.environ)
        env["FITS_ROOTS"] = str(self.archive)
        env["MANIFEST_PATH"] = str(self.manifest)
        env["ACP_SCAN_CACHE"] = str(self.cache)
        env["PIPELINE_DB"] = str(self.tmp / "missing.db")
        env["FULL_MASTERS"] = str(self.tmp / "missing_masters")
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(BUILDER), *extra_args],
            env=env, capture_output=True, text=True, timeout=600)
        if expect_ok:
            self.assertEqual(proc.returncode, 0,
                             f"scan failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return proc

    def counters(self, proc) -> tuple[int, int, int]:
        """(hits, misses, dropped) from the end of run summary line."""
        for line in proc.stdout.splitlines():
            if "Header cache:" in line:
                nums = [int(tok) for tok in line.replace(",", " ").split()
                        if tok.isdigit()]
                self.assertEqual(len(nums), 3, line)
                return tuple(nums)
        self.fail(f"no header cache summary line in output:\n{proc.stdout}")

    def manifest_body(self) -> dict:
        m = json.loads(self.manifest.read_text(encoding="utf-8"))
        # Timestamps and wall-clock durations legitimately differ run to run.
        m.pop("scan_date", None)
        m.pop("scan_duration_sec", None)
        return m


class TestColdThenWarm(ScanCacheCase):

    def test_warm_scan_matches_cold_scan(self):
        first = self.run_scan()
        self.assertEqual(self.counters(first), (0, 9, 0))
        cold = self.manifest_body()

        second = self.run_scan()
        hits, misses, dropped = self.counters(second)
        self.assertEqual((hits, misses, dropped), (9, 0, 0))
        self.assertEqual(self.manifest_body(), cold)

    def test_cache_file_has_one_entry_per_file(self):
        self.run_scan()
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], 1)
        self.assertTrue(doc["reader_hash"])
        self.assertEqual(len(doc["entries"]), len(self.files))
        for entry in doc["entries"].values():
            self.assertIn("size", entry)
            self.assertIn("mtime", entry)
            # Absolute paths are the key, not something baked into the metadata.
            self.assertNotIn("path", entry["meta"])
        self.assertEqual(
            {e["meta"]["filter"] for e in doc["entries"].values()}, {"Ha", "OIII"})


class TestInvalidation(ScanCacheCase):

    def test_changed_mtime_forces_a_reread(self):
        self.run_scan()
        target = self.files[0]
        st = target.stat()
        os.utime(target, (st.st_atime + 120, st.st_mtime + 120))
        hits, misses, dropped = self.counters(self.run_scan())
        self.assertEqual(misses, 1)
        self.assertEqual(hits, len(self.files) - 1)
        self.assertEqual(dropped, 0)

    def test_changed_content_forces_a_reread_and_shows_up(self):
        self.run_scan()
        target = self.files[0]
        _write_light(target, filt="S", exptime=900.0)
        os.utime(target, (target.stat().st_atime + 300, target.stat().st_mtime + 300))
        hits, misses, _ = self.counters(self.run_scan())
        self.assertEqual((hits, misses), (len(self.files) - 1, 1))
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        key = str(target).replace("\\", "/")
        self.assertEqual(doc["entries"][key]["meta"]["filter"], "SII")

    def test_deleted_file_leaves_the_cache(self):
        self.run_scan()
        gone = self.files[-1]
        gone.unlink()
        hits, misses, dropped = self.counters(self.run_scan())
        self.assertEqual((hits, misses, dropped), (len(self.files) - 1, 0, 1))
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["entries"]), len(self.files) - 1)
        self.assertNotIn(str(gone).replace("\\", "/"), doc["entries"])

    def test_reader_change_invalidates_the_cache(self):
        self.run_scan()
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        doc["reader_hash"] = "not the hash of the current readers"
        self.cache.write_text(json.dumps(doc), encoding="utf-8")
        hits, misses, _ = self.counters(self.run_scan())
        self.assertEqual((hits, misses), (0, len(self.files)))

    def test_schema_bump_invalidates_the_cache(self):
        self.run_scan()
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        doc["schema"] = doc["schema"] + 1
        self.cache.write_text(json.dumps(doc), encoding="utf-8")
        hits, misses, _ = self.counters(self.run_scan())
        self.assertEqual((hits, misses), (0, len(self.files)))


class TestFallbacks(ScanCacheCase):

    def test_corrupt_cache_falls_back_to_a_cold_scan(self):
        first = self.run_scan()
        cold = self.manifest_body()
        self.cache.write_text('{"schema": 1, "entries": {"a": ', encoding="utf-8")
        second = self.run_scan()
        hits, misses, _ = self.counters(second)
        self.assertEqual((hits, misses), (0, len(self.files)))
        self.assertEqual(self.manifest_body(), cold)
        # And the run repairs it for next time.
        self.assertEqual(
            len(json.loads(self.cache.read_text(encoding="utf-8"))["entries"]),
            len(self.files))

    def test_missing_cache_is_not_an_error(self):
        self.assertFalse(self.cache.exists())
        self.run_scan()
        self.assertTrue(self.cache.exists())

    def test_no_cache_flag_ignores_an_existing_cache(self):
        self.run_scan()
        cold = self.manifest_body()
        hits, misses, _ = self.counters(self.run_scan("--no-cache"))
        self.assertEqual((hits, misses), (0, len(self.files)))
        self.assertEqual(self.manifest_body(), cold)
        # The forced cold scan still leaves a usable cache behind.
        hits, misses, _ = self.counters(self.run_scan())
        self.assertEqual((hits, misses), (len(self.files), 0))


if __name__ == "__main__":
    unittest.main()
