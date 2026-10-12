# Spec: filter rows, shooting order and the Target Scheduler sync

This spec changes how a plan's exposure goals are edited in Astro Coverage Planner (ACP) and how they reach NINA's Target Scheduler (TS). It covers five changes that touch the same data, so they are built as one piece of work in six phases.

- A plan's goals become one ordered list of filter rows. "R at 180 s" and "R Stars at 15 s" are simply two rows.
- The sub length comes from the TS exposure template and is no longer edited in ACP.
- The list order is the shooting order, and it is written to TS.
- The filter switch frequency and smart exposure order become editable in ACP.
- The ACP side of the Astro Auto Calibration (AAC) incremental masters spec: `target_class`, AAC's figures per row, and the accepted write to TS.

Rohan approved the panel design in the mockup at https://claude.ai/artifact/QnU2KhRiAPtQpviLGAbGNS (artboards "Proposed", "New plan" and "Today"). The side panel stays 380 px wide. Section "What TS does with an order" explains, from TS's source, how the order has to be written.

## Terms

- **Filter row**: one entry in a plan's `filter_rows` list. It names a filter, a TS template and a goal.
- **Row id**: a filter row's stable identifier, a UUID that is never reused.
- **Exposure plan**: a row in the TS table called `exposureplan`. Each filter row with a goal above zero becomes one exposure plan per target.
- **Override order**: the rows in the TS table called `overrideexposureorderitem` for one target.
- **Cadence**: the rows in the TS table called `filtercadenceitem` for one target. TS writes them itself while imaging.
- **Natural order**: a target's enabled exposure plans sorted by `exposureplan.Id`, smallest first.
- **Writers**: the two sync implementations. The Python one is `nina_ts_sync` in `acp-nina-ts-sync`. The C# one is `Services/TargetScheduler/` in `ACP.NINA.Plugin`. They must produce identical rows.
- **Base**: `ts_links[P].base_snapshot` on a plan, the TS values this rig saw at the last sync. P is the rig's NINA profile id.

## What TS does with an order, from its source

All references are to `tcpalmer/nina.plugin.targetscheduler` at commit `17b36a4`, under `NINA.Plugin.TargetScheduler/`. TS cannot be built or run on the Mac, because it targets `net8.0-windows7.0`.

### Which code picks the next exposure

`Planning/Exposures/ExposureSelectionExpert.cs:15-29` chooses one of four selectors per target, in this order:

| Condition | Selector | Behaviour |
|---|---|---|
| `project.SmartExposureOrder` on | `SmartExposureSelector` | TS picks filters itself. The override order and the switch frequency are ignored. |
| override order has any rows | `OverrideOrderExposureSelector` | TS walks the override rows in a circle. The switch frequency is ignored. |
| switch frequency 0 | `RepeatUntilDoneExposureSelector` | TS shoots the first unfinished plan in natural order until it is done (`RepeatUntilDoneExposureSelector.cs:25`). |
| switch frequency N | `BasicExposureSelector` | TS builds a cadence of N frames per plan in natural order and walks it in a circle. |

The override order wins over the switch frequency. An override order with one row per filter makes TS shoot one frame of each filter in turn. That holds even when the project's switch frequency is 0.

### What `referenceIdx` points at

`referenceIdx` is a zero-based index into the target's enabled exposure plans, in natural order.

- The planner removes disabled plans before it builds the target (`Database/SchedulerPlanLoader.cs:53`).
- The planner then reads `target.ExposurePlans[oeo.ReferenceIdx]` (`Planning/Entities/PlanningTarget.cs:89`) and `target.AllExposurePlans[item.ReferenceIdx]` (`Planning/Exposures/OverrideOrderExposureSelector.cs:32`).
- TS's own override editor counts the index over enabled plans only (`Controls/DatabaseManager/OverrideExposureOrderViewVM.cs:146-151`).
- The editor loads plans sorted by `Id` (`Database/Schema/Target.cs:294-297`, called at `Database/SchedulerDatabaseContext.cs:350` and `:384`). The planner's loader at `SchedulerDatabaseContext.cs:306-320` does not sort explicitly. It relies on the database returning rows in `Id` order, and TS's own editor makes the same assumption.

`action` is 0 for an exposure and 1 for a dither (`Database/Schema/OverrideExposureOrderItem.cs:12-14`). A dither row has `referenceIdx` -1. The cadence uses the same two values (`Database/Schema/FilterCadenceItem.cs:9-11`).

### Three side effects the sync must handle

A target with override rows shoots only the plans those rows name. `PlanningTarget.cs:80-97` builds the active plan list from the override rows alone. A plan the sync adds later is never shot, and this holds even with smart order on.

An override order turns off the project's "dither every" setting for that target. `OverrideOrderExposureSelector` has no dither manager. It dithers only where a row has action 1 (`OverrideOrderExposureSelector.cs:25-30`). The other two non-smart selectors use the dither manager (`BasicExposureSelector.cs:16`, `RepeatUntilDoneExposureSelector.cs:16`).

TS reuses a stored cadence before anything else. `Planning/FilterCadenceFactory.cs:19-29` restores the cadence rows when any exist. Only when there are none does it build from the override order (lines 31-41) or from the switch frequency (lines 50-59). TS writes the cadence back after every frame (`Planning/Exposures/BaseExposureSelector.cs:53-72`). So a new override order or switch frequency does nothing while old cadence rows remain. TS's own editor deletes them when it saves an override order (`Controls/DatabaseManager/TargetViewVM.cs:641`, through `SchedulerDatabaseContext.cs:847`). It also deletes them when the switch frequency changes (`Database/Schema/Project.cs:257`, `SchedulerDatabaseContext.cs:752-755`), and when a plan is toggled or deleted (lines 973-975, 994-996).

### What that means for Rohan's database today

Three targets have override orders in the scratch copy of Voyager's database from 12 October 18:46. They are G7.7-3.7, Dolphin Nebula and Helix Nebula. The rows appeared between the 15:01 backups of 11 and 12 October, so they were set in TS on 11 October. Their projects have a switch frequency of 0. TS therefore rotates one frame per filter on those three targets, rather than finishing each filter. Helix's order is OIII, Ha, SII, B, G, R. B, G and R are complete, so Helix now shoots one frame each of OIII, Ha and SII in turn.

### What the sync writes so TS shoots in ACP's order

There is no column for plan order in TS. Natural order follows `Id`, and changing `Id` would break `acquiredimage.exposureId` and every pinned id in ACP's `ts_links`. So the order goes into the override rows. The sync writes them so they reproduce the switch frequency and the dithering TS would otherwise do itself.

For each target, the sync builds the override rows from ACP's order:

1. Walk the filter rows in list order. Skip rows with no enabled exposure plan on this target.
2. For each row, add a block of exposure rows pointing at its plan's `referenceIdx`. The block holds N rows when the switch frequency is N. When the switch frequency is 0, it holds the frames TS needs to call the plan done (table below).
3. When the project's `ditherevery` is above 0, insert dither rows by TS's own rule in `Planning/DitherInjector.cs:76-116`. A dither goes before the item where any filter is seen `ditherevery + 1` times since the last dither, with the list treated as a circle.
4. Number the rows 1, 2, 3 in the `order` column.

At switch frequency 0 the block size follows TS's completion rule in `Planning/Exposures/ExposureCompletionHelper.cs`:

| Project grader | TS calls the plan done when | Block size |
|---|---|---|
| on | `Accepted` reaches `Desired` (lines 20-27) | `desired` |
| off | `Acquired` reaches `(int)(throttle x Desired)` (lines 29-32) | ceil(throttle / 100 x `desired`) |

The throttle is `profilepreference.exposureThrottle` for the profile, a percentage. The writer reads the real value at push time. TS's default is 125 (`Database/Schema/ProfilePreference.cs:67`), and Rohan set his to 100 on 12 October.

A block sized this way works because TS skips finished plans (`OverrideOrderExposureSelector.cs:33`, and `PlanningTarget.cs:65-71` marks them rejected when the target loads). The filter finishes inside its block and TS moves straight on to the next block.

Two exceptions keep the footprint small:

- When ACP's order equals the natural order, the sync writes no override rows. TS then uses its own selector for the switch frequency, with the same result and with per-template dither settings intact.
- When smart order is on, the sync does not write override rows. TS ignores them.

### Restarting the walk every night

TS's place in the override rows only moves forward (`Planning/FilterCadence.cs:36-51`). Suppose TS passes the end of Ha's block, and next morning AAC lowers Ha's `accepted`. Without help, Ha's shortfall waits until every other filter in the circle is done.

So for every target that has override rows, the sync deletes the cadence rows on every push, whether or not it writes the order. TS then rebuilds the cadence from the override rows with row 1 marked next (`FilterCadenceFactory.cs:31-41`). Each night's walk starts at row 1, skips finished plans, and lands on the first unfinished plan in ACP's order.

At switch frequency N this costs at most N - 1 extra frames per night. The first unfinished plan gets a full block of N again, even if it shot part of a block the night before.

The sync also deletes a target's cadence rows whenever it deletes its override rows, and for every target in a project whose switch frequency or smart order it writes.

## 1. The filter list in the plan editor

### What stays the same

The side panel stays 380 px wide by default. It is already resizable from 300 to 760 px (`SIDE_PANEL_MIN_W` and `SIDE_PANEL_MAX_W`, `static/app.js:5731-5732`). Its other tabs are unchanged.

### The list

The "Filter goals" fieldset shows the plan's filter rows as one list, in shooting order. Each row is a card laid out as a grid with aligned columns:

| | Column 1, 22 px | Column 2, 104 px | Column 3, the rest | Column 4, 26 px |
|---|---|---|---|---|
| Line 1 | position and grab handle, spanning both lines | filter colour dot and filter drop-down | TS template drop-down | remove button |
| Line 2 | (spanned) | goal number and unit drop-down | progress bar, with done and left beneath | empty |

The progress block never sits under the remove button.

The filter drop-down lists the camera's filters in the order L, R, G, B, Ha, OIII, SII, then any others. The dot takes its colour from `FILTER_COLORS` (`static/app.js:16`).

The template drop-down lists the TS templates whose filter is the row's filter. Each option shows the template's sub length, for example "R · 180 s". The last option is "New template…", which asks for a name and a sub length in seconds.

The unit drop-down offers "h" and "fr". Switching it converts the goal using the row's `template_sub_s`:

- hours to frames: round(hours x 3600 / sub)
- frames to hours: frames x sub / 3600, rounded to 4 places

Picking a template with a sub under 60 s sets the unit to "fr", and 60 s or more sets "h". The goal converts with the new sub.

Progress shows "3.0 / 4.2 h" or "18 / 20 fr" on the left and "1.2 h left" on the right. A finished row shows "done" and the card is dimmed. A row with a goal of 0 shows "set a goal". Done comes from AAC's figures when the row has them (section 6), otherwise from `actual_hours`.

### Adding and removing

A new plan starts with no rows. The fieldset shows a short note and an "+ Add filter" button. The same button sits under the last row of a plan that has rows.

"+ Add filter" appends a row. Its filter is the first filter, in drop-down order, that no row uses yet. If every filter is used, it is the first filter again. Its template is that filter's usual template: the camera's `ts_template_name` for that filter when the template list has it, else the first template for that filter by name. Its goal is 0.

The remove button deletes the row from the plan. On the next push its exposure plan in TS is disabled, never deleted (section 3).

### Reordering

Rows move by their grab handle only. There are no up and down buttons.

- Dragging a handle shows a horizontal drop line, with a marker at each end, exactly where the row will land.
- The handle is a focusable button labelled "Drag to reorder. Arrow keys also move it." Up and down arrows move the row one place.
- Positions renumber at once.
- With "Smart order" on, the handles are disabled and dragging does nothing.

### Above the list

- A two-way switch, "Finish each" and "Rotate every N", with N editable (section 4).
- A "Smart order" checkbox (section 4).
- One line saying what TS will do (section 4).

### The placeholders mangled by commit cc2aecb

Commit `cc2aecb` is "Tidy the plugin prep branch: plain punctuation everywhere, site lookup by id", from 4 September 2026. It replaced em dashes with ", " in four files: `app.py`, `docs/api.md`, `docs/setup-archive.md` and `static/app.js`. In 18 places in `static/app.js` the dash was an empty-value placeholder. Each now renders as a stray comma. The line numbers are on `origin/main` at `7b05254`.

| Line | Where |
|---|---|
| 1207 | database sub hours |
| 1210 | telescope list |
| 1211 | camera list |
| 1213 | date range |
| 1214 | field of view |
| 1282 | pixel scale, so the row reads "@ ," |
| 3596, 3597 | diff renderer, from and to |
| 3630 | diff value formatter |
| 3778, 3779 | second diff renderer, from and to |
| 4651 | tile priority |
| 4742 | tile field of view |
| 4748 | uncovered band marker |
| 4809 | tile priority, short form |
| 5574 | peak altitude |
| 5601 | peak altitude, which also makes line 5602 read "Peak: , , 3h above N°" |
| 7006 | goal row, no TS template |

Two more changed a separator rather than a placeholder: line 1610 (a tooltip) and line 7736 ("Filters, n (sub s · gain · offset · bin)"). They read oddly but not wrongly, and this spec leaves them.

The fix adds one constant near the top of `static/app.js`, `const EMPTY_CELL = "–";` (an en dash, U+2013), with a comment saying it is data, not prose. All 18 places use it. The changes in `app.py` and the two docs are prose and stay as they are. Line 7006 goes away with the old goal table, so 17 remain.

## 2. The TS template list from the plugin

`/api/ts-templates` (`app.py:4370-4408`) reads the TS database at `TS_DB_PATH`. On Brutix that file does not exist, so ACP has no template list.

The plugin sends the list instead.

- During Sync for tonight, and on "Sync All to TS", the plugin reads every `exposuretemplate` row for its profile. It posts them to a new route, `POST /api/ts-templates`, with the same token scheme as `POST /api/plans/<id>/progress`.
- The body is `{"profile_id": "...", "sent_at": "<ISO time>", "templates": [{"name", "filter", "default_exposure_s", "gain", "offset", "bin"}]}`.
- ACP stores the latest list per profile in `data/ts_templates.json`. A new post for a profile replaces that profile's list.
- `GET /api/ts-templates` returns the database list with `"source": "database"` when the file is readable, as today. Otherwise it returns the stored lists with `"source": "plugin"` and each profile's `received_at`.

With no list at all, the template drop-down holds only the row's current template and "New template…". A new row then uses the camera's `ts_template_name` for the filter, or "{filter} ({camera name})", with the sub from the camera's `default_sub_s`, else 300.

## 3. Filter rows

### Data model

A plan gains `filter_rows`, an ordered list. Its order is the shooting order. Each row holds:

| Field | Type | Meaning |
|---|---|---|
| `id` | string, UUID | Stable, never reused. The editor makes one with `crypto.randomUUID()`. The server assigns one to a row that arrives without. |
| `filter` | string | Canonical filter name, such as "R". |
| `template` | string | TS exposure template name, such as "R Stars". |
| `template_sub_s` | number | Snapshot of the sub length TS uses for this row, in seconds. |
| `unit` | `"h"` or `"fr"` | The goal's unit. |
| `target_hours` | number | The goal, when `unit` is "h". |
| `target_frames` | integer | The goal, when `unit` is "fr". |
| `actual_hours` | number | TS's acquired hours, from the plugin. |
| `aac` | object | AAC's figures, written only by AAC (section 6). |

`filter_goals` and its per-row `sub_exposure_s` go. ACP no longer edits a sub length.

`template_sub_s` is what the writers divide by and what AAC matches frames against. It holds:

- for a row not yet in TS, the template's default exposure from the template list, or the sub typed into "New template…"
- after a push or import, the sub TS uses for that row's exposure plan: the plan's own `exposure` when it is above 0, else its template's `defaultexposure` (`Planning/Entities/PlanningExposure.cs:50`)

`_validate_plan_payload` in `app.py` rejects:

- two rows in one plan with the same `filter` and `template`
- two rows with the same `id`
- a `filter` the plan's camera does not have
- an empty `template`
- a `unit` other than "h" or "fr", or a row missing the goal field its unit needs
- a negative `target_hours`, or a `target_frames` that is not a whole number of at least 0
- a `template_sub_s` that is not above 0

### How the writers compute `desired`

| Unit | `desired` |
|---|---|
| h | max(1, ceil(`target_hours` x 3600 / `template_sub_s`)) |
| fr | `target_frames` |

A row whose goal is 0 pushes no exposure plan, as today. For an AAC-linked row the floor in section 6 applies on top.

### Removing and re-adding a row

When a row is removed in ACP, the next push sets `enabled` to 0 on its exposure plan on every target of the plan. It never deletes the plan, so the plan's `acquired` and `accepted` and its `acquiredimage` history stay. The writer finds the plan through the row id in `ts_links`. A plan with no link, or one never stamped by ACP, is left alone.

Disabling a plan shifts the `referenceIdx` of every enabled plan after it (`SchedulerPlanLoader.cs:53`). So in the same push the writer rebuilds that target's override rows:

- rows the sync generated are regenerated from the new list, by section 5
- rows set in TS are kept, with each `referenceIdx` remapped to the same plan's new index and the disabled plan's rows dropped

It also deletes that target's cadence rows. TS's own editor clears both tables when it toggles a plan (`SchedulerDatabaseContext.cs:973-975`). The writer keeps the override rows instead, because a cleared order would put the target back in natural order.

The importer reads enabled plans only, so a removed row does not come back on the next upload.

Adding a row with the same filter and template as a removed one produces the same exposure plan guid. The writer finds the disabled plan by that guid, sets `enabled` to 1 and links it to the new row id. The plan keeps its counts, so the row starts with the frames TS already took. Re-enabling shifts `referenceIdx` too, and the writer rebuilds the override rows the same way.

### What the push writes to `exposureplan.exposure`

The push writes -1, meaning "use the template's default", and only in two cases:

- when it inserts an exposure plan
- when the row's `filter` or `template` differs from the one recorded in the base for that row

Otherwise `exposure` joins `INSERT_ONLY_COLUMNS` for `exposureplan` (`nina_ts_sync/schema.py:312-319`) and `insertOnlyColumns` (`TsSchema.cs:90-99`).

The reason for -1: the sub length now belongs to the TS template. With -1, a change Rohan makes to a template in TS reaches every plan that uses it, and ACP never fights it.

The reason for leaving existing plans alone: today both writers put ACP's `sub_exposure_s` into `exposure` on every push (`nina_ts_sync/convert.py:377-391`). Every exposure plan ACP made therefore has an explicit `exposure`, which may differ from its template's default. Writing -1 to those would change what TS shoots. Leaving the column alone keeps TS shooting exactly what it shoots today. The migration sets `template_sub_s` to that same old sub, so ACP shows and computes with the true value.

The push result lists every row whose exposure plan's `exposure` differs from its template's default, such as "M42 R: TS shoots 300 s, template R says 180 s". Picking a template with the right sub in ACP clears it, because a template change writes -1.

### Templates

Both writers look a template up by profile and `name`. A hit is used unchanged, because TS owns the templates it has. On a miss, the writer inserts one:

- `name` is the row's `template`
- `filtername` is the row's `filter`
- `defaultexposure` is the row's `template_sub_s`
- gain, offset and bin come from the camera's filter settings in ACP's gear, as today
- the guid recipe is `"{profile}/template/{filter}/{camera}/{template}"`, with each free-text part length-prefixed as in `schema.py`

ACP no longer updates existing templates. Today the guid update rewrites a template's default exposure, gain, offset and bin from ACP's gear on every push.

The sync never deletes templates. The push result lists every template on the profile whose `filtername` is not a filter on any of the profile's cameras. In the scratch database copy that is three templates, ids 15, 17 and 20, with filter names "B Stars", "G Stars" and "R Stars". The old writer made them by using a goal key as a filter name. Rohan deletes them in TS.

### Exposure plan identity

Each row's exposure plan guid is `"{profile}/plan/{targetGuid}/{filter}/{template}"`, length-prefixed. Before inserting, the writer looks for the row's plan in this order and restamps any hit with the new guid:

1. the new guid
2. the exposure plan id pinned in `ts_links` for this row id and panel
3. the old recipe `exposure_plan_guid(profile, targetGuid, template)`, which older pushes used for keys like "R Stars"
4. the old recipe `exposure_plan_guid(profile, targetGuid, filter)`, for the first row of that filter in the list only

A restamped plan is repointed at the row's template. Its `acquired`, `accepted` and `exposure` stay as they are. This stops the migration creating a second exposure plan for every row already in TS.

`TsConvert.cs` and `TsGuid.cs` mirror these rules. `TsIdentityRecipeTests` gains both recipes.

### What the importer does

`_keyed_plans` (`nina_ts_sync/from_ts.py:346-404`) and its collision keying go. The importer emits `filter_rows` directly, one per enabled exposure plan, in the target's effective order (section 5):

- `filter`: the template's filter name, through `canonical_filter_name`
- `template`: the template's name
- `template_sub_s`: the sub TS uses, as defined above
- `unit`: "fr" when that sub is under 60 s, else "h"
- the goal from `desired`, in that unit
- `actual_hours` as today
- `id`: the id of the ACP row with the same filter and template when the plan has one, else a new UUID

The three-way diff (`diff.py`) compares rows by `id`, with the same noise rules as today's goals.

### Progress from the plugin

`ProgressMath.BuildFilters` (`ACP.NINA.Plugin/Services/ProgressMath.cs:57-75`) sums exposure plans by filter name. R and R Stars therefore both land on R. It changes to send one entry per exposure plan, as `{"filter": "R", "template": "R Stars", ...}`.

`api_plan_progress` (`app.py:4169-4237`) matches an entry to the row whose `filter` equals `canon_filter(entry.filter)` and whose `template` equals the entry's template exactly. An entry with no template, from an older plugin, matches when exactly one row has that filter. Otherwise it is returned in `unknown_filters`.

### Other readers of the goals

Every reader of `filter_goals` moves to `filter_rows`: 15 places in `app.py`, 23 in `static/app.js`, one in `shooting_publish.py`, and the sync and plugin files that read goals. Readers that want a total per filter (sky map dots, coverage, the live page) sum the rows of each filter through one helper in each language.

`GET /api/plans` still returns a `filter_goals` object for older plugins, built from the first row of each filter and never stored. Each entry has `target_hours`, `sub_exposure_s` from `template_sub_s`, and `actual_hours`. A filter whose only rows are frame rows gets `target_hours` = frames x sub / 3600. An older plugin therefore keeps pushing each filter's first row, and pushes no star rows, until the new plugin is installed. `PUT` ignores a `filter_goals` it receives.

### Migration of existing plans

`load_plans` runs a one-shot migration, guarded by `filter_rows_migrated: 1` on the `plans.json` document. It takes a timestamped backup first, as `_maybe_migrate_draft_plans_to_active` does (`app.py:637-672`). It ships in phase 3 with the importer change, because a push between the two would write a second exposure plan for every star row.

For each plan, the migration turns every `filter_goals` entry into a row:

| Old entry | `filter` | `template` |
|---|---|---|
| key is a canonical filter name | the key | the camera's `ts_template_name` for it, else "{key} ({camera name})", else the key: whatever the old writer pushed |
| key is not canonical but `canon_filter` maps it, such as "R Stars" | the canonical filter | the key |
| a `sub_rows` entry, from any interim build | its parent key | its key |
| key with no canonical filter | the key, logged by plan id | the key |

Each row gets a new `id` and `template_sub_s` = the old `sub_exposure_s`, else 300. A sub under 60 s makes a frame row with `target_frames` = max(1, ceil(hours x 3600 / sub)), or 0 when the hours were 0. Every other row keeps `target_hours`. Both choices give the same `desired` the old writer computed, so the next push changes no goal in TS. `actual_hours` and any `aac` block carry over.

Rows are ordered L, R, G, B, Ha, OIII, SII. Within a filter, the row from the canonical key comes first, then the others by template name. Filters outside that list come last, by name.

The migration also rewrites `ts_links` exposure plan keys from `"<targetId>_<filter key>"` to `"<targetId>_<row id>"`, and moves base snapshot entries to the row ids.

## 4. Switch frequency and smart order

### Behaviour

The switch above the list sets the project's switch frequency:

- "Finish each" stores 0.
- "Rotate every N" stores N, from 1 to 99.

The "Smart order" checkbox stores the project's smart exposure order. When it is on, dragging is disabled and the list order is kept, not cleared.

The line under them reads:

- "Shoots each filter until its goal is met, top to bottom."
- "Shoots 3 frames of each filter, top to bottom, then repeats."
- "Target Scheduler picks the order itself (moon and altitude). Dragging is off."

Both settings belong to the TS project. Like state and priority, they are saved through `PUT /api/projects/<name>/settings` (`app.py:3623`) and apply to every plan in the project. The editor says so with the existing one-line note.

### Data model

| Plan field | Type | Absent means |
|---|---|---|
| `filter_switch_frequency` | integer, 0 to 99 | unknown. The writers never write it on update, and the editor shows 0. |
| `smart_exposure_order` | boolean | unknown. The writers never write it on update, and the editor shows off. |

When plans in one project disagree, the writers take the value from the first plan in `plans.json` order and add a line to the push result.

### What the writers do

`filterswitchfrequency` and `smartexposureorder` leave `INSERT_ONLY_COLUMNS` and the project entry in `insertOnlyColumns`. They join `state`, `priority` and `minimumtime` in the rule from `docs/specs/ts-project-settings.md` section 5. A writer includes each column in an update only when the plan's value differs from the base.

- Python: `convert.py` adds them to `project_update_skip_cols_by_guid` when unchanged.
- C#: `TsConvert` adds them to `ConditionalColumnsToWrite` (`TsConvert.cs:413-445`) when changed.
- On insert both take ACP's value, or 0 and off when the plan has none.

When a writer writes either column, it deletes the cadence rows of every target in that project in the same transaction. It then rebuilds the override rows for those targets, by section 5's rules.

The base project block (`state.py` `_project_snapshot`, `TsState.cs`) gains `filterswitchfrequency`, `smartexposureorder` and `ditherevery`. The importer maps the first two to the plan fields.

## 5. Filter order

### Behaviour

The order is the order of `filter_rows`. There is no separate order field. Every panel of a mosaic gets the same order.

The safety rule: a push replaces an override order in TS only when the user changed the order in ACP. Everything below serves it.

The decision is per target, not per panel. A target's base order is the one its anchor panel (1,1) recorded. "ACP's order changed" means ACP's order differs from that. When it has not changed, every panel's TS rows are left alone, whatever they hold (a hand order on one panel is not copied to another). When it has changed, every panel gets ACP's order. An import records the anchor panel's order as the base order for the whole target, and never another panel's own order.

Two kinds of edit count as a change of order. Moving rows does. So does adding a row anywhere but the end of the list: the user placed it, and its plan is appended to the natural order, so the order has to be written for it to land where the user put it. Adding a row at the end does not. Removing a row never does. A row counts as added only if the base did not know its id at all: the base records every ACP row id it knew as `known_rows`, including rows with goal 0 and disabled ones, so a goal raised from 0 keeps the row's place and is not a reorder. A base without `known_rows` treats the ids in its `exposure_order` and its exposure plans as known.

### What the writers do

At each push, for each target of the plan, the writer computes:

| Name | Value |
|---|---|
| ACP order | the row ids in list order, reduced to rows with an enabled exposure plan on this target |
| Base order | `exposure_order` for the anchor panel (1,1) in the base, a list of row ids, used for every panel of the target, with `known_rows` recorded beside it |
| Base rows | `override_rows` for this panel in the base, as `[action, referenceIdx]` pairs |
| TS rows | the target's override rows now, as the same pairs |
| Generated rows | the rows built from the ACP order by "What the sync writes" above, or none when the ACP order equals the natural order |

The decision:

| Case | Writer does |
|---|---|
| Smart order is on, after this push | nothing to the override rows, except the new plan rule below |
| No base, or a base with no `exposure_order` | nothing |
| The push inserted this target | writes the generated rows (smart order is checked first), because the target has no rows of anyone's yet |
| ACP order differs from base order | writes the generated rows, so ACP wins |
| ACP order equals base order, and TS rows equal base rows, which are not empty | writes the generated rows if they differ from TS rows |
| ACP order equals base order, the base has no rows, TS has none, and ACP's order matched TS's natural order before the push | writes the generated rows if they differ from TS rows |
| ACP order equals base order, and TS rows differ from base rows, or the base has none (and the row above does not apply) | nothing, because the order was set in TS |

"Differs" and "equals" use the rule above: a row added anywhere but the end, or rows in another relative order, differ. A row removed, or added at the end, does not.

The fifth case refreshes rows the sync wrote itself. A changed goal, AAC floor, throttle, grader setting or switch frequency changes the block sizes, so the generated rows change. The sixth case keeps an order set in TS. A panel's base rows are only ever rows the sync wrote, because an import leaves out rows set in TS, so TS rows equal to them are the sync's own even when stale, and a panel holding stale sync rows is refreshed to ACP's order. A panel whose base has no rows has none of the sync's to refresh, so it is left alone: a hand order on panel 1 that ACP now holds is not copied to a panel 2 that has none. The one exception is a target where the base and TS both have no rows and ACP's order matched TS's natural order (its enabled plans in `Id` order, over the rows with plans) before the push. Nothing there was set in TS, and a plan added back after removal keeps its lower `Id`, so without the refresh TS would shoot it first while ACP lists it last.

An import records `known_rows` from the old base plus the row ids behind TS's plans, never from ACP's rows as they stand. A row the user added in ACP since the last push therefore stays new at the next push, however many imports come between. When an import leaves ACP listing rows in an order TS will not shoot (TS's effective order differs from ACP's over the enabled rows both have), it adds a warn note naming the target and both orders, such as "Helix: ACP lists Ha, OIII, SII but Target Scheduler will shoot SII, Ha, OIII. Push after reordering in ACP to change it." No note is made when the order is pulled, or when Keep ACP was chosen on the order, since that push replaces TS's rows.

"Writes" means: delete the target's override rows and insert the new ones, in the push transaction. Writing no rows deletes the override rows, which returns the target to natural order. Separately, every push deletes the cadence rows of every target that has override rows after it ("Restarting the walk every night" above).

New plan rule: a push may insert an exposure plan on a target whose override rows it did not replace. The writer then appends that plan's block to the end and deletes the cadence rows. Without this, TS never shoots the new plan (`PlanningTarget.cs:80-97`). The push result names the target.

After the push, the base records the target's effective order as `exposure_order` (the anchor panel's, for every panel), and each panel's override rows as `override_rows`. As with state, a push that left TS's rows alone keeps the old base order. A base built with no push outcome records `override_rows` only for a target that has none in TS, because rows set in TS must never be taken for rows the sync wrote. A stored `override_rows` that is not a list of integer pairs reads as not recorded, in both writers.

### What the importer does

The effective order of a target is read like this:

- With override rows: plans in the order of their first exposure row. Enabled plans named by no row go at the end, with a note. An example: "TS never shoots R Stars on Helix, because its override order leaves it out."
- Without override rows: natural order.

The importer emits `filter_rows` in that order. The diff compares the order as the list of row ids, by the three-way rules of `ts-upload-import.md`. A base with no `exposure_order` reads like a missing `state` in `ts-project-settings.md` section 3. ACP could not set an order before this change, so any difference is a change made in TS.

An override order with repeats or dithers, made by hand in TS, comes into ACP as its first-occurrence order. Its rows stay in TS until ACP's order changes.

The order is its own field in the import choices, apart from the plan's Keep ACP. Choosing Keep ACP on a plan for its other fields does not stop TS's order being pulled, and does not change how the order base is recorded. Choosing Keep ACP on the order itself is an explicit choice of ACP's order over TS's: the base still records TS's order, so the next push replaces the order set in TS. The import preview says so on that row: "Your next push will replace the order set in Target Scheduler." For a conflict that includes the order, the plan's Keep ACP is that choice. When the order is no part of the conflict, Keep ACP on the plan still pulls TS's order as an auto-pull, unless the order itself is kept, so the base and ACP agree; the conflict card shows the replace note only when the order is in the conflict or was kept.

The migration in section 3 does not count as an ACP change to the order. A base without `exposure_order` means the first push writes no order, and the next upload brings in each target's effective order from TS. Rohan's three hand-made override orders therefore stay in TS until he changes the order in ACP.

## 6. What AAC needs from ACP

AAC's incremental masters spec (`Astro-Auto-Calibration/docs/specs/incremental-master-builds.md`, section 7) defines the storage contract this section implements. The AAC spec is being amended to the `filter_rows` names used here.

### `target_class`

A plan gains `target_class`, one of `survey`, `deep` and `pretty`. Absent, or any other value, means `survey`. The plan editor shows it as a select in the Target fieldset. `_validate_plan_payload` rejects other strings. AAC reads it with the rows through `GET /api/plans`.

### `POST /api/plans/<plan_id>/accepted`

AAC is the only caller. It authenticates as the plugin does on `/progress`, with an `Authorization: Bearer <token>` header that ACP checks against `ACP_API_TOKEN`. A missing or wrong token gets 401, and an unknown plan id gets 404. The body carries `source`, `at`, `filed_through_night`, `min_total_frames` and a list of `rows`. AAC sends every row of the plan. Each row names its target by `id`, the row id, and echoes `filter` and `template` for checking. Each carries these fields:

- `accepted_seconds`, `accepted_frames`
- `seen_seconds`, `seen_frames`
- `complete`
- `estimated_hours`, `measurement`

ACP stores them as follows:

| Path on the plan | Content |
|---|---|
| `filter_rows[i].aac` | the row's fields above, replacing the previous block |
| `aac.filed_through_night` | from the request |
| `aac.min_total_frames` | from the request |
| `aac.received_at` | the time ACP received it |

A row is stored only when its `id` exists on the plan, its `filter` passes `canon_filter` to the row's filter, and its `template` equals the row's template exactly. Any other row comes back in `unknown_rows` with a reason: "no such row", or "row now has filter G and template G Stars". Values may go down. `actual_hours` is never changed.

### Done, as the editor shows it

A row with an `aac` block shows done as `aac.accepted_seconds / 3600` hours, or `aac.accepted_frames` in a frame row. It counts as finished when `aac.complete` is true. A row without one uses `actual_hours` against the goal, as today.

### The accepted write to TS

Both writers follow the AAC spec's section 7, "How the shortfall reaches TS", which is summarised here.

- Before any AAC write, the writer reads the profile's grading settings in `profilepreference`. If `delayGrading` is not 0, or any of `enableGradeRMS`, `enableGradeStars`, `enableGradeHFR`, `enableGradeFWHM` and `enableGradeEccentricity` is not 0, it makes no AAC write for that profile and logs a warning.
- A project is eligible when every exposure plan in it links to a row with an `aac` block, no target in it is a mosaic panel, and `aac.filed_through_night` is on or after the night that ended that morning.
- For an eligible project, one transaction writes `accepted` for every exposure plan and sets `project.enablegrader` to 1. Any other project gets neither write.
- `exposureplan.accepted` and `project.enablegrader` stay in `INSERT_ONLY_COLUMNS` and `insertOnlyColumns`. A per-project exception, built by `convert.py` and `TsConvert` like `project_update_skip_cols_by_guid`, lets them into the update for eligible projects.
- `acquired` is never written.

The values, for each AAC-linked row, with `sub` meaning `template_sub_s`:

| Quantity | Formula |
|---|---|
| `desired`, hour row | max(ceil(`target_hours` x 3600 / sub), `aac.min_total_frames`) |
| `desired`, frame row | max(`target_frames`, `aac.min_total_frames`) |
| AAC count | floor(`aac.accepted_seconds` / sub + 0.000001) |
| frames short of the floor | max(1, `aac.min_total_frames` - `aac.accepted_frames`) |
| `accepted`, row complete | max(AAC count, `desired`) |
| `accepted`, row not complete | max(0, min(AAC count, `desired` - frames short of the floor)) |

Both suites run the shared case file `tests/fixtures/aac_accepted_cases.json` in acp-nina-ts-sync, as the AAC spec requires.

## 7. Acceptance tests

Each test names the repo it lives in. "Python" means `acp-nina-ts-sync/tests`, run with `~/Developer/Astro-Coverage-Planner/.venv/bin/python -m pytest tests -q`. "C#" means `ACP.NINA.Plugin.Tests`, which runs only on Windows. "ACP" means `Astro-Coverage-Planner/tests`.

### A TS order oracle

The Python tests need to show that TS would shoot in a given order. TS's planner cannot run on the Mac. So the Python suite gains `tests/ts_order_oracle.py`, a port of the four pieces that decide the next frame:

- `ExposureSelectionExpert.GetExposureSelector`
- `FilterCadenceFactory.Generate`
- `PlanningTarget.GetActiveExposurePlans`
- the `Select` and `ExposureTaken` methods of the override, repeat-until-done and basic selectors

Each function carries the file and line it ports. The oracle reads a target from a test database, then simulates frames. A frame adds one to `acquired` and to `accepted`, and marks a plan done by `ExposureCompletionHelper`'s rules, including the throttle. It writes cadence rows back as TS does. It returns the filter of each frame, and "D" for a dither.

The oracle is checked against TS's own tests. `tests/test_ts_order_oracle.py` ports the expected values from TS's `NINA.Plugin.TargetScheduler.Test/Planning/FilterCadenceFactoryTest.cs` and `Planning/Exposures/OverrideOrderExposureSelectorTest.cs`.

The oracle is a port, so it cannot prove TS itself agrees. Test O9 below is the check against TS itself.

### Scenarios

Unless a test says otherwise, the fixture is one plan "M42" on a fresh database at `user_version` 28. It has two rows, Ha then OIII, each with template sub 300 s and a 4-frame goal. The grader is on, the switch frequency is 0, smart order is off and `ditherevery` is 0. Natural order is Ha then OIII.

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| U1 | ACP, `tests/frontend` | Open a new plan | No rows, the note, and "+ Add filter". The panel is 380 px wide. |
| U2 | ACP, `tests/frontend` | On U1, press "+ Add filter" twice, with templates R (180 s) and R Stars (15 s) in the list and L unused | Row 1 is L with L's usual template. Row 2 is R with template R. Each has goal 0 and shows "set a goal". |
| U3 | ACP, `tests/frontend` | On U2, set row 2's template to "R Stars" | Row 2's unit becomes "fr", and its option label reads "R Stars · 15 s". |
| U4 | ACP, `tests/frontend` | A row with template R at 180 s and goal 2.0 h. Switch to "fr", then back to "h". | The goal reads 40 fr, then 2.0 h. |
| U5 | ACP, `tests/frontend` | Remove row 1 of three | Two rows remain, numbered 1 and 2. Saving sends two rows. |
| U6 | ACP, `tests/frontend` | Drag row 3 over row 1 | While dragging, one drop line with two end markers shows above row 1. On drop the order is 3, 1, 2. |
| U7 | ACP, `tests/frontend` | Focus row 2's handle and press up | Row 2 becomes row 1. Focus stays on the moved handle. |
| U8 | ACP, `tests/frontend` | Turn on "Smart order" | Handles are disabled. Dragging and arrow keys change nothing. The explanation line reads the smart text. |
| U9 | ACP, `tests/frontend` | Panel at 380 px and at 300 px, a row with template "B Stars (ZWO ASI6200MM Pro)" | The card's four columns stay aligned. The template name truncates inside its column. The panel does not scroll sideways. |
| U10 | ACP, `tests/frontend` | R goal 4.2 h, actual 3.0 h. Then a frame row with goal 20 fr and 18 frames done. | "3.0 / 4.2 h" and "1.2 h left". Then "18 / 20 fr" and "2 fr left". |
| U11 | ACP, `tests/frontend` | Row with goal 4.2 h, actual 4.2 h, `aac.accepted_seconds` 10800, `aac.complete` false. Then `aac.complete` true. | First: "3.0 / 4.2 h" and the card is not dimmed. Second: "done" and the card is dimmed. |
| U12 | ACP, `tests/frontend` | A plan whose camera has no TS template list. Any page with an empty value. | The template drop-down offers the current template and "New template…". No rendered cell in the app is exactly ",". |
| V1 | ACP | Save a plan with two rows R / "R Stars" | 400, naming the duplicate. The same filter with two different templates saves. |
| V2 | ACP | Save rows with unit "fr" and no `target_frames`, with `target_frames` 2.5, and with an empty `template` | Each returns 400. |
| T1 | ACP | `POST /api/ts-templates` for profile P, then `GET` with no TS database file | `source` is "plugin", the list is P's, and `received_at` is set. A second post for P replaces the list. A post with no token is refused as `/progress` refuses it. |
| T2 | C# | Sync for tonight against a test database with three templates | One post to `/api/ts-templates` with the three templates. |
| M1 | ACP | `plans.json` with a plan whose `filter_goals` has L (120 s, 2 h), R (180 s, 3 h), "R Stars" (15 s, 0.0833 h) and Ha (300 s, 4 h), and a `ts_links` key `"77_R Stars"` | After load: four rows in the order L, R, R Stars, Ha. "R Stars" has filter R, template "R Stars", `template_sub_s` 15, unit "fr" and `target_frames` 20. Each row has a distinct `id`. The `ts_links` key is now `"77_<id of R Stars>"`. A backup exists. A second load changes nothing. |
| M2 | Python | M1's plan, pushed to a database the old writer filled. The "R Stars" plan has `exposure` 15 and points at a template with `filtername` "R Stars". TS also has a template "R Stars" with filter R. | No exposure plan is inserted. The R Stars plan carries the new guid and points at the template with filter R. Its `acquired`, `accepted` and `exposure` are unchanged. Every `desired` is unchanged. |
| M3 | Python | An exposure plan with `exposure` 300 whose template's default is 180 | The push leaves `exposure` at 300. The row's `template_sub_s` becomes 300. The push result names the mismatch. |
| M4 | Python | M3, then the row's template changed in ACP to one with default 300 | The push writes `exposure` -1 and the new template id. |
| S1 | Python | A new row R with template "R Stars", `template_sub_s` 15, 50 frames, no such template in TS | One new template named "R Stars", `filtername` "R", `defaultexposure` 15. One new exposure plan with `desired` 50 and `exposure` -1, pointing at it. |
| S2 | Python | As S1, with a TS template "R Stars" already present with a NINA guid and default 10 | No template is inserted or updated. The new plan points at the existing template. |
| S3 | Python | Push S1, then import | The plan comes back with the same row id, filter R, template "R Stars", unit "fr", `target_frames` 50. |
| S4 | C# | The golden rows for S1, S2, M2 to M4 | The plugin's rows equal the Python rows. |
| S5 | Python | A database holding templates with `filtername` "R Stars", "G Stars" and "B Stars" | The push deletes no template. Its result lists those three by name. |
| S6 | C# | Two TS plans on R, templates R (120 s) and R Stars (15 s), 10 frames each | Progress posts two entries, R with template R at 0.3333 h, and R with template R Stars at 0.0417 h. ACP stores each on its own row. |
| S7 | ACP | A progress post with filter R and no template, on a plan with one R row, then on a plan with two | The first updates the R row. The second returns R in `unknown_filters`. |
| S8 | ACP | `GET /api/plans` for M1's plan | `filter_goals` holds L, R and Ha, built from the first row of each filter, and no "R Stars". |
| R1 | Python | Plans Ha, OIII, SII inserted in that order, so natural order is Ha, OIII, SII. ACP rows SII, Ha, OIII, all pushed. Remove the Ha row. Push. | The Ha plan has `enabled` 0 and keeps its `acquired` and `accepted`. No plan is deleted. The override rows become SII x4 with `referenceIdx` 1, then OIII x4 with `referenceIdx` 0. The cadence rows are gone. The oracle shoots SII x4 then OIII x4. The next import has no Ha row. |
| R2 | Python | R1, then TS rows set by hand to SII, Ha, OIII before the removal | After the push the TS rows are SII, OIII, remapped to the new indexes, and the push did not regenerate them. |
| R3 | Python | After R1, add a row with filter Ha and template Ha again. Push. | No plan is inserted. The old Ha plan has `enabled` 1, its counts unchanged, and `ts_links` points the new row id at it. The override rows are rebuilt with Ha in its new list position. |
| R3b | Python, C# | ACP rows Ha, OIII in natural order, so no override rows exist. Remove Ha and push. Add Ha back at the end and push. | The Ha plan is enabled again with its lower `Id`. The override rows are written as OIII x4 then Ha x4, so TS shoots ACP's order. Golden scenario `x6_readded_row_with_no_rows_writes_acps_order`. |
| R4 | C# | R1 and R3 as golden scenarios | The plugin's rows equal the Python rows. |
| O1 | Python | ACP order OIII, Ha. Push. Then import. | TS has 8 override rows: OIII x4, then Ha x4. Cadence rows: none. The oracle shoots OIII, OIII, OIII, OIII, Ha, Ha, Ha, Ha. Import returns the rows in the order OIII, Ha, with the same ids, and no changes. |
| O2 | Python | After O1, edit TS's override rows to Ha, OIII (one each). Push with no ACP change. | The override rows stay Ha, OIII. The base order stays OIII, Ha. The next import shows the order changed in TS to Ha, OIII. |
| O3 | Python | After O2, reorder ACP's rows to Ha, OIII and push | ACP wins. Ha, OIII is the natural order, so no override rows are written. The existing TS rows are deleted, and the push result says it replaced an order set in TS. |
| O3b | Python | A target the push inserts, ACP order OIII, Ha, smart order off | The generated rows are written, OIII x4 then Ha x4. With smart order on, none are. |
| O3c | Python | A 1 by 2 mosaic. A hand order on panel 2 only. Push, import, push, with no ACP change. | Panel 2 keeps its rows through both pushes and panel 1 stays in natural order. After the import, every panel's base order is the anchor panel's. |
| O3d | Python | The same mosaic with the hand order on panel 1, imported into ACP, then pushed | Panel 1 keeps its rows. Panel 2 is not given panel 1's order. |
| O3e | Python | The same mosaic, ACP reordered, pushed | Both panels get the same generated rows. |
| O3f | Python | Hand order in TS, import with Keep ACP chosen for the plan only | TS's order still comes into ACP, and the next push leaves TS's rows. With Keep ACP chosen on the order field, ACP's order stays and the next push replaces TS's rows. The preview carries the sentence above on the order row. |
| O3g | Python | Hand order OIII, Ha in TS. Add SII in ACP and drag it to the top, then push. Separately, add SII at the end and push. | The first writes ACP's order, SII, OIII, Ha. The second leaves the hand order and appends SII's block. |
| O3i | Python | A goal-0 row in the middle, a hand order in TS, import, then raise that goal and push | The hand order stays and no replace note is made. With a base that has no `known_rows`, the old rule applies and ACP's order is written. |
| O3j | Python | A conflict on a goal plus a hand order change in TS, Keep ACP on the plan | TS's order still comes into ACP and the next push leaves TS's rows. With Keep ACP on the order as well, ACP's order stands. |
| O3k | Python | Mosaic: stale sync rows on panel 2, a hand order on panel 1 pulled into ACP, push | Panel 2 is refreshed to ACP's order. Panel 1 keeps its hand rows. A panel 2 with no rows is not given panel 1's hand order. |
| O3l | Python | A hand order OIII, Ha. Add SII mid-list in ACP, import before pushing, then push. | The import leaves SII unknown to the base (`known_rows` is the old base's plus TS's plans). The push treats SII as a reorder and writes ACP's order. |
| O3m | Python | O3i's setup with the goal raised and pushed, then import | The import adds a warn note naming the target and both orders. An import that leaves ACP in TS's order adds none. |
| O3h | C# | The golden scenarios x1 to x3b | The plugin's rows equal the Python rows. |
| O4 | Python | Switch frequency 0 against 3. ACP order OIII, Ha. | At 0: the oracle shoots OIII x4 then Ha x4. At 3: override rows are OIII x3, Ha x3. The oracle shoots OIII, OIII, OIII, Ha, Ha, Ha, OIII, Ha. |
| O4b | Python | Grader off, throttle 125, switch frequency 0, ACP order OIII, Ha | Blocks are 5 rows each. The oracle shoots OIII x5, then Ha x5, which is past `desired`, as TS does. |
| O4c | Python | After O1, the oracle shoots OIII x4 and one Ha. AAC then lowers OIII's `accepted` to 3. Push. | The cadence rows are gone. The oracle's next frame is OIII, then Ha. |
| O4d | Python | After O1, raise OIII's goal to 6 frames. Push with no order change. | Case 4 applies: the rows become OIII x6, Ha x4. The same happens when AAC's floor raises `desired`. |
| O5 | Python | ACP order equals natural order, Ha then OIII, at 0 and at 3 | No override rows. At 0 the oracle shoots Ha x4 then OIII x4. At 3 it shoots Ha x3, OIII x3, Ha, OIII. |
| O6 | Python | Smart order turned on in ACP, ACP order OIII, Ha | The project row has `smartexposureorder` 1. No override rows are written, and the cadence rows are deleted. |
| O7 | Python | `ditherevery` 2, order OIII, Ha, switch 0 | Override rows: OIII, OIII, D, OIII, OIII, Ha, Ha, D, Ha, Ha. |
| O8 | Python | A target with hand-made override rows for Ha and OIII. ACP adds a row for SII. Push. | The SII plan is inserted. Its 4-row block is appended to the override rows, and the cadence rows are deleted. |
| O9 | C# | Test O1's database, loaded through TS's assembly | `ExposureSelectionExpert` returns an override selector, and eight selections return OIII x4 then Ha x4. This test runs only where the TS plugin assembly is installed. If referencing it from `ACP.NINA.Plugin.Tests` proves impractical, the manual check in section 8 replaces it. |
| O10 | Python | Cadence rows left from an older order, then an ACP order change | The cadence rows are gone after the push. |
| O11 | Python | M1's plan with a base that has no `exposure_order`, and TS in natural order with OIII first | The push writes no override rows. The next import shows a change made in TS to TS's order. |
| F1 | Python | O1's database, base switch frequency 0, ACP 3, TS 0 | The push writes 3 and deletes the cadence rows of every target in the project. The override rows change from OIII x4, Ha x4 to OIII x3, Ha x3. |
| F2 | Python | Base 0, ACP 0, TS 3 | The push leaves 3. The base stays 0. |
| F3 | C# | F1 and F2 as golden scenarios | The plugin's rows equal the Python rows. |
| A1 | ACP | Save `target_class` "deep", then "giant" | "deep" saves. "giant" returns 400. A plan with none reads as survey in `GET /api/plans`. |
| A2 | ACP | `POST /api/plans/M42/accepted` with the Ha row's id, filter "Antlia Ha", template "Ha", 22320 s, complete false. Then 21240 s. A row with the R Stars row's id, filter R, template "R Stars". A row whose id exists but now has template "G Stars". The request has `filed_through_night` 2026-10-11 and `min_total_frames` 10. Also one request with no token. | The Ha values are stored on the Ha row and the second lowers them. The R Stars values are stored on its row. The changed row comes back in `unknown_rows` with its reason. `aac.filed_through_night`, `aac.min_total_frames` and `aac.received_at` are stored. `actual_hours` is unchanged. The request with no token is refused as `/progress` refuses it. |
| A3 | Python | Every case in `tests/fixtures/aac_accepted_cases.json`, which includes a frame row with `target_frames` 8 and floor 10 | Each case writes exactly the expected `accepted`, `desired` and `enablegrader`. The frame row gets `desired` 10. `acquired` never changes. |
| A4 | Python | Two exposure plans in one project, one with an `aac` block and one without. Then both with blocks, but `filed_through_night` two nights old. | Neither push writes `accepted` or `enablegrader` in that project. |
| A5 | Python | A mosaic plan with fresh figures. Then an eligible project on a profile with `delayGrading` 80. | Neither push makes an AAC write. The second logs a warning naming the profile setting. |
| A6 | C# | The same case file, and A4 and A5 | The plugin's rows equal the expected rows in every case. |

The full Python suite still passes, 223 tests on `main` before this change.

## 8. Phases

Each phase ends with its tests passing in every repo it touches.

| Phase | Content | Repos |
|---|---|---|
| 1 | `EMPTY_CELL` and the 17 placeholders that outlive the old goal table. Test U12's comma check. | ACP |
| 2 | Template list from the plugin. Tests T1 and T2. | ACP, ACP.NINA.Plugin |
| 3 | `filter_rows`: model, validation, the migration, the card list without dragging, both writers, the exposure rule, template lookup, plan identity, the importer, progress, the `filter_goals` view for older plugins. Tests U1 to U5, U9 to U12, V1, V2, M1 to M4, S1 to S8, R1, R3, R4. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 4 | Switch frequency and smart order: the switch, settings route, column rules, base fields, cadence deletion. Tests F1 to F3. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 5 | Filter order: dragging, the drop line, arrow keys, the oracle, the override writer, the nightly cadence reset, the importer's order. Tests U6 to U8, O1 to O11, R2. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 6 | AAC dependencies. Tests A1 to A6. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |

Phase 3 ships the migration and the importer change together, so no push can run between them. Within phase 3 the plugin goes onto Voyager before ACP core merges, as in `ts-project-settings.md` section 8. ACP deploys on merge, and the migration is the step that is hard to undo.

### Rolling back the plugin

Once the phase 3 plugin has pushed, the way back is to restore the `schedulerdb.sqlite` backup that sync wrote beside the database, with NINA closed. Never reinstall the older plugin over a database the new one has written to. The new push restamps every exposure plan with the filter row guid, and an older plugin looks plans up by the goal key guid, finds none, and inserts a second exposure plan for every row, starting from zero frames. The same holds for ACP: a rolled-back ACP core with a migrated `plans.json` needs the `plans.json.bak-*` copy the migration took.

Phase 4 comes before phase 5 because the order writer needs the switch frequency. Phase 2 comes before phase 3 so ACP has template sub lengths when the list replaces the sub field.

Phases 3 to 6 regenerate `golden-rows.json` with `tests/dump_golden_rows.py` and copy it into the plugin in the same session.

### Manual check on the imaging PC

I cannot run TS here, so the order needs one check on Voyager after phase 5 ships. Back up `schedulerdb.sqlite` first, with NINA closed.

- [ ] Set Helix's rows in ACP to OIII, Ha, SII with "Finish each", and run Sync for tonight.
- [ ] In TS's Database Manager, open Helix. The exposure order line shows OIII repeated, then Ha, then SII (`TargetViewVM.cs:606-622`).
- [ ] Run TS's plan preview for tonight. Its first Helix exposures are OIII. Preview never writes the cadence (`BaseExposureSelector.cs:54-56`), so it is safe to run.

## Assumptions made

1. The order goes into TS's override rows, expanded into blocks, rather than into exposure plan `Id` order. Changing `Id` would break `acquiredimage.exposureId` and the pinned ids in `ts_links`.
2. TS's planner loads a target's exposure plans in `Id` order. Its loader at `SchedulerDatabaseContext.cs:306-320` does not sort them, and TS's own editor assumes the same order.
3. With the switch frequency at 0, a block per plan sized by TS's completion rule stands in for TS's finish-each behaviour. A plan with a large frame goal makes a long override list, for example 500 rows for 500 star frames.
4. Dither rows use the project's `ditherevery`. A template's own `ditherevery` (column on `exposuretemplate`, -1 by default) is ignored inside an override order, as it is in TS when a user builds one by hand.
5. The push writes `exposure` -1 only on insert and on a template or filter change. Existing exposure plans keep the explicit sub the old writer gave them, so TS shoots what it shoots today.
6. `template_sub_s` holds the sub TS actually uses for the row, which is the template default only when the plan has no sub of its own. The name follows the agreed field list.
7. ACP stops updating existing TS templates from its gear settings. Gear filter settings are used only when a writer inserts a new template.
8. `GET /api/plans` keeps a derived `filter_goals` for older plugins, built from the first row of each filter. Star rows reach TS only once the new plugin is installed.
9. Switch frequency and smart order are per project, so they are saved for every plan in a project at once. When plans disagree, the first plan in `plans.json` wins.
10. The placeholders become an en dash rather than the original em dash, so a future punctuation tidy does not catch them.
11. Section 6 follows the AAC spec at commit `e41528c`: `filter_rows` with row ids, rows addressed by `id` in `/accepted`, and bearer token authentication. That spec defines `template_sub_s` as the template default copied when the row was made. This spec refreshes it to the sub TS actually uses (assumption 6), which is the same value for every row created after the migration.
12. "Rotate every N" keeps N between 1 and 99.

## Open questions

1. Rohan's three hand-made override orders (G7.7-3.7, Dolphin Nebula, Helix Nebula) make TS rotate one frame per filter, although their switch frequency is 0. Was that what he wanted when he set them on 11 October? If not, he can set each order once in ACP after phase 5, and the sync replaces them. Rohan has been asked and has not answered yet.
2. Does `ACP.NINA.Plugin.Tests` reference TS's assembly in a way that lets test O9 run on Windows? If not, the manual check is the only check against TS itself.
