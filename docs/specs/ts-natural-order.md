# Spec: write ACP's shooting order as Target Scheduler's natural order

This spec changes how the sync makes Target Scheduler (TS) shoot a never-shot target's filters in ACP's order. It replaces the override rows that `docs/specs/goal-panel-and-ts-order.md` section 5 writes for that job. A target that already has frames keeps the generated override rows. It applies to both writers: the plugin's C# one in `ACP.NINA.Plugin` and the Python one in `acp-nina-ts-sync`. Rohan decided it on 14 October 2026 and narrowed it the same day.

## Terms

The terms of `goal-panel-and-ts-order.md` apply: filter row, exposure plan, override order, cadence, natural order and writers. Four more are used here.

- **Slot**: an `exposureplan.Id`. A plan's place in natural order is its slot. A slot never changes once TS has created it.
- **Owned plan**: an enabled exposure plan that this push wrote for an ACP row. A plan made by hand in TS, or whose row has a goal of 0, is not owned. A row held back by a skipped template is not owned either.
- **Permutation**: a mapping from the current slot of each owned plan to the slot it moves to. The plan's contents move. The slot stays.
- **Never-shot target**: a target where every owned plan has `acquired` 0 and `accepted` 0, and no `acquiredimage` row points at any plan of the target.

## What is wrong with override rows

TS has no column for exposure plan order. Its natural order is the target's enabled plans sorted by `exposureplan.Id`. When ACP's order differs from that, the writer today writes an override order: one `overrideexposureorderitem` row for every frame and every dither.

That works, and it costs three things. The list is hand-written data that TS no longer builds from the project's settings. It ignores a template's dither count unless the writer injects one (plugin PR #24 does). And TS's own selectors, which already know how to finish each filter or rotate every N frames, are bypassed.

Rohan wants TS to shoot in its default order with the plans in the right slots. For a never-shot target the writer therefore moves the plans' contents between the target's existing slots. Natural order then equals ACP's order. The writer deletes the override rows it generated.

## The rule

After a push, take every target the sync owns whose order was not set by hand in TS. If it is a never-shot target, its owned plans are in ACP's row order in natural order, and it has no override rows. If it has frames, the writer follows the order spec: generated override rows, with dithers by template count.

"Set by hand in TS" has the meaning section 5 of the order spec already gives it: the decision table below returns "leave". Such a target is not changed by this spec at all.

## Why only never-shot targets

TS writes whole `exposureplan` rows back by `Id` from in-memory entities it loaded earlier. Some of those writes happen outside a running container, so a push cannot rule them out by checking the container. Repo: https://github.com/tcpalmer/nina.plugin.targetscheduler.

- `Grading/ImageGrader.cs` loads the plan before it grades an image, then does `Accepted++` and `AddOrUpdate`.
- `Grading/ImageGradingController.cs` grades on a background queue with capacity 5. Images graded after a run stops still write their plan.
- `Sequencer/ImageSaveWatcher.cs`: `Stop` does not drain the grader, so the queue outlives the run.
- `Controls/DatabaseManager/TargetViewVM.cs:303-316` and `629-642`: Save re-writes every plan and the override rows from memory through `SaveTarget`. A target view opened before a sync holds the old rows.
- `SchedulerDatabaseContext.cs:586-604`: `ManualUpdateGrading` writes a plan from memory.

After a permutation, one such write puts a plan's old counts and guid on an `Id` that now holds another plan. Meanwhile `acquiredimage.exposureId` still points at the remapped slot. The counts and the images then disagree, and nothing in the database shows it.

On a never-shot target a stale write is harmless. It restores the whole old row, guid and zeros together, so the target is back in its old order and is consistent. The next push permutes it again. No `acquiredimage` row exists to disagree with it, so the writer has no remap step to run.

## What stays and what moves

A plan is a row in `exposureplan`. Only some of its columns belong to the plan's identity, and they all travel together.

| Travels with the plan | Stays with the slot |
|---|---|
| `guid`, `exposure`, `desired`, `acquired`, `accepted`, `exposureTemplateId`, `enabled`, and any column a later schema adds | `Id`, `targetid`, `profileId` |

The writer reads the column list from `PRAGMA table_info(exposureplan)` at run time, so a column added in a later TS schema travels without a code change. This is new code. `ProbeColumns` (`TargetSchedulerDb.cs:121`) reads four tables and does not return the primary key flag, so the writer cannot reuse it.

Tables that point at a plan by slot:

| Table and column | Treatment |
|---|---|
| `acquiredimage.exposureId` | Never remapped. A never-shot target has no image row pointing at it, and step 2 refuses the target if one appears. `TargetSchedulerDb.cs:408` also reads this column, for the progress report, and is unaffected. Verified against the schema files for versions 23, 25 and 28: this is the only column that refers to `exposureplan.Id`. |
| `imagedata.acquiredimageid` | Points at `acquiredimage.Id`. Unaffected. |
| `overrideexposureorderitem.referenceIdx` | A zero-based position in the enabled plans in slot order. It changes meaning under a permutation, so the target's rows are deleted. |
| `filtercadenceitem.referenceIdx` | The same position. The target's rows are deleted when a plan moved or override rows existed. |
| `flathistory` | Keys on `targetId` and `filterName`, not on a plan. Unaffected. |

The `guid` is the plugin's and the Python writer's only identity for an ACP row, so it moves with the contents. After the push, each ACP row's guid is on a plan in its new slot.

## Where it runs

Everything below happens inside the one `BEGIN IMMEDIATE` transaction that `TargetSchedulerDb.RunWriteAsync` opens, in `TsUpsert.Apply`. The permutation is a new step in the existing order pass for each target. That pass comes after three things the push already does:

- the plans are inserted, found, restamped or disabled (step 4 in `TsUpsert.Apply`)
- the gear repoint has moved plans onto their new templates
- AAC's accepted counts are written (step 4b)

Inserts therefore happen first. A new ACP row gets a slot above every existing one, then the permutation moves its contents to wherever ACP's order puts it. A new plan has zero counts, so it does not make a target ineligible. Nothing in this spec depends on a new row landing last.

## The decision table

`OrderDecision` (`TsUpsert.cs`, `_order_decision` in `upsert.py`) is unchanged. Only what the writer does on each outcome changes. "Permute" below applies to a never-shot target that passes the guards in step 2. A target that fails the eligibility test or a guard takes the "Write" or "Refresh" action of the order spec instead.

| Outcome of `OrderDecision` | Today | Now |
|---|---|---|
| Smart order is on | Leave | Leave. No permutation. |
| No base, or a base with no `exposure_order` | Leave | Leave. |
| The push inserted this target | Write | Permute. The plans are inserted in ACP's order, so the mapping is usually the identity. |
| ACP's order differs from the base order | Write | Permute, and delete every override row on the target, including rows set in TS. ACP wins, as in the order spec. |
| Same order, TS rows equal the base rows, which are not empty | Refresh | Permute and delete the rows. This is how a never-shot target the old writer left with generated rows moves over. |
| Same order, base has no rows, TS has none, ACP's order matched natural order before the push | Refresh | Permute. This covers a plan re-enabled with a lower slot. |
| Anything else | Leave | Leave. The rows were set in TS. |

A target on "leave" keeps the current behaviour, and that code stays.

- Its override rows are remapped when the set of enabled plans changes.
- A plan that arrives gets its block on the end.
- Its cadence rows are deleted.

A target that has frames and whose outcome is "Write" or "Refresh" gets generated override rows. `BlockSize`, `GeneratedRows` and the dither injector stay in the code for it. Once a target has frames, a later push never permutes it. It keeps the order its plans already have.

### A hand-set order and a permutation

The two never meet. A hand-set order means override rows the base does not know, so the decision is "leave" and the writer does not touch the slots. If Rohan sets a hand order in TS on a permuted target, the target stays as it is until ACP's order changes. When ACP's order changes, ACP wins. The writer permutes the plans, if the target is still never-shot, and deletes the hand rows. It adds the existing note "ACP's filter order replaced an order set in Target Scheduler."

Rohan's three hand-made orders (G7.7-3.7, Dolphin Nebula, Helix Nebula) therefore stay until he reorders them in ACP, as the order spec says. Helix and the other two have frames, so a reorder writes override rows for them.

## The algorithm

For one target whose decision is "permute". The names `written`, `after`, `before`, `inserted` and `arrived` are the variables in `TsUpsert.Apply`.

1. **Eligibility.** The target must be a never-shot target. Read it with one query over its owned plans and one over `acquiredimage` for the target's plan ids and `targetId`. If it is not, take the override-row path and stop.
2. **Guards.** The writer refuses to permute this target, and falls back as described under "Safety", when any of these holds:
   - the permutation is off (`PermutePlans`), the container watch cannot tell whether TS is running, or any `acquiredimage` in the profile has `acquireddate` within the last 30 minutes
   - an owned guid appears on more than one `exposureplan` row anywhere in the database
   - an owned plan's `profileId` or `targetid` differs from the others
   - an `acquiredimage` row has an `exposureId` in `owned` and a `targetId` other than this target
   - `PRAGMA index_list(exposureplan)` shows a unique index other than the primary key (none exists in versions 23, 25 or 28, but a later TS may add one)
   - the plan columns cannot be read
3. **Owned order.** Take `written` (the plan ids this push wrote, in ACP's row order), drop duplicates, and keep only the ids in `after` (the target's enabled plans now). Call the result `owned`.
4. **Slots.** Sort the ids in `owned` ascending. Call the result `slots`.
5. **Mapping.** For each position `i`, the plan now in slot `owned[i]` moves to slot `slots[i]`. This is the permutation. A plan with `owned[i] == slots[i]` does not move. If no plan moves, skip to step 9.
6. **Read.** Read every moving plan's travelling columns into memory.
7. **Write.** In this order:
   1. `UPDATE exposureplan SET guid = NULL WHERE Id IN (<moving slots>)`. A unique index would allow repeated NULLs, so a mid-swap clash on `guid` is impossible even if one is added later.
   2. For each mapping entry, `UPDATE exposureplan SET <travelling columns> WHERE Id = <new slot>`, using the values read in step 6. The primary key never changes, and no other constraint exists, so the order of these updates does not matter.
   3. A guarded no-op replaces the old image remap: `SELECT COUNT(*) FROM acquiredimage WHERE exposureId IN (<moving slots>)` must return 0, and a count above 0 throws. Step 2 already refused such a target, so the assertion fails only if a frame arrived inside the transaction.
8. **Verify.** Read the target back by slot: `SELECT Id, guid, acquired, accepted FROM exposureplan WHERE Id IN (<slots>)`. Read by `Id`, never by guid, because a non-owned plan may hold a NULL or duplicate guid. Two things must hold, and a failure throws, which rolls the whole push back:
   - the guids, in slot order, equal ACP's row order
   - every `acquired` and `accepted` is still 0
9. **Clean up.** Delete the target's `filtercadenceitem` rows only when a plan moved or override rows existed. Delete its `overrideexposureorderitem` rows. An identity push therefore changes nothing, and a second push finds nothing to delete.
10. **Report.** Add the target to `outcome.PlansPermuted`, and to `OrderSyncedTargetIds` (`TsUpsert.cs:305`, read at `TsState.cs:300`). A target that took the fallback is added to `OrderSyncedTargetIds` too, so the base snapshot records the override rows it got. Add a push-result line such as "Helix: moved 3 exposure plans into ACP's order", and log the mapping at info level.

After the commit the writer re-reads the profile's `exposureplan` rows outside the transaction. If any owned guid is now on more than one row, it logs at error level. It also adds a warning that names the target. A stale TS write can cause this between the transaction and the read.

Plans that are not owned keep their slots and contents. A disabled plan keeps its slot too, because slots in `slots` come from `owned`, which holds enabled plans only. So after a push, natural order over the owned plans is ACP's order, and other plans may sit between them. Two kinds can: a plan made by hand in TS, and the plan of a row held back by a skipped template (`TsUpsert.cs:194-195`). The push result carries a line for them: "Rows waiting for a template were not moved: their plans may sit between ACP's rows."

### An example

Plans A, B and C sit in slots 5, 6 and 7 with no frames. ACP's order is C, A, B.

- `owned` is [7, 5, 6] and `slots` is [5, 6, 7]
- C moves from slot 7 to slot 5, A from 5 to 6, B from 6 to 7
- No image row exists to follow, and the cadence rows and override rows go

Natural order is now C, A, B, and the target has no override rows.

## What happens to ACP's pins

ACP pins each row's plan id in `ts_links` (`exposure_plan_ids`, keyed `<target id>_<row id>`). `TsState.BuildRefs` builds those ids after the write, from a fresh read of the database, by looking up each row's guid (`TsGuid.RowPlan`). The guid has moved with the contents, so the ids it reports are the new slots. `TsLinksReporter` already posts them, and `BuildBaseSnapshot` does the same for `exposure_plans_by_panel_filter`.

The base also records the target's effective order and its override rows. After a permutation, `override_rows` is empty and `exposure_order` is ACP's order.

### When ACP is not told

If ACP is not told (the `NotToldWarning` case), its pins name the old slots. The read-back after the commit is also swallowed on failure (`TsPushService.cs:360`). That failure produces the same warning, and the wording covers both: "The rows were written, but ACP could not read them back. Its pinned plan ids may name old slots. The next upload may ask about more plans."

`FindExposurePlan` (`TsUpsert.cs:885`) looks up the row's own guid first. It uses a pin only when the guid finds nothing. A stale pin matters when the same edit also changes a row's template or filter, or removes a row. The removed-row path (`TsConvert.cs:475-490`, `TsUpsert.cs:229-248`) acts on a pinned plan the same way. In both cases the pin can name a slot that now holds another row's plan. The guard: the writer skips a pinned plan whose guid equals a different row's guid in this payload. It logs the skip, and the row is treated as having no plan, so a new plan is inserted or the removal is left out. A pin that claims a plan made by hand with a foreign guid still works, because that guid matches no row.

## Safety

- **Backup.** `TsPushService` copies the database with `TargetSchedulerDb.BackupTo` before the transaction. Nothing here changes that. Rolling back by hand means restoring that copy with NINA closed.
- **Rollback.** Any exception in steps 1 to 9 leaves `RunWriteAsync`. That rolls back the whole push, every target included. A failure in step 8 is an exception for that reason.
- **No push while TS runs, or when the watch cannot tell.** The push already refuses while a TS container runs (`TsContainerWatch`, `TsPushService.cs:221`). `TsContainerWatch.IsRunning` returns false when it cannot tell (`TsContainerWatch.cs:161-175`). The permutation therefore needs a positive answer. When the watch cannot tell, the writer does not permute and uses the override rows.
- **Recent frames.** The writer does not permute any target when any `acquiredimage` in the profile has `acquireddate` within the last 30 minutes. The grader's queue can still be writing plan rows for that image.
- **Would permute.** The mapping is computed by a pure function before any statement runs. The setting `PermutePlans` is on by default. With it off, the writer logs "would permute <target>: <mapping>" and uses the old override-row writer. The setting exists so Rohan can compare one push in each mode.
- **Refusal falls back, it does not skip.** When a guard in step 2 refuses a target, the writer logs why and adds a note to the push result. It then writes the old generated override rows for that target. ACP's order still reaches TS, and the data is safe because the plans are untouched.
- **Idempotence.** A second push with no ACP change computes the identity mapping, moves nothing, finds no override rows and deletes no cadence rows.
- **Writers agree.** Both writers produce identical rows. The shared fixture `golden-rows.json` carries the scenarios in the tests below.

## Settings and reports

`PermutePlans` is a plugin setting in `AcpSettings`, a bool that defaults to on. `TsConvert.BuildPayload` copies it into the payload as `permute_plans`. The Python writer reads the same name from its config and defaults to on. Neither side shows it in the ACP web UI.

`PlansPermuted` is a list in the push result. Each entry has these fields:

- `target_id`, `target_name` and `panel`
- `moves`: a list of `{ guid, from_slot, to_slot }`, one entry per plan that moved

A second list, `PermuteFallbacks`, holds `{ target_id, target_name, reason }` for each target a guard refused. The links report carries both lists under each target as `plans_permuted` and `permute_fallbacks`, in the same shape with snake case names. ACP stores them with the push log and does not act on them. `SyncReport.plans_permuted` in Python serialises the same fields.

## How it meets the other rules

### Gear repoint

`UpsertExposurePlan` moves a plan onto its gear template, and writes `exposure`, before the order pass. The new template id and the sub travel with the plan. `repoint_done` is `RepointRows > 0` (`TsUpsert.cs:319`): it is true when any plan written in this push left a repoint list's templates. A permutation does not change that count, because it runs after the writes the count comes from. The push still leaves plans ACP does not own where they are.

### AAC

`WriteAac` writes `accepted` by plan id before the order pass. A never-shot target has no accepted count to write, so the permutation moves nothing AAC wrote. If AAC wrote a non-zero `accepted` in this push, the target has frames and is not permuted. The AAC rules do not change.

### Mosaics

Each panel is its own target. The decision and the eligibility test are per target, as the order spec already says, with the anchor panel's base order used for every panel. Every panel the decision allows and that is never-shot is permuted by the same ACP row order, over its own slots. Panels may have different enabled plans, and each panel's `owned` and `slots` come from its own plans. A panel on "leave", or one with frames, is not permuted, and the others are not given its order.

### Import (TS to ACP)

The importer reads a target's effective order. With no override rows that is natural order, which after a permutation is ACP's order. The row ids come back unchanged, because the importer matches rows by guid or by pinned id, and both follow the contents. A hand-made plan between owned slots is listed in the middle by the importer, at its natural position among ACP's rows. Otherwise an import reports no change in order. The warn note the order spec adds when ACP lists an order TS will not shoot appears only on a target on "leave".

### Switch frequency, dithers and smart order

With no override rows, TS uses its own selectors (`ExposureSelectionExpert.cs:15-29`). Switch frequency 0 uses the repeat-until-done selector, which shoots the first unfinished plan in natural order. Switch frequency N uses the basic selector's cadence. Dithers follow the project's "dither every" or the template's. The writer no longer computes block sizes, throttle or dither rows for a permuted target. Smart order on is a "leave" and nothing is permuted.

## The Python writer

`nina_ts_sync/upsert.py` gets the same behaviour and the same names where Python allows. The order pass at `upsert.py:1132-1148` calls the new `_plan_permutation`, `_permute_plans` and `_verify_permutation` in place of `_generated_rows` when the decision is write or refresh and the target is a never-shot target. `SyncReport` gains `plans_permuted` and `permute_fallbacks`, serialised beside `order_written`. `_order_decision`, `_remap_override_rows` and `_generated_rows` do not change. The Python writer has no container watch, so the watch guard applies to the plugin only. The rig uses the plugin's writer, so the Python one is held to the same golden file and is not shipped separately.

## Tests

"Python" means `acp-nina-ts-sync/tests`. "C#" means `ACP.NINA.Plugin.Tests`, which runs only on Windows. Unless a test says otherwise, the fixture is one target with plans A, B and C in slots 5, 6 and 7. Each has a template and a goal of 4 frames. All have `acquired` and `accepted` 0 and no `acquiredimage` row exists. The database is at `user_version` 28. Switch frequency is 0, smart order is off, and the base matches the database.

| Id | Side | Setup | Expected |
|---|---|---|---|
| N1 | Python | Two plans A (slot 5), B (6). ACP order B, A. Push. | B's guid, template and `desired` are in slot 5, and A's in slot 6. No override rows. No cadence rows. No plan inserted or deleted. |
| N2 | Python | A, B, C. ACP order C, A, B. | The mapping is 7 to 5, 5 to 6, 6 to 7. Natural order by guid is C, A, B. No override rows. |
| N3 | Python | N2, with `acquired` 1 on plan B. Variants: `accepted` 1 only; `acquired` and `accepted` 0 and one `acquiredimage` row with `exposureId` 6. | The target is not eligible. No slot changes. The generated override rows for C, A, B are written, and the plan rows and the image row are byte-identical. |
| N4 | Python | A target with hand-set override rows. ACP order differs from natural, but the decision is "leave" (the base has no rows, TS rows differ). | No slot changes. The rows are untouched. The plan list is byte-identical. |
| N4b | Python | N4's setup, then ACP's order is changed and the push repeated. The target is never-shot. | ACP wins: the plans are permuted, the hand rows are deleted, and the push result carries the "replaced an order set in Target Scheduler" note. |
| N5 | Python | Plans A, B, C. B is disabled in TS (its row has goal 0). ACP order C, B, A. | B keeps slot 6 and its contents. Owned plans C and A permute across slots 5 and 7: C in 5, A in 7. Natural order over owned plans is C, A. |
| N5b | Python | N5, then B's goal is raised and B is enabled again. ACP order C, B, A. | B is owned again. The mapping covers slots 5, 6 and 7: C in 5, B in 6, A in 7. |
| N6 | Python | ACP has a fourth row D with no plan yet. ACP order D, A, B, C. | D is inserted in slot 8 first, then the permutation moves D's contents to slot 5, A to 6, B to 7 and C to 8. No override rows. |
| N7 | Python | TS has a plan T made by hand in slot 6, between owned plans A (5) and C (7). ACP order C, A. | T keeps slot 6 and its contents. C is in slot 5 and A in slot 7. |
| N7b | Python | N7, where T and another non-owned plan U have a NULL `guid` and a duplicate `guid` between them. | The permutation succeeds. Step 8 reads by `Id`, so T and U do not confuse it. T and U are byte-identical. |
| N8 | Python | A 1 by 2 mosaic. Panel 1 has A, B, C in slots 5 to 7. Panel 2 has them in slots 15 to 17. ACP order C, B, A. | Both panels are permuted over their own slots. No plan moves between panels. |
| N8b | Python | The same mosaic, with a hand-set order on panel 2 only. | Panel 1 is permuted. Panel 2 is untouched and keeps its rows. |
| N8c | Python | The same mosaic, with A disabled on panel 2 only. ACP order C, B, A. | Panel 1 permutes over slots 5 to 7. Panel 2 permutes C and B over slots 16 and 17, and A keeps slot 15. |
| N8d | Python | The same mosaic, with `acquired` 2 on one plan of panel 2. | Panel 1 is permuted. Panel 2 gets generated override rows and its plans stay in their slots. |
| N9 | Python | Run N2 twice. | The second push moves nothing, deletes nothing and writes no `PlansPermuted` entry. The database rows equal the first push's. |
| N10 | Python | A never-shot target the old writer left with generated override rows and a matching base (the "refresh" case). Then the same with `acquired` 1 on one plan. | Never-shot: the first push permutes the plans and deletes the rows, and the second finds no rows and moves nothing. With frames: the rows are refreshed, the plans stay put. |
| N11 | Python | An `acquiredimage` row with `exposureId` 6 and `targetId` pointing at another target. ACP order C, A, B. | The target is not eligible. The writer writes the old generated override rows. No slot changes. The push result carries a note. |
| N11b | Python | A unique index on `exposureplan.guid` added to the test database. ACP order C, A, B. | The guard refuses and the fallback applies. With the guard disabled in the test, the NULL step still lets the writes succeed. |
| N11c | Python | One owned guid also on a plan of another target. ACP order C, A, B. | The guard refuses and the fallback applies. The push result names the guid. |
| N11d | C# | An `acquiredimage` row on another target with `acquireddate` 10 minutes before the push. Then 31 minutes before. | At 10 minutes no target in the profile is permuted and each takes the override rows. At 31 minutes the never-shot target is permuted. |
| N11e | C# | The container watch cannot tell whether TS runs (the sequencer check and the event log both unavailable). | No target is permuted. The override rows are written, and the push result says why. |
| N12 | Python | N2 with a fault injected after step 7. | The push fails, the database file's SHA-256 equals its value before the push, and only the backup file is new. The journal mode in the test is the rollback journal. |
| N12b | Python | N2 with a fault injected in step 8's check (a guid left in the wrong slot). | The same: the whole push is rolled back. |
| N12c | C# | N2 with the read-back after the commit made to throw. | The rows stay committed. The push succeeds with the "could not read them back" warning, and ACP's pins are not updated. |
| N12d | C# | N2, then after the commit a second row gets an owned guid. | The post-commit read logs at error level and the push result carries a warning naming the target. |
| N13 | Python | Switch frequency 3, ACP order C, A, B. | No override rows. The oracle in `tests/ts_order_oracle.py` shoots C, C, C, A, A, A, B, B, B, then repeats. |
| N14 | Python | Plans with different `desired`, `exposure`, `exposureTemplateId` and `enabled` values move slot. | Every travelling column is in the new slot with its guid. `Id`, `targetid` and `profileId` are unchanged. |
| N15 | C# | Golden scenarios `n1_two_swapped`, `n2_three_rotated`, `n3_target_with_frames`, `n3b_image_row_only`, `n5_disabled_plan`, `n6_new_row`, `n7_foreign_plan`, `n8_mosaic`, `n8c_mosaic_different_sets`, `n10_refresh_from_generated`, `n11c_duplicate_guid`, `n12_skipped_template`, `n13_template_and_position_change`, `n14_smart_order_on`, `n16_stale_pin`, and the changed scenarios below. | The plugin's rows equal the Python rows in every table. The comparison also checks slots: for each target, the plan guids ordered by `Id` must equal the golden file's list. |
| N16 | C# | After a permuting push, build the refs and base snapshot from the read-back. | `exposure_plan_ids` for each row name its new slot, and `exposure_plans_by_panel_filter` agrees. The links request carries them and the `plans_permuted` list. |
| N17 | C# | N12 on `TsUpsert.Apply` through `RunWriteAsync`. | The file hash is unchanged after the forced failure. |
| N18 | Python and C# | `PermutePlans` off, ACP order C, A, B. Then on, with no value set. | Off: the log holds "would permute" with the mapping, the old override rows are written, and no slot changes. Unset: the setting reads as on. |
| N19 | Python and C# | ACP's pins name slot 6 for row X, but slot 6 now holds the plan whose guid belongs to row Y in the same payload. X's template changes. | The writer skips the pin. X gets a new plan, Y's plan is untouched, and the log names the skip. A second case removes X: no plan is disabled. |
| N20 | Python and C# | A row whose template waits (filter not on the wheel). Others are reordered around it. | The held-back plan keeps its slot. Owned plans permute among their own slots. The push result carries the "Rows waiting for a template" line. |
| N21 | Python and C# | In one push, a row's template changes and another row moves up the list. | The template change lands first. The plan keeps its new template when it moves, and the order is ACP's. |
| N22 | Python and C# | Smart order on, ACP order C, A, B. | No slot changes and no override rows. |
| N23 | Python | N2, then the first frame is recorded (`acquired` 1 on one plan) and ACP's order is left alone. Then ACP reorders. | The first push after the frame changes nothing. The reorder writes generated override rows and leaves the plans in their slots. |

The golden file has one slot check beyond the key scheme. `TsPushGoldenTests.Normalise` drops `Id` and keys rows by guid, so it cannot see a slot, and the slot list in N15 closes that gap. No `n` scenario puts an image through `Normalise`. N3 and N3b leave `acquiredimage` byte-identical, and a direct assertion on `exposureId` covers them. If a later scenario touches `acquiredimage` it needs a key scheme first, such as the image's guid or its `acquireddate` and plan guid.

### Existing golden scenarios

A scenario's target has frames when its setup sets `acquired` or `accepted` above 0 before the push under test. The setups of `o4c`, `o4d`, `f1_override_rows_rebuilt` and `x5` do not, so they change as the second column says. The list lives in `TsPushGoldenTests.cs:85-115` and `TsRowScenarios.cs:41-86`, and `row_scenarios.py` mirrors it.

| Scenario | Target has frames | Expected after the change |
|---|---|---|
| `o1_acp_order_written` | No | The plans are permuted to OIII, Ha. No override rows and no cadence rows. |
| `o3_acp_order_replaces_ts_order` | No | ACP's reorder to Ha, OIII wins. The plans are permuted, the hand rows are deleted, and the replace note is present. |
| `o4_rotate_every_3` | No | Permuted, no override rows. The oracle shoots the rotation from TS's own selector. |
| `o4c_cadence_reset_every_push` | Setup is changed so the counts are set before the reorder | The target has frames. The reorder writes OIII x4, Ha x4 as override rows. The cadence rows are deleted by the next push. The assertion in the order spec stays. |
| `o4d_goal_change_refreshes_rows` | Setup is changed so the counts are set before the reorder | The rows become OIII x6, Ha x4 as before. A new `n` variant covers the never-shot case: `desired` 6 on OIII and no rows. |
| `o7_dither_rows` | No | Permuted, no dither rows. A variant with frames keeps O7's rows, with dithers by template count. |
| `o8_new_plan_appended_to_ts_order` | No | Unchanged. The hand rows make it "leave", and SII's block is appended. |
| `o11_no_base_order_writes_nothing` | No | Unchanged. "Leave". |
| `x1_mosaic_hand_order_on_panel_two_kept` | No | Unchanged. Panel 2 is "leave", and panel 1 is already in natural order. |
| `x2_mosaic_acp_reorder_writes_both_panels` | No | Both panels are permuted. The hand rows on panel 2 are deleted. A variant with frames on both panels keeps today's generated rows. |
| `x3_added_row_dragged_to_top_is_a_reorder` | No | ACP wins. The plans are permuted and the hand rows are deleted. |
| `x3b_added_row_at_end_keeps_hand_order` | No | Unchanged. "Leave", and SII's block is appended. |
| `x4_goal_raised_from_zero_keeps_hand_order` | No | Unchanged. "Leave". |
| `x5_stale_sync_rows_refreshed_hand_rows_kept` | Setup is changed so panel 2 has frames | Panel 2 keeps refreshed generated rows. Panel 1 keeps its hand rows. A variant with no frames permutes panel 2 and keeps panel 1's hand rows. |
| `x6_readded_row_with_no_rows_writes_acps_order` | No | Permuted, so Ha lands in the last slot. No override rows. A variant with frames keeps today's rows. |
| `f1_override_rows_rebuilt` | Setup is changed so the counts are set before the reorder | The rows are rebuilt in blocks of 3. The never-shot case has no rows. |
| `r1_removed_row_disabled` | Yes (`acquired` 3 on Id 1) | Unchanged. |
| `r2_ts_rows_kept_and_remapped` | Yes | Unchanged. The hand rows are kept and remapped. |
| `r3_readded_row_enabled` | Yes | Unchanged. The override rows are rebuilt. |
| `m2`, `m3`, `m4`, `s1`, `s2`, `f2`, the smart order scenario and the rest | Not applicable | Unchanged. They write no order. |

## Asking upstream

TS has no column for exposure plan order, which is why the sync has to move plans between slots. An explicit order column would make this spec unnecessary, and would also let a target with frames keep its order without override rows. The writer would set one number per plan. TS's loader and editor would have to honour it. The ask belongs with the TS maintainers (`tcpalmer/nina.plugin.targetscheduler`). I have not made it, and Rohan opens upstream issues himself.

## Phases

| Phase | Content | Repos |
|---|---|---|
| 1 | The permutation, its guards and the verify step in Python. Tests N1 to N14, N18 to N23. | acp-nina-ts-sync |
| 2 | The same in C#, the `PlansPermuted` outcome and push-result line, the setting, the stale-pin guard, the golden scenarios. Tests N11d, N11e, N12c, N12d and N15 to N19. | ACP.NINA.Plugin |
| 3 | Manual check on Voyager, below. | none |

The plugin goes onto Voyager as in `goal-panel-and-ts-order.md` section 8. The first push after that migrates every never-shot target the old writer left on generated rows. Targets with frames keep theirs.

### Manual check on the imaging PC

Back up `schedulerdb.sqlite` first, with NINA closed.

- [ ] Reorder a never-shot target that has no hand-set order in ACP, then run Sync for tonight. In TS's Database Manager the target shows its filters in ACP's order, and no exposure order line.
- [ ] Reorder a target that has frames. It keeps its plans in their slots and shows an exposure order line, as it does today.
- [ ] Open TS's plan preview for tonight. The first exposures are the first filter in ACP's list.

## Assumptions made

1. A target the decision table returns "leave" for is never permuted, even when its natural order differs from ACP's. This keeps the order spec's rule that rows set in TS survive.
2. Plans that ACP does not own stay in their slots, so they can sit between owned plans. The rule is stated over owned plans for that reason.
3. A refused permutation falls back to the old override rows, not to leaving the target in the wrong order.
4. No new field goes to ACP's pins. They follow through the refs the plugin already builds from a fresh read. The new `plans_permuted` list is informational.
5. `acquiredimage.exposureId` is the only reference to `exposureplan.Id`. I checked this in the three schema fixtures and not in a live database.
6. The 30-minute window for a recent frame is a chosen value, not one derived from TS.
7. TS writes plan rows outside a container run, by the paths listed under "Why only never-shot targets". The journal mode on the rig is "delete", the rollback journal, so a rolled-back push leaves the file's bytes unchanged.

## Decided

1. Nightly walk restart. A permuted target has no override rows, so the push does not delete its cadence rows each night. Target Scheduler then walks its cadence the way it does for any target built by hand, carrying on from where the last night stopped. A target with frames keeps the order spec's nightly reset, because it still has override rows. (Decided 14 Oct 2026: follow Target Scheduler's own behaviour wherever no override is involved.)
