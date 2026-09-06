"""A master stripped of its FITS keywords must still read (issue #63).

Stephen2615's M17 Ha master came back from Blind Solve 2000 with 17 FITS
keywords and no filter, exposure, camera or subframe count. All of it was
still in the XISF properties, which is where PixInsight keeps the real
record.
"""
import html
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def prop(value):
    return {"id": "x", "type": "String", "value": value}


STRIPPED_PROPS = {
    "Instrument:Filter:Name": prop("Ha"),
    "Instrument:FrameExposureTime": prop(300),
    "Instrument:Camera:Name": prop("QHY268M"),
    "Instrument:Telescope:Name": prop("WO Z61"),
    "Instrument:Camera:XBinning": prop(1),
    "Instrument:Telescope:FocalLength": prop(0.370026),
    "Instrument:Sensor:XPixelSize": prop(3.76),
    "Observation:Object:Name": prop("M 17"),
    "Observation:Time:Start": prop("2026-08-13T11:27:58.317Z"),
    "Observation:Center:RA": prop(276.5999896462167),
    "Observation:Center:Dec": prop(-15.83356733476789),
    "PCL:AstrometricSolution:LinearTransformationMatrix": prop(
        [[-4.48449729e-05, 5.80760904e-04], [-5.80176651e-04, -4.50739454e-05]]),
}


def fake_xisf(properties, fits_keywords=None, geometry=(6252, 4176, 1)):
    """Stand in for the xisf library with one image's metadata."""
    meta = {
        "geometry": geometry,
        "colorSpace": "Gray" if geometry[2] == 1 else "RGB",
        "FITSKeywords": fits_keywords or {},
        "XISFProperties": properties,
    }

    class _Fake:
        def __init__(self, path):
            pass

        def get_images_metadata(self):
            return [meta]

    return _Fake


class TestPropertyBackfill(unittest.TestCase):
    def _read(self, properties, fits_keywords=None, geometry=(6252, 4176, 1)):
        fake = fake_xisf(properties, fits_keywords, geometry)
        with mock.patch.dict(sys.modules, {"xisf": mock.MagicMock(XISF=fake)}):
            return bam.read_xisf_meta(Path("/a/M 17/master_FILTER-Ha_mono.xisf"))

    def test_filter_comes_from_the_property(self):
        self.assertEqual(self._read(STRIPPED_PROPS)["filter"], "Ha")

    def test_exposure_comes_from_the_property(self):
        self.assertEqual(self._read(STRIPPED_PROPS)["exptime"], 300.0)

    def test_camera_telescope_and_object_come_from_properties(self):
        m = self._read(STRIPPED_PROPS)
        self.assertEqual(m["camera"], "QHY268M")
        self.assertEqual(m["telescope"], "WO Z61")
        self.assertEqual(m["object"], "M 17")

    def test_binning_and_date_come_from_properties(self):
        m = self._read(STRIPPED_PROPS)
        self.assertEqual(m["xbinning"], 1)
        self.assertTrue(m["date_obs"].startswith("2026-08-13T11:27:58"))

    def test_fits_keyword_still_wins_over_the_property(self):
        kw = {"FILTER": [{"value": "OIII"}], "EXPTIME": [{"value": "180.0"}],
              "INSTRUME": [{"value": "ASI2600MM"}]}
        m = self._read(STRIPPED_PROPS, kw)
        self.assertEqual(m["filter"], "OIII")
        self.assertEqual(m["exptime"], 180.0)
        self.assertEqual(m["camera"], "ASI2600MM")

    def test_a_colour_camera_name_from_the_property_sets_colour(self):
        props = dict(STRIPPED_PROPS)
        props["Instrument:Camera:Name"] = prop("QHY268C")
        self.assertTrue(self._read(props)["colour"])


class TestPixInsightHistoryCount(unittest.TestCase):
    def test_reads_the_integration_count(self):
        raw = html.escape('<parameter id="numberOfImages" value="59"/>')
        self.assertEqual(bam.ncombine_from_pixinsight_history(raw), 59)

    def test_last_integration_wins(self):
        raw = html.escape('<parameter id="numberOfImages" value="12"/>'
                          '<parameter id="numberOfImages" value="59"/>')
        self.assertEqual(bam.ncombine_from_pixinsight_history(raw), 59)

    def test_channel_count_is_not_mistaken_for_a_subframe_count(self):
        raw = html.escape('<parameter id="numberOfImages" value="59"/>'
                          '<parameter id="numberOfChannels" value="3"/>')
        self.assertEqual(bam.ncombine_from_pixinsight_history(raw), 59)

    def test_zero_is_rejected(self):
        raw = html.escape('<parameter id="numberOfImages" value="0"/>')
        self.assertIsNone(bam.ncombine_from_pixinsight_history(raw))

    def test_empty_and_none_are_safe(self):
        self.assertIsNone(bam.ncombine_from_pixinsight_history(None))
        self.assertIsNone(bam.ncombine_from_pixinsight_history(""))
        self.assertIsNone(bam.ncombine_from_pixinsight_history("no integration here"))

    def test_it_reaches_the_meta_dict(self):
        props = dict(STRIPPED_PROPS)
        props["PixInsight:ProcessingHistory"] = prop(
            html.escape('<parameter id="numberOfImages" value="59"/>'))
        fake = fake_xisf(props)
        with mock.patch.dict(sys.modules, {"xisf": mock.MagicMock(XISF=fake)}):
            m = bam.read_xisf_meta(Path("/a/M 17/m.xisf"))
        self.assertEqual(m["ncombine"], 59)

    def test_an_explicit_keyword_still_wins(self):
        props = dict(STRIPPED_PROPS)
        props["PixInsight:ProcessingHistory"] = prop(
            html.escape('<parameter id="numberOfImages" value="59"/>'))
        fake = fake_xisf(props, {"NCOMBINE": [{"value": "40"}]})
        with mock.patch.dict(sys.modules, {"xisf": mock.MagicMock(XISF=fake)}):
            m = bam.read_xisf_meta(Path("/a/M 17/m.xisf"))
        self.assertEqual(m["ncombine"], 40)


class TestFilterTokenInFilename(unittest.TestCase):
    def test_wbpp_filter_token_is_read(self):
        p = Path("/a/M 17/masterLight_BIN-1_6252x4176_EXPOSURE-300.00s_FILTER-Ha_mono.xisf")
        self.assertEqual(bam.filter_from_path(p), "Ha")

    def test_oiii_token_is_read(self):
        p = Path("/a/masterLight_BIN-1_EXPOSURE-300.00s_FILTER-Oiii_mono.xisf")
        self.assertEqual(bam.filter_from_path(p), "OIII")

    def test_underscore_form_is_read(self):
        p = Path("/a/masterLight_FILTER_SII_mono.xisf")
        self.assertEqual(bam.filter_from_path(p), "SII")

    def test_an_explicit_token_outranks_the_parent_folder(self):
        """WBPP wrote the token on purpose, so it beats a folder name.

        An unfamiliar name passes through unchanged and is then reported
        under "Unrecognised filter names", which is where the user can see
        it and ask for the catalogue to be extended.
        """
        p = Path("/a/Ha/masterLight_FILTER-Zorb_mono.xisf")
        self.assertEqual(bam.filter_from_path(p), "Zorb")

    def test_a_folder_name_still_works_with_no_token(self):
        p = Path("/a/Ha/masterLight_mono.xisf")
        self.assertEqual(bam.filter_from_path(p), "Ha")


if __name__ == "__main__":
    unittest.main()
