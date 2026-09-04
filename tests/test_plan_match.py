"""Tests for POST /api/plans/match and GET /api/fingerprints.

The NINA companion plugin posts a fingerprint of the connected rig and
gets a verdict per plan back. Everything the plugin renders comes out of
these two endpoints, so the rules they encode (pixel scale within 15%,
field of view at least 90% of the plan's, every goal filter reachable)
are pinned here rather than only in the spec.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

# A 540mm scope and two different cameras that both use the Sony IMX571
# sensor (3.76 um pixels, 6252 x 4176). Same sensor behind the same scope
# is the case the plugin has to get right: different brand, identical
# optics, so every plan for one should fit the other.
TEL_540 = {"id": "tel-540", "name": "Esprit 100", "focal_length_mm": 540}
TEL_250 = {"id": "tel-250", "name": "Samyang 135", "focal_length_mm": 250}
CAM_2600MM = {"id": "cam-2600", "name": "ASI2600MM Pro",
              "sensor_px": [6252, 4176], "pixel_size_um": 3.76, "colour": False}
CAM_268M = {"id": "cam-268", "name": "QHY268M",
            "sensor_px": [6252, 4176], "pixel_size_um": 3.76, "colour": False}
# 2/3 the sensor and a bigger pixel: same-ish scale on a shorter scope but
# a much smaller field.
CAM_SMALL = {"id": "cam-small", "name": "ASI533MM",
             "sensor_px": [3008, 3008], "pixel_size_um": 3.76, "colour": False}


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.GEAR_PATH = td / "gear.json"
    app_module.PLANS_PATH = td / "plans.json"
    app_module.FINGERPRINTS_PATH = td / "fingerprints.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for name in ("_gear_cache", "_plans_cache", "_fingerprints_cache",
                 "_destinations_cache", "_gear_cache_mtime", "_plans_cache_mtime",
                 "_fingerprints_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, name, None)
    return td


def _write_gear(telescopes, cameras):
    app_module.GEAR_PATH.write_text(
        json.dumps({"version": 2, "telescopes": telescopes, "cameras": cameras}),
        encoding="utf-8")
    app_module._gear_cache = None
    app_module._gear_cache_mtime = None


def _write_plans(plans):
    app_module.PLANS_PATH.write_text(
        json.dumps({"version": 1, "plans": plans}), encoding="utf-8")
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None


def _plan(plan_id, telescope_id, camera_id, filters, **extra):
    p = {
        "id": plan_id,
        "project_name": "Test",
        "target": {"name": plan_id, "center_ra_deg": 83.8, "center_dec_deg": -5.4,
                   "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 10}},
        "telescope_id": telescope_id,
        "camera_id": camera_id,
        "filter_goals": {f: {"target_hours": 3.0, "sub_exposure_s": 300} for f in filters},
    }
    p.update(extra)
    return p


def _fingerprint(**overrides):
    fp = {
        "profile_name": "Travel rig",
        "mode": "fit",
        "camera": {"name": "QHY268M", "sensor_px": [6252, 4176],
                   "pixel_size_um": 3.76, "colour": False, "bin": 1},
        "filters": ["L", "R", "G", "B", "Ha", "OIII", "SII"],
        "mount": {"name": "EQ6-R Pro"},
        "site": {"lat": -33.87, "lon": 151.21, "elev_m": 40},
        "focal_length_mm": {"profile": 540.0, "solved": 540.4, "source": "solved"},
        "rotation_deg": 12.3,
        "nina_version": "3.3.0.1041",
    }
    fp.update(overrides)
    return fp


def _match(client, fp):
    r = client.post("/api/plans/match", json=fp)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _verdicts(body):
    return {p["id"]: p["match"]["verdict"] for p in body["plans"]}


class TestMatchGeometry(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_same_sensor_same_scope_fits(self):
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha", "OIII"])])
        body = _match(self.client, _fingerprint())
        self.assertEqual(_verdicts(body), {"p-540": "fit"})
        m = body["plans"][0]["match"]
        self.assertAlmostEqual(m["pixel_scale_ratio"], 1.0, places=2)
        self.assertEqual(m["filters_missing"], [])
        self.assertEqual(body["summary"]["fit"], 1)

    def test_short_lens_does_not_fit_a_long_scope_plan(self):
        """A 250mm lens on the same camera is half the plan's pixel scale
        and twice its field. Scale alone is enough to rule it out."""
        _write_gear([TEL_540, TEL_250], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        fp = _fingerprint(focal_length_mm={"profile": 250.0, "solved": 250.6,
                                           "source": "solved"})
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-540": "no_fit"})
        self.assertTrue(any("Pixel scale" in r for r in body["plans"][0]["match"]["reasons"]))

    def test_solved_focal_length_wins_over_profile(self):
        """The profile says 250mm but the plate solve says 540mm — a
        reducer or simply a stale profile. The solve is what's real."""
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        fp = _fingerprint(focal_length_mm={"profile": 250.0, "solved": 540.4,
                                           "source": "solved"})
        self.assertEqual(_verdicts(_match(self.client, fp)), {"p-540": "fit"})

    def test_smaller_sensor_is_no_fit(self):
        """Right pixel scale, 48% of the field: the plan's framing doesn't
        fit on the sensor, so it's a miss even though the scale matches."""
        _write_gear([TEL_540], [CAM_2600MM, CAM_SMALL])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        fp = _fingerprint(camera={"name": "ASI533MM", "sensor_px": [3008, 3008],
                                  "pixel_size_um": 3.76, "colour": False, "bin": 1})
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-540": "no_fit"})
        self.assertTrue(any("Field of view" in r for r in body["plans"][0]["match"]["reasons"]))

    def test_slightly_small_sensor_is_a_warning(self):
        """85% of the plan's field in one axis: shootable, but the framing
        is tighter than planned, so warn rather than hide the plan."""
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        fp = _fingerprint(camera={"name": "cropped", "sensor_px": [6252, 3550],
                                  "pixel_size_um": 3.76, "colour": False, "bin": 1})
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-540": "fit_with_warnings"})
        self.assertTrue(any("Field of view" in r for r in body["plans"][0]["match"]["reasons"]))

    def test_larger_field_is_still_a_fit(self):
        _write_gear([TEL_540], [CAM_SMALL])
        _write_plans([_plan("p-small", "tel-540", "cam-small", ["Ha"])])
        self.assertEqual(_verdicts(_match(self.client, _fingerprint())), {"p-small": "fit"})

    def test_binning_is_taken_into_account(self):
        """bin 2 doubles the pixel scale, which puts a 540mm plan well
        outside the 15% window, and leaves the field of view unchanged."""
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        fp = _fingerprint(camera={"name": "QHY268M", "sensor_px": [6252, 4176],
                                  "pixel_size_um": 3.76, "colour": False, "bin": 2})
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-540": "no_fit"})
        self.assertAlmostEqual(body["plans"][0]["match"]["fov_ratio"][0], 1.0, places=2)

    def test_explicit_pixel_scale_is_used_when_supplied(self):
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        # A scale NINA reports that disagrees with the derived one wins.
        fp = _fingerprint(pixel_scale_arcsec=3.0)
        self.assertEqual(_verdicts(_match(self.client, fp)), {"p-540": "no_fit"})


class TestMatchFilters(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_gear([TEL_540], [CAM_2600MM])

    def test_mono_plan_missing_ha_is_no_fit(self):
        _write_plans([_plan("p-ha", "tel-540", "cam-2600", ["Ha", "OIII"])])
        body = _match(self.client, _fingerprint(filters=["L", "R", "G", "B", "OIII"]))
        self.assertEqual(_verdicts(body), {"p-ha": "no_fit"})
        self.assertEqual(body["plans"][0]["match"]["filters_missing"], ["Ha"])

    def test_branded_filter_names_canonicalise(self):
        _write_plans([_plan("p-ha", "tel-540", "cam-2600", ["Ha"])])
        body = _match(self.client, _fingerprint(filters=["Antlia Ha"]))
        self.assertEqual(_verdicts(body), {"p-ha": "fit"})

    def test_osc_fingerprint_fits_an_rgb_plan(self):
        """A one-shot colour camera with no filter wheel shoots R, G and B
        in every frame, exactly as the scanner's bands_for says."""
        _write_plans([_plan("p-rgb", "tel-540", "cam-2600", ["R", "G", "B"])])
        fp = _fingerprint(
            camera={"name": "ASI2600MC Pro", "sensor_px": [6252, 4176],
                    "pixel_size_um": 3.76, "colour": True, "bin": 1},
            filters=[])
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-rgb": "fit"})
        self.assertEqual(body["plans"][0]["match"]["reasons"], [])

    def test_dual_band_fits_ha_oiii_with_a_warning(self):
        _write_plans([_plan("p-nb", "tel-540", "cam-2600", ["Ha", "OIII"])])
        fp = _fingerprint(
            camera={"name": "ASI2600MC Pro", "sensor_px": [6252, 4176],
                    "pixel_size_um": 3.76, "colour": True, "bin": 1},
            filters=["L-eXtreme"])
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-nb": "fit_with_warnings"})
        reasons = body["plans"][0]["match"]["reasons"]
        self.assertEqual(len(reasons), 2)
        self.assertTrue(all("dual band" in r for r in reasons))

    def test_dual_band_does_not_cover_sii(self):
        _write_plans([_plan("p-sho", "tel-540", "cam-2600", ["Ha", "OIII", "SII"])])
        fp = _fingerprint(
            camera={"name": "ASI2600MC Pro", "sensor_px": [6252, 4176],
                    "pixel_size_um": 3.76, "colour": True, "bin": 1},
            filters=["L-eXtreme"])
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-sho": "no_fit"})
        self.assertEqual(body["plans"][0]["match"]["filters_missing"], ["SII"])

    def test_zero_hour_goals_are_not_requirements(self):
        plan = _plan("p-l", "tel-540", "cam-2600", ["L"])
        plan["filter_goals"]["SII"] = {"target_hours": 0}
        _write_plans([plan])
        self.assertEqual(_verdicts(_match(self.client, _fingerprint(filters=["L"]))),
                         {"p-l": "fit"})


class TestMatchUnconstrained(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_plan_with_no_gear_is_unconstrained(self):
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([
            _plan("p-none", "", "", ["Ha"]),
            _plan("p-no-cam", "tel-540", "", ["Ha"]),
        ])
        body = _match(self.client, _fingerprint())
        self.assertEqual(_verdicts(body),
                         {"p-none": "unconstrained", "p-no-cam": "unconstrained"})
        self.assertEqual(body["summary"]["unconstrained"], 2)
        self.assertIsNone(body["plans"][0]["match"]["pixel_scale_ratio"])

    def test_plan_pointing_at_missing_gear_is_unconstrained(self):
        """A plan whose telescope_id was deleted from gear.json can't be
        scored, and that isn't the connected rig's fault."""
        _write_gear([], [])
        _write_plans([_plan("p-ghost", "tel-540", "cam-2600", ["Ha"])])
        self.assertEqual(_verdicts(_match(self.client, _fingerprint())),
                         {"p-ghost": "unconstrained"})


class TestMatchResponseShape(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_gear([TEL_540], [CAM_2600MM])

    def test_plans_come_back_expanded(self):
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"],
                            target={"name": "M42", "center_ra_deg": 83.8,
                                    "center_dec_deg": -5.4, "rotation_deg": 0,
                                    "mosaic": {"rows": 2, "cols": 2, "overlap_pct": 10}})])
        entry = _match(self.client, _fingerprint())["plans"][0]
        self.assertEqual(entry["gear"]["telescope"]["id"], "tel-540")
        self.assertEqual(entry["gear"]["camera"]["sensor_width_px"], 6252)
        self.assertAlmostEqual(entry["gear"]["pixel_scale_arcsec"], 1.436, places=2)
        self.assertEqual(len(entry["panels"]), 4)

    def test_expand_on_get_plans_is_opt_in(self):
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        plain = self.client.get("/api/plans").get_json()["plans"][0]
        self.assertNotIn("gear", plain)
        expanded = self.client.get("/api/plans?expand=gear,panels").get_json()["plans"][0]
        self.assertEqual(expanded["gear"]["telescope"]["name"], "Esprit 100")
        self.assertEqual(len(expanded["panels"]), 1)

    def test_mode_is_echoed_and_does_not_change_verdicts(self):
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])
        for mode in ("fit", "everything"):
            body = _match(self.client, _fingerprint(mode=mode))
            self.assertEqual(body["mode"], mode)
            self.assertEqual(_verdicts(body), {"p-540": "fit"})

    def test_summary_counts_every_plan(self):
        _write_plans([
            _plan("a", "tel-540", "cam-2600", ["Ha"]),
            _plan("b", "tel-540", "cam-2600", ["SII"]),
            _plan("c", "", "", ["Ha"]),
        ])
        body = _match(self.client, _fingerprint(filters=["Ha"]))
        self.assertEqual(body["summary"],
                         {"fit": 1, "fit_with_warnings": 0, "no_fit": 1, "unconstrained": 1})
        self.assertEqual(sum(body["summary"].values()), len(body["plans"]))


class TestMatchValidation(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])

    def _reject(self, fp, fragment):
        r = self.client.post("/api/plans/match", json=fp)
        self.assertEqual(r.status_code, 400)
        self.assertIn(fragment, r.get_json()["error"])

    def test_camera_required(self):
        fp = _fingerprint()
        del fp["camera"]
        self._reject(fp, "camera")

    def test_focal_length_required(self):
        fp = _fingerprint()
        del fp["focal_length_mm"]
        self._reject(fp, "focal_length_mm")

    def test_non_finite_numbers_rejected(self):
        fp = _fingerprint()
        fp["camera"]["pixel_size_um"] = float("nan")
        self._reject(fp, "pixel_size_um")

    def test_bad_mode_rejected(self):
        self._reject(_fingerprint(mode="maybe"), "mode")

    def test_bad_filters_rejected(self):
        self._reject(_fingerprint(filters="Ha"), "filters")

    def test_everything_optional_is_optional(self):
        """Only camera and focal length are required: a rig with no filter
        wheel, no mount driver and no site still gets an answer."""
        fp = {"camera": {"name": "QHY268M", "sensor_px": [6252, 4176],
                         "pixel_size_um": 3.76},
              "focal_length_mm": 540.4}
        body = _match(self.client, fp)
        self.assertEqual(_verdicts(body), {"p-540": "no_fit"})  # no filters at all


class TestFingerprintStore(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_gear([TEL_540], [CAM_2600MM])
        _write_plans([_plan("p-540", "tel-540", "cam-2600", ["Ha"])])

    def test_round_trip(self):
        self.assertEqual(self.client.get("/api/fingerprints").get_json()["profiles"], {})
        body = _match(self.client, _fingerprint())
        stored = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(list(stored), ["Travel rig"])
        entry = stored["Travel rig"]
        self.assertEqual(entry["fingerprint_id"], body["fingerprint_id"])
        self.assertEqual(entry["summary"], body["summary"])
        self.assertEqual(entry["mode"], "fit")
        self.assertEqual(entry["fingerprint"]["camera"]["name"], "QHY268M")
        self.assertTrue(entry["received_at"].endswith("+00:00"))
        # Written to disk, not just held in the cache.
        on_disk = json.loads(app_module.FINGERPRINTS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["profiles"]["Travel rig"]["fingerprint_id"],
                         body["fingerprint_id"])

    def test_last_report_per_profile_wins(self):
        _match(self.client, _fingerprint())
        _match(self.client, _fingerprint(nina_version="3.3.0.2000"))
        _match(self.client, _fingerprint(profile_name="Backyard"))
        stored = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(sorted(stored), ["Backyard", "Travel rig"])
        self.assertEqual(stored["Travel rig"]["fingerprint"]["nina_version"], "3.3.0.2000")

    def test_id_is_stable_across_incidental_changes(self):
        """Same rig, different night: rotation, site and NINA build move,
        the id must not."""
        first = _match(self.client, _fingerprint())["fingerprint_id"]
        same = _match(self.client, _fingerprint(rotation_deg=99.0,
                                                site={"lat": 0, "lon": 0},
                                                nina_version="9.9.9"))["fingerprint_id"]
        self.assertEqual(first, same)
        other = _match(self.client, _fingerprint(
            camera={"name": "ASI533MM", "sensor_px": [3008, 3008],
                    "pixel_size_um": 3.76, "colour": False, "bin": 1}))["fingerprint_id"]
        self.assertNotEqual(first, other)

    def test_missing_profile_name_falls_back_to_the_id(self):
        fp = _fingerprint()
        del fp["profile_name"]
        body = _match(self.client, fp)
        stored = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(list(stored), [body["fingerprint_id"]])


if __name__ == "__main__":
    unittest.main()
