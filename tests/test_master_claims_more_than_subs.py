"""A master that claims more time than the unique subs behind it is not trusted.

The Christmas Tree Cluster case. Its nightly masters were stacked from pipeline
job folders (Images/calibrated/<hash>) holding each frame three or four times,
so a master reads 5.5 h of luminance where 2.92 h were ever shot. The scan now
counts those frames once and drops the job-folder copies, which used to leave
the master with no scanned subs at all and so exempt from the anomaly check.
"""
from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402

EXP = 60.0
RAW = "/Images/RAW_SCANNED/190MN/Christmas Tree Cluster/LIGHTS"
JOB = "/Images/calibrated/11e1dc0a18e2"

# filter -> (captured hours, integrated hours) as the manifest read on 27 Oct.
CHRISTMAS_TREE = {
    "L": (2.92, 5.5), "R": (1.38, 1.6), "G": (1.12, 2.57), "B": (1.19, 2.28),
    "Ha": (1.25, 4.0), "SII": (1.38, 4.42), "OIII": (0.33, 1.67),
}


def _row(band, *, absorbed=(), master_root=JOB, captured=1.0, integrated=3.0,
         extra=()):
    members = [
        _subs(n=round(captured * 3600 / EXP), exptime=EXP, filt=band,
              bucket=f"{RAW}/{band}", path=f"{RAW}/{band}/l_0001.fits",
              session_root=f"{RAW}/{band}"),
        _master(ncombine=round(integrated * 3600 / EXP), exptime=EXP, filt=band,
                path=f"{JOB}/nightly_master_{band}.fit", session_root=master_root),
        *extra,
    ]
    members[0]["_absorbed_session_roots"] = list(absorbed)
    return bam.build_filters_data(members)[band]["rigs"][RIG]


class TestChristmasTree(unittest.TestCase):
    def test_every_filter_is_flagged_and_headlined_on_captured_hours(self):
        for band, (captured, integrated) in CHRISTMAS_TREE.items():
            with self.subTest(band=band):
                row = _row(band, absorbed=[JOB], captured=captured,
                           integrated=integrated)
                self.assertTrue(row["integration_anomaly"])
                self.assertEqual(row["headline_basis"], "captured")
                self.assertAlmostEqual(row["headline_hours"], row["captured_hours"],
                                       places=6)
                self.assertAlmostEqual(row["integrated_hours"], integrated, places=2)

    def test_the_headline_for_the_target_is_the_unique_captured_total(self):
        total = 0.0
        for band, (captured, integrated) in CHRISTMAS_TREE.items():
            total += _row(band, absorbed=[JOB], captured=captured,
                          integrated=integrated)["headline_hours"]
        self.assertAlmostEqual(total, 9.57, delta=0.1)

    def test_without_the_dropped_root_the_old_behaviour_is_kept(self):
        row = _row("L", absorbed=[], captured=2.92, integrated=5.5)
        self.assertTrue(row["masters_without_subs"])
        self.assertFalse(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "integrated")


class TestHonestMastersAreLeftAlone(unittest.TestCase):
    def test_a_master_within_tolerance_keeps_an_integrated_headline(self):
        row = _row("Ha", absorbed=[JOB], captured=3.0, integrated=3.1)
        self.assertFalse(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "integrated")
        self.assertAlmostEqual(row["headline_hours"], 3.1, places=2)

    def test_a_master_whose_subs_were_never_scanned_stays_as_it_was(self):
        row = _row("Ha", absorbed=[], master_root="/Gone/session",
                   captured=1.0, integrated=8.0)
        self.assertTrue(row["masters_without_subs"])
        self.assertFalse(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "integrated")
        self.assertAlmostEqual(row["headline_hours"], 8.0, places=2)

    def test_a_master_with_no_session_stays_as_it_was(self):
        row = _row("Ha", absorbed=[JOB], master_root=None,
                   captured=1.0, integrated=8.0)
        self.assertFalse(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "integrated")


class TestOnlyTheIntegrationMasterIsJudged(unittest.TestCase):
    def test_a_stray_master_without_subs_does_not_hide_the_deep_one(self):
        library = _master(ncombine=30, exptime=EXP, filt="Ha",
                          path="/Library/masters/full_master_Ha.fit",
                          session_root="/Library")
        row = _row("Ha", absorbed=[JOB], captured=1.0, integrated=4.0,
                   extra=[library])
        self.assertTrue(row["masters_without_subs"])
        self.assertTrue(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "captured")

    def test_a_deepest_master_without_subs_hides_the_anomaly(self):
        shallow = _master(ncombine=60, exptime=EXP, filt="Ha",
                          path=f"{JOB}/nightly_master_Ha.fit", session_root=JOB)
        deep = _master(ncombine=600, exptime=EXP, filt="Ha",
                       path="/Library/masters/full_master_Ha.fit",
                       session_root="/Library")
        members = [
            _subs(n=30, exptime=EXP, filt="Ha", bucket=f"{RAW}/Ha",
                  path=f"{RAW}/Ha/l.fits", session_root=f"{RAW}/Ha"),
            shallow, deep,
        ]
        members[0]["_absorbed_session_roots"] = [JOB]
        row = bam.build_filters_data(members)["Ha"]["rigs"][RIG]
        self.assertFalse(row["integration_anomaly"])
        self.assertEqual(row["headline_basis"], "integrated")


class TestBandRowKeepsTheFlag(unittest.TestCase):
    def test_the_band_headline_is_captured_and_still_flagged(self):
        members = [
            _subs(n=60, exptime=EXP, filt="L", bucket=f"{RAW}/L",
                  path=f"{RAW}/L/l.fits", session_root=f"{RAW}/L"),
            _master(ncombine=300, exptime=EXP, filt="L",
                    path=f"{JOB}/nightly_master_L.fit", session_root=JOB),
        ]
        members[0]["_absorbed_session_roots"] = [JOB]
        band = bam.build_filters_data(members)["L"]
        self.assertTrue(band["integration_anomaly"])
        self.assertEqual(band["headline_basis"], "captured")
        self.assertAlmostEqual(band["headline_hours"], 1.0, places=6)
        self.assertAlmostEqual(band["total_hours"], 1.0, places=6)
        self.assertAlmostEqual(band["integrated_hours"], 5.0, places=6)
        self.assertEqual(list(band["sources"]), ["L"])
        self.assertAlmostEqual(band["sources"]["L"], 1.0, places=6)

    def test_the_anomaly_still_reaches_the_integrity_flag_rows(self):
        row = _row("L", absorbed=[JOB], captured=1.0, integrated=5.0)
        flags = bam.build_integrity_hour_flags(
            [{"target_id": 30, "filters": {"L": {"rigs": {RIG: row}}}}],
            uncoordinated_captured_hours=0.0)
        r = flags["integration_anomalies"][0]
        self.assertEqual(r["basis"], "captured")
        self.assertAlmostEqual(r["integrated_hours"], 5.0, places=2)
        self.assertAlmostEqual(r["reference_hours"], 1.0, places=2)


def _block(bucket, times, filt="R", **over):
    times = list(times)
    b = {"bucket": bucket, "filter": filt, "exptime": EXP, "n_subs": len(times),
         "total_hours": len(times) * EXP / 3600.0,
         "_capture_times": Counter(times), "first_date_obs": min(times),
         "last_date_obs": max(times), "has_wcs": False,
         "telescope": "190MN", "camera": "ASI2600MM Pro",
         "_session_root": bucket, "_counts_captured": True}
    b.update(over)
    return b


def _times(count, offset=0):
    return [f"2024-12-28T{20 + (i * 75) // 3600:02d}:{(i * 75) % 3600 // 60:02d}:"
            f"{(i * 75) % 60:02d}" for i in range(offset, offset + count)]


class TestAbsorbedRoots(unittest.TestCase):
    def test_a_job_folder_dropped_as_a_copy_offers_its_root(self):
        raw = _block(f"{RAW}/R", _times(70))
        job = _block(JOB, _times(70))
        kept, _ = bam.collapse_copied_sub_blocks([raw, job])
        self.assertEqual([b["bucket"] for b in kept], [f"{RAW}/R"])
        out = bam.absorbed_session_roots([raw, job], kept)
        self.assertEqual(out, {("190mn|asi2600mm pro", "R"): {JOB}})

    def test_a_trimmed_folder_stays_and_offers_nothing_new(self):
        raw = _block(f"{RAW}/R", _times(70))
        job = _block(JOB, _times(70) + _times(30, offset=70))
        kept, _ = bam.trim_partially_copied_sub_blocks([raw, job])
        self.assertEqual(len(kept), 2)
        self.assertEqual(bam.absorbed_session_roots([raw, job], kept), {})

    def test_a_job_folder_dropped_by_the_trim_offers_its_root(self):
        raw = _block(f"{RAW}/R", _times(70))
        job = _block(JOB, _times(70)[:40])
        other = _block("/Images/calibrated/7084edf06536", _times(70)[30:])
        kept, _ = bam.trim_partially_copied_sub_blocks([raw, job, other])
        out = bam.absorbed_session_roots([raw, job, other], kept)
        dropped = {b["bucket"] for b in (raw, job, other)} - {b["bucket"] for b in kept}
        self.assertTrue(dropped)
        self.assertEqual(out.get(("190mn|asi2600mm pro", "R"), set()), dropped)

    def test_roots_are_kept_apart_by_rig_and_filter(self):
        raw = _block(f"{RAW}/R", _times(70))
        job = _block(JOB, _times(70))
        other_rig = _block("/Images/calibrated/aaaa", _times(70), telescope="RedCat 51")
        kept = [raw]
        out = bam.absorbed_session_roots([raw, job, other_rig], kept)
        self.assertEqual(set(out), {("190mn|asi2600mm pro", "R"),
                                    ("redcat 51|asi2600mm pro", "R")})

    def test_an_uncaptured_or_rootless_folder_offers_nothing(self):
        raw = _block(f"{RAW}/R", _times(70))
        junk = _block("/x", _times(70), _counts_captured=False)
        rootless = _block("/y", _times(70), _session_root=None)
        self.assertEqual(bam.absorbed_session_roots([raw, junk, rootless], [raw]), {})


class TestMasterInAJobFolderHasItsOwnRoot(unittest.TestCase):
    def test_a_master_in_a_plain_folder_resolves_to_that_folder(self):
        m = {"path": f"{JOB}/nightly_master_R.fit", "filter": "R", "role": "master",
             "ncombine": 90, "exptime": EXP, "has_wcs": True,
             "telescope": "190MN", "camera": "ASI2600MM Pro"}
        out = bam.prepare_sub_blocks([], [m])
        self.assertEqual(out["master_plain_roots"], {m["path"]: JOB})

    def test_a_master_in_a_stage_folder_is_left_to_the_session_logic(self):
        m = {"path": "/S/sess1/master/M31_Ha.xisf", "filter": "Ha", "role": "master",
             "ncombine": 36, "exptime": 300.0, "has_wcs": True,
             "telescope": "190MN", "camera": "ASI2600MM"}
        self.assertEqual(bam.prepare_sub_blocks([], [m])["master_plain_roots"], {})


if __name__ == "__main__":
    unittest.main()
