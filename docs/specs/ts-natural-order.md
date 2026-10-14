# Spec: write ACP's shooting order as Target Scheduler's natural order

This spec changes how the sync makes Target Scheduler (TS) shoot a target's filters in the order ACP lists them. It replaces the override rows that `docs/specs/goal-panel-and-ts-order.md` section 5 writes for this job. It applies to both writers: the plugin's C# one in `ACP.NINA.Plugin` and the Python one in `acp-nina-ts-sync`. Rohan decided it on 14 October 2026.

## Terms

The terms of `goal-panel-and-ts-order.md` apply: filter row, exposure plan, override order, cadence, natural order and writers. Three more are used here.

- **Slot**: an `exposureplan.Id`. A plan's place in natural order is its slot. A slot never changes once TS has created it.
- **Owned plan**: an enabled exposure plan that this push wrote for an ACP row. A plan made by hand in TS, or whose row has a goal of 0, is not owned.
- **Permutation**: a mapping from the current slot of each owned plan to the slot it moves to. The plan's contents move. The slot stays.

## What is wrong with override rows

TS has no column for exposure plan order. Its natural order is the target's enabled plans sorted by `exposureplan.Id`. When ACP's order differs from that, the writer today writes an override order: one `overrideexposureorderitem` row for every frame and every dither.

That works, and it costs three things. The list is hand-written data that TS no longer builds from the project's settings. It ignores a template's dither count unless the writer injects one (plugin PR #24 does). And TS's own selectors, which already know how to finish each filter or rotate every N frames, are bypassed.

Rohan wants TS to shoot in its default order with the plans in the right slots. The writer therefore moves the plans' contents between the target's existing slots. Natural order then equals ACP's order. The writer deletes the override rows it generated.

## The rule

After a push, take every target the sync owns whose order was not set by hand in TS. Its owned plans are in ACP's row order in natural order, and it has no override rows.

"Set by hand in TS" has the meaning section 5 of the order spec already gives it: the decision table below returns "leave". Such a target is not changed by this spec at all.

## What stays and what moves

A plan is a row in `exposureplan`. Only some of its columns belong to the plan's identity, and they all travel together.

| Travels with the plan | Stays with the slot |
|---|---|
| `guid`, `exposure`, `desired`, `acquired`, `accepted`, `exposureTemplateId`, `enabled`, and any column a later schema adds | `Id`, `targetid`, `profileId` |

The writer reads the column list from `PRAGMA table_info(exposureplan)` at run time, so a column added in a later TS schema travels without a code change.

Tables that point at a plan by slot:

| Table and column | Treatment |
|---|---|
| `acquiredimage.exposureId` | Remapped with the permutation (step 6). Verified against the schema files for versions 23, 25 and 28: this is the only column that refers to `exposureplan.Id`. |
| `imagedata.acquiredimageid` | Points at `acquiredimage.Id`. Unaffected. |
| `overrideexposureorderitem.referenceIdx` | A zero-based position in the enabled plans in slot order. It changes meaning under a permutation, so the target's rows are deleted. |
| `filtercadenceitem.referenceIdx` | The same position. The target's rows are deleted. |
| `flathistory` | Keys on `targetId` and `filterName`, not on a plan. Unaffected. |

The `guid` is the plugin's and the Python writer's only identity for an ACP row, so it moves with the contents. After the push, each ACP row's guid is on a plan in its new slot.

## Where it runs

Everything below happens inside the one `BEGIN IMMEDIATE` transaction that `TargetSchedulerDb.RunWriteAsync` opens, in `TsUpsert.Apply`. The permutation is a new step in the existing order pass for each target. That pass comes after three things the push already does:

- the plans are inserted, found, restamped or disabled (step 4 in `TsUpsert.Apply`)
- the gear repoint has moved plans onto their new templates
- AAC's accepted counts are written (step 4b)

Inserts therefore happen first. A new ACP row gets a slot above every existing one, then the permutation moves its contents to wherever ACP's order puts it. A new plan holds no acquired images, so moving it is safe. Nothing in this spec depends on a new row landing last.

## The decision table

`OrderDecision` (`TsUpsert.cs`, `_order_decision` in `upsert.py`) is unchanged. Only what the writer does on each outcome changes.

| Outcome of `OrderDecision` | Today | Now |
|---|---|---|
| Smart order is on | Leave | Leave. No permutation. |
| No base, or a base with no `exposure_order` | Leave | Leave. |
| The push inserted this target | Write | Permute. The plans are inserted in ACP's order, so the mapping is usually the identity. |
| ACP's order differs from the base order | Write | Permute, and delete every override row on the target, including rows set in TS. ACP wins, as in the order spec. |
| Same order, TS rows equal the base rows, which are not empty | Refresh | Permute and delete the rows. This is how a target the old writer left with generated rows moves over. |
| Same order, base has no rows, TS has none, ACP's order matched natural order before the push | Refresh | Permute. This covers a plan re-enabled with a lower slot. |
| Anything else | Leave | Leave. The rows were set in TS. |

A target on "leave" keeps the current behaviour, and that code stays.

- Its override rows are remapped when the set of enabled plans changes.
- A plan that arrives gets its block on the end.
- Its cadence rows are deleted.

### A hand-set order and a permutation

The two never meet. A hand-set order means override rows the base does not know, so the decision is "leave" and the writer does not touch the slots. If Rohan sets a hand order in TS on a target that was permuted, the target stays as it is until ACP's order changes. When ACP's order changes, ACP wins. The writer permutes the plans and deletes the hand rows. It adds the existing note "ACP's filter order replaced an order set in Target Scheduler."

Rohan's three hand-made orders (G7.7-3.7, Dolphin Nebula, Helix Nebula) therefore stay until he reorders them in ACP, as the order spec says.

## The algorithm

For one target whose decision is "permute". The names `written`, `after`, `before`, `inserted` and `arrived` are the variables in `TsUpsert.Apply`.

1. **Owned order.** Take `written` (the plan ids this push wrote, in ACP's row order), drop duplicates, and keep only the ids in `after` (the target's enabled plans now). Call the result `owned`.
2. **Slots.** Sort the ids in `owned` ascending. Call the result `slots`.
3. **Mapping.** For each position `i`, the plan now in slot `owned[i]` moves to slot `slots[i]`. This is the permutation. A plan with `owned[i] == slots[i]` does not move. If no plan moves, skip to step 8.
4. **Guards.** The writer refuses to permute this target, and falls back as described under "Safety", when any of these holds:
   - an owned plan's `profileId` or `targetid` differs from the others
   - an `acquiredimage` row has an `exposureId` in `owned` and a `targetId` other than this target
   - `PRAGMA index_list(exposureplan)` shows a unique index other than the primary key (none exists in versions 23, 25 or 28, but a later TS may add one)
   - the plan columns cannot be read
5. **Read.** Read every moving plan's travelling columns into memory.
6. **Write.** In this order:
   1. `UPDATE exposureplan SET guid = NULL WHERE Id IN (<moving slots>)`. A unique index would allow repeated NULLs, so a mid-swap clash on `guid` is impossible even if one is added later.
   2. For each mapping entry, `UPDATE exposureplan SET <travelling columns> WHERE Id = <new slot>`, using the values read in step 5. The primary key never changes, and no other constraint exists, so the order of these updates does not matter.
   3. One statement remaps the images: `UPDATE acquiredimage SET exposureId = CASE exposureId WHEN <old> THEN <new> ... END WHERE targetId = <target> AND exposureId IN (<old ids>)`. SQLite evaluates the whole statement against the old values, so a swap does not map an image twice.
7. **Verify.** Read the target back. Three things must hold, and a failure throws, which rolls the whole push back:
   - the owned plans' guids, in slot order, equal ACP's row order
   - every moved plan's `acquired` and `accepted` equal the values read in step 5
   - the count of `acquiredimage` rows for each plan's guid equals the count before the permutation
8. **Clean up.** `DELETE FROM overrideexposureorderitem WHERE targetid = <target>` and `DELETE FROM filtercadenceitem WHERE targetid = <target>`. Cadence rows hold positions that now mean something else, so they go even when no plan moved but override rows did.
9. **Report.** Add the target to `outcome.PlansPermuted` with the mapping, add a push-result line such as "Helix: moved 3 exposure plans into ACP's order", and log the mapping at info level.

Plans that are not owned keep their slots and contents. A disabled plan keeps its slot too, because slots in `slots` come from `owned`, which holds enabled plans only. So after a push, natural order over the owned plans is ACP's order, and a plan made by hand in TS may sit between them. The rule above counts only owned plans for this reason.

### An example

Plans A, B and C sit in slots 5, 6 and 7. ACP's order is C, A, B.

- `owned` is [7, 5, 6] and `slots` is [5, 6, 7]
- C moves from slot 7 to slot 5, A from 5 to 6, B from 6 to 7
- Images that pointed at slot 7 now point at slot 5, those at 5 point at 6, those at 6 point at 7

Natural order is now C, A, B, and the target has no override rows.

## What happens to ACP's pins

ACP pins each row's plan id in `ts_links` (`exposure_plan_ids`, keyed `<target id>_<row id>`). `TsState.BuildRefs` builds those ids after the write, from a fresh read of the database, by looking up each row's guid (`TsGuid.RowPlan`). The guid has moved with the contents, so the ids it reports are the new slots. `TsLinksReporter` already posts them, and `BuildBaseSnapshot` does the same for `exposure_plans_by_panel_filter`. No new field is needed.

The base also records the target's effective order and its override rows. After a permutation, `override_rows` is empty and `exposure_order` is ACP's order.

One gap remains. If ACP is not told (the `NotToldWarning` case), ACP's pins name the old slots. `FindExposurePlan` looks up the row's own guid first. It uses a pin only when the guid finds nothing. A stale pin therefore matters only when the same edit also changes a row's template or filter. In that case the pin can name a plan that now holds another row's contents. I have not found a clean guard, because a pin legitimately claims plans made by hand with foreign guids. The warning already tells the user the next upload may ask about more plans; this spec leaves that as it is.

## Safety

- **Backup.** `TsPushService` copies the database with `TargetSchedulerDb.BackupTo` before the transaction. Nothing here changes that. Rolling back by hand means restoring that copy with NINA closed.
- **Rollback.** Any exception in steps 4 to 8 leaves `RunWriteAsync`. That rolls back the whole push, every target included. Step 7's failure is an exception for that reason.
- **No push while TS runs.** The push already refuses while a TS container runs (`TsContainerWatch`, `TsPushService.cs:221`). That matters more here. TS writes `acquired` back by slot after each frame, so a permutation during a run would let it overwrite a moved plan's counts.
- **Would permute.** The mapping is computed by a pure function before any statement runs. A payload flag `PermutePlans` is on by default. With it off, the writer logs "would permute <target>: <mapping>" and uses the old override-row writer. The flag exists so Rohan can compare one push in each mode.
- **Refusal falls back, it does not skip.** When a guard in step 4 refuses a target, the writer logs why and adds a note to the push result. It then writes the old generated override rows for that target. ACP's order still reaches TS, and the data is safe because the plans are untouched.
- **Idempotence.** A second push with no ACP change computes the identity mapping, moves nothing and finds no override rows to delete.
- **Writers agree.** Both writers produce identical rows. The shared fixture `golden-rows.json` carries the scenarios in the tests below.

## How it meets the other rules

### Gear repoint

`UpsertExposurePlan` moves a plan onto its gear template, and writes `exposure`, before the order pass. The new template id and the sub travel with the plan. `repoint_done` is judged from the set of enabled plans and their template ids, which a permutation does not change. The push still leaves plans ACP does not own where they are.

### AAC

`WriteAac` writes `accepted` by plan id before the order pass, and the permutation then moves the counts with the plan. The AAC rules do not change.

### Mosaics

Each panel is its own target. The decision is per target, as the order spec already says, with the anchor panel's base order used for every panel. Every panel the decision allows is permuted by the same ACP row order, over its own slots. A panel on "leave", such as one with a hand order, is not touched, and the others are not given its order.

### Import (TS to ACP)

The importer reads a target's effective order. With no override rows that is natural order, which after a permutation is ACP's order. The row ids come back unchanged, because the importer matches rows by guid or by pinned id, and both follow the contents. An import therefore reports no change in order. The warn note the order spec adds when ACP lists an order TS will not shoot appears only on a target on "leave".

### Switch frequency, dithers and smart order

With no override rows, TS uses its own selectors (`ExposureSelectionExpert.cs:15-29`). Switch frequency 0 uses the repeat-until-done selector, which shoots the first unfinished plan in natural order. Switch frequency N uses the basic selector's cadence. Dithers follow the project's "dither every" or the template's. The writer no longer computes block sizes, throttle or dither rows for a permuted target. `BlockSize`, `GeneratedRows` and the dither injector stay in the code for the fallback.

## The Python writer

`nina_ts_sync/upsert.py` gets the same behaviour and the same names where Python allows. The order pass at `upsert.py:1132-1148` calls the new `_plan_permutation`, `_permute_plans` and `_verify_permutation` in place of `_generated_rows` when the decision is write or refresh. `SyncReport` gains `plans_permuted`, serialised beside `order_written`. `_order_decision`, `_remap_override_rows` and `_generated_rows` do not change. The rig uses the plugin's writer, so the Python one is held to the same golden file and is not shipped separately.

## Tests

"Python" means `acp-nina-ts-sync/tests`. "C#" means `ACP.NINA.Plugin.Tests`, which runs only on Windows. Unless a test says otherwise, the fixture is one target with plans A, B and C in slots 5, 6 and 7. Each has a template, a goal of 4 frames and a different `acquired`. The database is at `user_version` 28. Switch frequency is 0, smart order is off, and the base matches the database.

| Id | Side | Setup | Expected |
|---|---|---|---|
| N1 | Python | Two plans A (slot 5), B (6). ACP order B, A. Push. | B's guid, template, `desired`, `acquired` and `accepted` are in slot 5, and A's in slot 6. No override rows. No cadence rows. No plan inserted or deleted. |
| N2 | Python | A, B, C. ACP order C, A, B. | The mapping is 7 to 5, 5 to 6, 6 to 7. Natural order by guid is C, A, B. No override rows. |
| N3 | Python | N2, and `acquiredimage` rows: 3 with `exposureId` 5, 2 with 6, 4 with 7, plus 1 with `exposureId` 0. | After the push: 3 rows have `exposureId` 6, 2 have 7, 4 have 5, and the unlinked row still has 0. The `imagedata` rows and `acquiredimage.Id` values are unchanged. |
| N4 | Python | A target with hand-set override rows. ACP order differs from natural, but the decision is "leave" (the base has no rows, TS rows differ). | No slot changes. The rows are untouched. The plan list is byte-identical. |
| N4b | Python | N4's setup, then ACP's order is changed and the push repeated. | ACP wins: the plans are permuted, the hand rows are deleted, and the push result carries the "replaced an order set in Target Scheduler" note. |
| N5 | Python | Plans A, B, C. B is disabled in TS (its row has goal 0). ACP order C, B, A. | B keeps slot 6 and its contents. Owned plans C and A permute across slots 5 and 7: C in 5, A in 7. Natural order over owned plans is C, A. |
| N5b | Python | N5, then B's goal is raised and B is enabled again. ACP order C, B, A. | B is owned again. The mapping covers slots 5, 6 and 7: C in 5, B in 6, A in 7. |
| N6 | Python | ACP has a fourth row D with no plan yet. ACP order D, A, B, C. | D is inserted in slot 8 first, then the permutation moves D's contents to slot 5, A to 6, B to 7 and C to 8. No override rows. |
| N7 | Python | TS has a plan T made by hand in slot 6, between owned plans A (5) and C (7). ACP order C, A. | T keeps slot 6 and its contents. C is in slot 5 and A in slot 7. |
| N8 | Python | A 1 by 2 mosaic. Panel 1 has A, B, C in slots 5 to 7. Panel 2 has them in slots 15 to 17. ACP order C, B, A. | Both panels are permuted over their own slots. No image on one panel changes `exposureId` to a slot of the other. |
| N8b | Python | The same mosaic, with a hand-set order on panel 2 only. | Panel 1 is permuted. Panel 2 is untouched and keeps its rows. |
| N9 | Python | Run N2 twice. | The second push moves nothing, deletes nothing and writes no `PlansPermuted` entry. The database rows equal the first push's. |
| N10 | Python | A target the old writer left with generated override rows and a matching base (the "refresh" case). | The first push permutes the plans and deletes the rows. The second push finds no rows and moves nothing. |
| N11 | Python | An `acquiredimage` row with `exposureId` 6 and `targetId` pointing at another target. ACP order C, A, B. | The guard refuses. The writer logs why and writes the old generated override rows. No slot changes. The push result carries a note. |
| N11b | Python | A unique index on `exposureplan.guid` added to the test database. ACP order C, A, B. | The guard refuses and the fallback applies. With the guard disabled in the test, the NULL step still lets the writes succeed. |
| N12 | Python | N2 with a fault injected after step 6. | The push fails, the database file's SHA-256 equals its value before the push, and only the backup file is new. The journal mode in the test is the rollback journal. |
| N12b | Python | N2 with a fault injected in step 7's check (a guid left in the wrong slot). | The same: the whole push is rolled back. |
| N13 | Python | Switch frequency 3, ACP order C, A, B. | No override rows. The oracle in `tests/ts_order_oracle.py` shoots C, C, C, A, A, A, B, B, B, then repeats. |
| N14 | Python | A plan with `acquired` 18 and `accepted` 17 moves slot. | Both counts are 18 and 17 in the new slot. |
| N15 | C# | Golden scenarios `n1_two_swapped`, `n2_three_rotated`, `n3_images_follow`, `n5_disabled_plan`, `n6_new_row`, `n7_foreign_plan`, `n8_mosaic`, `n10_refresh_from_generated`. | The plugin's rows equal the Python rows in every table, including `acquiredimage`. |
| N16 | C# | After a permuting push, build the refs and base snapshot from the read-back. | `exposure_plan_ids` for each row name its new slot, and `exposure_plans_by_panel_filter` agrees. The links request carries them. |
| N17 | C# | N12 on `TsUpsert.Apply` through `RunWriteAsync`. | The file hash is unchanged after the forced failure. |
| N18 | Python and C# | `PermutePlans` off, ACP order C, A, B. | The log holds "would permute" with the mapping, the old override rows are written, and no slot changes. |

`TsPushGoldenTests` gains the scenarios in N15 and compares `acquiredimage` as well as the tables it compares now. `dump_golden_rows.py` regenerates `golden-rows.json`, and it is copied into the plugin in the same session.

## Asking upstream

TS has no column for exposure plan order, which is why the sync has to move plans between slots. An explicit order column would make this spec unnecessary. The writer would set one number per plan. TS's loader and editor would have to honour it. The ask belongs with the TS maintainers (`tcpalmer/nina.plugin.targetscheduler`). I have not made it, and Rohan opens upstream issues himself.

## Phases

| Phase | Content | Repos |
|---|---|---|
| 1 | The permutation, its guards and the verify step in Python. Tests N1 to N14, N18. | acp-nina-ts-sync |
| 2 | The same in C#, the `PlansPermuted` outcome and push-result line, the golden scenarios. Tests N15 to N18. | ACP.NINA.Plugin |
| 3 | Manual check on Voyager, below. | none |

The plugin goes onto Voyager as in `goal-panel-and-ts-order.md` section 8. The first push after that migrates every target the old writer left on generated rows.

### Manual check on the imaging PC

Back up `schedulerdb.sqlite` first, with NINA closed.

- [ ] Reorder a target that has no hand-set order in ACP, then run Sync for tonight. In TS's Database Manager the target shows its filters in ACP's order, and no exposure order line.
- [ ] Open TS's plan preview for tonight. The first exposures are the first filter in ACP's list.
- [ ] In the Database Manager, open the target's image grid. The frame counts per filter equal what they were before the push.

## Assumptions made

1. A target the decision table returns "leave" for is never permuted, even when its natural order differs from ACP's. This keeps the order spec's rule that rows set in TS survive.
2. Plans that ACP does not own stay in their slots, so they can sit between owned plans. The rule is stated over owned plans for that reason.
3. A refused permutation falls back to the old override rows, not to leaving the target in the wrong order.
4. No new field goes to ACP. The pins follow through the refs the plugin already builds from a fresh read.
5. `acquiredimage.exposureId` is the only reference to `exposureplan.Id`. I checked this in the three schema fixtures and not in a live database.

## Open questions

1. Nightly walk restart. The order spec deletes a target's cadence rows on every push, but only for targets with override rows. The aim is that a shortfall AAC finds in the morning is shot first. A permuted target has no override rows. At switch frequency 0 the repeat-until-done selector is stateless, so nothing is lost. At switch frequency N the basic selector's cadence only moves forward. Should the push delete the cadence rows of every permuted target with N above 0, each night?
2. Does TS ever write `acquiredimage.exposureId` for a frame while no container is running, for example during a manual capture? If so, a push between the capture and TS's own save could mismatch. I have not found such a path in the plugin and have not read TS's source for it.
3. Is the journal mode of Voyager's `schedulerdb.sqlite` the rollback journal or WAL? Test N12 assumes the rollback journal. With WAL, a rolled-back push leaves the file's bytes unchanged only after a checkpoint.
