# Deep code review — Astro Coverage Planner

Scope: correctness review prioritising coordinate handling (RA/Dec, epochs,
projection math), FITS/XISF parsing edge cases, the Target Scheduler export
format, and anything that could silently corrupt a planning database.
Baseline: 292 tests passing before this review; 315 passing after (all new
tests are additive, nothing removed or weakened).

## Confirmed bugs (fixed)

### 1. `plans.json`/`gear.json`/`sites.json`/`destinations.json`/
   `target_overrides.json`/`saved_searches.json` writes were non-atomic

**File:** `app.py` — `save_plans`, `save_gear`, `save_sites`,
`save_destinations`, `save_target_overrides`, `save_saved_searches`.

All six persisted state via `Path.write_text(json.dumps(...))`, which opens
the target file for writing (truncating it immediately) and then streams
the new content. A process kill mid-write — OOM kill, container restart,
power loss, `docker stop -t 0` — during any of these (including the plan
autosave that fires on every UI edit, and the write at the end of every
`/api/sync`) leaves a half-written, unparseable JSON file on disk. The next
`load_*()` call then raises `json.JSONDecodeError`, and every endpoint that
touches that store (which for `plans.json` is nearly the whole app) starts
500ing until someone manually repairs or deletes the file — i.e. exactly
the "silently corrupt the planning database" failure mode called out in the
review brief, except it isn't even silent: it's a hard outage with no
built-in recovery.

**Fix:** added `_atomic_write_json()` (temp file in the same directory +
`os.replace()`, which is atomic on POSIX and Windows) and routed all six
`save_*` functions through it. A failed or interrupted write now either
leaves the previous file completely intact or lands the complete new file
— never a partial one.

**Regression tests:** `tests/test_atomic_write.py` — verifies the helper
writes valid JSON, leaves no stray temp files on success, leaves the
existing file byte-for-byte untouched when `json.dumps` or `os.replace`
raises, and that each of the six `save_*` wrappers routes through it (via
`os.replace` failure injection).

### 2. XISF pointing used `CRVAL` as the frame centre regardless of `CRPIX`

**File:** `scripts/build_archive_manifest.py` — `read_xisf_meta`.

Whenever a WCS was present, the XISF reader reported `CRVAL1`/`CRVAL2`
directly as the frame's RA/Dec. `CRVAL` is the sky position *at `CRPIX`*,
not necessarily the image centre — a plate solver is free to put its
reference pixel anywhere in the frame (astrometry.net in particular does
not guarantee `CRPIX` is centred; PixInsight's own solvers usually do
centre it, but nothing in the format requires it). Any XISF whose solve
didn't happen to centre `CRPIX` got silently mis-pointed by however far off
centre `CRPIX` sat — up to half the field of view for a solve with `CRPIX`
in a corner. That wrong position then propagates into spatial clustering
(which target a frame belongs to), the exported footprint polygon
(`corners_icrs`), and the sky-map position shown for that target.

The neighbouring FITS reader (`read_fits_meta`) already avoids this: it
builds a real `astropy.wcs.WCS` and evaluates `pixel_to_world()` at the
image-centre pixel, which is correct regardless of where `CRPIX` sits. The
XISF path never did the equivalent — it had no code reading `CRPIX1`/
`CRPIX2` at all.

**Fix:** read `CRPIX1`/`CRPIX2` (+ `CD`/`CDELT`/`CTYPE`) from the XISF
FITSKeywords, build a `WCS` object, and evaluate it at the image-centre
pixel — mirroring the FITS path exactly. Falls back to the previous
CRVAL-as-centre approximation only when `CRPIX`/`CD` aren't present at all
(preserves behaviour for older/partial headers and the existing test
fixtures, none of which set `CRPIX`).

**Regression tests:** `tests/test_scanner_xisf.py::TestXisfOffCenterCrpix`
— one case with `CRPIX` pinned to a frame corner, asserting the resolved
position moves substantially off the corner `CRVAL` and matches an
independently-computed (via a second, from-scratch `astropy.wcs.WCS`
call) expected centre to within 1 arcsec; one case confirming the
CRVAL-as-centre fallback still fires when `CRPIX` is absent.

## Test backfill (no code change — coverage only)

### `_mosaic_panel_centers` (app.py) — zero prior direct coverage

This function computes the RA/Dec of every panel in a mosaic before it's
written into the Target Scheduler export zip. It was only exercised
indirectly, through single-panel (non-mosaic) plans in
`tests/test_sync_edge_cases.py` / `tests/test_export_sync.py` — a sign
error in the rotation matrix, a dropped `cos(dec)` correction, or a broken
row/col stride would have shipped wrong panel coordinates into every
mosaic sync with no test failing.

`tests/test_mosaic_geometry.py` adds direct coverage: row/col stride vs.
FOV+overlap, the documented "row 0 is north" / "PA east-of-north" rotation
convention (independently derived by hand from the east/north tangent-
plane basis and cross-checked against `astropy.coordinates.SkyCoord.
separation`, not just re-running the implementation's own formula), the
`cos(dec)` pole clamp, and the `rows`/`cols`/`overlap_pct` input clamps.
Verified during review: the rotation formula (`de = cx·cosR + cy·sinR`,
`dn = -cx·sinR + cy·cosR`) is mathematically correct for the "PA measured
east of north" convention documented at the call site — this was
scrutinised as a leading bug candidate and found sound, so it's pinned
down with tests rather than "fixed."

## Suspicious but unconfirmed (not fixed — flagged for follow-up)

These looked like they *could* be bugs but I couldn't confirm real-world
impact without either a live plate-solver sample I don't have, or domain
judgement calls this review shouldn't make unilaterally. Left alone per
"don't fix on theory."

- **`read_fits_meta` image-centre pixel is off by 0.5 px.**
  `scripts/build_archive_manifest.py`, the `cx, cy = naxis1/2.0, naxis2/2.0`
  lines (and the `wcs_w/2.0, wcs_h/2.0` downsampled-grid equivalent).
  `astropy.wcs.WCS.pixel_to_world()` takes 0-indexed pixel coordinates; the
  true geometric centre of an N-pixel axis (0-indexed) is `(N-1)/2`, not
  `N/2`. Confirmed via a standalone astropy check that this produces a
  real, non-zero offset (≈1.8 arcsec for a 3.6"/px example). Left
  unfixed: at typical amateur pixel scales this is sub-arcsecond to a
  couple of arcsec, dwarfed by the app's own 30-arcmin clustering radius
  and typical FOV sizes — not a source of visible corruption, and "fixing"
  it would only matter for precision use cases this app doesn't claim to
  serve. Flagging in case a future astrometric-precision feature depends
  on it. (Note: the XISF path, post-fix-#2 above, now uses the *same*
  `N/2.0` convention for consistency with the FITS path — deliberately
  not "more correct" than its sibling.)

- **`corners_icrs` footprint-corner computation has no pole clamp.**
  `scripts/build_archive_manifest.py`, around line 1778:
  `c_ra = ra_c + (dx * w_arcmin / 2.0) / 60.0 / np.cos(np.radians(dec_c))`.
  `_mosaic_panel_centers` in `app.py` clamps `cos(dec)` to a `1e-6` floor
  before dividing; this corner computation doesn't. A target with a
  cluster centre within a few arcsec of ±90° Dec (astronomically it would
  have to be an actual near-pole target — vanishingly rare for amateur
  imaging, but not impossible for polar-alignment or circumpolar shots)
  could produce a very large or `inf`/`nan` RA corner, which `mocpy`'s
  `MOC.from_polygon_skycoord` would likely reject or mishandle. Left
  unfixed pending a decision on whether it's worth the code churn for a
  practically-unreachable input; noting it here so it isn't lost.

- **No RADESYS/EQUINOX handling anywhere in the WCS/coordinate code.**
  Every `SkyCoord` construction in the app assumes ICRS/J2000 implicitly
  (astropy's default when the header doesn't specify otherwise). Real-world
  mounts/software occasionally report apparent (JNOW) coordinates in
  `OBJCTRA`/`OBJCTDEC` without a `RADESYS`/`EQUINOX` header to say so — a
  known footgun in amateur astro tooling generally, not something specific
  to this codebase. I found no way to confirm or refute this affects any
  real device this app's users have without their actual FITS headers, so
  it's noted rather than "fixed" against a guess.

- **Target Scheduler export ID/format:** reviewed `_build_ts_export` in
  full — grouping, strictest-wins constraint merging (min-altitude/
  meridian-window/priority), per-entity ID sequencing (guards against the
  `Id: 0` collision the code comments describe), RA-in-hours conversion,
  and the exposure Desired/Acquired math. All read correct and are already
  covered by `tests/test_export_sync.py` / `tests/test_sync_edge_cases.py`
  / `tests/test_priority_export_fallback.py`. No changes made here.

## What the new tests now cover

- `tests/test_atomic_write.py` (11 tests) — the atomic-write helper itself,
  plus all six `save_*` wrappers, under simulated write failures.
- `tests/test_mosaic_geometry.py` (10 tests) — `_mosaic_panel_centers`
  rotation, stride, pole-clamp, and input-clamp behaviour.
- `tests/test_scanner_xisf.py` (+2 tests) — XISF off-centre-`CRPIX`
  handling and the no-`CRPIX` fallback.

Full suite: 315 passed (292 pre-existing + 23 new), 0 failed.
