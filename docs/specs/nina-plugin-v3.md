# NINA plugin v3: one ACP server, any rig, no rig to declare

Status: draft, 2026-09-04. Supersedes the parked v3.0 notes from 2026-05-19 in notes/todo.md. Decisions in this document were made in conversation on 2026-09-04 and are recorded here so a fresh clone or a bus agent can build from it.

## The problem

ACP runs once, on the home server. NINA runs on whichever NUC is at the telescope, at home or at a remote site, reaching the home LAN over a VPN. The plugin today assumes ACP and NINA share a machine, and its Target Scheduler sync runs as a Python extension on the ACP host that opens the TS database on the NINA machine, which does not exist across a network.

The obvious fix, a rig entity that each NINA install declares, was rejected. The travel rig changes telescope, reducer, camera and mount from trip to trip. Anything the user has to set by hand at the telescope will be forgotten, and NINA's own profile fields already show that: the focal length in the profile is routinely wrong because the user reuses one profile across setups. A declared rig would sync the wrong plans on the nights it is wrong, silently.

## The idea

Never ask what rig is connected. Derive it.

- The camera reports its name, sensor width and height in pixels, and pixel size when it connects. Hardware, never wrong.
- The filter wheel reports its filter names. Hardware.
- The mount reports its name and the site latitude and longitude. Hardware and profile.
- Focal length is the one field NINA takes from the profile, and the one the user forgets. A plate solve gives the true pixel scale, and pixel scale with pixel size gives the true focal length. The sky corrects the profile.

Those values make a gear fingerprint. The plugin sends it to ACP, ACP returns the plans that fit it, and the plugin loads them into Target Scheduler. A fixed remote site produces the same fingerprint every night without anyone naming it. The travel rig produces whatever it is that night. Plans do not name a rig; they name the telescope and camera they were planned for, which ACP already stores, and matching is by what those imply: pixel scale, field of view and filters. Two cameras with the same sensor match the same plans.

Intent, meaning which targets are for the dark site and which are for casual nights, does not need a tag. The dark site has one telescope and one camera, so a plan made for that gear routes there and nowhere else. A plan made for a wide field lens sits until that lens is under a dark sky.

## Two modes, one switch

Most users run one rig on one computer and want none of this in their way. The plugin's Options page has a single setting, "Which plans to load into Target Scheduler", with two values:

- Everything. Every plan in ACP is synced. The fingerprint is still built and still shown in the dock, and the focal length write-back still works, but nothing is filtered. The user adjusts in TS if a plan does not suit the night. This is the default.
- Only what fits tonight. Plans are matched against the fingerprint and only the fit ones are synced. For people with several rigs, sites or computers.

Everything else in this document applies to both modes. The matching endpoint is always available; the mode decides whether the sync step uses its verdicts. A plan with no gear set is synced in both modes.

## Goals

1. The plugin talks to ACP over a LAN or VPN with a bearer token, and optionally over https.
2. The plugin builds a gear fingerprint from connected hardware and a plate solve, and writes the solved focal length back into the active NINA profile.
3. ACP matches plans to a fingerprint and returns only the ones that fit.
4. Target Scheduler sync runs inside the plugin, so it works from a NUC with ACP elsewhere.
5. A sequencer instruction and a dock button do the whole start of night in one step: solve, fingerprint, match, sync.
6. Acquired hours flow back to ACP while imaging.

## Non-goals

- Exposing ACP to the Internet. Remote sites reach home through a VPN or overlay such as Tailscale. ACP does app-layer auth only, the overlay does everything else. Unchanged from the May scope decision.
- Replacing Target Scheduler's own scheduling. TS still picks what to shoot on the night from what the plugin loaded.
- Mosaic planning inside NINA. Panels are computed by ACP and arrive with the plan.

## Parts

### Part A: connection

Already in place after ACP PR #69: `GET /api/version`, `?expand=gear,site,panels` on `/api/plans`, bearer token via `ACP_API_TOKEN`, CORS on API reads.

Plugin changes:

- Options page gains a token field, stored in Windows Credential Manager, never in plain text settings.
- Server URL accepts `https://`. A self signed certificate is accepted only if the user pastes its fingerprint into a second field; otherwise standard validation.
- Every request carries `Authorization: Bearer`. A 401 shows in the dock as "ACP rejected the token" rather than a generic connection failure.
- The dock polls `/api/version` every 60 seconds and refetches plans only when `plans_last_modified` changes.

Acceptance: from a NINA machine on the LAN with `ACP_API_TOKEN` set on the server, the dock connects with the token and shows plans, and shows the rejection message with a wrong token. Tested by hand on Windows; the server side is covered by `tests/test_plugin_api_prep.py`.

### Part B: gear fingerprint

Built by the plugin from NINA's mediators at the moment of use, never cached across sessions.

```
{
  "camera": {"name": "QHY268M", "sensor_px": [6252, 4176], "pixel_size_um": 3.76, "colour": false},
  "filters": ["L", "R", "G", "B", "Ha", "OIII", "SII"],
  "mount": {"name": "EQ6-R Pro"},
  "site": {"lat": -33.87, "lon": 151.21, "elev_m": 40},
  "focal_length_mm": {"profile": 250.0, "solved": 540.4, "source": "solved"},
  "pixel_scale_arcsec": 1.436,
  "rotation_deg": 12.3,
  "profile_name": "Travel rig",
  "nina_version": "3.2.0.1001"
}
```

- `sensor_px` and `pixel_size_um` come from the camera mediator's connected device info. Binning is reported separately and the plugin sends unbinned values plus the current bin factor.
- `colour` is true when the camera reports a Bayer pattern.
- `filters` is the filter wheel's slot names in slot order, or empty when no wheel is connected. An empty list with a colour camera reads as OSC on the ACP side, consistent with the scanner's band model.
- `focal_length_mm.solved` is derived from the most recent plate solve: `solved = pixel_size_um * 206.265 / pixel_scale_arcsec`, with the bin factor applied. When no solve has happened this session, `source` is `profile` and the profile value is used, and the dock says so.
- `rotation_deg` is the solved camera angle, used to preset the framing rotation.

Profile write-back: when a solve gives a focal length that differs from the profile by more than 2 percent, the plugin offers to write the solved value into `ActiveProfile.TelescopeSettings.FocalLength`. In the sequencer instruction this is a checkbox, default on, so it happens without a prompt. If ACP knows the aperture of the matched telescope, the plugin also writes `FocalRatio` as focal length over aperture. Both then land in the FITS headers of every frame that night, which is the win the scanner benefits from next time it reads the archive.

Verified 2026-09-04 against the NINA SDK: camera info, filter names, the capture and solve call, and the focal length setter are all public API. The exact calls are recorded at the end of `docs/nina-plugin-research.md`. No spike is needed before building.

### Part C: plan matching in ACP

New endpoint: `POST /api/plans/match` with the fingerprint as the body. Returns the plans that fit and, for each, why.

Matching rules, all evaluated against the plan's stored telescope and camera:

- Pixel scale within 15 percent of the plan's pixel scale. This is the primary test; it captures telescope plus camera together and does not care which brand of IMX571 is attached.
- Field of view of the connected setup at least 90 percent of the plan's field in both axes. Larger is fine; a mosaic planned for a smaller field still fits inside a bigger one. Smaller fails, since the panels would not cover the target.
- Every filter the plan has a goal for is present in the fingerprint's filter list after ACP's filter canonicalisation, or the plan's goals are all bands an OSC camera credits when the fingerprint says colour with no wheel.
- Plans with no gear set match everything and are flagged `unconstrained`.

Each returned plan carries `match: {pixel_scale_ratio, fov_ratio, filters_missing, verdict}` where verdict is `fit`, `fit_with_warnings` or `no_fit`. The endpoint returns all three verdicts so the dock can show why something was left out; the sequencer instruction loads only `fit`.

The fingerprint is also stored on the ACP side as the last seen fingerprint per profile name, shown in the planning rail, so the user can see what the NUC reported without walking to it.

Acceptance, in `tests/test_plan_match.py`: a plan for a 540 mm scope and an IMX571 camera matches a fingerprint from a different IMX571 camera on the same scope; fails a fingerprint from a 250 mm lens; a mono plan with an Ha goal fails a fingerprint with no Ha filter; an OSC fingerprint matches a plan whose goals are R, G and B; a plan with no gear returns `unconstrained`.

### Part D: Target Scheduler sync inside the plugin

The Python extension `nina_ts_sync` stays as it is for same machine users. The plugin gains its own implementation of the push direction so it works with ACP elsewhere.

- Reads the TS database path from the TS plugin's own settings for the active profile, the same way the extension finds it today.
- Uses the TS schema documented in `docs/nina-plugin-research.md`: Project, Target, ExposurePlan, ExposureTemplate. Creates or updates a Project per ACP project name, a Target per plan panel, and ExposurePlans from the plan's filter goals, deduplicating ExposureTemplates by camera and filter as the Python code does.
- Uses the same strictest wins rule for project level fields and the same base snapshot idea so a later sync can tell ACP changes from TS changes. Port the logic, do not redesign it.
- Retries on `database is locked` with the same backoff the extension uses. Never syncs while a TS container is running, using the TS pub/sub `ContainerStarted` and `ContainerStopped` topics to know.
- Pull direction, meaning TS edits flowing back to ACP as plan changes, is out of v3.0. Acquired hours flowing back is Part F.

Acceptance: a fixture TS database in the plugin's test project; pushing three plans produces the same rows the Python extension produces for the same plans, compared field by field. The Python extension's tests provide the expected rows.

### Part E: one step start of night

Two entry points, one code path.

Sequencer instruction, "ACP: Sync for tonight", in the Advanced Sequencer under a new ACP category:

1. Optional slew to a target chosen in the instruction, or stay put.
2. Capture one frame with the instruction's exposure setting and plate solve it, using the same solver the profile uses.
3. Build the fingerprint with the solved focal length.
4. Write the focal length and focal ratio back to the profile if the checkbox is on.
5. `POST /api/plans/match`. In the everything mode take all plans; in the fit mode take only the `fit` verdicts.
6. Run the TS sync for those plans.
7. Report in the sequencer log: N plans loaded, M left out and why, the focal length change if any. If nothing fits, say so plainly and leave TS as it was.

The dock gets a "Sync for tonight" button that does the same from step 2, using the current pointing.

Acceptance: on Windows, a sequence with polar alignment, autofocus, then this instruction ends with the expected plans in TS's project manager and the profile focal length updated. Recorded as a checklist in notes/qa when built.

### Part F: acquired hours back to ACP

Subscribe to TS pub/sub `TargetStart` and `TargetComplete`. On each event, read the acquired counts for the affected exposure plans from the TS database and `POST /api/plans/<id>/progress` with acquired hours per filter. ACP updates `filter_goals.<f>.actual_hours`. This replaces the Python `sync-acquired` poll for cross machine users and can coexist with it.

Acceptance: server side in tests; plugin side by hand during a real session, the existing parked "night out validation" checklist covers it.

## Phasing

| Version | Contents | Needs NINA on Windows to test |
|---|---|---|
| v3.0 | Parts A, B, C, E without TS sync (Framing push only) | Yes, for B and E |
| v3.1 | Part D, then E gains the TS step | Yes |
| v3.2 | Part F | Yes, a real night |

ACP side work for v3.0 and v3.2 can be built and tested without NINA and should go first. The plugin builds from the Windows machine over SSH, so plugin work can be built and unit tested without a person present; only the NINA UI checks need someone at the screen.

## Security

Unchanged from the May decision: same machine needs nothing, same LAN or overlay needs the token, Internet exposure is out of scope and the docs say not to port forward ACP. The token lives in Windows Credential Manager. Https is optional and off by default.

## Open questions

1. Solve at the start of the night versus using the last solve NINA already did. Using the last solve avoids an extra exposure but can be stale from a previous session. Default: the instruction always solves; the dock button uses the last solve if it is less than an hour old and says so.
2. Pixel scale tolerance of 15 percent. Wide enough for a reducer the user forgot, tight enough to keep a 250 mm lens off 540 mm plans. Revisit after a month of fingerprints.
3. Whether the profile write-back should ever be automatic outside the sequencer instruction. Default: never, only the instruction with its checkbox.
4. Whether the everything mode should still warn when a synced plan does not fit the fingerprint. Default: yes, one line in the dock and the sequencer log, no blocking.
