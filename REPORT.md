# ACP server side of the NINA plugin v3 design

Branch: `feat/v3-server-side`. Three commits, full test suite green.

## What was built

### Part C: matching

`POST /api/plans/match` takes the gear fingerprint from the spec and returns every plan with a `match` block, plus a `summary` of the verdict counts and the `fingerprint_id`. Only `camera` and `focal_length_mm` are required; the solved focal length wins over the profile value when both are present; the pixel scale is derived from pixel size, bin and focal length when `pixel_scale_arcsec` is absent.

Verdicts follow the spec: pixel scale within 15 percent is the primary test, field of view must be at least 90 percent of the plan's in both axes, and every filter the plan has a goal for must be reachable from the fingerprint's filter list. Below 80 percent of the field, or outside the scale window, or missing a filter, is `no_fit`; between 80 and 90 percent of the field, or a filter reachable only through a dual band filter, is `fit_with_warnings` with a reason string each; a plan with no telescope or camera is `unconstrained`. All verdicts are returned and `mode` is echoed without changing any of them.

Filter canonicalisation imports `canon_filter` and `bands_for` from `scripts/build_archive_manifest.py` the way `shooting_publish.py` does, so "Antlia Ha" counts as Ha here exactly as it does in the manifest. A colour fingerprint with an empty filter list is treated as `NoFilter`, which `bands_for` credits as R, G and B. A dual band filter credits Ha and OIII, a quad band adds SII, and both are flagged as warnings because the plan's per-filter hours can't be shot independently through them.

The plan's own pixel scale and field of view come from its `telescope_id` / `camera_id` pair in `gear.json` via the existing `_fov_arcmin` plus a new `_plan_pixel_scale`. Both are unbinned, matching `_fov_arcmin`. The fingerprint side applies its `bin` to the pixel scale and divides it back out of the pixel count, so binning shifts the scale and leaves the field of view alone, which is what the optics actually do.

### Fingerprint store

`data/fingerprints.json`, keyed by `profile_name` (falling back to the fingerprint id when there is no name), holding the last fingerprint, its sha1 id, `received_at`, `mode` and the summary counts. Atomic write through the existing `_atomic_write_json`, mtime-keyed cache like the other stores, and the path is overridable with `FINGERPRINTS_PATH` for tests. The file carries the observing site's latitude and longitude, so it is gitignored with the rest of `data/`. A failed write is logged rather than costing the caller its match results.

The id hashes a normalised subset: profile name, camera identity, sorted canonical filters, mount name and rounded focal length. Rotation, site and NINA build are deliberately excluded, so pointing the same rig somewhere else or updating NINA does not make it a different rig.

`GET /api/fingerprints` reads the store back. `static/app.js` gains an `initProfiles()` that renders a read-only "NINA rigs" accordion in the rail (profile name, camera, solved focal length, fit count, how long ago it reported), hidden until at least one install has reported, following the `initInventory()` / `railInventory` pattern.

### Part F: acquired hours

`POST /api/plans/<id>/progress` updates `filter_goals.<f>.actual_hours`, never lowering a stored value unless `"force": true`, ignoring filters the plan has no goal for and naming them in `unknown_filters`. Filter names are canonicalised so a plugin reporting "Antlia Ha" updates the plan's `Ha` goal. Validation is `_validate_progress_payload`, in the style of `_validate_plan_payload`: NaN, infinities, negatives and wrong types are 400s and nothing is written. `acquired_count` is validated but not stored, since the plan schema carries hours and the sync builder derives the count.

Response is `{ok, plan, updated, unknown_filters, not_lowered}`. The spec says "returns the updated plan" and also asks for the ignored filters to be listed, so the plan sits under `plan` rather than being the whole body.

### Tests and docs

`tests/test_plan_match.py` (30 tests) and `tests/test_plan_progress.py` (17 tests) cover everything in the acceptance list plus binning, explicit pixel scale, branded filter names, quad band, zero-hour goals, deleted gear, the expand parameter, validation and the store round trip. Full suite: 432 passed. Frontend: 78 passed. `docs/api.md` documents all three endpoints and the expand parameter.

## Things you need to know

**The prerequisites named in the task are not in this checkout.** `docs/specs/nina-plugin-v3.md` does not exist, and neither do PR #68 or PR #69: there is no `GET /api/version`, no `?expand=` on `/api/plans`, no `_api_gate`, no `_plan_panels`, no `renderScanHealth`, no `colour` field written on cameras in `gear.json`, and `tests/test_plugin_api_prep.py` is absent. `origin/main` is at #67 (`802c444`) and a fetch brought nothing new. `_fov_arcmin` and the `sources` map under each manifest band do exist.

I built Parts C and F from the restatement in the task itself, which is detailed enough to stand alone, and worked around the gaps as follows. Each of these is a merge conflict risk against the real #69:

- **`?expand=gear,panels` on `GET /api/plans`.** The spec pins the match response's plan shape to what this returns, so it had to exist for the contract to mean anything. I implemented it, and the shape is my guess: `gear: {telescope, camera, fov_arcmin, pixel_scale_arcsec}` and `panels: [{row, col, ra_deg, dec_deg}]` from the existing `_mosaic_panel_centers`. If the real #69 chose different key names, the plugin side needs to be told which one wins.
- **No auth.** `_api_gate` does not exist, so the three new endpoints are unauthenticated like every other route here. They will need the gate applied once #69 lands, especially `/api/plans/<id>/progress`, which writes.
- **Camera `colour`.** The fingerprint's own `camera.colour` is what drives the R/G/B credit, and that comes from the plugin, so nothing here depends on #68's gear.json field. Plan-side camera colour is not consulted.
- **`renderScanHealth`** does not exist, so the rail section follows `initInventory` / `railInventory` instead: a `<details class="rail-accordion">` hidden until the endpoint returns something.

**I could not push.** `git push` fails with `could not read Username for 'https://github.com'` (no credentials in this environment). All work is committed on `feat/v3-server-side` locally.

**`npm test` does not run as written in this sandbox.** The script is `node --test tests/frontend/**/*.test.mjs`, and the `/bin/sh` here has no globstar, so npm reports `Could not find ...`. This is pre-existing and unrelated to these changes. `node --test tests/frontend/` runs the same 78 tests and they pass.

**No Python environment was present.** I created `.venv` (already gitignored) and installed from `requirements.txt` to run the suite.

## Judgement calls in the matching rules

- The spec says the field of view must be at least 90 percent "or it is `no_fit`", and separately that 80 to 90 percent is `fit_with_warnings`. I read the warning band as the exception: below 80 percent is `no_fit`, 80 to 90 percent warns.
- A filter goal with `target_hours` of zero is not treated as a requirement, matching how the sync builder skips those goals.
- A plan pointing at a `telescope_id` or `camera_id` no longer in `gear.json` is `unconstrained` rather than `no_fit`: it can't be scored, and that isn't the connected rig's fault.
- Every failing test contributes a reason, not just the first, so the plugin can show the user everything wrong in one go.
