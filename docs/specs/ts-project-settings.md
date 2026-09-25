# Spec: TS project state, priority and minimum time, managed in ACP

Status: draft, 2026-09-25. Not built. It touches the same three repos as `ts-upload-import.md`, and follows its per-rig link model:

- this one, ACP core
- the extension, `acp-nina-ts-sync`
- the plugin, `ACP.NINA.Plugin`

## Goal

Rohan wants to pause, resume and prioritise projects from ACP, and have Target Scheduler (TS) follow. A change made in TS at the rig should come back into ACP too.

ACP owns three project settings and syncs them both ways:

- state: draft, active, inactive or closed, the four TS values
- priority: low, normal or high
- minimum time, in minutes

Altitude already syncs both ways and stays as it is.

TS owns the rest of the project settings. ACP writes TS's defaults when it creates a project, then never writes them again. Those columns are custom horizon and its offset, dither, filter switch frequency, smart exposure order, the image grader, maximum altitude and flats handling. Meridian window is on the brief's TS list, but ACP already has a field for it. See "Decisions for Rohan".

A better project-level view in ACP is later work, outside this spec.

## What is true today

These facts shape every rule below.

- A new plan starts as `state: "draft"` (`static/app.js:6012`), and ACP has no control to change it. That is why all 47 of Rohan's plans are drafts.
- ACP's own `/api/sync` skips drafts (`app.py:4409`). The plugin ignores `state` entirely, so it pushes drafts anyway. 46 of the 47 are live in Voyager's TS as active projects.
- Both writers hardcode project `state = 1` (`convert.py:180`, `TsEntities.cs`). So every push marks the project active, and undoes a pause made in TS.
- The plugin's update by guid (`TsUpsert.cs:270`) rewrites every project column except `createdate` and `flatsHandling`. Every push therefore resets dither, custom horizon, the grader and the rest to 0. The update through `ts_refs` writes only `name`, `priority`, `minimumaltitude` and `meridianwindow` (`TsSchema.cs:118`).
- ACP already has `priority`, `min_altitude_deg` and `meridian_window_min` on each plan, in the editor and in the export. Minimum time is written as 0 (`app.py:4360`).
- The Python base snapshot records project `state` (`state.py:101`). The plugin's does not (`TsState.cs:192`). The Python one also reads priority with `int(proj.priority or 1)`, which turns low (0) into normal.
- The project table has the same columns in TS schema versions 23, 25 and 28, including `activedate` and `inactivedate`.

## 1. ACP's model

### Where the settings live

ACP has no project entity. A TS project is the group of plans that share a `project_name`, the same grouping `_build_ts_export` uses. So each setting lives on every plan in the group:

| Field | Values | Absent means |
|---|---|---|
| `state` | `"draft"`, `"active"`, `"inactive"`, `"closed"` | active |
| `priority` | `"low"`, `"normal"`, `"high"` | normal (unchanged) |
| `minimum_time_min` | integer, 0 or more | 0 |

`_validate_plan_payload` rejects any other value with `400`. `"parked"` from held PR #97 is accepted on read as `"inactive"` and never written.

### Keeping plans in one project consistent

The editor keeps the group consistent. Saving state, priority or minimum time on one plan writes the same value to every plan with the same `project_name`. It does this through one new route, `PUT /api/projects/<project_name>/settings`, body `{state, priority, minimum_time_min}`. The route updates every plan in the group in one write to `plans.json`, so a half-saved group cannot happen.

Plans can still disagree, through a hand edit or an old import. Every writer then resolves the group the same way, and adds a warning as priority does today:

- state: the most running value wins, in the order active, inactive, draft, closed. One active plan keeps the project imaging.
- priority: the highest wins, as today.
- minimum time: the largest wins, the same rule as minimum altitude.

### What each state means in ACP

ACP's states mean what TS's mean. ACP adds no meaning of its own.

| State | In TS | Sync for tonight | Sync All to TS | ACP `/api/sync` zip |
|---|---|---|---|---|
| active | scheduled | loaded when it fits | written | included |
| inactive | kept, not scheduled | state written to an existing row only | written | included |
| draft | kept, not scheduled | state written to an existing row only | written | included |
| closed | finished, hidden in TS's tree | state written to an existing row only | written to an existing row only | left out |

Draft no longer means "never leaves ACP". It means the same as TS draft: the project exists in TS and TS will not schedule it. Draft and inactive behave the same in TS. The difference is intent: draft is not ready yet, inactive is paused.

A closed plan never creates a new TS project. That stops ACP recreating a project Rohan deleted from TS after finishing it.

### Migration of the 47 drafts

Every plan with `state: "draft"` becomes `state: "active"`, once, when ACP starts on the new version.

The reason: 46 of them are already live in TS, because the plugin never honoured draft. If they stayed draft under the new meaning, the next push would set those projects to draft. TS would then stop imaging all of them. Moving them to active leaves TS exactly as it is.

The 9 projects that are inactive in TS stay inactive, because of the push rule in section 5. A push never writes a setting ACP has not changed since the last sync. The next upload then brings their inactive state into ACP as a change made only in TS.

The migration:

- runs in `load_plans` on first start, guarded by a `settings_migrated: 1` key in `plans.json`
- takes a timestamped backup of `plans.json` first, as `_write_plans_json` does
- logs "Moved 47 plans from draft to active, because the NINA plugin was already syncing them"

A new plan starts as active. See "Decisions for Rohan".

## 2. The UI

Kept small. Three changes in `static/app.js`.

The plan list shows a badge after the name for any state but active: "Draft", "Inactive" or "Closed". Active plans show nothing, so the usual list looks as it does now. Closed plans are drawn at reduced opacity. The sort order does not change.

The plan editor's Constraints fieldset gains two fields, beside the existing Priority select:

- a State select with Draft, Active, Inactive and Closed
- a "Minimum time (min)" number input, step 1, min 0

When the plan shares its project with other plans, one line under those fields says: "State, priority and minimum time apply to all 3 plans in project M31."

Saving calls `PUT /api/projects/<project_name>/settings` when any of the three changed, then the usual plan save for everything else. PR #97's Park and Unpark button is not built. The State select does its job.

## 3. Import, TS to ACP

### Mapping

`from_ts.py` reads three project columns:

| TS column | ACP field | Mapping |
|---|---|---|
| `state` | `state` | 0 draft, 1 active, 2 inactive, 3 closed. Anything else is active, with a loss note. |
| `priority` | `priority` | 0 low, 1 normal, 2 high, as today |
| `minimumtime` | `minimum_time_min` | integer minutes. Null is 0. |

The import always writes `state` explicitly, including `"active"`. The loss notes for draft and closed projects go away, because ACP now holds both.

This replaces the extension branch `fm/acp-nina-ts-sync-ship-19ec05`'s mapping of state 2 to `"parked"`. The rest of that branch is kept, including the Cancel button on the review page.

A new plan from a closed TS project is listed on the review page unticked. Rohan ticks it only if he wants the history in ACP.

### Base snapshot

The base snapshot's `project` block records `state`, `priority` and `minimumtime`, as integers, in both writers:

- `state.py` `_project_snapshot` adds `minimumtime`, and reads priority as `int(proj.priority) if proj.priority is not None else 1`, so low stays low.
- `TsState.cs` adds `state` and `minimumtime` to its project block. It also finds the project through the pinned id when the plan has one. Today it looks only at ACP's guid. A project made by hand in TS carries NINA's guid, so today its snapshot has no project block at all.

`base_snapshot_to_plan_shape` maps them back to `state`, `priority` and `minimum_time_min`.

### How the merge compares them

`diff.py` `_PLAN_FIELDS` gains two entries, and `state` compares on its full value:

| Field | Kind | Absent on the ACP side reads as |
|---|---|---|
| `state` | string | `"active"` |
| `priority` | string, as today | `"normal"` |
| `minimum_time_min` | count | 0 |

These differences are noise and never count as a change:

- a missing `state` against `"active"`
- a missing `minimum_time_min` against 0
- `"parked"` against `"inactive"`

A base snapshot taken before this change may have no `state` or `minimumtime`. Today's plugin posts bases like that. The missing `state` reads as `"active"` and the missing `minimumtime` as 0. That is safe because ACP had no way to set either before this change, so ACP cannot have changed them since that base. Any difference with TS is therefore a change made in TS. The "no base" rule in `ts-upload-import.md` still applies to a plan with no base snapshot at all.

The parked-axis comparison from the 19ec05 branch is replaced by this plain comparison of the four values.

Every other difference is real and follows the three-way rules in `ts-upload-import.md`. A plan whose state changed in TS only takes TS's state. A plan paused in both to the same value is no change.

## 4. Push, ACP to TS

Both writers follow the same column rules. The extension writes through `convert.py` and `upsert.py`, the plugin through `TsConvert.cs` and `TsUpsert.cs`.

### What each writer computes

For each project group, the writer computes:

- `state` from the group rule in section 1, as the TS integer
- `priority` as today
- `minimumtime` from the group's `minimum_time_min`
- `activedate` set to now when the written state becomes 1, and `inactivedate` set to now when it becomes 2 or 3. Otherwise neither date is written. This matches what TS does when its own editor changes state.

### Columns on insert

Every column, as today. The ACP-owned columns take ACP's values. The TS-owned columns take TS's defaults, the same values the golden rows hold now, with `flatsHandling = 1`.

### Columns on update

The update splits the project columns into three sets. The rule is the same whether the row was found through `ts_refs`, by guid, or claimed by name.

| Set | Columns | Written on update |
|---|---|---|
| ACP owns | `name`, `minimumaltitude`, `meridianwindow`, `isMosaic`, `profileId` | always |
| ACP owns, synced both ways | `state`, `priority`, `minimumtime` | only when ACP changed it since the last sync on this rig (section 5) |
| TS owns | `createdate`, `flatsHandling`, `usecustomhorizon`, `horizonoffset`, `filterswitchfrequency`, `ditherevery`, `smartexposureorder`, `enablegrader`, `maximumAltitude`, `description` | never |

`activedate` and `inactivedate` are in none of the sets. The writer adds the matching one only when it writes `state`, so they never change on their own.

The TS set becomes the project entry in `insertOnlyColumns` in `TsSchema.cs` and in `INSERT_ONLY_COLUMNS` in `schema.py`, word for word. That fixes the guid update resetting dither and the grader on every push. The project entry in `pinnedUpdateColumns` goes, because the update through `ts_refs` now follows the same three sets.

`priority` moves from "always written" to "written when changed". That is a change for the update through `ts_refs`, which writes priority on every push today.

### Golden parity

The golden rows in `ACP.NINA.Plugin.Tests/Fixtures/golden-rows.json` are made by `tests/dump_golden_rows.py` in the extension, and `TsPushGoldenTests.cs` compares the plugin's rows against them. The change:

- `tests/plans.py` in the extension gives its three plans a spread of settings: one active, one inactive with high priority, one draft with a minimum time of 30.
- The `versions` slices at 23 and 28 then hold three different project rows:

  | Plan | `state` | `priority` | `minimumtime` |
  |---|---|---|---|
  | one | 1 | 1 | 0 |
  | two | 2 | 2 | 0 |
  | three | 0 | 1 | 30 |

  `inactivedate` and `activedate` are pinned by the injected clock, as `createdate` is today.
- Three new `scenarios` slices:
  - `ts_owned_columns_survive_a_second_push`: the rows are edited by hand with dither 3, grader on and custom horizon on, then pushed again. They keep those values.
  - `ts_pause_survives_a_push`: the base says active, TS says inactive, ACP says active. After the push, TS still says inactive.
  - `acp_pause_reaches_ts`: the base says active, TS says active, ACP says inactive. After the push, TS says inactive and `inactivedate` is set.
- Regenerate the file with the extension, copy it into the plugin, and commit both in the same session. The plugin test fails on any difference, which is what keeps the two writers in step.

## 5. Conflicts

The rule for the three ACP-owned settings on push: write ACP's value only when ACP changed it since the last sync on this rig. The base is `ts_links[P].base_snapshot.project`, the same base the upload merge uses. P is the rig's NINA profile.

For each of state, priority and minimum time:

| Base | ACP | TS row | Push writes | New base |
|---|---|---|---|---|
| active | active | active | nothing | active |
| active | inactive | active | inactive | inactive |
| active | active | inactive | nothing, TS keeps its pause | active, unchanged |
| active | inactive | closed | inactive, ACP wins | inactive |
| none | any | any | nothing | none, unchanged |
| no row | any | none | ACP's value on insert | ACP's value |

The third row is the important one. After a push that leaves TS's value alone, the base keeps its old value, not the value found in TS. If the base took TS's value, ACP would look changed on the next push and would overwrite the pause.

### Paused in ACP, resumed in TS

ACP changed state since the base, so the next push writes inactive. ACP wins, and the push result says so: "M31: paused from ACP, which undid a change made in TS". If an upload runs first, it shows the plan as a conflict with the three values, as for any field.

### Paused in TS, still active in ACP

ACP has not changed state, so the push leaves TS paused and the base stays active. The next upload sees a change made only in TS and takes it. ACP then shows the plan as inactive. Until that upload, ACP still shows active. The push result says "M31: TS has it inactive, send TS to ACP to bring that in".

### No base

A plan with no base snapshot for this rig never has these three settings written on update. A base from before this change counts as a base, with the missing values read as in section 3.

So the first push after the upgrade changes no state, priority or minimum time in TS, unless Rohan changed one in ACP first. A project paused in TS stays paused. The live run order below still does an upload first, so ACP shows the true state before anything is pushed.

The same rules apply in the extension's own `/sync` route, which writes TS from a path on the ACP host.

## 6. Sync for tonight, `/api/plans/match` and `/api/sync`

### `/api/plans/match`

The plugin's Sync for tonight calls this to choose what to load. It needs every plan's state, because a paused plan's row still has to be set to inactive. If the paused plan were missing, the push would never touch its row and TS would keep imaging it. That is the gap in held PR #97.

- The fingerprint body gains `"supports_state": true`, sent by the new plugin.
- With it, `/api/plans/match` returns every plan with its `state`. Only active plans get a verdict and count in `summary`. The others get `match: {"verdict": "held", "state": "inactive"}`.
- Without it, from an older plugin, only active plans are returned. An old plugin writes state 1 on every push, so it must never see a paused plan.

### The plugin's Sync for tonight

- It loads active plans that fit, as today, with the column rules from section 4.
- A draft, inactive or closed plan with a TS row for this profile gets its state column written, by the rule in section 5. Nothing else in the row changes. No row is ever inserted for these plans.
- The dock result adds one line when that happened: "Set 2 projects inactive, 1 closed".

### Sync All to TS

It writes every plan by the rules in section 4, except that a closed plan with no TS row is skipped. Old plugins take `GET /api/plans` and push every plan as active. That cannot be fixed from ACP, so the plugin must be installed before Rohan relies on pausing from ACP.

### ACP `/api/sync`

This builds TS's import zip. TS imports it as new projects every time, so it has no base and no row to update.

- Active, inactive and draft plans are included, each with its `State` name: `"Active"`, `"Inactive"` or `"Draft"`.
- Closed plans are left out.
- `Priority` as today, and `MinimumTime` from the group.
- `skipped_draft_count` becomes `skipped_closed_count`. The docstring and `docs/api.md` change to match.

## 7. Acceptance tests

### ACP core (unittest, in `tests/`)

- `test_plan_validation.py`: each of the four states saves. `"paused"` returns `400`. `"parked"` saves as `"inactive"`. `minimum_time_min` of -1, 1.5 and `"x"` return `400`. 0 and 60 save.
- `test_plan_migration.py`: a `plans.json` with three drafts and one plan with no state loads as four active plans. A backup file exists. Loading a second time changes nothing.
- `test_project_settings.py`: `PUT /api/projects/M31/settings` with `{state: "inactive"}` sets it on all three M31 plans and on no other plan. An unknown project returns `404`.
- `test_plan_match.py`: with `supports_state`, an inactive plan appears with verdict `held` and is not in `summary`. Without it, the inactive plan is absent. A draft plan follows the same two rules.
- `test_export_sync.py`: an inactive plan exports with `"State": "Inactive"`. A closed plan is left out and counted in `skipped_closed_count`. Two plans in one project with minimum times 20 and 45 export `"MinimumTime": 45` with a warning.
- `tests/frontend`: the plan list shows the Inactive badge on an inactive plan and no badge on an active one.

### Extension (pytest)

- Import: a TS database with one project in each state imports four plans with the four states. No loss note mentions state. `minimumtime` 30 imports as `minimum_time_min: 30`. Priority 0 imports as low.
- Snapshot: a project with priority 0 snapshots as 0, not 1.
- Merge, TS only: base active, ACP active, TS inactive. The preview lists an update, and apply sets the plan inactive.
- Merge, conflict: base active, ACP inactive, TS closed. The preview lists a conflict.
- Merge, noise: an ACP plan with no `state` against an active TS project shows no difference. Neither does a missing `minimum_time_min` against 0.
- Merge, old base: a base snapshot with no `state`, an ACP plan that is active, and a TS project that is inactive give a TS-only change. Apply sets the plan inactive.
- Merge, no base at all: an ACP plan that is active and a TS project that is inactive give a conflict on upload, marked "no sync record for this rig".
- Push, old base: a base with no `state`, ACP active, TS inactive. The push writes no state.
- Push: the three new golden scenarios pass. A second push with no ACP change writes no `state`, `priority` or `minimumtime`.
- Review page: Cancel sends `DELETE` and the page says nothing in ACP changed. A new plan from a closed TS project starts unticked.

### Plugin (`ACP.NINA.Plugin.Tests`)

- `TsPushGoldenTests` passes against the regenerated golden rows, at 23 and 28 and in every scenario.
- `TsSchema.ColumnsForUpdate("project", ...)` returns none of the TS-owned columns.
- A row found by guid, with dither 3 set by hand, still has dither 3 after a push.
- The base snapshot has `state` and `minimumtime`, and has a project block for a pinned project made by hand in TS.
- Sync for tonight with one inactive plan that has a TS row writes state 2 to that row and inserts nothing. With an inactive plan that has no row, it writes nothing.
- The fingerprint body carries `supports_state: true`.

## 8. Build order and the live test on Voyager

### Build order

1. Extension: fold the 19ec05 branch in and change "parked" to the four states, then the snapshot, diff, import and writer changes, then the golden rows.
2. Plugin: the column sets, the snapshot, Sync for tonight's state-only writes, `supports_state`, then the copied golden rows.
3. ACP core: validation, the migration, the settings route, the UI, `/api/plans/match` and `/api/sync`. PR #97 is closed unmerged, and its tests move here.

ACP core goes last on purpose. It is the only piece that deploys on merge, and the migration is the only step that is hard to undo. Until the plugin is installed on Voyager, a plan set inactive in ACP will not reach TS.

### Live run on Voyager

Nothing in TS is edited from the first step to the last.

- [ ] Cancel the current pending upload from its review page.
- [ ] Merge the extension and copy it to Brutix. Merge ACP core, which deploys. Check the log says 47 plans moved to active, and ACP shows no badges.
- [ ] Install the plugin on Voyager and restart NINA.
- [ ] Back up `schedulerdb.sqlite` with NINA closed, then open NINA.
- [ ] Press "Send TS to ACP". The review page shows the 9 inactive projects as TS-only changes to inactive, and no other state change.
- [ ] Apply. ACP shows those 9 plans with the Inactive badge.
- [ ] Press "Sync All to TS". Then check in TS that the 9 are still inactive, the other projects are still active, and no dither, grader or horizon setting changed.
- [ ] Press "Send TS to ACP" again. The preview shows nothing new, nothing updated and no conflicts.
- [ ] Set one plan inactive in ACP and run Sync for tonight. TS shows that project inactive.

## Decisions for Rohan

Approved by Rohan 2026-09-25, all three as recommended.

1. Meridian window stays owned by ACP. Recommended: yes. ACP already has the field in the editor, the import and the diff, and the rule from PR #12 says ACP owns what it has a setting for. Making it TS-only means removing the field from the editor.
2. A new plan starts as active, not draft. Recommended: active. Every plan you have made was left as draft and you expected it to image. Starting as draft makes each new plan sit unscheduled in TS until you change it.
3. When ACP and TS both change a setting between syncs, the push from the rig writes ACP's value. Recommended: yes, ACP wins. The push can run unattended at dusk, so the other choice would stop it to ask. The cost is that a change made at the rig can be undone. The dock says when that happens.
