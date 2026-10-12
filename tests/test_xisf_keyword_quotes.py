"""FITS text values in an XISF master keep their apostrophes single.

A FITS header writes an apostrophe inside a text value as two, so OBJECT
"Cat's Paw" is stored as 'Cat''s Paw'. The xisf library strips the outer
quotes but leaves the doubled apostrophe, which showed up in the target list
as "Cat''s Paw".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _read_with_keywords(keywords: dict) -> dict:
    class _FakeXISF:
        def __init__(self, path):
            pass

        def get_images_metadata(self):
            return [{
                "geometry": (64, 48, 1),
                "FITSKeywords": {k: [{"value": v, "comment": ""}] for k, v in keywords.items()},
            }]

    with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(XISF=_FakeXISF)}):
        return bam.read_xisf_meta(Path("masterLight.xisf"))


class TestXisfDoubledApostrophe(unittest.TestCase):
    def test_object_name_apostrophe_is_single(self):
        # What the xisf library hands back for 'Cat''s Paw'.
        meta = _read_with_keywords({"OBJECT": "Cat''s Paw", "EXPTIME": 300.0})
        self.assertEqual(meta["object"], "Cat's Paw")

    def test_telescope_and_camera_apostrophes_are_single(self):
        meta = _read_with_keywords({
            "TELESCOP": "Rohan''s RedCat",
            "INSTRUME": "Rohan''s Camera",
        })
        self.assertEqual(meta["telescope"], "Rohan's RedCat")
        self.assertEqual(meta["camera"], "Rohan's Camera")

    def test_numeric_values_are_untouched(self):
        meta = _read_with_keywords({"EXPTIME": 300.0, "OBJECT": "M 42"})
        self.assertEqual(meta["exptime"], 300.0)
        self.assertEqual(meta["object"], "M 42")


if __name__ == "__main__":
    unittest.main()
