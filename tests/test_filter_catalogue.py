"""Data-driven filter catalogue sourced from AstroBin (issue #63 follow-up).

The catalogue in ``data/filter_catalogue.json`` fills in filter names the hand
tables in ``build_archive_manifest.py`` don't know. It is consulted by
``canon_filter``/``bands_for`` only after the hand tables have had a chance,
so the existing exact-match behaviour never regresses.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_archive_manifest as bam  # noqa: E402


def _fake_catalogue(tmpdir: Path, filters: list[dict]) -> Path:
    path = Path(tmpdir) / "filter_catalogue.json"
    path.write_text(json.dumps({
        "fetched": "2026-09-04",
        "source": "test",
        "filters": filters,
    }), encoding="utf-8")
    return path


class _PatchedCatalogue(unittest.TestCase):
    """Points the module at a fabricated catalogue file for the duration of a test."""

    def setUp(self):
        self._orig_path = bam.FILTER_CATALOGUE_PATH
        self._orig_cache = bam._FILTER_CATALOGUE
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        bam.FILTER_CATALOGUE_PATH = self._orig_path
        bam._FILTER_CATALOGUE = self._orig_cache

    def use(self, filters: list[dict]):
        bam.FILTER_CATALOGUE_PATH = _fake_catalogue(self._tmp.name, filters)
        bam._FILTER_CATALOGUE = None


class TestNormalisation(_PatchedCatalogue):
    def test_brand_and_punctuation_variants_resolve_to_same_entry(self):
        self.use([
            {"brand": "Optolong", "name": "L-eXtreme", "astrobin_type": "MULTIBAND",
             "bands": ["Ha", "OIII"], "bandwidth_nm": 7.0,
             "resolution": "name", "evidence": "test"},
        ])
        variants = ["Optolong L-eXtreme", "optolong l extreme", "L-EXTREME", "l-extreme"]
        results = {bam.canon_filter(v) for v in variants}
        self.assertEqual(results, {"L-eXtreme"})
        for v in variants:
            self.assertEqual(bam.bands_for(bam.canon_filter(v), colour=True), ["Ha", "OIII"])


class TestMultibandResolution(_PatchedCatalogue):
    def test_multiband_product_returns_catalogue_bands(self):
        self.use([
            {"brand": "Askar", "name": "ColorMagic D1", "astrobin_type": "MULTIBAND",
             "bands": ["Ha", "OIII"], "bandwidth_nm": 6.0,
             "resolution": "name", "evidence": "test"},
        ])
        canon = bam.canon_filter("Askar ColorMagic D1")
        self.assertEqual(bam.bands_for(canon, colour=True), ["Ha", "OIII"])


class TestUnknownName(_PatchedCatalogue):
    def test_unknown_name_still_returns_itself(self):
        self.use([
            {"brand": "Askar", "name": "ColorMagic D1", "astrobin_type": "MULTIBAND",
             "bands": ["Ha", "OIII"], "bandwidth_nm": 6.0,
             "resolution": "name", "evidence": "test"},
        ])
        before = bam.UNRECOGNISED_FILTER_COUNTS["Zorblatt 9000 Filter"]
        canon = bam.canon_filter("Zorblatt 9000 Filter")
        self.assertEqual(canon, "Zorblatt 9000 Filter")
        self.assertEqual(bam.bands_for(canon, colour=False), ["Zorblatt 9000 Filter"])
        self.assertEqual(
            bam.UNRECOGNISED_FILTER_COUNTS["Zorblatt 9000 Filter"], before + 1
        )


class TestHandTableWins(_PatchedCatalogue):
    def test_hand_table_beats_a_conflicting_catalogue_entry(self):
        # A fabricated catalogue entry that, if consulted, would relabel the
        # plain "Ha" filter and give it different bands. The hand tables
        # (FILTER_CANON / _MULTI_BAND) must win regardless.
        self.use([
            {"brand": "Generic", "name": "Ha", "astrobin_type": "MULTIBAND",
             "bands": ["Ha", "OIII", "SII"], "bandwidth_nm": None,
             "resolution": "default", "evidence": "test"},
        ])
        self.assertEqual(bam.canon_filter("HA"), "Ha")
        self.assertEqual(bam.bands_for("Ha", colour=False), ["Ha"])

    def test_hand_table_multiband_beats_catalogue(self):
        self.use([
            {"brand": "Optolong", "name": "L-eXtreme", "astrobin_type": "MULTIBAND",
             "bands": ["Ha", "OIII", "SII"], "bandwidth_nm": None,
             "resolution": "default", "evidence": "test (deliberately wrong)"},
        ])
        # L-eXtreme is already in the hand-coded _MULTI_BAND table as Ha+OIII;
        # the catalogue's (fabricated, wrong) Ha+OIII+SII must not win.
        self.assertEqual(bam.bands_for("L-eXtreme", colour=True), ["Ha", "OIII"])


class TestRealCatalogueFile(unittest.TestCase):
    """Sanity checks against the actual committed data/filter_catalogue.json."""

    def test_file_loads_and_has_entries(self):
        self.assertTrue(bam.FILTER_CATALOGUE_PATH.exists())
        data = json.loads(bam.FILTER_CATALOGUE_PATH.read_text(encoding="utf-8"))
        self.assertGreater(len(data["filters"]), 1000)

    def test_a_real_multiband_row_resolves_via_catalogue(self):
        data = json.loads(bam.FILTER_CATALOGUE_PATH.read_text(encoding="utf-8"))
        multiband = [f for f in data["filters"] if f["astrobin_type"] == "MULTIBAND"
                     and f["resolution"] in ("name", "maker_page")]
        self.assertTrue(multiband)
        sample = multiband[0]
        raw = f"{sample['brand']} {sample['name']}"
        canon = bam.canon_filter(raw)
        self.assertEqual(bam.bands_for(canon, colour=True), sample["bands"])


class TestSvbonyNamesIssue158(unittest.TestCase):
    """Names from a user's archive that the report listed as unrecognised."""

    def setUp(self):
        bam.UNRECOGNISED_FILTER_COUNTS.clear()

    def _bands(self, raw, colour=True):
        return bam.bands_for(bam.canon_filter(raw), colour=colour)

    def test_sv220_ha_oiii_3nm(self):
        self.assertEqual(bam.canon_filter("SV220_Ha-Oiii_3nm"), "SV220 Ha-OIII 3nm")
        self.assertEqual(self._bands("SV220_Ha-Oiii_3nm"), ["Ha", "OIII"])

    def test_sv220_sii_oiii_7nm(self):
        self.assertEqual(bam.canon_filter("SV220_Sii-Oiii_7nm"), "SV220 SII-OIII 7nm")
        self.assertEqual(self._bands("SV220_Sii-Oiii_7nm"), ["SII", "OIII"])

    def test_sv220_7nm_ha_oiii_in_any_style(self):
        for raw in ("SV220_Ha-Oiii_7nm", "sv220 ha oiii 7nm", "SVBONY SV220 Ha-OIII 7nm"):
            self.assertEqual(bam.canon_filter(raw), "SV220 Ha-OIII 7nm", raw)

    def test_sv240_is_ha_oiii(self):
        self.assertEqual(bam.canon_filter("SV240"), "SV240")
        self.assertEqual(self._bands("SV240"), ["Ha", "OIII"])

    def test_non_is_no_filter(self):
        self.assertEqual(bam.canon_filter("non"), "NoFilter")
        self.assertEqual(self._bands("non"), ["R", "G", "B"])
        self.assertEqual(self._bands("non", colour=False), ["L"])

    def test_other_short_strings_are_not_no_filter(self):
        for raw in ("no", "nope", "nonsense"):
            self.assertNotEqual(bam.canon_filter(raw), "NoFilter", raw)

    def test_none_of_the_four_are_reported_unrecognised(self):
        for raw in ("SV220_Ha-Oiii_3nm", "SV220_Sii-Oiii_7nm", "non", "SV240"):
            bam.canon_filter(raw)
        self.assertEqual(dict(bam.UNRECOGNISED_FILTER_COUNTS), {})

    def test_existing_sv220_spellings_unchanged(self):
        for raw in ("SV220", "Svbony SV220", "sv220"):
            self.assertEqual(bam.canon_filter(raw), "SV220", raw)
            self.assertEqual(self._bands(raw), ["Ha", "OIII"])


if __name__ == "__main__":
    unittest.main()
