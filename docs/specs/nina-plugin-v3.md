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

Profile write-back happens in exactly two places: the ACP sequencer instruction and the dock's Sync for tonight button. No other plate solve, whether NINA's own centring, a manual solve from the imaging tab, or anything another plugin does, ever touches the profile. Decided 2026-09-04.

- The solved focal length is written into `ActiveProfile.TelescopeSettings.FocalLength` only when it differs from the profile value by more than 5 percent. A RedCat that solves at 248 mm against a profile of 250 mm is left alone. A forgotten reducer at 20 to 30 percent off, or a Hyperstar at several times the native length, is corrected.
- If ACP knows the aperture of the matched telescope, `FocalRatio` is written as focal length over aperture at the same time.
- The user is told, in three places: the instruction's description text in the sequencer palette says it updates the profile focal length; the dock button's tooltip says the same; and every write logs one line in the NINA log and the sequencer output with the old and new values. The instruction has a checkbox to turn the write-back off, default on.
- Both values then land in the FITS headers of every frame that night, which the scanner benefits from next time it reads the archive.

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

- Reads the TS database path the same way the extension finds it today. In practice that is a fixed location, `%LOCALAPPDATA%\NINA\SchedulerPlugin\schedulerdb.sqlite`, with an environment override for testing. Target Scheduler has no setting that moves the file, so there is no per profile path to read: the profile picks which rows are touched, not which file.
- Uses the TS schema documented in `docs/nina-plugin-research.md`: Project, Target, ExposurePlan, ExposureTemplate. Creates or updates a Project per ACP project name, a Target per plan panel, and ExposurePlans from the plan's filter goals, deduplicating ExposureTemplates by camera and filter as the Python code does.
- Checks `PRAGMA user_version` is 23 to 28 before writing anything and refuses otherwise, in the same words the extension uses.
- Uses the same strictest wins rule for project level fields and the same base snapshot idea so a later sync can tell ACP changes from TS changes. Port the logic, do not redesign it. That includes the UUIDv5 namespace and the four name recipes the extension stamps rows with, because both tools write the same database and have to recognise each other's rows.
- Retries on `database is locked` with 2, 4 and 8 second backoff, on top of SQLite's own ten second busy timeout.
- Never syncs while a TS container is running. TS publishes but never subscribes and there is no `ContainerStarted` topic, so a run is inferred: `WaitStart`, `NewTargetStart` and `TargetStart` all mean one is going, and only `ContainerStopped` clears it. `TargetComplete` is one target finishing, not the container. A run with no event at all for three hours is treated as over, so a NINA killed mid-run does not refuse every sync until restart.
- Pull direction, meaning TS edits flowing back to ACP as plan changes, is out of v3.1. Acquired hours flowing back is Part F.

Acceptance: fixture TS databases at 23, 25 and 28 in the plugin's test project, sharing the extension's own schema files; pushing three plans produces the same rows the Python extension produces for the same plans, compared field by field. The Python extension's tests provide the expected rows. Rows are matched by guid rather than by row Id, and foreign keys are compared as the guid of the row they point at, because the guid is the identity both tools use and neither promises a row order.

### Part E: one step start of night

Two entry points, one code path.

Sequencer instruction, "ACP: Sync for tonight", in the Advanced Sequencer under a new ACP category:

1. Optional slew to a target chosen in the instruction, or stay put.
2. Capture one frame with the instruction's exposure setting and plate solve it, using the same solver the profile uses.
3. Build the fingerprint with the solved focal length.
4. Write the focal length and focal ratio back to the profile if the checkbox is on.
5. `POST /api/plans/match`. In the everything mode take all plans; in the fit mode take only the `fit` verdicts.
6. Run the TS sync for those plans. Back the database up beside itself first, and write in one immediate transaction so a clash with TS is a clean retry rather than a half written project.
7. Report in the sequencer log: N plans loaded, M left out and why, the focal length change if any. If nothing fits, say so plainly and leave TS as it was.

Two things stop step 6 rather than failing the run. A TS container already running means nothing is written and the log says so, which is why the instruction belongs before the container in a sequence and not inside it. A schema version outside 23 to 28 means the same. In both cases the fingerprint is still built and the profile focal length still corrected, because those are worth having on their own.

The dock gets a "Sync for tonight" button that does the same from step 2, using the current pointing.

Acceptance: on Windows, a sequence with polar alignment, autofocus, then this instruction ends with the expected plans in TS's project manager and the profile focal length updated. Recorded as a checklist in notes/qa when built.

### Part F: acquired hours back to ACP

Subscribe to TS pub/sub `TargetStart` and `TargetComplete`. On each event, read the acquired counts for the affected exposure plans from the TS database and `POST /api/plans/<id>/progress` with acquired hours per filter. ACP updates `filter_goals.<f>.actual_hours`. This replaces the Python `sync-acquired` poll for cross machine users and can coexist with it.

Acceptance: server side in tests; plugin side by hand during a real session, the existing parked "night out validation" checklist covers it.

## Phasing

| Version | Contents | Needs NINA on Windows to test |
|---|---|---|
| v3.0 | Parts A, B, C, E without TS sync (Framing push only) | Yes, for B and E. Built. |
| v3.1 | Part D, then E gains the TS step | Yes. Built. |
| v3.2 | Part F | Yes, a real night |

ACP side work for v3.0 and v3.2 can be built and tested without NINA and should go first. The plugin builds from the Windows machine over SSH, so plugin work can be built and unit tested without a person present; only the NINA UI checks need someone at the screen.

## Security

Unchanged from the May decision: same machine needs nothing, same LAN or overlay needs the token, Internet exposure is out of scope and the docs say not to port forward ACP. The token lives in Windows Credential Manager. Https is optional and off by default.

## Decisions on the open questions, 2026-09-04

1. Solve at the start of the night versus reusing NINA's last solve. The instruction always solves. The dock button reuses the last solve if it is under an hour old and says so, otherwise solves.
2. Pixel scale tolerance stays at 15 percent. Wide enough for a forgotten reducer, tight enough to keep a 250 mm lens off 540 mm plans. Revisit after a month of fingerprints.
3. Profile write-back only from the instruction and the dock button, only when the solved focal length is more than 5 percent from the profile, always announced. See Part B.
4. In the everything mode, plans that do not fit the fingerprint are still synced, and the dock and sequencer log carry one warning line naming them. Nothing blocks.

## Part G: what counts as progress, decided 2026-09-05

Three adversarial reviews of the v3 branches on 2026-09-05 found that the push and the read back had a single number where there are really three, and that mosaics finished early. The decisions below came out of that.

### Three numbers, three owners

Imaged hours are what is on disk. The scanner owns this. Acquired frames are what the camera took during a Target Scheduler session. Target Scheduler owns this, always, because the camera is attached to it. Accepted frames are what a grader kept. Who owns this depends on where grading happens, and that is the only real difference between users.

Grader in Target Scheduler: it owns accepted, ACP reads and never writes. Grading later in PixInsight or Siril: ACP becomes the authority, and the push carries the verdict to Target Scheduler so it stops scheduling a target that already has enough good frames. No grading anywhere: accepted equals acquired, which is exactly the convention Target Scheduler uses with its grader off.

### The push rule

The push never writes `acquired` on a row that exists. It writes `accepted` on an existing row only when the plan or project is marked as graded outside Target Scheduler, which is off by default. Both are still written when a row is first created. `createdate` is never written on update. A stranger who never opens a setting therefore cannot lose a frame count to a sync, which was the failure the review demonstrated.

### The read back

Part F brings both `acquired` and `accepted` into ACP as separate numbers rather than picking one by the grader flag. A plan's filter goal carries `actual_hours` for imaged and `accepted_hours` for graded. Each goal says which one it means, imaged by default. A goal of six hours means six imaged hours unless it says accepted, and then it means six graded hours whether the grading happened in Target Scheduler or later.

### The scanner's part

The scanner produces imaged and accepted hours per filter per target from evidence on disk, so the coverage map stops over-reporting for anyone who grades. The evidence, in order of strength, is in `docs/research/frame-grading-traces.md`, read from the tools' own sources. The strongest is one the scanner already parses: a master's frame count keyword, since a stack only integrates what was kept. Then Siril's sequence sidecar, which flags each frame. Then a `rejected` subfolder, which Target Scheduler writes when its move option is on and people also make by hand, corroborated rather than trusted alone. The fallback is explicit: no evidence of grading means accepted equals imaged, so a user who never grades sees no change at all.

Two facts from that research shape the rest. Target Scheduler writes nothing to a frame's header, and NINA puts no quality metrics there either, so a graded archive that loses its database and its rejected folder cannot be reconstructed from the frames. And PixInsight's weight keyword is a convention rather than a default, so it is a hint, not proof.

### Mosaics, per panel

ACP stores a mosaic goal per panel. Six hours on a 4x4 means six hours on each of sixteen panels, and the plan's total is the per-panel target times the panel count, so 96 hours, never 6 or 16. Progress is tracked per panel: each filter goal carries a map from panel to its imaged and accepted hours, which Target Scheduler already holds since every panel is its own target there. A panel contributes up to its own target and no more, so a panel shot past its goal shows its true hours in the panel view but cannot pay for a neighbour. The plan is finished when every panel has met its target.

The plan list shows the totals. A mosaic plan opens to a per-panel view that reuses the panel grid the visibility heatmap already draws, filled with progress instead. Before this, the finished check compared the plan's single stored number against the per-panel target with no panel dimension, so one finished panel marked a whole mosaic done; that predates v3 and is fixed by this.

### One definition of finished

The interface had two. The plan list compared a goal against the plan's own stored hours, which the plugin feeds. The sky map compared it against the manifest's hours, which the scanner feeds. From here a plan's finished state comes from the plan's own progress and nothing else. The manifest's hours are coverage, shown on the map as coverage, and never used to decide whether a plan is done.

### A toggle for the hours shown

One switch, global, between total and accepted hours everywhere hours appear: the map, the target panel, the plan list, the panel view. Total is the default. It reads from the same two numbers; it does not change what is stored.

### Identity that cannot collide

The recipe that gives a Target Scheduler row its identity joined free text names with a slash, so a project called M42 with a target called M43/NGC1977 collided with a project called M42/M43 and a target called NGC1977, and the second push reparented the first's rows. Each name component is now length prefixed so it cannot run into the next. Existing rows are migrated on first contact, inside the push's own transaction after the backup: any row whose stored identity matches the old recipe recomputed from its own names is rewritten to the new one, and anything else is left alone. An empty target name is refused at push time, and two plans in one project with the same target name are refused rather than silently merged.

### Syncing from inside Target Scheduler's own event containers

Target Scheduler's container exposes six places a user can drop instructions, and it waits for each before carrying on: before wait, after wait, before target, after each exposure, after target, and after all targets. Read from `TargetSchedulerContainer.cs` on 2026-09-05. The order inside its loop is what matters: after a target completes it runs the after target container, waits for it, and only then re-plans by reading the database.

So an ACP sync placed in after target runs in a guaranteed quiet moment, with nothing imaging and nothing writing, and the next planning decision reflects whatever the sync wrote. That is strictly better than a pause: no new mechanism, Target Scheduler calls us at the safe point itself, and there is nothing to infer.

The sync instruction gains one behaviour to make this work. When it finds itself parented inside a Target Scheduler event container, it treats the running guard as satisfied, because the guard exists to avoid writing under a live session and here the session has handed control to us. It detects this by walking up the sequence tree rather than by a checkbox, so a user cannot set it wrong. Outside such a container the guard applies as before, and the three hour staleness window stays as the fallback for people who never use the event containers.

The instruction's description and the docs say where to put it: in the Target Scheduler container's after target slot for a sync at every target change, or after all targets for one at the end of the night. The solve is optional there, since the fingerprint from the start of the night still holds.

### What this leaves for the settings columns

The push still overwrites Target Scheduler's project level settings on update, so a minimum altitude changed in its own interface goes back to ACP's value next sync. That is inherited from the Python extension and not decided here. The candidates are leaving it and saying so in the log, or a per-column rule about which side owns which setting. Deferred until there is a real night's use to judge it against.
