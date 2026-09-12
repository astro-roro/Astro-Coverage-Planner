"""Canonical member fixtures for every hours test. One definition, imported by
Phase 3, Phase 5 and Phase 6 tests. Do not redefine these anywhere else.
"""
from __future__ import annotations

SCOPE = "190MN"
CAM = "ASI2600MM"
RIG = "190MN|ASI2600MM"


def _master(*, ncombine=36, exptime=300.0, scope=SCOPE, cam=CAM,
            path="/A/M31/Ha/master_Ha.xisf", filt="Ha",
            date="2026-02-14T22:30:00", session_root=None, rig=None):
    m = {"role": "master", "path": path, "filter": filt, "colour": False,
         "exptime": exptime, "ncombine": ncombine,
         "telescope": scope, "camera": cam, "date_obs": date,
         "_session_root": session_root}
    if rig is not None:
        m["_rig_key"] = rig
    return m


def _subs(*, n=45, exptime=300.0, scope=SCOPE, cam=CAM, bucket="/S/sess1/og",
          path=None, filt="Ha", date="2026-02-14T22:30:00", colour=False,
          captured=True, accepted=None, session_root=None,
          first=None, last=None, total_hours=None):
    accepted = captured if accepted is None else accepted
    block = {
        "bucket": bucket, "filter": filt, "n_subs": n, "exptime": exptime,
        "total_hours": n * exptime / 3600.0 if total_hours is None else total_hours,
        "sample_path": path or f"{bucket}/l_0001.fits",
        "telescope": scope, "camera": cam, "colour": colour,
        "date_obs": date, "first_date_obs": first, "last_date_obs": last,
        "_stage": bucket.rstrip("/").rsplit("/", 1)[-1],
        "_session_root": session_root or bucket.rstrip("/").rsplit("/", 1)[0],
        "_counts_captured": captured, "_counts_accepted": accepted,
        "_accepted_basis": "no_rejects" if accepted or not captured
        else "rejected_folders",
    }
    return {"role": "folder_sub", "path": path or f"{bucket}/l_0001.fits",
            "filter": filt, "colour": colour, "exptime": exptime, "ncombine": n,
            "telescope": scope, "camera": cam, "date_obs": date,
            "_folder_sub": block}
