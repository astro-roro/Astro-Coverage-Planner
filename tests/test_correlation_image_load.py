"""The SII vs Ha correlation check must read XISF as well as FITS.

Stephen2615 reported (issue #63) that every narrowband target logged
"No SIMPLE card found, this file does not appear to be a valid FITS file",
because the check opened whatever path it was given with astropy.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestLoadImagePlane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _fits(self, name, data):
        p = self.dir / name
        fits.PrimaryHDU(data.astype("float32")).writeto(p)
        return p

    def test_reads_mono_fits(self):
        data = np.arange(64 * 48, dtype="float32").reshape(48, 64)
        got = bam.load_image_plane(self._fits("mono.fits", data))
        self.assertEqual(got.shape, (48, 64))
        self.assertAlmostEqual(float(got[10, 10]), float(data[10, 10]), places=3)

    def test_collapses_three_plane_fits_to_one(self):
        data = np.zeros((3, 48, 64), dtype="float32")
        data[0] = 1.0
        data[1] = 2.0
        data[2] = 3.0
        got = bam.load_image_plane(self._fits("rgb.fits", data))
        self.assertEqual(got.shape, (48, 64))
        self.assertAlmostEqual(float(got[0, 0]), 2.0, places=5)

    def test_missing_file_raises_rather_than_returning_junk(self):
        with self.assertRaises(Exception):
            bam.load_image_plane(self.dir / "nope.fits")

    def test_xisf_is_not_opened_with_astropy(self):
        """A file named .xisf must never reach astropy.

        Writing a valid XISF needs the writer half of the library, so this
        asserts on the failure mode instead: astropy's complaint about a
        missing SIMPLE card is exactly the message Stephen saw, and it must
        not appear for an .xisf path.
        """
        p = self.dir / "master.xisf"
        p.write_bytes(b"XISF0100" + b"\0" * 64)
        try:
            bam.load_image_plane(p)
        except Exception as e:
            self.assertNotIn("SIMPLE card", str(e))


class TestCorrelationSurvivesXisf(unittest.TestCase):
    def test_xisf_pair_does_not_log_a_fits_error(self):
        with tempfile.TemporaryDirectory() as d:
            ha = Path(d) / "masterLight_FILTER-Ha_mono.xisf"
            sii = Path(d) / "masterLight_FILTER-Sii_mono.xisf"
            for p in (ha, sii):
                p.write_bytes(b"XISF0100" + b"\0" * 64)
            logged = []
            bam.detect_sii_ha_correlation(
                [
                    {"role": "master", "filter": "Ha", "path": str(ha)},
                    {"role": "master", "filter": "SII", "path": str(sii)},
                ],
                log=logged.append,
            )
            joined = " ".join(logged)
            self.assertNotIn("SIMPLE card", joined)


if __name__ == "__main__":
    unittest.main()
