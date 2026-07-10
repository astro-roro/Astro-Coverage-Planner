"""Timing benchmark + correctness pin for compute_year_visibility.

Not part of the regression suite (astropy AltAz transforms are slow enough
that running this under `unittest discover` would make every CI run pay
the cost). Invoked manually:

    python tests/test_visibility_perf.py

The AltAz transform used to flatten (target x time) into one long array
via np.repeat/np.tile, which forced ERFA to recompute the per-timestamp
precession/nutation/polar-motion matrices once per target instead of once
per timestamp. That made the function ~O(n_targets) per erfa call and hurt
badly at manifest scale (~135s for 3000 targets). The fix broadcasts a
(n_targets, 1) SkyCoord against a (1, n_times) obstime so those matrices
are computed once per timestamp and shared across all targets.

This file pins two things:
  1. correctness: the broadcast transform must match a target-by-target
     reference implementation (the pre-fix code path, kept here verbatim)
     within float tolerance, for a set of targets spanning the sky
     (dec near +/-88, ra near 0/360) at two sites (north/south).
  2. performance: ~30 and ~200 target runs should land far under the
     pre-fix numbers (measured baseline: 1.4s @ 30 targets, 8.7s @ 200
     targets on this machine) -- if the speedup regresses towards linear
     in target count again, this will make it obvious.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app as app_module  # noqa: E402

YEAR = 2026
SITES = [
    dict(lat=19.82, lon=-155.47, elev_m=4205.0, min_alt_deg=30.0, label="mauna_kea (N)"),
    dict(lat=-24.63, lon=-70.40, elev_m=2635.0, min_alt_deg=30.0, label="paranal (S)"),
]

# Fixed set spanning the sky, including near-pole declinations and ra
# wraparound near 0/360 -- these are the cases most likely to expose a
# broadcast/reshape mistake.
CORRECTNESS_TARGETS = [
    {"target_id": 1, "center_ra_deg": 0.0, "center_dec_deg": 0.0},
    {"target_id": 2, "center_ra_deg": 0.5, "center_dec_deg": 87.9},
    {"target_id": 3, "center_ra_deg": 359.5, "center_dec_deg": -87.9},
    {"target_id": 4, "center_ra_deg": 45.0, "center_dec_deg": 30.0},
    {"target_id": 5, "center_ra_deg": 180.0, "center_dec_deg": -30.0},
    {"target_id": 6, "center_ra_deg": 270.0, "center_dec_deg": 60.0},
    {"target_id": 7, "center_ra_deg": 90.0, "center_dec_deg": -60.0},
    {"target_id": 8, "center_ra_deg": 12.3, "center_dec_deg": 5.5},
]


def _reference_alt_grid(targets, *, lat, lon, elev_m, year, month, sample_step_min=15):
    """Pre-fix implementation: flatten (target, time) via repeat/tile.

    Kept here (not in app.py) purely as an independent oracle for the
    correctness check below.
    """
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u
    import numpy as np
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elev_m * u.m)
    ras = np.array([float(t["center_ra_deg"]) for t in targets])
    decs = np.array([float(t["center_dec_deg"]) for t in targets])
    n_targets = len(ras)
    samples_per_day = (24 * 60) // sample_step_min

    anchor = _dt(year, month, 15, 12, 0, 0, tzinfo=_tz.utc).replace(tzinfo=None)
    times_dt = [anchor + _td(minutes=sample_step_min * i) for i in range(samples_per_day + 1)]
    t_grid = Time(times_dt)
    n_t = len(t_grid)

    ras_full = np.repeat(ras, n_t)
    decs_full = np.repeat(decs, n_t)
    times_full = Time(np.tile(t_grid.jd, n_targets), format="jd")
    sc = SkyCoord(ras_full * u.deg, decs_full * u.deg)
    alt_grid = sc.transform_to(AltAz(obstime=times_full, location=loc)).alt.deg
    return alt_grid.reshape(n_targets, n_t)


def check_correctness() -> None:
    import numpy as np

    for site in SITES:
        for month in (1, 6, 12):
            expected = _reference_alt_grid(
                CORRECTNESS_TARGETS, lat=site["lat"], lon=site["lon"],
                elev_m=site["elev_m"], year=YEAR, month=month,
            )
            # Drive the real (broadcast) code path through the public
            # function and compare its month-bin outputs to bins derived
            # from the reference alt grid directly, since
            # compute_year_visibility only returns the reduced bins, not
            # the raw grid. Re-derive peak/hours from `expected` using the
            # same reduction the function performs, and compare against
            # what compute_year_visibility actually returned.
            bins = app_module.compute_year_visibility(
                CORRECTNESS_TARGETS, lat=site["lat"], lon=site["lon"],
                elev_m=site["elev_m"], min_alt_deg=site["min_alt_deg"],
                year=YEAR,
            )
            from astropy.coordinates import AltAz, EarthLocation, get_sun
            from astropy.time import Time
            import astropy.units as u
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td

            loc = EarthLocation(lat=site["lat"] * u.deg, lon=site["lon"] * u.deg,
                                 height=site["elev_m"] * u.m)
            anchor = _dt(YEAR, month, 15, 12, 0, 0, tzinfo=_tz.utc).replace(tzinfo=None)
            times_dt = [anchor + _td(minutes=15 * i) for i in range(97)]
            t_grid = Time(times_dt)
            sun_altaz = get_sun(t_grid).transform_to(AltAz(obstime=t_grid, location=loc))
            is_dark = sun_altaz.alt.deg < -18.0

            for i, t in enumerate(CORRECTNESS_TARGETS):
                tid = int(t["target_id"])
                actual_bin = next(b for b in bins[tid] if b["month"] == month)
                alts_dark = expected[i, is_dark]
                if alts_dark.size == 0:
                    assert actual_bin["label"] == "not_visible", (
                        f"{site['label']} target {tid} month {month}: expected "
                        f"not_visible (no dark samples), got {actual_bin}"
                    )
                    continue
                exp_peak = float(np.max(alts_dark))
                exp_hours = float(np.sum(alts_dark >= site["min_alt_deg"]) * 15.0 / 60.0)
                if actual_bin["peak_alt_deg"] is None:
                    raise AssertionError(
                        f"{site['label']} target {tid} month {month}: "
                        f"got peak_alt_deg=None, expected ~{exp_peak:.3f}"
                    )
                assert np.allclose(actual_bin["peak_alt_deg"], round(exp_peak, 2), rtol=1e-9, atol=1e-6), (
                    f"{site['label']} target {tid} month {month}: peak mismatch "
                    f"{actual_bin['peak_alt_deg']} vs {exp_peak}"
                )
                assert np.allclose(actual_bin["hours_above_min"], round(exp_hours, 2), rtol=1e-9, atol=1e-6), (
                    f"{site['label']} target {tid} month {month}: hours mismatch "
                    f"{actual_bin['hours_above_min']} vs {exp_hours}"
                )
    print("correctness: OK (broadcast transform matches repeat/tile reference, "
          f"{len(SITES)} sites x 3 months x {len(CORRECTNESS_TARGETS)} targets)")


def _make_targets(n: int) -> list[dict]:
    out = []
    for i in range(n):
        ra = (i * 37.0) % 360.0
        dec = -80.0 + (i * 173.0 % 160.0)
        out.append({"target_id": i, "center_ra_deg": ra, "center_dec_deg": dec})
    return out


def bench() -> None:
    site = SITES[0]
    # Warm-up call absorbs one-off IERS/ERFA setup cost so the timed runs
    # reflect steady-state performance.
    app_module.compute_year_visibility(
        _make_targets(5), lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
        min_alt_deg=site["min_alt_deg"], year=YEAR,
    )
    for n in (30, 200):
        t0 = time.perf_counter()
        app_module.compute_year_visibility(
            _make_targets(n), lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=YEAR,
        )
        dt = time.perf_counter() - t0
        print(f"  n_targets={n:4d}  time={dt:.3f}s")


if __name__ == "__main__":
    check_correctness()
    print("benchmark (compute_year_visibility, broadcast transform):")
    bench()
