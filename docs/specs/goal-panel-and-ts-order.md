# Spec: the goal panel, secondary sub rows, and filter order in Target Scheduler

This spec changes how a plan's exposure goals are edited in Astro Coverage Planner (ACP) and how they reach NINA's Target Scheduler (TS). It covers five changes that touch the same table, so they are built as one piece of work in six phases.

- A wider side panel and a rebuilt goal table, with progress bars and the mangled placeholders fixed.
- Secondary sub rows, such as "R Stars" with 15 s subs beside "R" with 120 s subs.
- A filter order set in ACP and written to TS.
- The filter switch frequency and smart exposure order, editable in ACP.
- The ACP side of the Astro Auto Calibration (AAC) incremental masters spec: `target_class`, AAC's figures per row and the accepted write to TS.

The design was agreed with Rohan on 2026-10-10 and 2026-10-12 (`notes/research/secondary-subs-and-goal-panel.md`). One part changes in this spec: how the order reaches TS. Section 3 explains why, from TS's source.

## Terms

- **Primary row**: `filter_goals.<f>`, keyed by a canonical filter name ("R", "Ha"). A filter has at most one.
- **Secondary row**: `filter_goals.<f>.sub_rows.<template>`, keyed by a TS exposure template name ("R Stars"). Its filter is the parent key.
- **Goal row**: a primary or a secondary row.
- **Goal key**: the name that identifies a goal row on a plan. It is the filter for a primary row and the template name for a secondary row. Template names are unique on a plan and never equal a canonical filter name, so one list of goal keys can hold both kinds.
- **Exposure plan**: a row in the TS table called `exposureplan`. Each goal row with a goal above zero becomes one exposure plan per target.
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

Three targets have override orders in the scratch copy of Voyager's database from 12 October 18:46. They are G7.7-3.7, Dolphin Nebula and Helix Nebula. They appeared between the 15:01 backups of 11 and 12 October, so they were set in TS on 11 October. Their projects have a switch frequency of 0. TS therefore rotates one frame per filter on those three targets, rather than finishing each filter. Helix's order is OIII, Ha, SII, B, G, R. B, G and R are complete, so Helix now shoots one frame each of OIII, Ha and SII in turn.

### What the sync writes so TS shoots in ACP's order

There is no column for plan order in TS. Natural order follows `Id`, and changing `Id` would break `acquiredimage.exposureId` and every pinned id in ACP's `ts_links`. So the order goes into the override rows. The sync writes them so they reproduce the switch frequency and the dithering TS would otherwise do itself.

For each target, the sync builds the override rows from ACP's order:

1. Walk the goal keys in ACP's order. Skip keys with no enabled exposure plan on this target.
2. For each key, add a block of exposure rows pointing at that plan's `referenceIdx`. The block holds N rows when the switch frequency is N. When the switch frequency is 0, it holds the frames TS needs to call the plan done (table below).
3. When the project's `ditherevery` is above 0, insert dither rows by TS's own rule in `Planning/DitherInjector.cs:76-116`. A dither goes before the item where any filter is seen `ditherevery + 1` times since the last dither, with the list treated as a circle.
4. Number the rows 1, 2, 3 in the `order` column.

At switch frequency 0 the block size follows TS's completion rule in `Planning/Exposures/ExposureCompletionHelper.cs`:

| Project grader | TS calls the plan done when | Block size |
|---|---|---|
| on | `Accepted` reaches `Desired` (lines 20-27) | `desired` |
| off | `Acquired` reaches `(int)(throttle x Desired)` (lines 29-32) | ceil(throttle / 100 x `desired`) |

The throttle is `profilepreference.exposureThrottle` for the profile, a percentage. The writer reads the real value at push time. TS's default is 125 (`Database/Schema/ProfilePreference.cs:67`), and Rohan set his to 100 on 12 October.

A block sized this way works because TS skips finished plans (`OverrideOrderExposureSelector.cs:33`, and `PlanningTarget.cs:65-71` marks them rejected when the target loads). The filter finishes inside its block and TS moves straight on to the next block.

### Restarting the walk every night

TS's place in the override rows only moves forward (`Planning/FilterCadence.cs:36-51`). Suppose TS passes the end of Ha's block, and next morning AAC lowers Ha's `accepted`. Without help, Ha's shortfall waits until every other filter in the circle is done.

So for every target that has override rows, the sync deletes the cadence rows on every push, whether or not it writes the order. TS then rebuilds the cadence from the override rows with row 1 marked next (`FilterCadenceFactory.cs:31-41`). Each night's walk starts at row 1, skips finished plans, and lands on the first unfinished plan in ACP's order.

At switch frequency N this costs at most N - 1 extra frames per night. The first unfinished plan gets a full block of N again, even if it shot part of a block the night before.

Two exceptions keep the footprint small:

- When ACP's order equals the natural order, the sync writes no override rows. TS then uses its own selector for the switch frequency, with the same result and with per-template dither settings intact.
- When smart order is on, the sync does not write override rows. TS ignores them.

The sync also deletes a target's cadence rows whenever it deletes its override rows, and for every target in a project whose switch frequency or smart order it writes.

## 1. The side panel and the goal table

### Width

The default side panel width changes from 380 px to 560 px.

- `static/style.css`: `--side-panel-w: 560px` on `.panel`.
- `static/app.js`: `sidePanelState.width` defaults to 560.
- A stored width of exactly 380 in `localStorage` key `acp.sidePanel.v1` is read as 560, once, then saved. A width the user dragged to any other value is kept.

`clampSidePanelWidth` (`static/app.js:5749-5754`) still leaves 120 px of sky. So in a window too narrow for 560 px, the panel takes what room there is, down to 300 px. Below 641 px the phone layout applies and the panel becomes a sheet (`static/style.css:355-388`).

### The goal table

The table is built from the plan's goal rows, not from `FILTER_DOT_ORDER` (`static/app.js:6992-7008`). Each primary row has these columns, left to right:

| Column | Content |
|---|---|
| Move | A drag handle, plus up and down buttons |
| Position | 1, 2, 3 in the current order |
| Filter | The filter chip, coloured from `FILTER_COLORS` (`static/app.js:16`) |
| Goal | A number with its unit, "h" or "fr" |
| Sub | Sub length in seconds |
| Progress | A bar, "3.0 / 4.2 h" and "1.2 h left" |
| TS template | A select, or a placeholder when no list is available |

Secondary rows sit indented under their filter's primary row. They have their own position and move on their own. Under each primary row a link reads "+ sub exposure for R". Filters the camera has but the plan has no goal for sit below a divider labelled "Not in this plan". They are greyed and have no position. Typing a goal into one moves it above the divider, at the bottom of the order.

Above the table:

- A two-way switch: "Finish each filter" and "Rotate every N frames", with a number input for N (section 4).
- A checkbox, "Smart order". When it is on, the move controls are disabled (section 4).
- A one-line explanation of what TS will do. Examples are in section 4.
- A "Reset order" link (section 3).

The whole row is not draggable, because it is full of inputs. The up and down buttons give keyboard and touch users the same moves.

### Progress

The bar shows done against goal for the row's unit. Done comes from AAC's figures when the row has them (section 6), otherwise from `actual_hours`. Left is goal minus done, floored at 0. A finished row stays in its place and is dimmed. The bare "Done" badge goes.

For a frame row, done in frames is `round(done_hours x 3600 / sub_exposure_s)`.

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

The fix adds one constant near the top of `static/app.js`, `const EMPTY_CELL = "–";` (an en dash, U+2013), with a comment saying it is data, not prose. All 18 places use it. The changes in `app.py` and the two docs are prose and stay as they are.

## 2. The TS template list from the plugin

`/api/ts-templates` (`app.py:4370-4408`) reads the TS database at `TS_DB_PATH`. On Brutix that file does not exist, so the template column is blank.

The plugin sends the list instead.

- During Sync for tonight, and on "Sync All to TS", the plugin reads every `exposuretemplate` row for its profile. It posts them to a new route, `POST /api/ts-templates`, with the same authentication as `POST /api/plans/<id>/progress`.
- The body is `{"profile_id": "...", "sent_at": "<ISO time>", "templates": [{"name", "filter", "default_exposure_s", "gain", "offset", "bin"}]}`.
- ACP stores the latest list per profile in `data/ts_templates.json`. A new post for a profile replaces that profile's list.
- `GET /api/ts-templates` returns the database list with `"source": "database"` when the file is readable, as today. Otherwise it returns the stored lists with `"source": "plugin"` and each profile's `received_at`. With neither, it returns `available: false` and the column hides itself, as today.

The editor offers the templates whose filter matches the row's filter. A secondary row's template select also offers "New template…", which takes a name.

## 3. Secondary sub rows

### Behaviour

"+ sub exposure for R" adds a secondary row under R. The row asks for a TS template name, chosen from the list or typed. The name becomes the goal key. It must be unique on the plan and must not be a canonical filter name.

A secondary row can exist without a primary goal. In that case `filter_goals.R` holds only `sub_rows`, its own `target_hours` is 0, and it may have no `sub_exposure_s`. The writers push no exposure plan for it, as for any row with no goal. `GET /api/plans` adds `"primary_goal": false` to such a row in its response, without storing it. AAC treats that row as having no primary goal, the same as an absent one.

A row's unit follows its sub length. Under 60 s the goal is in frames, and from 60 s up it is in hours. Clicking the unit flips that one row and stores `goal_unit`. Changing the sub length does not flip a row that was flipped by hand.

### Data model

The shape follows the storage contract in the AAC spec, section 7, "The ACP storage contract". A secondary row sits under its filter:

```json
"filter_goals": {
  "R": {
    "target_hours": 4.0, "sub_exposure_s": 120, "actual_hours": 1.2,
    "sub_rows": {
      "R Stars": {"target_hours": 0.2083, "target_frames": 50,
                  "sub_exposure_s": 15, "actual_hours": 0.05}
    }
  }
}
```

Nesting keeps the template name out of `canon_filter` (`app.py:4203-4209`), which would fold a top-level "R Stars" into R. It also keeps every existing reader of `filter_goals` working, because each top-level key is still a filter.

Every goal row, primary or secondary, may carry these new fields:

| Field | Type | Meaning |
|---|---|---|
| `target_frames` | integer | The goal in frames. Present only when the row's unit is frames. |
| `goal_unit` | `"hours"` or `"frames"` | Present only when flipped by hand. |
| `aac` | object | AAC's figures, written only by AAC (section 6). |

`target_hours` stays present on every row, because AAC reads it. For a frame row it is `target_frames x sub_exposure_s / 3600`, rounded to 4 places. Both writers take `desired` from `target_frames` when it is present. Otherwise they use `max(1, ceil(target_hours x 3600 / sub))` as today.

`_validate_plan_payload` in `app.py` rejects:

- a `sub_rows` entry under a key that is not a canonical filter name
- a template name equal to a canonical filter name
- the same template name twice on one plan
- a `target_frames` that is not a whole number of at least 1

### What the writers do

Today `convert.py` uses the goal key as the template's filter name (`nina_ts_sync/convert.py:299-324`). An imported "B Stars" key therefore created a TS template with `filtername` "B Stars". The scratch database copy has three of them, ids 15, 17 and 20. They are "B Stars (ZWO ASI6200MM Pro)" and its G and R counterparts. NINA has no filter called "B Stars", so those plans cannot shoot.

The new rules, in both writers:

- A primary row keeps today's template and plan identities, so no existing row moves.
- A secondary row first looks for a TS template with that profile and `name` equal to the template name. If one exists, the plan points at it and the writer does not update the template. TS owns templates it already has.
- If none exists, the writer inserts one. Its name is the template name, its `filtername` is the parent filter, and its `defaultexposure` is the row's sub. Its guid uses a new recipe, `"{profile}/template/{filter}/{camera}/{template}"`.
- A secondary row's exposure plan guid uses a new recipe, `"{profile}/plan/{targetGuid}/{filter}/{template}"`. Each free-text part in both recipes is length-prefixed as in `schema.py`.
- A secondary row's exposure plan is linked in `ts_links` like any other.
- Before inserting, a secondary row looks for its exposure plan under the flat recipe older pushes used, `exposure_plan_guid(profile, target_guid, template)`. A hit is restamped with the new guid and repointed at the right template, as `migrate.py` does for the length-prefix change. This stops the migration in section 5 from creating a second exposure plan for every star row already in TS.

The sync never deletes templates. Instead, the push result lists every template on the profile whose `filtername` is not a filter on any of the profile's cameras, such as "R Stars", "G Stars" and "B Stars". Rohan deletes them in TS.

`TsConvert.cs` and `TsGuid.cs` mirror these rules. `TsIdentityRecipeTests` gains both recipes.

### What the importer does

`_keyed_plans` (`nina_ts_sync/from_ts.py:346-404`) keys every plan by template name when two plans share a canonical filter. It changes so that one of them becomes the primary and the rest become secondary rows:

1. The plan whose template name canonicalises to the filter itself is the primary.
2. If none or several do, the plan with the longest sub is the primary, then the smallest `Id`.
3. Every other plan on that filter becomes `filter_goals.<f>.sub_rows.<template name>`.

On Rohan's Sculptor, "R" (template "R") stays the primary R row, and "R Stars" becomes R's secondary row.

The three-way diff (`diff.py`) compares each secondary row as its own entry, with the same fields and noise rules as a primary row.

### Progress from the plugin

`ProgressMath.BuildFilters` (`ACP.NINA.Plugin/Services/ProgressMath.cs:57-75`) sums rows by template filter name. R and R Stars therefore both land on R, and R's `actual_hours` includes the star subs. It changes to send each secondary row separately, as `{"filter": "R", "template": "R Stars"}`, using the same rule as the importer. `api_plan_progress` (`app.py:4169-4237`) writes such an entry to the matching `sub_rows` entry, matching the template name exactly.

## 4. Switch frequency and smart order

### Behaviour

The switch above the goal table sets the project's switch frequency:

- "Finish each filter" stores 0.
- "Rotate every N frames" stores N, from 1 to 99.

The "Smart order" checkbox stores the project's smart exposure order. When it is on, the move controls are disabled and the order is kept, not cleared.

The explanation line reads, for example:

- "TS shoots L until it is done, then R, then G."
- "TS shoots 3 frames of L, 3 of R, 3 of G, then starts again at L."
- "TS chooses the order itself. Your order is kept for when Smart order is off."

Both settings belong to the TS project. Like state and priority, they are saved through `PUT /api/projects/<name>/settings` (`app.py:3623`) and apply to every plan in the project. The editor says so with the existing one-line note.

### Data model

| Plan field | Type | Absent means |
|---|---|---|
| `filter_switch_frequency` | integer, 0 to 99 | unknown. The writers never write it on update, and the editor shows 0. |
| `smart_exposure_order` | boolean | unknown. The writers never write it on update, and the editor shows off. |

When plans in one project disagree, the writers take the value from the first plan in `plans.json` order and add a line to the push result.

### What the writers do

`filterswitchfrequency` and `smartexposureorder` leave `INSERT_ONLY_COLUMNS` (`nina_ts_sync/schema.py:312-319`) and the project entry in `insertOnlyColumns` (`TsSchema.cs:90-99`). They join `state`, `priority` and `minimumtime` in the rule from `docs/specs/ts-project-settings.md` section 5. A writer includes each column in an update only when the plan's value differs from the base.

- Python: `convert.py` adds them to `project_update_skip_cols_by_guid` when unchanged.
- C#: `TsConvert` adds them to `ConditionalColumnsToWrite` (`TsConvert.cs:413-445`) when changed.
- On insert both take ACP's value, or 0 and off when the plan has none.

When a writer writes either column, it deletes the cadence rows of every target in that project in the same transaction. It then rebuilds the override rows for those targets, by section 5's rules.

The base project block (`state.py` `_project_snapshot`, `TsState.cs`) gains `filterswitchfrequency`, `smartexposureorder` and `ditherevery`. The importer maps the first two to the plan fields.

## 5. Filter order

### Behaviour

The order is per plan. Every panel of a mosaic gets the same order.

- Dragging a row, or pressing up or down, moves it. Secondary rows move independently of their primary.
- Positions renumber at once.
- "Reset order" sets the default order (below).
- The order saves with the plan.

### Data model

| Plan field | Type | Meaning |
|---|---|---|
| `goal_order` | list of goal keys | Shooting order. Keys with no goal, or not in `filter_goals`, are ignored. A goal key missing from the list goes after the listed ones, in default order. |

The default order is:

1. Primary rows in the order L, R, G, B, Ha, OIII, SII.
2. Any other primary rows, by key.
3. Secondary rows, in the same filter order, then by key.

So star rows go last by default.

### What the writers do

At each push, for each target of the plan, the writer computes:

| Name | Value |
|---|---|
| ACP order | `goal_order` reduced to keys with an enabled exposure plan on this target |
| Base order | `exposure_order` for this panel in the base |
| Base rows | `override_rows` for this panel in the base, as `[action, referenceIdx]` pairs |
| TS rows | the target's override rows now, as the same pairs |
| Generated rows | the rows built from the ACP order by "What the sync writes" above, or none when the ACP order equals the natural order |

The decision:

| Case | Writer does |
|---|---|
| Smart order is on, after this push | nothing to the override rows, except the new plan rule below |
| No base, or a base with no `exposure_order` | nothing |
| ACP order differs from base order | writes the generated rows, so ACP wins |
| ACP order equals base order, and TS rows equal base rows | writes the generated rows if they differ from TS rows |
| ACP order equals base order, and TS rows differ from base rows | nothing, because the order was changed in TS |

The fourth case refreshes rows the sync wrote itself. A changed goal, AAC floor, throttle, grader setting or switch frequency changes the block sizes, so the generated rows change. The fifth case keeps an order set in TS.

"Writes" means: delete the target's override rows and insert the new ones, in the push transaction. Writing no rows deletes the override rows, which returns the target to natural order. Separately, every push deletes the cadence rows of every target that has override rows after it ("Restarting the walk every night" above).

New plan rule: a push may insert an exposure plan on a target whose override rows it did not replace. The writer then appends that plan's block to the end and deletes the cadence rows. Without this, TS never shoots the new plan (`PlanningTarget.cs:80-97`). The push result names the target.

After the push, the base records the target's effective order as `exposure_order`, and its override rows as `override_rows`. As with state, a push that left TS's rows alone keeps the old base order.

### What the importer does

The effective order of a target is read like this:

- With override rows: goal keys in the order of their first exposure row. Enabled plans named by no row go at the end, with a note. An example: "TS never shoots R Stars on Helix, because its override order leaves it out."
- Without override rows: natural order.

The diff (`diff.py` `_PLAN_FIELDS`) gains `goal_order`, `filter_switch_frequency` and `smart_exposure_order`. They follow the three-way rules of `ts-upload-import.md`. A base with no `exposure_order` reads like a missing `state` in `ts-project-settings.md` section 3. ACP could not set an order before this change, so any difference is a change made in TS.

An override order with repeats or dithers, made by hand in TS, comes into ACP as its first-occurrence order. Its rows stay in TS until ACP's order changes.

### Migration of existing plans

`load_plans` runs two one-shot migrations. Each takes a timestamped backup first, as `_maybe_migrate_draft_plans_to_active` does (`app.py:637-672`).

The first ships in phase 3, guarded by `sub_rows_migrated: 1` on the `plans.json` document. It must ship with the importer change, because a push between the two would write a second exposure plan for every star row. For each plan it:

- moves every top-level key that is not a canonical filter name, but whose `canon_filter` is one, into that filter's `sub_rows`. "R Stars" moves to `filter_goals.R.sub_rows."R Stars"`, and `filter_goals.R` is created with `target_hours` 0 if it was absent.
- logs keys whose filter it cannot work out, by plan id, and leaves them where they are

The second ships in phase 5, guarded by `goal_order_migrated: 1`. It sets `goal_order` to the default order over each plan's existing keys.

Neither migration sets `filter_switch_frequency` or `smart_exposure_order`. Those arrive from TS on the next "Send TS to ACP".

The migration does not count as an ACP change. A base without `exposure_order` means the next push writes no order. The next upload brings in each target's effective order from TS. Rohan's three hand-made override orders therefore stay in TS until he changes the order in ACP.

## 6. What AAC needs from ACP

AAC's incremental masters spec (`Astro-Auto-Calibration/docs/specs/incremental-master-builds.md`, section 7) defines a storage contract that this spec adopts. Its dependency list asks ACP for four things, specified here to that spec's needs. The secondary row shape it requires is in section 3.

### `target_class`

A plan gains `target_class`, one of `survey`, `deep` and `pretty`. Absent, or any other value, means `survey`. The plan editor shows it as a select in the Target fieldset. `_validate_plan_payload` rejects other strings. AAC reads it with the goals through `GET /api/plans`.

### `POST /api/plans/<plan_id>/accepted`

AAC is the only caller. The route uses the same token scheme as `POST /api/plans/<plan_id>/progress`. The body is the one in the AAC spec: `source`, `at`, `filed_through_night`, `min_total_frames`, and a list of `rows`. Each row is identified by `filter` and `template`, with `template` null for a primary row. Each carries these fields:

- `accepted_seconds`, `accepted_frames`
- `seen_seconds`, `seen_frames`
- `complete`
- `estimated_hours`, `measurement`

ACP stores them as follows:

| Path on the plan | Content |
|---|---|
| `<row>.aac` | the row's fields above, replacing the previous block |
| `aac.filed_through_night` | from the request |
| `aac.min_total_frames` | from the request |
| `aac.received_at` | the time ACP received it |

`<row>` is `filter_goals.<f>` for a primary row and `filter_goals.<f>.sub_rows.<template>` for a secondary row. ACP matches `filter` through `canon_filter` and `template` as an exact string. A row with no match comes back in `unknown_rows` and is not stored. Values may go down. `actual_hours` is never changed.

### Done, as the editor shows it

A row with an `aac` block shows done as `aac.accepted_seconds / 3600` and counts as finished when `aac.complete` is true. A row without one uses `actual_hours` against the goal, as today.

### The accepted write to TS

Both writers follow the AAC spec's section 7, "How the shortfall reaches TS", which is summarised here.

- Before any AAC write, the writer reads the profile's grading settings in `profilepreference`. If `delayGrading` is not 0, or any of `enableGradeRMS`, `enableGradeStars`, `enableGradeHFR`, `enableGradeFWHM` and `enableGradeEccentricity` is not 0, it makes no AAC write for that profile and logs a warning.
- A project is eligible when every exposure plan in it links to a row with an `aac` block, no target in it is a mosaic panel, and `aac.filed_through_night` is on or after the night that ended that morning.
- For an eligible project, one transaction writes `accepted` for every exposure plan and sets `project.enablegrader` to 1. Any other project gets neither write.
- `exposureplan.accepted` and `project.enablegrader` stay in `INSERT_ONLY_COLUMNS` and `insertOnlyColumns`. A per-project exception, built by `convert.py` and `TsConvert` like `project_update_skip_cols_by_guid`, lets them into the update for eligible projects.
- `acquired` is never written.

The values, for each AAC-linked row:

| Quantity | Formula |
|---|---|
| `desired` | max(ceil(`target_hours` x 3600 / `sub_exposure_s`), `aac.min_total_frames`) |
| AAC count | floor(`aac.accepted_seconds` / `sub_exposure_s` + 0.000001) |
| frames short of the floor | max(1, `aac.min_total_frames` - `aac.accepted_frames`) |
| `accepted`, row complete | max(AAC count, `desired`) |
| `accepted`, row not complete | max(0, min(AAC count, `desired` - frames short of the floor)) |

A frame row with `target_frames` uses `max(target_frames, aac.min_total_frames)` for `desired`.

Both suites run the shared case file `tests/fixtures/aac_accepted_cases.json` in acp-nina-ts-sync, as the AAC spec requires.

## 7. Acceptance tests

Each test names the repo it lives in. "Python" means `acp-nina-ts-sync/tests`, run with `~/Developer/Astro-Coverage-Planner/.venv/bin/python -m pytest tests -q`. "C#" means `ACP.NINA.Plugin.Tests`, which runs only on Windows. "ACP" means `Astro-Coverage-Planner/tests`.

### A TS order oracle

The Python tests need to show that TS would shoot in a given order. TS's planner cannot run on the Mac. So the Python suite gains `tests/ts_order_oracle.py`, a port of the four pieces that decide the next frame:

- `ExposureSelectionExpert.GetExposureSelector`
- `FilterCadenceFactory.Generate`
- `PlanningTarget.GetActiveExposurePlans`
- the `Select` and `ExposureTaken` methods of the override, repeat-until-done and basic selectors

Each function carries the file and line it ports. The oracle reads a target from a test database, then simulates frames. A frame adds one to `acquired` and to `accepted`, and marks a plan done by `ExposureCompletionHelper`'s rules. It writes cadence rows back as TS does. It returns the filter of each frame, and `"D"` for a dither.

The oracle is checked against TS's own tests. `tests/test_ts_order_oracle.py` ports the expected values from TS's `NINA.Plugin.TargetScheduler.Test/Planning/FilterCadenceFactoryTest.cs` and `Planning/Exposures/OverrideOrderExposureSelectorTest.cs`.

The oracle is a port, so it cannot prove TS itself agrees. Test O9 below is the check against TS itself.

### Scenarios

Unless a test says otherwise, the fixture is one plan "M42" on a fresh database at `user_version` 28. It has primary rows Ha and OIII, each with sub 300 s and a 4-frame goal. The switch frequency is 0, smart order is off and `ditherevery` is 0. Natural order is Ha then OIII.

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| P1 | ACP, `tests/frontend` | Window 1280 px wide, no stored width | Panel is 560 px wide. The goal table has the seven columns and no horizontal scroll. |
| P2 | ACP, `tests/frontend` | Window 641 px wide, the narrowest desktop layout | Panel width is the map area's width minus 120 px, so less than 560 px. The goal table scrolls sideways inside the panel, and the page does not. |
| P2b | ACP, `tests/frontend` | Window 640 px wide | The phone sheet layout applies, as today, and the table scrolls sideways inside the sheet. |
| P3 | ACP, `tests/frontend` | Stored width 380, then 450 | 380 loads as 560 and is saved as 560. 450 loads as 450. |
| P4 | ACP, `tests/frontend` | A plan whose camera has no TS template list | The template cell shows the en dash. No rendered cell in the app is exactly ",". |
| P5 | ACP, `tests/frontend` | R goal 4.2 h, actual 3.0 h | The bar reads "3.0 / 4.2 h" and "1.2 h left". |
| P6 | ACP, `tests/frontend` | R goal 4.2 h, actual 4.2 h, `aac.accepted_seconds` 10800, `aac.complete` false. Then `aac.complete` true. | First: the bar reads "3.0 / 4.2 h" and the row is not dimmed. Second: the row is dimmed as finished. |
| T1 | ACP | `POST /api/ts-templates` for profile P, then `GET` with no TS database file | `source` is `"plugin"`, the list is P's, and `received_at` is set. A second post for P replaces the list. |
| T2 | C# | Sync for tonight against a test database with three templates | One post to `/api/ts-templates` with the three templates. |
| S1 | Python | Plan adds `filter_goals.R.sub_rows."R Stars"`, sub 15 s, 50 frames, no such template in TS | One new template named "R Stars", `filtername` "R", `defaultexposure` 15. One new exposure plan with `desired` 50, pointing at it. The primary R plan is unchanged. |
| S2 | Python | As S1, with a TS template "R Stars" already present with a NINA guid | No template is inserted or updated. The new exposure plan points at the existing template. |
| S3 | Python | Push S1, then import | The plan comes back with a primary R row and `filter_goals.R.sub_rows."R Stars"`, with `target_frames` 50 and `sub_exposure_s` 15. |
| S4 | C# | The golden rows for S1 and S2 | The plugin's rows equal the Python rows. |
| S5 | ACP | Save a plan with a secondary row named "Ha" under R, then one with "R Stars" under both R and G | Both return 400. |
| S7 | Python | A database pushed before this change with a flat "R Stars" goal: an exposure plan under the flat guid, pointing at a template with `filtername` "R Stars". TS also has a template "R Stars" with filter R. Push the migrated plan. | No exposure plan is inserted. The existing one carries the new guid, points at the "R Stars" template with filter R, and keeps its `acquired` and `accepted`. |
| S8 | Python | A database holding templates with `filtername` "R Stars", "G Stars" and "B Stars" | The push deletes no template. Its result lists those three by name. |
| S6 | C# | Two TS rows, R 120 s and R Stars 15 s, 10 frames each | Progress posts R with 0.3333 h, and R with template "R Stars" with 0.0417 h. ACP stores the second under `sub_rows`. |
| O1 | Python | ACP order OIII, Ha. Push. Then import. | TS has 8 override rows: OIII x4, then Ha x4. Cadence rows: none. The oracle shoots OIII, OIII, OIII, OIII, Ha, Ha, Ha, Ha. Import returns `goal_order` OIII, Ha and no changes. |
| O2 | Python | After O1, edit TS's override rows to Ha, OIII (one each). Push with no ACP change. | The override rows stay Ha, OIII. The base order stays OIII, Ha. The next import shows `goal_order` changed in TS to Ha, OIII. |
| O3 | Python | After O2, change ACP's order to Ha, OIII and push | ACP wins. The rows become Ha x4, OIII x4, and the push result says it replaced an order set in TS. |
| O4b | Python | Grader off, throttle 125, switch frequency 0, ACP order OIII, Ha, desired 4 each | Blocks are 5 rows each. The oracle shoots OIII x5, then Ha x5, which is past `desired`, as TS does. |
| O4c | Python | After O1, the oracle shoots OIII x4 and one Ha. AAC then lowers OIII's `accepted` to 3. Push. | The cadence rows are gone. The oracle's next frame is OIII, then Ha. |
| O4d | Python | After O1, raise OIII's goal to 6 frames. Push with no order change. | Case 4 applies: the rows become OIII x6, Ha x4. The same happens when AAC's floor raises `desired`. |
| O4 | Python | Switch frequency 0 against 3. ACP order OIII, Ha. Desired 4 each. | At 0: the oracle shoots OIII x4 then Ha x4. At 3: override rows are OIII x3, Ha x3. The oracle shoots OIII, OIII, OIII, Ha, Ha, Ha, OIII, Ha. |
| O5 | Python | ACP order equals natural order, Ha then OIII, at 0 and at 3 | No override rows. At 0 the oracle shoots Ha x4 then OIII x4. At 3 it shoots Ha x3, OIII x3, Ha, OIII. |
| O6 | Python | Smart order turned on in ACP, ACP order OIII, Ha | The project row has `smartexposureorder` 1. No override rows are written, and the cadence rows are deleted. The editor shows the move controls disabled and the order unchanged. |
| O7 | Python | `ditherevery` 2, order OIII, Ha, switch 0 | Override rows: OIII, OIII, D, OIII, OIII, Ha, Ha, D, Ha, Ha. |
| O8 | Python | A target with hand-made override rows for Ha and OIII. ACP adds SII. Push. | The SII plan is inserted. Its 4-row block is appended to the override rows, and the cadence rows are deleted. |
| O9 | C# | Test O1's database, loaded through TS's assembly | `ExposureSelectionExpert` returns an override selector, and ten selections return OIII x4 then Ha x4. This test runs only where the TS plugin assembly is installed. If referencing it from `ACP.NINA.Plugin.Tests` proves impractical, the manual check in section 8 replaces it. |
| O10 | Python | Cadence rows left from an older order, then an ACP order change | The cadence rows are gone after the push. |
| M1 | ACP | `plans.json` with a plan keyed L, R, R Stars, Ha, and no `goal_order` | After the phase 3 migration: "R Stars" sits under `filter_goals.R.sub_rows`. After the phase 5 migration: `goal_order` is L, R, Ha, R Stars. Each takes a backup. A second load changes nothing. |
| M2 | Python | M1's plan, with a base that has no `exposure_order`, and TS in natural order OIII-first | The push writes no override rows. The next import shows a change made in TS to TS's order. |
| F1 | Python | O1's database, base switch frequency 0, ACP 3, TS 0 | The push writes 3 and deletes the cadence rows of every target in the project. The override rows change from OIII x4, Ha x4 to OIII x3, Ha x3. |
| F2 | Python | Base 0, ACP 0, TS 3 | The push leaves 3. The base stays 0. |
| F3 | C# | F1 and F2 as golden scenarios | The plugin's rows equal the Python rows. |
| A1 | ACP | Save `target_class` "deep", then "giant" | "deep" saves. "giant" returns 400. A plan with none reads as survey in `GET /api/plans`. |
| A2 | ACP | `POST /api/plans/M42/accepted` with Ha 22320 s, complete false, then Ha 21240 s. A row with filter "Antlia Ha" and template null. A row with filter R and template "R Stars". The request has `filed_through_night` 2026-10-11 and `min_total_frames` 10. Also one request with no token. | The Ha values are stored under `filter_goals.Ha.aac`, and the second lowers them. "Antlia Ha" lands on Ha through `canon_filter`. "R Stars" lands under `filter_goals.R.sub_rows."R Stars".aac`, or in `unknown_rows` on a plan without that row. `aac.filed_through_night`, `aac.min_total_frames` and `aac.received_at` are stored. `actual_hours` is unchanged. The request with no token gets the same refusal as `/progress`. |
| A3 | Python | Every case in `tests/fixtures/aac_accepted_cases.json`, which includes a frame row with `target_frames` 8 and floor 10 | Each case writes exactly the expected `accepted`, `desired` and `enablegrader`. The frame row gets `desired` 10. `acquired` never changes. |
| A4 | Python | Two exposure plans in one project, one with an `aac` block and one without. Then both with blocks, but `filed_through_night` two nights old. | Neither push writes `accepted` or `enablegrader` in that project. |
| A5 | Python | A mosaic plan with fresh figures. Then an eligible project on a profile with `delayGrading` 80. | Neither push makes an AAC write. The second logs a warning naming the profile setting. |
| A6 | C# | The same case file, and A4 and A5 | The plugin's rows equal the expected rows in every case. |

The full Python suite still passes, 223 tests on `main` before this change.

## 8. Phases

Each phase ends with its tests passing in every repo it touches. The order respects two dependencies. The order writer needs the switch frequency, so phase 4 comes before phase 5. The plugin must post templates before ACP relies on them, so phase 2 comes before phase 3.

| Phase | Content | Repos |
|---|---|---|
| 1 | Panel width, the rebuilt table without order controls, progress bars, `EMPTY_CELL` and the 18 placeholders. Tests P1 to P5. | ACP |
| 2 | Template list from the plugin. Tests T1 and T2. | ACP, ACP.NINA.Plugin |
| 3 | Secondary rows: model, validation, editor, both writers, the importer, progress keying, the lookup under the old guid, and the first migration. Tests S1 to S8. S7 must pass in this phase. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 4 | Switch frequency and smart order: settings route, column rules, base fields, cadence deletion. Tests F1 to F3. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 5 | Filter order: `goal_order`, the move controls, the second migration, the oracle, the override writer, the nightly cadence reset, the importer. Tests O1 to O10, M1 and M2. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |
| 6 | AAC dependencies. Tests A1 to A6 and P6. | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |

Phases 3 to 6 regenerate `golden-rows.json` with `tests/dump_golden_rows.py` and copy it into the plugin in the same session.

### Manual check on the imaging PC

I cannot run TS here, so the order needs one check on Voyager after phase 5 ships. Back up `schedulerdb.sqlite` first, with NINA closed.

- [ ] Set Helix's order in ACP to OIII, Ha, SII with "Finish each filter", and run Sync for tonight.
- [ ] In TS's Database Manager, open Helix. The exposure order line shows OIII repeated, then Ha, then SII (`TargetViewVM.cs:606-622`).
- [ ] Run TS's plan preview for tonight. Its first Helix exposures are OIII. Preview never writes the cadence (`BaseExposureSelector.cs:54-56`), so it is safe to run.

## Assumptions made

1. The order goes into TS's override rows, expanded into blocks, rather than into exposure plan `Id` order. Changing `Id` would break `acquiredimage.exposureId` and the pinned ids in `ts_links`. The agreed design named the override table but not the expansion, which section "What TS does with an order" shows is needed.
2. TS's planner loads a target's exposure plans in `Id` order. Its loader at `SchedulerDatabaseContext.cs:306-320` does not sort them, and TS's own editor assumes the same order.
3. With the switch frequency at 0, a block per plan sized by TS's completion rule stands in for TS's finish-each behaviour. A plan with a large frame goal makes a long override list, for example 500 rows for 500 star frames.
4. Dither rows use the project's `ditherevery`. A template's own `ditherevery` (column on `exposuretemplate`, -1 by default) is ignored inside an override order, as it is in TS when a user builds one by hand.
5. A secondary row that names an existing TS template uses it unchanged. The template's default exposure may differ from the row's sub, and the row's sub wins because it is written to the exposure plan.
6. Switch frequency and smart order are per project, so they are saved for every plan in a project at once. When plans disagree, the first plan in `plans.json` wins.
7. The goal unit threshold is the sub length: under 60 s is frames. A frame row stores both `target_frames` and `target_hours`.
8. The panel's narrowest desktop width is 641 px. Below that the phone sheet applies and is unchanged by this spec.
9. The 18 placeholders become an en dash rather than the original em dash, so a future punctuation tidy does not catch them.
10. Section 6 follows the AAC spec at commit `26d7be1`, including its storage contract. Field names come from that version. The AAC spec is being amended to use this spec's guid recipe for secondary rows.
11. Secondary rows nest under `filter_goals.<f>.sub_rows`, as the AAC storage contract requires, rather than sitting beside primary rows as top-level keys. The importer's flat keying changes to match, and the migration moves existing flat keys.

## Open questions

1. Rohan's three hand-made override orders (G7.7-3.7, Dolphin Nebula, Helix Nebula) make TS rotate one frame per filter, although their switch frequency is 0. Was that what he wanted when he set them on 11 October? If not, he can set each order once in ACP after phase 5, and the sync replaces them. Rohan has been asked and has not answered yet.
2. Does `ACP.NINA.Plugin.Tests` reference TS's assembly in a way that lets test O9 run on Windows? If not, the manual check is the only check against TS itself.
