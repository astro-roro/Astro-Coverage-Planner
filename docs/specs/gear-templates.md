# Spec: gear templates, the wheel, and ACP owning Target Scheduler templates

Status: draft, 2026-10-13, amended after two reviews the same day. Builds on `docs/specs/goal-panel-and-ts-order.md`, whose sections 2 and 3 ("The TS template list from the plugin" and "Templates") this spec changes. Where the two disagree, this one wins once built.

This spec redesigns the gear editor in Astro Coverage Planner (ACP) so that each camera's filters can carry several exposure templates, and so that ACP owns the Target Scheduler (TS) templates instead of only reading them. It covers five changes that touch the same data, so they are built as one piece of work in five phases.

- A camera's filter holds a list of templates, for example Ha 300 and Ha 600, or R and R Stars. One template per filter is starred. A new plan row for that filter starts on the starred template.
- Editing a template in gear updates it in TS on the next sync, but only the fields changed in ACP since the last push. A field changed in both places is held, not overwritten.
- The filters a camera really has come from the NINA profile's filter wheel, as the plugin reports it. A filter can be added to gear before it is on the wheel.
- A one-time Tidy screen collapses the duplicate templates TS holds today.
- The side panel widens to 420 px, in the style of the approved mockup.

Acceptance tests are listed at the end of each section, and there are 130 in all (prefixes W, G, F, U, P, S, L, T and M).

The mockup is three artboards: Main (a camera with its filters and templates), Tidy, and Telescopes. Its files are `Main.dc.html`, `Tidy.dc.html` and `Telescopes.dc.html` in the design scratchpad. The goal panel's card list is unchanged except where section 7 says.

## Terms

- Gear template: one entry in a camera filter's `templates` list. It is ACP's record of a TS exposure template. It has a stable `id` (a UUID, never reused) that survives renames.
- Starred template: the gear template of a filter with `starred: true`. Exactly one per filter that has templates. A new plan row for the filter starts on it.
- Wheel slot name: the text NINA's profile holds for a filter wheel slot, for example "H".
- ACP filter name: the key of `cameras[].filters`, and the `filter` of a plan row, for example "Ha". The names ACP uses everywhere are the canonical ones.
- TS filtername: `exposuretemplate.filtername` in TS.
- Profile: a NINA profile, identified by its `profileId` (a GUID). TS keeps templates per profile.
- Primary profile: of the profiles linked to a camera, the one whose fingerprint has the newest `seen_at`.
- TS link: `template.ts[P]`, what ACP knows about a gear template's counterpart in profile P: the TS id, the guid and the base.
- Base: `template.ts[P].base`, the values the TS template held at the last push, pull or adoption. The guard in section 8 compares against it. It is the same idea as a plan's base snapshot in the goal panel spec section 5.
- Follows: a plan row follows its template when the row's `template_sub_s` equals the gear template's `sub_s`. A row that does not follow is deviating.
- Group: a set of TS templates in one profile that do the same job. Its key is `<ACP filter>/<class>`, for example `R/short`. Tidy picks one per group (section 10).
- Writers: the Python sync (`nina_ts_sync`) and the C# one (`Services/TargetScheduler/` in `ACP.NINA.Plugin`). They must produce identical TS rows, as before.
- Sync notes: the lines of the push result. The plugin shows them after Sync for tonight and Sync All to TS. The extension returns them as `notes`.

## 1. What the data says today

All figures are from the scratch copy of IMAGING-PC's database taken on 12 October (`schedulerdb.sqlite`, opened read-only) and `gear.json` beside it. The wheel slots in the NINA profile on IMAGING-PC were confirmed on 13 October as L, R, G, B, H, O, S.

- Profile `9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7` holds 20 templates. Ids 1 to 10 are Rohan's: L, R, G, B at 180 s, R Stars, G Stars, B Stars at 15 s, and H, O, S at 300 s. Ids 11 to 20 are ACP's copies at offset -1: OIII, Ha, SII at 300 s, B, G, L, R at 120 s named "B", "G (ZWO ASI6200MM Pro)", "L", "R (ZWO ASI6200MM Pro)", and three star templates at 15 s.
- Rohan said his originals use offset 25. Nine of the ten do. Id 3 (G, 180 s) has offset -1.
- The two sets differ in more than offset. Ids 1 to 4 have moon avoidance on (120 degrees, 14 days), twilight level 0, and dither every 2. Ids 14, 16, 18 and 19 have moon avoidance off, twilight level 1, humidity limit 100 and dither every -1. Ids 5 to 7 (stars) have dither every 0 and the copies 15, 17 and 20 have -1. Tidy has to show these (section 10), because keeping a copy silently drops Rohan's moon avoidance on those filters.
- Two names repeat: "B" is id 4 and id 14, and "L" is id 1 and id 18. A lookup by name returns the lowest id, so ACP's name-based lookup finds the originals while 26 and 11 plans sit on the copies.
- Ids 15, 17 and 20 have filtername "B Stars", "G Stars" and "R Stars". Those are not wheel filters. Each has 22 enabled exposure plans.
- Enabled exposure plans per template: ids 1 to 8 have 1 each, 9 and 10 have 0, then id 11 has 55, 12 has 23, 13 has 3, 14 has 26, 15 has 22, 16 has 26, 17 has 22, 18 has 11, 19 has 26, 20 has 22. The total is 244, which is every row in `exposureplan`.
- Every one of the 244 exposure plans carries an explicit `exposure` (none is -1). 89 of them differ from their template's default: ids 14, 16 and 19 each hold 23 plans at 180 s and 3 at 15 s on a 120 s template, and id 18 holds 11 plans at 180 s on a 120 s template. The rest equal their template.
- The nine 15 s plans on ids 14, 16 and 19 are star rows that sit on normal-class templates: plan ids 404, 405, 408, 409, 410, 413, 415, 416 and 419. Plans 404, 405, 408, 409, 410 and 413 each hold 18 acquired and 18 accepted frames.
- `gear.json` for the ASI6200MM Pro pins `ts_template_id` "15" to B, "12" to Ha, "4" to L, "13" to OIII and "14" to SII. Only Ha's is right (id 15 is "B Stars (ZWO...)", id 4 is a B template, id 13 is SII, id 14 is B). The pinned ids in gear are stale and the migration must not trust them.
- `gear.json` holds filters named "Antlia Ha" and "IDAS + RED" on the ASI294MM Pro. `_FILTER_NAME_RE` (`app.py:3355`) is `^[A-Za-z0-9_+\-]{1,32}$`, which rejects a space, so `POST /api/gear` returns 400 for the whole document today. Section 4 widens the rule.
- The second profile that appears in `plans.json` is `093effd2-29a5-4ce6-ae38-d6eea265a912`, on one plan. A camera can therefore meet more than one profile.
- Plan `plan-mouufrs7` (G7.7-3.7) pins exposure plan ids 21, 22 and 23 in its links. No such plans exist in TS.
- After every push the sync runs `refresh_template_subs` (`nina_ts_sync/rows.py:270`), which copies the sub recorded in the base (the plan's explicit `exposure`) back onto each row's `template_sub_s`. Section 8 changes its rule.

## 2. Three names for one filter

### What the code does today

- ACP plans, rows and gear use canonical names: Ha, OIII, SII, L, R, G, B. The alias table is `fr.ROW_FILTER_ALIASES` (`filter_rows.py:39`), a copy of `nina_ts_sync/filter_canon.py` and of `TsFilterNames.Canon` in the plugin. Keys match upper-cased, so "H" is Ha, "O" is OIII and "S" is SII.
- TS matches an exposure to a wheel slot by exact name (`TsFilterNames.cs` header comment). So the template's `filtername` has to be the wheel slot name.
- The plugin writes it that way. `TsConvert.cs:215-232` asks `TsFilterNames.ResolveToWheel(row.Filter, wheelFilters)` for the slot, writes the slot as `FilterName`, and lists the rename in the push result (`TsPushService.cs:72-75`). A filter with no slot is still pushed, with a note that TS will not run it (`TsPushService.cs:76-79`). The Python writer has no wheel list and writes `filtername=row["filter"]` (`convert.py:412`).
- The importer reads the other way: `row_filter(tpl.filtername)` turns "H" into Ha.

### The oddity, and what explains it

NINA's fingerprint was reported as listing B, G, Ha, L, OIII, R, SII, while TS templates and the frames TS saved say H, O, S. Both are true, and the two lists are not the same thing.

- `_normalise_fingerprint` (`app.py:4067`) stores `"filters": sorted({canon_filter(f) ...})`. It folds every slot through the scanner's `canon_filter` and sorts the result. So ACP's stored list is canonical and alphabetical, whatever the wheel says, and the slot order the plugin sent (`FingerprintMath.FilterNamesInSlotOrder`) is gone.
- Frames TS saved are tagged with the template's filtername: `acquiredimage.filtername` holds B 54, G 54, R 54, H 99, O 74, S 3. The newest H frame is 10 October 2026 (UTC). TS shot Ha because the wheel slot is named "H", and ACP's template 12 ("Ha") carries filtername "H".
- Templates 11, 12 and 13 were made by ACP's writer with names OIII, Ha, SII. Their filternames are O, H, S, which only the plugin's wheel rename writes.

Conclusion, confirmed from NINA's profile on IMAGING-PC on 13 October: the wheel slots are L, R, G, B, H, O, S. ACP's stored fingerprint hid them by folding, and nothing was shot wrongly.

### The rule this design uses

- ACP filter name: canonical, and what plans, rows and gear use. A filter typed into gear passes through `fr.known_filter`'s alias lookup, so typing "H" stores "Ha". A name the table does not know (for example "Antlia Ha") is kept as typed.
- Wheel slot name: whatever the profile says. ACP stores it raw, in slot order, in the fingerprint (section 5).
- TS filtername: the wheel slot name for that profile. It is never typed by the user. For ACP filter F on profile P with slots S, the slot is `resolve_to_wheel(F, S)`:
  1. the slot equal to F exactly,
  2. else the slot equal to F ignoring case and surrounding spaces,
  3. else the first slot whose alias-table spelling equals the alias-table spelling of F. A spelling the table does not know passes through unchanged.
  4. else none.
- The table used is `ROW_FILTER_ALIASES` (35 entries), never the scanner's `canon_filter`, because the scanner also strips maker names ("Antlia Ha" becomes Ha). The plugin's table does not, so using the scanner would let ACP say a filter is on the wheel when the plugin cannot find the slot.
- A writer writes a `filtername` only when the profile's slot list is known and not empty and `resolve_to_wheel` found a slot. In every other case it leaves the existing filtername alone and skips that field in the comparison with the base. A template that does not exist yet cannot be inserted without a slot (the column is not null), so it is not inserted and its rows are skipped with the note "wheel unknown" (section 5).
- A filter with no slot is not on the wheel of that profile. With an empty or unknown slot list the state is unknown.
- Reverse direction, for the importer and for Tidy: the ACP filter of a TS template is `fr.known_filter(filtername)`, which also reads "R Stars" as R.

The same function exists three times (ACP `filter_rows.py`, `nina_ts_sync/filter_canon.py`, `TsFilterNames.cs`) and one shared case file, `tests/fixtures/wheel_resolve_cases.json`, drives all three.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| W1 | ACP | `resolve_to_wheel(F, slots)` for slots `["L","R","G","B","H","O","S"]` with F = Ha, OIII, SII, R, "R Stars", "Antlia Ha" | "H", "O", "S", "R", none, none |
| W2 | ACP | slots `["Ha","H"]`, F = Ha. Then slots `["Ha"]`, F = "ha". Then slots `["Lum"]`, F = L. | "Ha", "Ha", "Lum" |
| W3 | Python, C# | The same file as W1 and W2 | Every case matches the ACP result |

## 3. Data model

### gear.json, version 3

```
{
  "version": 3,
  "rev": 17,
  "templates_migrated": 1,
  "telescopes": [ ... unchanged ... ],
  "telescope_colours": { ... unchanged ... },
  "tidy": { "<profile id>": { "state": "done" | "later", "at": "<ISO time>" } },
  "links_capable": { "<profile id>": { "templates": true, "repoint": true, "at": "<ISO time>" } },
  "cameras": [
    {
      "id": "zwo-asi6200mm-pro",
      "name": "ZWO ASI6200MM Pro",
      "pixel_size_um": 3.76,
      "sensor_px": [9576, 6388],
      "nina_names": ["ASI6200MM Pro"],
      "filters": {
        "Ha": { "templates": [ <gear template>, ... ] }
      }
    }
  ]
}
```

`rev` is an integer that goes up by one on every successful write to the file. `nina_names` is optional, extra NINA camera names the user linked to this camera (section 5). `links_capable` records which profiles have reported through a plugin that understands template links (section 9). A filter object holds `templates` only. The old per-filter keys (`default_sub_s`, `gain`, `offset`, `bin`, `ts_template_id`, `ts_template_name`) are not stored.

`load_gear()` returns the stored document with the derived keys added: the old per-filter scalars, each template's `status` and `used_by`, and each camera's `wheel` block. `save_gear()` strips every derived key before it writes, so no derived value is ever stored. `GET /api/gear` serves the result (section 4).

A gear template:

| Field | Type | Meaning |
|---|---|---|
| `id` | string, UUID | Stable. The server makes one for a template that arrives without. |
| `name` | string | The TS template name. Not empty, at most 64 characters, no control characters. Unique within the camera, compared ignoring case. |
| `sub_s` | number | The sub length in seconds, above 0 and at most 3600. Becomes `defaultexposure`. It is the default for new rows. A row's own `template_sub_s` can differ (section 4). |
| `gain` | integer | -1 means the camera's NINA setting. Otherwise 0 or more. |
| `offset` | integer | The same. |
| `bin` | integer | 1 to 4. |
| `starred` | boolean | See Terms. The server repairs the flag: if a filter has no starred template the first one is starred, and if several, the first. |
| `aliases` | list of strings | Earlier names from ACP renames, newest first, at most 8. Server-owned. Used to find this template's TS row after a rename and to match a plan row, a progress entry or an AAC row that still says the old name. |
| `ts_names` | list of strings | The names of the other TS templates in this template's group when adoption or Tidy linked it. Server-owned. Used only to map a plan row or a TS import that uses one of those names onto this template. Never used to look up a TS row. |
| `ts` | object | Server-owned. Keyed by profile id. |

`ts[P]`:

| Field | Meaning |
|---|---|
| `id` | `exposuretemplate.Id` in P. |
| `guid` | The guid on the TS row. |
| `pushed_iso` | When the base was last recorded. |
| `base` | `{name, filtername, defaultexposure, gain, offset, bin}`, the values TS held then. A value of 0 is kept as 0. |
| `repoint_from` | TS ids whose ACP-linked plans the next push moves onto this template (section 10). Cleared when the push reports it done. |

The client never writes `aliases`, `ts_names` or `ts`. On every save the server takes them from the stored template with the same `id`.

### Plan rows

A filter row gains `template_id`, the gear template's `id`. `template` stays and is kept equal to that template's name. `template_sub_s` keeps its goal panel meaning, and it is the sub TS actually shoots for the row's exposure plan. It is not "whatever the template says". Section 4 says how gear edits reach it.

A row whose `template_id` is missing or names no template of the plan's camera is repaired when the plan loads and when a plan is saved: `template_id` is found from `template` by name, then by `aliases`, then by `ts_names`, among the camera's templates on the row's filter. There is no fallback to the filter's starred template. A row nothing matches keeps its `template` name, gets `template_unresolved: true`, is logged by plan id, and the writers skip it with a note ("M42 R: template 'R Stars' is not in gear, row skipped"). Editing the row's template in the plan editor clears the flag.

`_validate_plan_payload` gains: a `template_id`, when present, must name a template of the plan's camera on the row's filter, and `template` must then equal its name.

### Fingerprints

`fingerprints.json` entries gain `profile_id` and `wheel_slots` (section 5) and are keyed by `profile_id` when the report has one.

### TS template list from the plugin

`POST /api/ts-templates` bodies gain, per template: `id` (`exposuretemplate.Id`), `guid`, and the fields Tidy compares: `moonavoidanceenabled`, `moonavoidanceseparation`, `moonavoidancewidth`, `twilightlevel`, `maximumhumidity`, `ditherevery`. The existing `filter` field is the TS filtername. They also gain `plans`, a list of `{"id": <exposureplan.Id>, "exposure": <exposureplan.exposure>}` for every exposure plan using the template, enabled or not, at most 5000 per template. ACP stores them in `data/ts_templates.json` beside the existing fields. A list whose templates carry no `id` comes from an older plugin. It still feeds the plan editor as before, but it never triggers adoption or Tidy.

## 4. The gear API and stale pages

### GET /api/gear

Returns `version`, `rev`, `telescopes`, `telescope_colours`, `tidy`, `cameras`, and two lists, `wheel_unlinked` and `wheel_ambiguous` (section 5). Each camera has a read-only `wheel` block. Each template has read-only `status`, `conflicts` and `used_by` (the count of plan rows using it, over every plan in every state). `status` is computed for the camera's primary profile:

| `status` | When |
|---|---|
| `new` | the template has no TS link for the primary profile, or the filter is on the wheel and the link's TS row is missing |
| `waiting` | the filter is `not_on_wheel` for the primary profile, so the sync skips it |
| `conflict` | some synced field has A different from B and TS's value (from the last list) different from both. `conflicts` lists `{field, acp, ts}`. |
| `changed` | some synced field has A different from B, and no conflict |
| `in_ts` | no field differs from the base |

For older readers each filter object also carries `default_sub_s`, `gain`, `offset`, `bin`, `ts_template_name` and `ts_template_id` from its starred template. `ts_template_id` is the primary profile's TS id as a string, or null.

### POST /api/gear

Body: `{"gear_schema": 3, "rev": 16, "telescopes": [...], "cameras": [...]}`.

- If `rev` is not the stored `rev`, the answer is 409 with the stored document, and nothing is written. The editor tells the user the gear changed elsewhere and reloads. The same check protects telescopes, which the page also posts.
- A body with no `gear_schema` is from a page loaded before this shipped. The server keeps every stored template, link and alias. It applies telescope fields and each existing camera's `name`, `pixel_size_um` and `sensor_px`. It ignores `filters` entirely. It adds a camera it does not know, with one default template per filter (name "{filter} ({camera name})", sub from `default_sub_s`, else 300, starred). It never deletes a camera. The answer is 200 with `"ignored": ["filters"]`, and the log carries a warning with the camera ids. Reason: a stale page sends every filter's old scalars on every save, so applying them would revert edits made on a newer page. This is the rule PR 114 and 115 set for plans, applied to gear.
- Filter names must match `^[A-Za-z0-9_+.\- ]{1,32}$` and have no leading or trailing space. This replaces `_FILTER_NAME_RE` so that "Antlia Ha" and "IDAS + RED" save.
- A save is refused with 400 when it would remove a template, or a filter holding one, that a plan row uses (`template_id`, or `template` and `filter` for a row not yet repaired). The error lists the plan ids and the row counts. It is refused when two templates of one camera share a name, when a name is empty, when `sub_s`, `gain`, `offset` or `bin` is out of range, and when a camera has a filter with no template.
- A rename to a name that a TS template of a profile linked to the camera already holds, where that TS template is not this gear template's own link, is refused with 400 naming the profile and the TS id. The check reads the last list the plugin posted.
- A rename, or a change of `sub_s`, on a template of a camera that is linked to a profile is refused with 400 ("The NINA plugin on this rig has not reported that it can update templates. Install the new plugin first.") until `links_capable` holds an entry for at least one profile that reports the camera. An entry counts only when it lists both `templates` and `repoint`. The same capable entry is the one rule for adoption, Tidy, renames and sub changes. A camera counts as reported when a fingerprint matches it by name (`_norm_gear_name`), whether the report carries a `profile_id` or only a `profile_name`, or when any of its templates has a `ts` link. A camera that no fingerprint matches and that has no link is not restricted. So an older plugin, which sends no `profile_id` and never reports `links_capable`, still trips the guard. Reason: an older plugin looks templates up by name and would insert a duplicate after a rename.
- Removing a template nothing uses is allowed. Its TS counterpart is never deleted. The push result lists TS templates with no gear counterpart in the profile's linked cameras ("stray" templates, as the goal panel spec section 3 already does).

What a save carries into plans, in the same request:

- Rename: every row with that `template_id` gets the new `template`. The old name goes to the front of `aliases`.
- Sub change from old to new: only the rows that follow the template (their `template_sub_s` equals the old `sub_s`) get the new `template_sub_s`. An hours row keeps its `target_hours`, so its frame count changes. A frames row keeps its `target_frames`. If the sub crosses 60 s, the row changes unit and keeps the hours it stood for, as `setRowTemplate` does (`filter-rows.mjs`). A deviating row keeps its own sub. The response carries `sub_changes`: the rows changed, and for each the frames already acquired at the old sub, so the editor can name them. The editor shows a "differs from template" flag on every deviating row (section 7).
- Moving a template to another filter is not allowed. It is a remove and an add.

Locks and order (see also the callers list below). A gear write takes `GEAR_LOCK` (new, re-entrant), then `PLANS_LOCK` when it touches plans. The plans file is written first, then the gear file. If the second write fails, the plans hold names that the gear file lacks, and the row repair fixes them on the next load because `template_id` is authoritative.

The gear migration (section 11) runs once at startup, before the app serves requests. `load_gear()` takes no lock and only reads the cache, which a writer replaces whole after an atomic file write. `GEAR_LOCK` guards gear writes only. Code that holds `PLANS_LOCK` never takes `GEAR_LOCK`. Every current caller of `load_gear()` is safe on that basis: `app.py:740` (inside the plans migration, under `PLANS_LOCK`), `:3522` (plan validation, under `PLANS_LOCK`), `:4169` (`api_plans_match`, which does not hold `PLANS_LOCK`) and the other read-only callers at `:3378`, `:3653`, `:4438` and `:5073` read a snapshot and never wait. The writers at `:1696` (the seed route, which reads then writes), `:3402` (this route) and `:3432` (the telescope colour route) take `GEAR_LOCK` and bump `rev`. The migration function also takes `GEAR_LOCK` and re-checks `templates_migrated` once inside it, so a second caller (a test, or the extension loading early) cannot run it twice.

### Single-template and link routes

Each takes `GEAR_LOCK`, bumps `rev`, and returns the new `rev`:

- `POST /api/gear/templates` with `{camera_id, filter, name, sub_s, gain, offset, bin}` adds a template (not starred unless the filter has none). The plan editor's "New template…" uses it.
- `POST /api/gear/templates/<id>/star` stars it and unstars the filter's others.
- `POST /api/gear/templates/<id>/resolve` with `{profile_id, field, take}` settles one held conflict. `take: "acp"` sets that field's base to TS's value, so the next push writes ACP's value. `take: "ts"` sets the gear field and the base to TS's value, and carries a changed sub into rows as a sub change does.
- `POST /api/gear/cameras/<id>/nina-names` with `{name}` appends to the camera's `nina_names` (section 5).

The links route (`POST /api/ext/nina-ts-sync/links`) and the end of the extension's sync route call `app.record_template_results(profile_id, results)`, an export that stores the writer's per-template results into `ts[P]` under `GEAR_LOCK`. It never touches `links_capable`. Only the links route does: it sets `links_capable[P]` from the post's `capabilities`, and a post whose `capabilities` lacks `templates` or `repoint` (an older plugin, or no key) deletes `links_capable[P]`, so a downgraded plugin loses the capability at once. A Python sync therefore leaves `links_capable[P]` as the last links post set it. `tests/host.py` fakes the export.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| G1 | ACP | `GET /api/gear` on a v3 file where Ha has templates "Ha" (300, starred) and "Ha 600" (600) | The Ha filter object carries `default_sub_s` 300, `ts_template_name` "Ha", and `templates` with both. |
| G2 | ACP | `POST` with `rev` one behind | 409, the body holds the stored document, the file is unchanged, `rev` is unchanged. |
| G3 | ACP | A linked camera with a links-capable profile. `POST` with `gear_schema` 3 that renames "R (ZWO ASI6200MM Pro)" to "R 120", on a plan set with 26 rows on it | Rows hold `template` "R 120". `aliases` starts with "R (ZWO ASI6200MM Pro)". `rev` is one higher. |
| G4 | ACP | `POST` that deletes "R Stars" while 23 rows use it | 400 naming the plan ids and 23. Nothing is written. |
| G5 | ACP | `POST` that deletes "Ha 600" which no row uses | 200. The template is gone. |
| G6 | ACP | `POST` with two templates "Ha" and "ha" on one camera | 400 naming the duplicate. |
| G7 | ACP | 23 Ha rows with `template_sub_s` 300 and `target_hours` 3 on "Ha" (300 s). Change "Ha" to 600 s and save. | The 23 rows keep 3 h and get `template_sub_s` 600. `desired` goes from 36 to 18 in the writers. `sub_changes` lists them. |
| G8 | ACP | A row with 20 frames at `template_sub_s` 15 on "R Stars" (15 s). Change the sub to 90. | The row becomes unit "h" with `target_hours` 0.0833 and `template_sub_s` 90. |
| G9 | ACP | A body with no `gear_schema`, with `filters` `{"Ha": {"default_sub_s": 600}}` and no templates, on a stored file whose Ha template is 300 s with a TS link | 200 and `ignored` holds "filters". The Ha template is still 300 s, still linked, and every other template is untouched. |
| G10 | ACP | The same old-style body with a camera missing from `cameras` | The camera and all its templates remain. |
| G11 | ACP | Save a camera with filters "Antlia Ha" and "IDAS + RED" | 200. (Today it is 400.) |
| G12 | ACP | A `POST` whose template carries `ts`, `aliases` and `ts_names` that differ from the stored ones | The stored values win. |
| G13 | ACP | A plan row whose `template_id` names a template of another camera | 400 on the plan save. |
| G14 | ACP | A `POST /api/gear/templates` with a name that exists on the camera | 400. |
| G15 | ACP | Load a plans file whose row has `template` "Ha" and no `template_id`. Then `PUT` a plan whose rows carry `template` only. | In both cases the row gets the id of "Ha". A row naming "H" gets the id of the template with "H" in `ts_names`. |
| G16 | ACP | The camera has no gear template named "OIII" on Ha's filter or elsewhere, so the duplicate-name check does not apply. Rename gear "Ha" to "OIII" while the posted list for the camera's profile holds id 11 named "OIII". | 400 from the TS name check, naming the profile and TS id 11 (not the duplicate-name 400 of G6). |
| G17 | ACP | Two threads call the migration at once on a version 2 file | One set of template UUIDs is written, `rev` is 1, and one backup exists. |
| G18 | ACP | The seed route (`/api/gear/seed`) adds a camera found in the manifest | The camera is in v3 shape with a starred template per filter, `rev` is one higher, and existing cameras' templates and links are untouched. |
| G19 | ACP | `POST /api/gear/telescope-colour` on a v3 file | `rev` is one higher and every template and link is unchanged. |
| G20 | ACP | `load_gear()` on a v3 file, then `save_gear()` of the result | The derived keys (`default_sub_s`, `status`, `used_by`, `wheel`) are present on load and absent in the written file. |
| G21 | ACP | Rename a template of a camera that only a name-only fingerprint (no `profile_id`, as an older plugin sends) matches, with no `links_capable` entry. Then the same rename on a camera no fingerprint matches and with no `ts` links. | The first is 400 with the plugin message. The second is 200. |
| G22 | ACP | Template "R (ZWO ASI6200MM Pro)" (id 19, 120 s) has 26 rows whose `template_sub_s` is 180 (23) or 15 (3). Change it to 150. | No row changes. `sub_changes` is empty. The editor data marks 26 rows as differing. The writers later send `defaultexposure` 150 and write no plan `exposure`. |
| G23 | ACP | A `POST .../resolve` with `take: "ts"` for a conflicted sub | The gear sub and base equal TS's value, and following rows change as in G7. |
| G24 | ACP | A `POST .../resolve` with `take: "acp"` | The base for that field equals TS's value, and status is `changed`. |
| G25 | ACP | A plan row naming "Q Stars", which no template, alias or `ts_names` entry matches | `template_unresolved` is true, the row's `template` is unchanged, and no starred template is assigned. |
| G26 | ACP | `links_capable[P]` is set. A links post arrives with no `capabilities`. Then a rename of a linked template. | `links_capable[P]` is gone and the rename is refused with 400. |

## 5. The wheel, and linking a camera to a profile

### What the plugin sends

The fingerprint (`/api/plans/match`) gains two fields:

- `profile_id`, the NINA profile's id. Today it sends `profile_name` only, and TS links are keyed by profile id, so the two could not be joined.
- `wheel_slots`, the slot names from the profile in slot order (`FilterWheelSettings.FilterWheelFilters`), whether or not the wheel is connected. `filters` stays as it is: `ReadFilters` returns an empty list when the wheel is not connected (`GearFingerprintService.cs:115`), which ACP reads as OSC for a colour camera. The push already reads the profile for the same reason (`SyncForTonightRunner.cs:208-212`).

`_validate_fingerprint` accepts `wheel_slots` as a list of at most 64 strings of at most 32 characters, and `profile_id` as a string of at most 64 characters. `_normalise_fingerprint` and `_store_fingerprint` keep `wheel_slots` raw and in order, and `profile_id`. The stored entry's `filters` stays canonical and sorted.

One storage rule. A report with a `profile_id` is stored under that id, and it replaces any entry stored under the same `profile_name` with no id. A report with no `profile_id` (an older plugin) is stored under its name, as today, and is never linked to a TS profile, because without the id there is nothing to link it by. It still counts as reporting the camera for the rename and sub guard (section 4).

The plugin must send a fingerprint on each Sync for tonight and Sync All to TS. Sync for tonight already does (`SyncForTonightRunner.cs:116-141`). Sync All to TS builds only the wheel list for the push (`AcpDockableVM.cs:897-903`) and I found no match call on that path, so it sends the fingerprint first (section 9).

### Linking a camera to a profile

A camera is linked to a profile when the profile's latest fingerprint names a camera that matches it:

- `_norm_gear_name(fingerprint.camera.name)` (`app.py:1577`) equals `_norm_gear_name` of the gear camera's `name` or of any of its `nina_names`.
- If two gear cameras match one fingerprint, neither links. `GET /api/gear` lists it in `wheel_ambiguous` as `{profile_id, profile_name, camera_name, camera_ids}`.
- A fingerprint camera that matches no gear camera is listed in `wheel_unlinked` as `{profile_id, profile_name, camera_name}`. The Cameras tab shows it at the top as "NINA profile 'Foo' reports 'ASI Camera (1)', which is not in gear." with two buttons: "Link to…" (pick a camera, which calls `POST /api/gear/cameras/<id>/nina-names` with `{name}`) and "Add as camera".

A camera can link to several profiles. A profile links to the camera of its latest fingerprint, so after a camera swap in one profile the profile moves to the new camera at the next sync.

### The wheel block of a camera

`GET /api/gear` computes, per camera:

```
"wheel": {
  "profiles": [ {"profile_id", "profile_name", "seen_at", "slots": ["L","R","G","B","H","O","S"], "primary": true} ],
  "filters": { "Ha": {"state": "on_wheel", "slots": {"<profile id>": "H"}} }
}
```

`state` per filter is `on_wheel` when at least one linked profile has a slot for it (by `resolve_to_wheel`), `partial` when some linked profile has it and some do not, `not_on_wheel` when profiles with slots exist and none has it, and `unknown` when no linked profile has any slots.

Several profiles per camera:

- The editor shows one note per filter. If every profile agrees on the slot it reads "wheel slot H". If they differ it reads "H in Foo, Ha in Bar". For `partial` it reads "not on Bar's wheel yet".
- A template's `status` (section 4) is judged against the primary profile only. If a filter is on the wheel of a secondary profile but not the primary, its templates read `waiting`, and the note names the profiles where it is on the wheel. A push to the secondary profile still writes it, because the writer judges each profile by its own slots.
- Templates are per profile in TS, so each gear template has a separate TS link per profile (section 3). The sync writes only to the profile it runs against, using that profile's slots, and records only that profile's link.
- Gear values come from the primary profile only. Adoption (section 11) links a secondary profile's TS templates without taking their values. The base it records for them is the current gear values, so the first push writes nothing, and the sync notes list any difference ("Bar: Ha offset is 10 in TS and -1 in gear"). The pull (section 8) also applies to the primary profile only.
- Two gear cameras linked to one profile can both want a template of the same name. The sync inserts the first (in gear order) and skips the second, with the note "Template 'Ha' of ASI2600MM Pro was not created in profile Foo, because ASI6200MM Pro's 'Ha' already uses that name."

### Not on the wheel yet

A filter can be added to gear at any time. Its templates exist in ACP, plans can use the filter, and the plan editor marks it "not on the wheel yet". For a filter in state `not_on_wheel` for the profile being pushed:

- The writers do not create or update its templates and do not push its rows. They do not disable existing exposure plans of those rows or touch their `desired`.
- The push result names them: "ZWO ASI6200MM Pro: SII is not on the wheel of Foo yet, so 3 rows were skipped: Antlia SII, M42 SII, M43 SII."
- A filter in `unknown` state (no slots known) is not inserted either, by the rule in section 2. The note reads "ZWO ASI6200MM Pro: the wheel of Foo is unknown, so 3 rows were skipped". Existing templates and plans are updated as usual, except for the filtername.
- Nothing needs flagging for them to start. When a later fingerprint puts a slot on the wheel for the filter, the next push inserts its templates and the rows.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| F1 | ACP | `POST /api/plans/match` with `filters` `["L","R","G","B","H","O","S"]`, `wheel_slots` the same, `profile_id` "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7", camera "ZWO ASI6200MM Pro" | The stored entry has `filters` `["B","G","Ha","L","OIII","R","SII"]`, `wheel_slots` `["L","R","G","B","H","O","S"]` and `profile_id`. The entry key is the profile id. |
| F2 | ACP | F1, then a report with `wheel_slots` over 64 entries, then one with a 40 character slot | 400 for each. |
| F3 | ACP | F1, then a report with no `profile_id` and the same `profile_name` | It is stored under its own name key. The id-keyed entry is unchanged. `GET /api/gear` links the camera through the id-keyed entry only. |
| F4 | ACP | A no-id report stored under "Foo", then a report for "Foo" with a `profile_id` | The name-keyed entry is gone and the id-keyed entry holds the report. |
| W4 | ACP | After F1, `GET /api/gear` for the 6200 camera, which has filters L, R, G, B, Ha, OIII, SII, "V" | `wheel.filters.Ha` is `on_wheel` with slot "H". `V` is `not_on_wheel`. |
| W5 | ACP | Fingerprints for two profiles on the 6200, one with slots `[..."H"...]` and one with `[..."Ha"...]` | Ha is `on_wheel`, slots `{P1: "H", P2: "Ha"}`. |
| W6 | ACP | Two profiles, one with slot "S", one without | SII is `partial`. |
| W7 | ACP | A camera with no matching fingerprint | Every filter is `unknown`, and no template has status `waiting`. |
| W8 | ACP | A fingerprint camera "ASI Camera (1)" and no gear camera that matches | `wheel_unlinked` lists it. After `POST /api/gear/cameras/<id>/nina-names` with `{"name": "ASI Camera (1)"}`, the camera's `nina_names` holds it, `rev` is one higher, and the next `GET` shows the link and no `wheel_unlinked` entry. |
| W9 | ACP | A fingerprint whose camera matches both ASI2600MM Pro and a second camera whose normalised name is equal | Neither links, and `wheel_ambiguous` names both camera ids. |
| W10 | ACP | The 6200 linked to P1 (primary, no slot for SII) and P2 (slot "S"). Template "SII" exists. | SII is `partial`. The template's status is `waiting`, and its note names P2. |
| W11 | ACP | A fingerprint for P1 and a newer one for P2, both on the 6200 | P2 is marked `primary`. |

## 6. The gear editor

`renderGearEditor()` (`static/app.js:7776`) is rewritten to the Main and Telescopes artboards. The shared parts of the page keep their names. The side panel's default width becomes 420 px (`SIDE_PANEL_DEFAULT_W` in `static/filter-rows.mjs`, and the matching CSS). Its 300 to 760 px range stays. The goal panel spec's test U1 changes from 380 to 420.

### Frame

A header with "← Plans", the title "Gear" and a Save button. Two tabs, "Telescopes · n" and "Cameras · n". Save sends the whole document with `gear_schema` 3 and the `rev` the page loaded. On 409 the editor says "Gear changed elsewhere. Reload to see it." and offers Reload, which discards the draft after asking. A successful save refreshes `rev`.

Cancel and the back link discard the draft, as now.

### Cameras tab

Cameras are cards. One is open at a time, the rest are one-line buttons with a summary ("7 filters · 10 templates"). An open card has:

- Name field, an actions menu ("Duplicate", "Link to NINA camera", "Remove"), pixel size, width, height, and the line "On the 110GTX: 1.41″ per pixel, field 3.75° × 2.50°" using the telescope's usual camera, as the mockup shows. Remove is refused while a plan uses the camera.
- A heading "Filters and templates" and the legend "★ = used for new plans".
- One box per filter, in the wheel slot order of the primary profile (each slot mapped to its gear filter), then the filters not on the wheel alphabetically. With no slots known the order is L, R, G, B, Ha, OIII, SII, then the rest alphabetically. Each box's header shows the filter name, the wheel note (section 5), and a "+ Template" button. A filter with state `not_on_wheel` also carries the pill "Not on the wheel yet". `partial` carries "Not on every wheel".
- Under the header, one line per template: star, name (truncating), "300 s", and a status pill: "In TS" (green), "New" (amber), "Changed" (blue), "Waiting" (grey), "Conflict" (red).
- Tapping a line opens its editor: name, sub, gain, offset, bin, the checkbox "Use for new plans with this filter", a note, and the buttons Duplicate and Remove. Remove is disabled while `used_by` is above 0, and the note says "Used by 23 plans, so it cannot be removed."
- The note reads, by status: "Matches TS. Edits here update the TS template on the next sync." (`in_ts`), "Offset changed from Default to 25 here. The next sync updates this template in TS, for every plan that uses it." (`changed`, naming the changed fields), "Not in TS yet. The next sync creates it, and plans can pick it now." (`new`), "The filter is not on the wheel yet. The sync skips it and says so in its notes." (`waiting`).
- For `conflict` the note reads "Offset is 25 here and 10 in TS, and TS changed it after the last sync. The sync leaves it alone until you choose." with two buttons per conflicted field, "Keep ACP's (25)" and "Take TS's (10)". They call the resolve route.
- Conflicts are surfaced outside the editor too, so the user does not have to go looking. `GET /api/gear` adds `conflict_count` (the number of held fields across all templates and profiles). The Gear button on the plans page shows a red badge with that count when it is above 0, and its tooltip reads "2 template settings differ between ACP and TS. Open Gear to choose." The plans page reads the count on load and after each sync.
- When the sub field is edited, the editor lists the rows that would change and the rows that already have frames at the old sub, from the plans it holds: "3 rows have frames at 300 s: M42 Ha 24 fr, M43 Ha 8 fr, M44 Ha 30 fr. Their goals stay in hours, so their frame goals change." A deviating row is listed as "keeps its own 180 s".
- "Default" is shown for gain or offset -1. The field accepts "Default" or a whole number.
- A "+ Add filter" button at the bottom. It offers the filters on the wheel but not in gear first (from `wheel.profiles[].slots`, shown by ACP name), then a free-text name. A typed name goes through the alias lookup. Adding a filter creates one starred template named "{filter}" with sub 300 for the narrow filters (Ha, OIII, SII, and any name the alias table maps to them) and 120 otherwise.
- A banner above the filters when Tidy applies (section 10): "TS has 20 templates for this camera, and most filters have two. Tidy…". It shows until the profile's `tidy.state` is "done" or "later".
- A one-line note under the same rule as the server guard in section 4: it shows when a fingerprint matches the camera by name (with or without a `profile_id`) or a template has a `ts` link, and no profile for the camera has a capable `links_capable` entry. "The NINA plugin on this rig does not update TS templates yet. Renaming and sub changes are off until it does."

Exactly one star per filter. Ticking "Use for new plans" on another template moves the star. Unticking the starred one is not possible (the checkbox is disabled on it), which keeps the invariant in the browser as the server keeps it in the file.

The editor shows the wheel and status data as of load. Pressing Save after a sync that changed statuses is fine: statuses are server-computed and not part of the save.

### Telescopes tab

Restyled to the Telescopes artboard: a colour well, name, focal length, aperture and usual camera (`default_camera_id`), one open at a time, with the f ratio and the field with the usual camera. Colour is still saved at once through `/api/gear/telescope-colour`. The summary line shows "aperture missing" or "focal length missing" in amber. No new telescope fields.

### Tests (ACP, `tests/frontend`)

The fixture is the gear after M3: the 6200 camera with the ten templates, the 20-template list, slots `["L","R","G","B","H","O","S"]`, and the other seven cameras from the live `gear.json`.

| Id | Inputs | Expected |
|---|---|---|
| U1 | Open the editor on the fixture | The panel is 420 px. The tabs read "Telescopes · 8" and "Cameras · 8". The 6200 card shows filter boxes in the order L, R, G, B, Ha, OIII, SII, with 1, 2, 2, 2, 1, 1, 1 template lines (slot order L, R, G, B, H, O, S), and the wheel notes "wheel slot L", "R", "G", "B", "H", "O", "S". |
| U2 | Open template "R Stars" in R | The fields show name "R Stars", sub 15, offset 25. Remove is disabled with "Used by 23 plans, so it cannot be removed." |
| U3 | Tick "Use for new plans" on "Ha 600" | The star moves from "Ha" to "Ha 600". The checkbox on "Ha 600" is disabled. |
| U4 | Click "+ Template" on Ha, name "Ha 600", sub 600 | A line appears with status "New" and no star. |
| U5 | State `not_on_wheel` for V | The V box carries "Not on the wheel yet". Its templates show "Waiting". |
| U6 | The 409 answer | The editor shows the message and offers Reload. The draft is kept until Reload is pressed. |
| U7 | Add filter "H" | The filter is stored as "Ha". |
| U8 | Set a gain field to "Default" | The draft holds -1. |
| U9 | Width 420 and 300 px, template name 60 characters | Four columns of the template line stay aligned, the name truncates, and the panel does not scroll sideways. |
| U10 | A template with status `conflict` on offset (25 here, 10 in TS) | The pill reads "Conflict". The note shows both values. "Keep ACP's (25)" and "Take TS's (10)" call the resolve route with `take` "acp" and "ts". |
| U11 | Edit "Ha" from 300 to 600 where three rows have frames at 300 s and one row deviates at 180 s | The editor lists the three rows with their frame counts and the deviating row as keeping its own 180 s. |
| U12 | The camera is linked to a profile not in `links_capable` | The one-line plugin note shows, and the name and sub fields of its templates are disabled. |
| U14 | The camera is matched only by a name-only fingerprint (no `profile_id`), with no `links_capable` entry. Then the same camera after a capable links post for another profile that reports it. | The note shows and the fields are disabled. After the capable post the note is gone and the fields are enabled. |
| U15 | `conflict_count` 2, then 0 after both are resolved and the plans page reloads the count | The Gear button shows a red badge "2" with the tooltip, then no badge. |
| U13 | Add filter "V" (not on the wheel) and "Antlia Ha" to the fixture camera | The boxes read L, R, G, B, Ha, OIII, SII (slot order), then "Antlia Ha", then V (alphabetical). |

## 7. The plan editor

The goal panel's card list is unchanged except for the template drop-down and "+ Add filter".

- The template drop-down for a row lists the camera's gear templates for the row's filter, in the gear order, each as "R Stars · 15 s", the starred one marked "★". It no longer lists templates from the plugin's TS list. The last option is still "New template…", which asks for a name and a sub and calls `POST /api/gear/templates` (the new template is unstarred, and the sync creates it in TS).
- Choosing a template sets `template`, `template_id` and `template_sub_s` through `setRowTemplate` (`filter-rows.mjs`), with the same unit rules as now.
- "+ Add filter" starts the new row on the filter's starred template, replacing `usualTemplate`'s lookup of the camera's `ts_template_name`.
- A row whose `template_sub_s` differs from its template's `sub_s` shows "differs from template" beside the sub, for example "180 s, template says 120 s". It is information, not an error.
- A row with `template_unresolved` shows "template not in gear" in amber and its drop-down offers the camera's templates for the filter.
- The filter drop-down marks filters not on the wheel: "SII · not on the wheel yet". They stay selectable.
- A row on a filter that is not on the wheel shows "Not synced until SII is on the wheel" under its progress block. It adds no state to the plan.
- Progress from the plugin and AAC's `/accepted` match a row by filter and template name exactly (goal panel spec sections 3 and 6). They now also accept the template's `aliases` and `ts_names`, so a rename between AAC reading the plans and writing back does not drop the AAC block.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| P1 | ACP, `tests/frontend` | A plan on the 6200 with a row on R. Open the template drop-down. | Options "R (ZWO ASI6200MM Pro) · 120 s ★", "R Stars · 15 s", "New template…". |
| P2 | ACP, `tests/frontend` | Press "+ Add filter" on a plan with no rows, where L's starred template is "L" (120 s) | Row 1 is L, template "L", `template_id` of that template, sub 120, unit "h", goal 0. |
| P3 | ACP, `tests/frontend` | Pick "R Stars" on an R row with 2 h | Unit "fr", `target_frames` 480, `template_id` the R Stars id. |
| P4 | ACP | `POST /api/plans/<id>/progress` for a row renamed from "Ha" to "Ha 300", with the entry still saying "Ha" | The row is updated through the alias. |
| P5 | ACP | `POST /api/plans/<id>/accepted` the same way | The row's `aac` block is stored, and `unknown_rows` is empty. |
| P6 | ACP, `tests/frontend` | A row on "R (ZWO ASI6200MM Pro)" (120 s) with `template_sub_s` 180 | The row shows "180 s, template says 120 s". |
| P7 | ACP, `tests/frontend` | A row with `template_unresolved` | "template not in gear" shows, and the drop-down lists "R (ZWO ASI6200MM Pro)" and "R Stars". |

## 8. What the writers do

### Inputs

Both writers receive, besides the plans, `payload.gear_templates`: for each camera of a pushed plan, a list of dicts with `id`, `camera_id`, `name`, `filter`, `sub_s`, `gain`, `offset`, `bin`, `starred`, `aliases`, `ts_names` and the `ts` entry for the profile being pushed. They also receive `payload.wheel_slots`, the profile's slot names. The plugin reads them from the NINA profile. The Python route `POST /api/ext/nina-ts-sync/sync` accepts `wheel_slots` in its body. When the body has none, the extension reads the profile's `wheel_slots` from ACP's fingerprint store through a new host import, `load_fingerprints`, and with no entry the list is empty. `tests/host.py` fakes the import. The writers hold the TS database, so they read the profile's templates at push time and do not depend on the template list ACP last received.

### Deciding what to write for one gear template

For each gear template T of a pushed plan's camera, with ACP filter F:

1. Skip it, with the note from section 5, when F is `not_on_wheel` for this profile's slots. With no slots known, an existing TS row is updated except its filtername, and a missing one is not inserted.
2. Find its TS row, in this order: the row with `Id` equal to `T.ts[P].id` in profile P; the row with the guid `T.ts[P].guid`; then a row of P named `T.name` or one of `T.aliases`, when exactly one such row exists, no other gear template of the camera has claimed it, and it belongs to T's group (the same `known_filter` and sub class). A name shared by two TS rows is never linked by name. Such a template waits for adoption (section 11). Names in `ts_names` are never used to find a TS row. If no row is found, insert (below).
3. For an existing row, decide each field by comparing three values: ACP's value A (from T; for the filtername, the slot), the base value B, and TS's value now C. The fields are name, filtername, defaultexposure (A is `sub_s`), gain, offset and bin.

| Case | The writer |
|---|---|
| T has no `ts[P]` base (found by name, never linked) | Writes nothing. Records the TS values as the base. Adds the note "Ha 600 already exists in profile Foo, so ACP took its values." For the camera's primary profile it also sets T's fields to those values. For any other profile it records the link with the base equal to the current gear values (so nothing is written) and the note lists the differences (section 5). |
| A equals B | Writes nothing. TS may have changed by hand. ACP picks it up through the pull below. |
| A differs from B and C equals B | Writes A. |
| A differs from B and C equals A | Writes nothing. The base becomes C. |
| A differs from B and C differs from both | Conflict. Writes nothing for that field and adds the note "Ha: offset is 25 in ACP and 10 in TS. Left alone until you choose in the gear editor." The other fields are decided on their own. |

The conflict rule is hold. When the user has changed a field in ACP and TS changed it too since the last sync, neither side is right by default. ACP owns the template, so the gear editor offers "Keep ACP's" (the next push writes it) and "Take TS's" (gear adopts it), but the sync never silently discards a deliberate TS edit. This keeps the filter-order rule's spirit (an edit is written only when made in ACP) without its overwrite on conflict.

Two special cases:

- A row found through an alias takes the alias as its base name, so a rename is still written and not undone by the adopt-the-TS-values case. A name write is skipped, with a note, when another template of P already has that name. The lookup stays on the TS id.
- A filtername is written only under the rule in section 2. When the slot name differs from the base it is decided like any other field. This repairs the off-wheel names on ids 15, 17 and 20 if ACP keeps one of them. A filtername is never edited by the user.

An insert writes `name`, `filtername` (the slot), `defaultexposure`, gain, offset, bin, and the guid `stable_guid("{profile}/gear-template/{template id}")` with the free-text parts length-prefixed as in `schema.py:name_part`. The other columns keep the writer's defaults (`schema.py:ExposureTemplate`). The writer creates a template when its filter is on the wheel, whether or not any plan uses it yet, because the mockup says "The next sync creates it".

After the transaction the writer returns, per gear template, `{template_id, ts_id, guid, base, action, fields_written, note}`. `base` holds the values TS has after the push. The base must keep a gain or offset of 0 as 0: `state.py:_template_entry` (`int(tpl.gain or -1)`) turns 0 into -1 today, and is fixed as part of this.

### The pull

When `POST /api/ts-templates` arrives for profile P, ACP applies the new list, under `GEAR_LOCK`, to every gear template linked to P whose camera has P as its primary profile:

- For each synced field where A equals B and TS's value now differs, ACP sets A and B to TS's value and adds the note "Changed in TS: Ha offset 25".
- A changed default sub in TS updates the template's `sub_s`, and only the rows that follow the template and whose TS plan has `exposure` -1 take it. Rows whose TS plan has an explicit `exposure` are not touched. The list's `plans` entries carry `exposure`, which is how ACP knows.
- A field where A differs from B is left alone. The push decides it.
- If the TS row is gone (no id, no guid, no unique name match), the template's status is `new`, and the next push inserts it again.
- A profile that is not the camera's primary gets a note listing differences and nothing else.

The writers do not depend on the pull. They apply the table above to TS as it is, so the push is correct even when the post failed or arrived late.

### Renaming

Rename carries by TS id. Gear holds a stable `id`, and `ts[P].id` pins the TS row. The push finds the row by id, sees A (new name) differs from B (old name), sees C equals B, and writes the new name to the same row. No second template is inserted. The old name goes to `aliases`.

Exposure plans identify themselves by a guid that includes the template name (goal panel spec "Exposure plan identity"). After a rename the new guid finds nothing, and that spec's second lookup, the exposure plan id pinned in `ts_links` for the row id, finds the existing plan and restamps it with the new guid. `acquired`, `accepted` and `exposure` stay. A pinned exposure plan id that names no plan in TS (plan-mouufrs7 pins 21, 22 and 23) is ignored and the lookup falls through to the next step.

### Which template a plan is on

A plan moves to another template only when ACP changed which template the row uses. The base entry of an exposure plan gains `template_id`. The writer compares the row's `template_id` with it:

- Different: the plan is repointed at the row's template's TS id, as the goal panel spec already does for a template change. Its `exposure` follows the sub rules below, with -1 when the row follows the new template.
- Equal: the plan keeps the template it has, even if TS has moved it by hand.
- The base entry has no `template_id` (a base from before this spec): the row counts as unchanged when the base's template name is the row's template's name, one of its `aliases` or `ts_names`, or `ts[P].base.name`. Otherwise it counts as changed.

### The sub length

A row's `template_sub_s` is the sub TS shoots for that plan. A template's `sub_s` is a default. The writers keep the two consistent, and never leave TS shooting a sub different from the row's:

- A row's sub counts as changed by ACP when it differs from the sub recorded for it in the base. For such a plan in this push, the writer writes `exposure` -1 when the row follows its template (its sub equals the template's `sub_s`) and TS's explicit `exposure` differs from it. It writes an explicit `exposure` equal to the row's `template_sub_s` when the row deviates and TS's `exposure` differs from that.
- A row whose sub equals the base is left alone, so a sub set by hand in TS stays.
- When the writer writes `defaultexposure` for T it writes no plan `exposure` because of that alone. A plan with an explicit `exposure` keeps shooting it. Only the rule above changes a plan.
- The sync's `refresh_template_subs` (`rows.py:270`) copies TS's effective sub onto a row only when the row's `template_sub_s` equals the sub in the previous base, that is, when ACP did not change it since the last push. A row ACP changed keeps its value and the push writes it, so the refresh can never undo an ACP change.

### Tests (Python in `acp-nina-ts-sync/tests`, then C# golden)

Fixtures, shared by the Python and C# suites: `tests/fixtures/ts-templates-20.sql` builds the TS tables from the 12 October copy (the 20 templates with every column the writers read, and the 244 exposure plans with their template id, `exposure`, `acquired` and `accepted`), and `tests/fixtures/gear-templates-v3.json` holds the gear after M3. Scenarios are recipes: `row_scenarios.py` in Python and `TsRowScenarios.cs` in the plugin tests, each a named sequence of steps (edit gear, edit TS, set the slot list, push, pull). The slot list is an argument of the step, with `["L","R","G","B","H","O","S"]` as the default. `dump_golden_rows.py` writes each scenario's rows to `golden-rows.json`, which the plugin tests read.

| Id | Inputs | Expected |
|---|---|---|
| S1 | Push the fixture with no gear edits | No template is written or inserted. The result's template list has 10 entries, each with `action` "unchanged". The stray list is S10's. |
| S2 | Gear "Ha" (TS id 12): offset -1 to 25. Push. | Id 12 has offset 25 and nothing else changes. `ts.base.offset` is 25. The result says `fields_written` ["offset"]. |
| S3 | Gear "Ha" offset 25 (base -1) and TS id 12 offset edited by hand to 10. Push. | Id 12 still has offset 10. The note reads "Ha: offset is 25 in ACP and 10 in TS. Left alone until you choose in the gear editor." Status is `conflict`. |
| S4 | Gear "Ha" unchanged (offset -1). TS id 12 edited to offset 10. Push, then the pull. | The push writes nothing. After the pull, gear Ha has offset 10 and base 10, and the note "Changed in TS: Ha offset 10" is returned. |
| S5 | Gear "Ha" renamed "Ha 300" (base "Ha"). Push. | Id 12 is named "Ha 300". The template count is still 20. The 23 plans on id 12 keep their template, `acquired`, `accepted` and `exposure`. Their guids use "Ha 300". The `aliases` start with "Ha". |
| S6 | Rename gear "Ha" to "OIII" (id 11 has that name), bypassing the server check | Id 12 keeps its name. The note says the name was skipped. |
| S7 | Gear "Ha 600" (new, 600 s, offset 25). Push. | One new template named "Ha 600", filtername "H", `defaultexposure` 600, offset 25, guid from the gear template id. No plan uses it. |
| S8 | A gear template "SII" with filter SII and slots `["L","R","G","B","H","O"]`. A plan row on it. Push. | No template change for SII, no exposure plan written for the row. The notes say "SII is not on the wheel of ... yet, so 1 row was skipped". Add "S" to the slots and push again: the SII template (id 13) and plan are written as normal. |
| S9 | Slots `[]`, gear "Ha", "OIII" and "SII" offset -1 to 25, and a new gear template "Ha 600". Push. Then the same with no `wheel_slots` in the body and no fingerprint entry for the profile. | Ids 12, 11 and 13 get offset 25 and their filternames stay "H", "O" and "S". "Ha 600" is not inserted, and the notes say the wheel is unknown and its rows were skipped. The second run gives the same rows. |
| S10 | Push with gear holding only the 10 templates of section 11 | The stray list is ids 1, 2, 3, 4, 8, 9, 10, 15, 17, 20. |
| S11 | Gear "Ha" sub 300 to 600 (rows follow, `template_sub_s` 300 to 600). The 23 Ha plans have explicit `exposure` 300. Push. | Id 12 has `defaultexposure` 600. The 23 plans have `exposure` -1. A plan of another project, not in the push, keeps its `exposure`. |
| S12 | Gain 0 in gear, base 0. Push. | The base holds 0, not -1. |
| S13 | A plan row whose `template_id` equals the base's and whose plan sits on TS id 4 while the row's template links to id 14 (the "B" duplicate) | The plan is not moved. |
| S14 | A plan row whose `template_id` is different from the base's | The plan moves to the new template. If the row follows the new template its `exposure` is -1. |
| S15 | A base entry without `template_id`, base template name "R Stars (ZWO ASI6200MM Pro)", the row's template "R Stars" with that name in `ts_names` | The plan is not moved. |
| S16 | Two gear cameras linked to this profile both with a template "Ha" | Only the first is inserted. The second is skipped with the clash note. |
| S17 | TS ids 1 and 18 are both named "L". Gear has one "L" (the camera cannot hold two), linked to id 18 with its base equal to id 18's values. (a) Gear offset -1 to 25, push. (b) Remove the `ts[P]` link from the gear template, then push. | (a) Id 18 gets offset 25 and id 1 is not touched. (b) Nothing is written to either row, no template is inserted, the status is `new`, and the note reads "Template 'L' matches 2 templates in Foo (ids 1, 18), left for adoption." |
| S18 | A new gear template named "B" with no link, TS having ids 4 and 14 both named "B" | It is not linked by name. It waits for adoption. Nothing is written for it. |
| S19 | A gear template found by name to a single TS row, with no base | Nothing is written. The base is the TS values. The gear template takes the TS values. The note says so. |
| S20 | A gear template whose alias names a TS row of another group (alias "R" on "R Stars", TS id 2 is R normal) | The alias does not claim id 2. |
| S21 | Run the extension's sync route and the links route with the same results | `record_template_results` is called once per route with the same arguments, and `ts[P]` ends the same. |
| S22 | `POST .../sync` with no `wheel_slots`, a fingerprint entry for the profile with slots `["L","R","G","B","H","O","S"]` | The writer uses those slots (host fake of `load_fingerprints`). With no entry, it uses an empty list and behaves as S9. |
| S23 | A plan row whose pinned exposure plan ids are 21, 22 and 23, which are not in TS | The pins are ignored. The plan is found by guid or created, and no error is raised. |
| S24 | 23 rows at `template_sub_s` 180 on template 19 (120 s, deviating). The gear sub for 19 stays 120. A row's sub is changed by ACP from 180 to 200. Push. | That plan's `exposure` becomes explicit 200. The other 22 plans keep 180. |
| S25 | A row at `template_sub_s` 120 on template 19, changed by ACP to 150 along with a gear change of the template to 150 | The row follows the template, so the plan's `exposure` is -1. |
| S26 | After S24's push, run `refresh_template_subs` | The changed row keeps 200 and the others keep 180. A plan whose `exposure` was edited to 240 in TS (row sub still equal to the base) comes back as 240. |
| S27 | C#: S1 to S26 and S28 to S31 as golden scenarios | The plugin's rows equal the Python rows. |
| S28 | Template 19 (R, 120 s): TS default edited to 150 by hand. Gear unchanged, base 120. Plan 258 has `exposure` -1 and its row `template_sub_s` 120 (it follows). Plan 265 keeps `exposure` 180 with row sub 180. Plan 408 keeps 15. Run the pull. | Gear R has `sub_s` 150 and base 150. Plan 258's row takes 150. Rows 265 and 408 keep 180 and 15, and their plans are untouched. |
| S29 | A links post with `capabilities` `["templates","repoint"]`, then a Python sync of the same profile | `links_capable[P]` is still set after the sync. A links post with no `capabilities` then clears it. |
| S30 | Gear "R (ZWO ASI6200MM Pro)" with `ts_names` ["R"], its `ts[P]` removed, and TS id 19 deleted, so TS id 2 is the only row named "R". Push. | Id 2 is not touched or linked, because `ts_names` never finds a row. The template is inserted as new. |
| S31 | Gear "Ha" renamed "Ha 300" (`aliases` ["Ha"]), its `ts[P]` removed, TS id 12 still named "Ha" and the only row with that name in its group. Push. | Id 12 is found through the alias, linked, and renamed to "Ha 300". The template count stays 20. |

## 9. The plugin

The plugin is built and installed first (section 12). It is written to work against today's ACP as well as the new one.

- `GearFingerprintService`: add `profile_id` and `wheel_slots` (section 5). `ReadFilters` stays. Today's ACP ignores the new fields.
- Sync All to TS: send a fingerprint (the same build as Sync for tonight, with no solve) before the push, so ACP's wheel data is as fresh as the push. This is for the display only. The push uses the profile's slots directly.
- `TsTemplatesReporter`: add `id`, `guid`, the moon, twilight, humidity and dither columns, and `plans` to each template (`TsTemplateInfo`). Post the list before reading gear and pushing, in both Sync for tonight and Sync All to TS, so the pull runs first. The writers do not rely on this order.
- `Gear.cs`: `CameraFilter` gains `Templates` (a list of gear template objects) and keeps the scalar properties.
- `TsConvert` and the writer: when a filter has `templates`, apply section 8. When it has none (today's ACP), read the scalars as now, look templates up by name, and write nothing new. The three-way comparison, the rename and the repoint only run for gear templates.
- `TsPushService.TsPushResult` gains the per-template results, and `TsLinksReporter` sends them in the `templates` array of `POST /api/ext/nina-ts-sync/links` with the profile id, the machine and `capabilities` (`["templates","repoint"]`), as it already sends plan states. Today's ACP ignores the extra keys. The new ACP stores the results in `ts[P]` and sets `links_capable[P]`.
- `TsFilterNames.ResolveToWheel` keeps its behaviour and joins the shared case file.
- `FiltersNotOnWheel` changes from "push anyway and warn" to "skip and say so" (section 5). `FilterRenames` stays.
- Older plugin against a new ACP keeps working: it reads the derived scalar keys, looks templates up by name, and ignores the rest. It never reports `links_capable`, and its fingerprint still matches its camera by name, so ACP refuses renames and sub changes for that camera (section 4) and Tidy is not offered.
- Rolling back follows the goal panel spec's rule: restore the `schedulerdb.sqlite` backup the sync wrote, never reinstall an older plugin over a database the new one has written to. The new plugin renames and rewrites templates by id, and an older one would insert duplicates by name.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| L1 | C# | Build a fingerprint for a profile with slots H, O, S, L, R, G, B and a disconnected wheel | `profile_id` is the profile's id, `wheel_slots` is `["L","R","G","B","H","O","S"]` in the profile's slot order, and `filters` is empty. |
| L2 | C# | `TsTemplatesReporter` against the 20 template database | One post with 20 templates, each with `id`, `guid` and the moon, twilight, humidity and dither columns. Id 11's `plans` has 55 entries, id 14's has 26 with 3 at `exposure` 15, and the total over all templates is 244. |
| L3 | C# | Sync All to TS | A fingerprint post happens before the template post, which happens before the push. |
| L4 | C# | A push that renames id 12 to "Ha 300" | The links post carries a `templates` entry with `template_id` the gear id, `ts_id` 12, `action` "updated", `fields_written` ["name"] and `base.name` "Ha 300". It also carries `capabilities`. |
| L5 | C# | A push against a gear document whose filters have no `templates` (today's ACP), every filter on a known wheel slot, with the new fields in the posts | The rows equal today's plugin's rows for the same input. The extra fields are sent. No template link is created. |
| L6 | C# | The same, with slots `["L","R","G","B","H","O"]` and a row on SII (off the wheel) and slots `[]` with a row on Ha (unknown wheel) | Today's plugin pushed both rows (the first with a warning). This plugin skips both with the notes from section 5 and writes nothing for them. This is the one intended difference from today's behaviour. |

## 10. The Tidy screen

Tidy is a one-time cleanup of the duplicate TS templates in a profile. It never deletes anything in TS.

### When it shows

The banner on the camera card (section 6) shows when, for a linked profile P: the template list from the plugin has `id`s, at least one group has more than one template, `tidy[P].state` is not "done", and `links_capable[P]` is set with `repoint` (the profile's last links post listed the capability). Tidy is gated on that capability, not on any past `repoint_done`, which is how this spec reads Decision 4 of the amendments. The per-push result `repoint_done` is a different thing: it says one push finished its moves. "Later" sets the state to "later" and the banner stays, in a smaller form. When no group has two templates Tidy is skipped, and the banner does not show.

### Groups

For each template in P's list:

- Its ACP filter is `fr.known_filter(filtername)`, which reads "R Stars" as R. A template for which that gives none is not placed in any group. The screen lists those in a read-only bucket at the bottom, "Not placed", with their names and plan counts. They cannot be kept or dropped, and Tidy never moves their plans.
- It is on the wheel when `resolve_to_wheel(known_filter, slots)` gives a slot equal to its filtername. Ids 15, 17 and 20 are not.
- Its class is "short" when its sub is under 60 s (`FRAME_UNIT_BELOW_S`) and "normal" otherwise.
- A group is the templates with the same ACP filter and class, keyed `<filter>/<class>`. Only the camera's linked filters are grouped.

From the live data, the groups are `L/normal` (ids 1, 18), `R/normal` (2, 19), `G/normal` (3, 16), `B/normal` (4, 14), `R/short` (5, 20), `G/short` (6, 17), `B/short` (7, 15), `Ha/normal` (8, 12), `OIII/normal` (9, 11) and `SII/normal` (10, 13). Ten groups, each with two templates.

### The default pick

Per group: the template on the wheel with the most plans (`len(plans)`), ties to the lowest id. A template that is not on the wheel is never the default and cannot be selected. In a group whose templates are all off the wheel, the screen says "None of these is on the wheel. The sync will create a new template." and nothing is kept.

Defaults for the live data: id 18 (11 plans, over id 1's 1), 19 (26 over 1), 16 (26 over 1), 14 (26 over 1), id 5 for `R/short` (1 plan, because id 20 is off the wheel and cannot win), 6, 7, id 12 (23 over 1), id 11 (55 over 0), id 13 (3 over 0).

### What the screen shows

As the Tidy artboard: each group a box with a radio per template. Each option shows the name, the plan count, and, for an off-wheel template, "filter 'R Stars' is not on the wheel". It also shows every field that differs within the group: sub, gain, offset, bin, moon avoidance (on or off, with the separation and width), twilight level, humidity limit and dither every. A differing field is marked, for example for `L/normal`: "sub 180 s or 120 s, offset 25 or Default, moon avoidance on (120°, 14 days) or off, twilight 0 or 1, humidity 0 or 100, dither every 2 or Default". Tidy changes none of the kept template's fields (except the optional offset), so this is the only chance to see what the choice drops. Under the groups:

- A warning inside a group when the plans of the other templates would change sub length. It names only rows that follow their old template: "3 plans that follow their old template change sub length to 120 s. Their goals in hours stay the same, so frame counts change. 2 plans keep their own sub."
- A checkbox per `normal` group that has them, on by default: "Move N star plans to R Stars". It lists the plans by plan name. These are ACP-linked plans whose sub is under 60 s but whose template is normal class. On the live data that is 3 each in `B/normal`, `G/normal` and `R/normal`, nine in all (plan ids 404, 405, 408, 409, 410, 413, 415, 416, 419).
- The amber box "Offsets differ" with the checkbox "Set offset 25 on every template I keep" (default off).
- The box "Unused after this, remove in TS", listing every template of P that is not kept and that no plan will use after the move. Counts: a template's plans minus those that are in ACP's pinned plan ids (`ts_links[P].refs.exposure_plan_ids`) and so move. TS plans ACP does not know about stay where they are, and keep their template off the unused list.
- A footer with "Apply to N plans" and "Later". N is the number of ACP plans that will be repointed, including star plans when their boxes are ticked.

### What Apply does

All in one `POST /api/gear/tidy` with `{profile_id, picks: {<group key>: <TS id>}, move_stars: {<group key>: true|false}, set_offset_25: false, rev}`, under `GEAR_LOCK` then `PLANS_LOCK`. A 409 if `rev` is stale.

For each group with a pick:

1. Find the gear template that stands for the group: the gear template of that camera and filter whose `ts[P].id` is in the group, else the one whose name or `ts_names` equals a name in the group, else a new gear template.
2. Set its fields from the picked TS template: `name`, `sub_s`, `gain`, `offset`, `bin`, `ts[P] = {id, guid, base = those values, repoint_from = the other ids in the group}`. Add the names of the others to `ts_names`. Its other TS columns (moon, twilight, humidity, dither) are not touched.
3. Sub on rows, by the rule in section 4. A row that moves from a dropped template takes the kept template's sub when it followed its old template (its sub equalled that template's), and keeps its own sub when it deviated. A row already on the kept template is not touched.
4. If `set_offset_25`, set `offset` 25 on it. This is an ACP change against the base, so the next push writes it.
5. For a `normal` group with `move_stars` true, move each listed star row to the gear template of the filter's `short` group: set the row's `template_id` and `template`. The row's sub (15 s) already equals that template's sub, so it follows it.

Then `tidy[P] = {state: "done", at: <now>}`. A group that is all off the wheel gets a gear template with `new` status and `repoint_from` set.

The next push moves, for each ACP row whose plan is on a template id in `repoint_from` of the row's gear template, the plan onto the gear template's TS id, and writes `exposure` by the sub rule in section 8. A star row's plan moves from the normal template to the short one through the `template_id` change rule in section 8 (the row's `template_id` differs from the base's), not through `repoint_from`, and keeps its `acquired` and `accepted`. The push does not touch TS plans that ACP does not own. When the move is done the writer reports `repoint_done: true` and ACP clears `repoint_from`. The move is done only when, after the push, no enabled TS plan of the profile that ACP owns sits on a template id in any `repoint_from` list, read from TS, not from the rows this push wrote. Sync for Tonight pushes only tonight's plans, so it usually reports false, and a later Sync All finishes the job. Phase 4 reports true when any written row had a `repoint_from` list; phase 5 replaces that with this rule. The 22 plans each on ids 15, 17 and 20 move to ids 7, 6 and 5, which are 15 s templates, so their sub does not change.

Nothing is deleted in TS. The unused templates stay until the user removes them in TS. Because the gear template no longer lists them, the push result lists them as stray each time, as the goal panel spec does.

Tidy after the first Apply can be run again from the same banner and works the same way; it simply finds fewer groups.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| T1 | ACP | `GET /api/gear/tidy?profile_id=9d3a61fd-...` with the 20-template list and slots `["L","R","G","B","H","O","S"]` | Ten groups of two, keyed `L/normal` and so on. Defaults 18, 19, 16, 14, 5, 6, 7, 12, 11, 13. Ids 15, 17, 20 are marked off the wheel. |
| T2 | ACP | The same, but with ids 5 and 20 both holding 22 plans | The default for `R/short` is still 5, because id 20 is off the wheel. |
| T3 | ACP | A group of two off-wheel templates only | The group has no default and says the sync will create one. |
| T4 | ACP | Apply T1's defaults and all star boxes on a plan set where every one of the 244 plans is ACP-linked | N is 80: 71 from dropped templates (1 each on ids 1, 2, 3, 4, 8 and 22 each on 15, 17, 20) and 9 star plans. The unused list is ids 1, 2, 3, 4, 8, 9, 10, 15, 17, 20. |
| T5 | ACP | As T4, with the plans on ids 1 to 8 not linked to ACP | N is 75. Ids 1 to 4 and 8 are not in the unused list. |
| T6 | ACP | Apply with `set_offset_25` true | Each kept gear template has offset 25. Their base offsets are the TS values (-1 for 18, 19, 16, 14, 12, 11, 13, 25 for 5, 6, 7). |
| T7 | ACP | Apply, then `GET /api/gear` | The gear templates for R are "R (ZWO ASI6200MM Pro)" (id 19) and "R Stars" (id 5). `tidy` for the profile is "done". |
| T8 | ACP | Apply picking id 2 (R, 180 s) instead of 19 for `R/normal`, star boxes off, with the 23 R rows at `template_sub_s` 180 on id 19 | The gear template R is named "R" with sub 180. The 23 rows keep 180 and now follow the template. The 3 rows at 15 s keep 15 and deviate. `repoint_from` is [19]. |
| T9 | ACP | Apply with a stale `rev` | 409. Nothing is written. |
| T10 | ACP | Apply T1's defaults. The plan on id 1 (L, `template_sub_s` 180, followed its template) and the 11 plans on id 18 (`template_sub_s` 180, template 120 s, deviating) | The plan from id 1 takes sub 120. The 11 on id 18 keep 180, because they are already on the kept template. |
| T11 | ACP | `GET /api/gear/tidy` for `L/normal` | The group lists ids 1 and 18 with the marked differences: sub, offset, moon avoidance, twilight, humidity, dither. Gain and bin are not marked. |
| T12 | ACP | A list whose templates have no `id` (older plugin), the profile otherwise capable | No Tidy banner, and `GET /api/gear/tidy` answers 409 "the list has no template ids". |
| T12b | ACP | A list with ids, but `links_capable[P]` lacks `repoint` (or has no entry) | No Tidy banner, and `GET /api/gear/tidy` answers 409 "the plugin cannot repoint plans". |
| T13 | ACP | Apply with the `R/normal` star box on | Plans 408, 413 and 419 get `template_id` of "R Stars" and `template` "R Stars". Plans 404, 409, 415 go to "B Stars" and 405, 410, 416 to "G Stars" with their boxes on. |
| T14 | ACP | A profile whose templates each have a different group | Tidy is skipped, with no banner and no group. |
| T15 | ACP | A template whose filtername is "Narrowband Dual" | It appears in "Not placed". It has no radio, and Apply never moves its plans. |
| T16 | Python | After T4's gear, push | The 22 plans on id 20 move to id 5, 15 to 7 and 17 to 6, with `exposure` -1 (a template change writes -1, as in section 8). Plans 408, 413, 419 move from id 19 to id 5 and keep their `acquired` and `accepted` (18, 18 and 0). The plan on id 1 moves to id 18 and takes sub 120. No template is deleted. `repoint_from` is cleared after the push result is stored. |
| T17 | C# | T16 | Golden rows match. |
| T18 | ACP, `tests/frontend` | Open Tidy on T1's data | Ten fieldsets. Ids 15, 17, 20 radios are disabled with the off-wheel text. The star boxes show "Move 3 star plans to B Stars" and its siblings. The footer reads "Apply to 80 plans". |

## 11. Migration

The gear migration runs once at startup, before the app serves, guarded by `templates_migrated: 1` on `gear.json`, like `_maybe_migrate_filter_rows` (`app.py:710`). It takes `GEAR_LOCK` and re-checks the flag inside it. It takes a timestamped backup first (`gear.json.bak-<time>`, written beside the file by a helper that mirrors `_backup_plans_json`, `app.py:650`). If the backup fails the migration does not run and the old shape keeps being served, which `GET /api/gear` handles by building the derived view from the old keys.

### Step 1: the shape

For each camera filter in the old shape `{default_sub_s, gain, offset, bin, ts_template_id, ts_template_name}`, one gear template is made:

- `name`: the old `ts_template_name` when set, else "{filter} ({camera name})". These are the names the writers already used.
- `sub_s`: `default_sub_s` when above 0, else 300 for Ha, OIII, SII and 120 for anything else.
- `gain`, `offset`, `bin`: the old values.
- `starred`: true. `aliases`, `ts_names`: empty. `ts`: empty. The old `ts_template_id` is dropped because it cannot be trusted (section 1).

A filter with no keys at all gets the defaults above. The result is written with `version` 3, `rev` 1.

For the live copy, the 6200 camera migrates to seven templates: "B" (120 s), "G (ZWO ASI6200MM Pro)" (120), "Ha" (300), "L" (120), "OIII" (300), "R (ZWO ASI6200MM Pro)" (120) and "SII" (300).

### Step 2: adoption of the TS list

Adoption needs a template list whose templates carry `id` and a `links_capable[P]` entry with `templates` and `repoint` (section 4). A list without ids, or a profile without that entry, never triggers it. It runs per profile, once for each camera that is linked to the profile, the first time such a list for that profile can be paired with a fingerprint that names the camera. That is at the end of the migration if `ts_templates.json` and the fingerprint store already hold both, and otherwise when `POST /api/ts-templates` or the next fingerprint completes the pair. It is recorded as `ts[P]` links existing on the camera's templates, so it never runs twice for a camera and profile. It runs under `GEAR_LOCK`.

Gear values are adopted from the camera's primary profile only. For any other profile, adoption links each gear template to the TS template that stands for it and records the base as the current gear values, so nothing is written. It adds a note listing the differences.

For each group of section 10 whose ACP filter is one of the camera's filters:

1. Pick the group's default template D (section 10), or none when all are off the wheel.
2. Find the camera's gear template for the group: the one whose name equals the name of any TS template in the group (ignoring case), choosing D's name first. If more than one matches, the starred one.
3. If found and D exists: link it to D (`ts[P] = {id, guid, base = D's values}`) and, for the primary profile, set its fields to D's values (name, sub, gain, offset, bin). Add the names of the group's other templates, apart from D's, to `ts_names`.
4. If not found and D exists: only the primary profile's list may create a gear template. Create one from D, unstarred, in the group's filter, with the other names in `ts_names`. If the filter has no starred template, star it. When the list belongs to a secondary profile, create nothing: the group is left pending, its TS templates show as `waiting` with the note "Bar has templates for R Stars that gear lacks, waiting for Foo (primary) to report", and adoption for that profile runs again after the primary's adoption has created the templates. It then links them with the base equal to the gear values, as above.
5. If D does not exist (all off the wheel): leave the gear templates alone. They are `new`.

Class matters: "R Stars" (short) is never merged into "R" (normal) because they are different groups. The base is set from D, so the first push after adoption writes nothing to TS. Plans sitting on TS ids that are not D are not moved (S13). They move only if Rohan runs Tidy. Adoption names are in `ts_names`, never in `aliases`, so they never find a TS row (section 8).

For the live copy, adoption gives the 6200 camera ten templates: L (id 18), "R (ZWO ASI6200MM Pro)" (19) and "R Stars" (5), "G (ZWO ASI6200MM Pro)" (16) and "G Stars" (6), "B" (14) and "B Stars" (7), "Ha" (12), "OIII" (11), "SII" (13). `ts_names`: R: "R"; "R Stars": "R Stars (ZWO ASI6200MM Pro)"; G: "G"; "G Stars": "G Stars (ZWO ASI6200MM Pro)"; "B Stars": "B Stars (ZWO ASI6200MM Pro)"; Ha: "H"; OIII: "O"; SII: "S". L and B have none, since the duplicates share their names. Offsets and subs come from the picked TS template.

Cameras with no matching fingerprint, or profiles with no list, stay on step 1 and are `new`. The first push inserts their templates in TS, or leaves them waiting if a name is shared by two TS rows (S18).

### Step 3: the rows

For every plan row, `template_id` is found from `template` by exact name, then `aliases`, then `ts_names`, within the plan's camera and the row's filter. Step 3 runs in `load_plans()` right after `_maybe_migrate_filter_rows`, and again at the end of every adoption. A row naming "B Stars (ZWO ASI6200MM Pro)" gets the id of "B Stars" and the template name "B Stars". A row left unmatched keeps its template name and gets `template_unresolved: true` (section 3).

`template_sub_s` is not touched. Rows keep the sub TS really uses. The push result flags a difference with the template as it does today (goal panel spec M3), and the editor shows "differs from template".

### Rollback

Restore `gear.json.bak-*` and `plans.json.bak-*` together. The rows and the gear have to agree on `template_id`. An older ACP core that reads a v3 gear file would see no filter scalars, so rollback means restoring both backups, as for the filter rows migration.

### Tests

| Id | Repo | Inputs | Expected |
|---|---|---|---|
| M1 | ACP | The live `gear.json` (version 2) | After load, version 3, `rev` 1, a backup exists, and the 6200 camera has seven templates as above, each starred, none with a `ts` entry. A second load changes nothing. |
| M2 | ACP | The same with the backup directory not writable | The file is untouched, `GET /api/gear` still answers, with the old keys. |
| M3 | ACP | M1, then `POST /api/ts-templates` with the 20 templates (with ids) for profile `9d3a61fd-...`, and a fingerprint for that profile naming "ZWO ASI6200MM Pro" with the seven slots | The 6200 camera has ten templates with the TS ids, `ts_names` and subs above. Every `ts[P].base` equals the TS row. Plan rows naming "B Stars (ZWO ASI6200MM Pro)", "R" and "H" now carry `template_id` of "B Stars", "R (ZWO ASI6200MM Pro)" and "Ha", with those names in `template`. |
| M4 | ACP | M3, then compare the order of events: fingerprint first then list, and list first then fingerprint | The same result. |
| M5 | ACP | M3, then a second `POST /api/ts-templates` with the same list | Gear is unchanged and `rev` does not move. |
| M6 | ACP | A plan row naming "R Stars (ZWO ASI6200MM Pro)" on filter R, after M3 | `template_id` is the "R Stars" id. `template` is rewritten to "R Stars". |
| M7 | ACP | A camera in the live file with no filters (`ASI Camera (1) (AS`) | It migrates with `filters` empty. |
| M8 | ACP | Gain 0 in the old shape | The new template has gain 0. |
| M9 | ACP | A plans file with a row on a filter that the migration leaves with no template | The load logs the plan id, the row has `template_unresolved: true`, and no template is assigned. |
| M10 | ACP | M1, then a template list with no `id` fields (older plugin) | No adoption happens, the 6200 camera keeps seven templates with no `ts` entry, and `GET /api/gear/tidy` is refused. |
| M11 | ACP | M3 with a second profile `093effd2-...` whose list has the same templates but Ha offset 10, and the first profile primary | The Ha template keeps offset -1. Its `ts` entry for the second profile has base offset -1. The notes list "Ha offset is 10 in TS and -1 in gear". |
| M12 | ACP | The 6200's primary profile is P1 and no gear template for "R Stars" exists. P2's list (with ids, capable) arrives first. | Nothing is created. P2's R Stars templates show `waiting` with the note. After P1's list arrives and adoption creates "R Stars", P2's adoption runs again and links it with the base equal to the gear values. |

## 12. Build order

Each phase ends with its tests passing in every repo it touches. The plugin goes first and the ACP and sync changes follow together, because ACP deploys on merge and the plugin has to be on IMAGING-PC before ACP can rely on it. The spec has 131 tests across sections 4 to 11, and each one belongs to a phase below.

| Phase | Content | Tests | Repos |
|---|---|---|---|
| 4 (first) | The plugin, built against today's ACP: fingerprint fields, template list fields, the C# writer for gear templates (used when a filter has `templates`, otherwise today's behaviour), links with templates and capabilities, Sync All fingerprint, and the shared fixtures `ts-templates-20.sql` and `gear-templates-v3.json` with the C# scenario recipes | L1 to L6 | ACP.NINA.Plugin |
| 1 | Gear v3 data, migration steps 1 and 3, `rev`, `GEAR_LOCK`, `load_gear` and `save_gear` derivation, `GET` and `POST /api/gear`, the single-template and link routes, `record_template_results`, `template_id` on rows, rename and sub propagation, the guard, the old-page rule, the widened filter name rule, wheel resolve in ACP, the fingerprint fields and store rule, the wheel block | G1 to G26, W1, W2, W4 to W11, F1 to F4, M1, M2, M6 to M9, P4, P5 | ACP |
| 2 | The gear editor, the 420 px panel, the plan editor's template drop-down and flags, "+ Add filter" on the starred template, the not-on-the-wheel marks | U1 to U15, P1 to P3, P6, P7 | ACP |
| 3 | The Python writer: three-way template writes with the hold rule, rename by id, skip not-on-wheel, the sub rules, `refresh_template_subs` rule, plan repoint rule, template results, the pull, adoption, shared wheel case file, host import `load_fingerprints`, golden rows | W3 (Python half), S1 to S26, S28 to S31, S27, M3 to M5, M10 to M12 | ACP, acp-nina-ts-sync |
| 5 | The Tidy screen and `POST /api/gear/tidy`, repointing and star moves in both writers | T1 to T18 (with T12b) | ACP, acp-nina-ts-sync, ACP.NINA.Plugin |

Phases 1, 2 and 3 merge together as one ACP and sync release after the plugin is on IMAGING-PC. The old editor cannot save filters against the new store (the old-page rule ignores them on purpose), so shipping the data before the editor would leave gear unsaveable. The migration, which is the step that is hard to undo, ships with them, and the backup covers it. The server guard in section 4 refuses renames and sub changes for a camera until a links-capable plugin has reported for one of its profiles, so an older plugin cannot meet a renamed template.

The parity checks are a gate on the ACP deploy, not part of phase 4. Before phases 1 to 3 are deployed, three things must pass: W3 in all three languages against the shared `wheel_resolve_cases.json`, S27 (the plugin's rows equal the golden rows phase 3 wrote), and G21 with a name-only fingerprint. Phase 4 alone proves only the fallback against today's ACP (L5), the off-wheel difference (L6) and the plugin-side facts (L1 to L4). The fixtures are created in phase 4 and used by phase 3's tests, which land after it.

Phase 5 needs the others, because Tidy needs the template ids and plans in the list, the repoint in both writers and `links_capable`.

Phases 3 and 5 regenerate `golden-rows.json` with `tests/dump_golden_rows.py` and copy it into the plugin in the same session. The plugin's C# scenarios (`TsRowScenarios.cs`) are written in phase 4 against the recipes, and they first run against the golden file when phase 3 lands, which is the gate above.

The full ACP and Python suites still pass at the end of each phase. The goal panel's tests U1 (380 px) and the `templateOptions` and `usualTemplate` tests change with phase 2.

### Manual check on the imaging PC

After phases 1 to 3 are deployed (the phase 4 plugin is already on IMAGING-PC), with NINA closed and `schedulerdb.sqlite` backed up first:

- [ ] In ACP, change template "Ha" offset from Default to 25. Run Sync for tonight. In TS's Database Manager, the template named Ha (id 12) shows offset 25 and nothing else on it changed.
- [ ] In TS, change that template's sub to 240, run Sync for tonight, and confirm ACP's gear shows Ha at 240 s with the note "Changed in TS".
- [ ] Rename "Ha" to "Ha 300" in ACP and sync. TS shows one template with the new name, still 20 in total, and an Ha plan's counts are intact.

## Settled while building phase 4

- The plugin cannot tell which profile is a camera's primary. A template it finds in TS by name with no base is reported with `action` "adopted" and TS's values as `base`. Phase 1's `record_template_results` accepts "adopted" beside "inserted", "updated", "unchanged", "held" and "skipped", and applies Decision 5: it takes the values only for the primary profile and otherwise records the link with the gear values as base.
- An inserted gear template's guid is `Stable("{NamePart(profile)}/gear-template/{NamePart(templateId)}")`. Phase 3's Python writer uses the same recipe.
- In gear mode the stray list is every template of the profile that no gear template of any camera claims, so it can include templates of a linked camera this push did not cover. The editor shows it as is.
- Sync All posts no fingerprint when no camera is connected, so a daytime sync never unlinks the camera.
- A wheel list of null means "not given" and keeps today's behaviour. An empty list means "wheel unknown" and skips rows on templates TS lacks. Production always sends the profile's slot list.

## Settled after review

- Any rewrite of plan rows by this feature (`template_id`, `template_unresolved`, M6 renames, sub carries) takes a `plans.json.bak-<stamp>` backup first, with the same naming as the other plans backups, so Rollback in section 11 can restore `plans.json` beside `gear.json`. It is taken once per process and path, just before the first write that actually changes rows, so it holds the rows as they were before any rewrite. A load that changes nothing writes no backup. If the backup cannot be made, the `template_id` repair does not write and retries on the next load.
- `_carry_into_plans` (renames and sub carries) logs an error and writes plans.json anyway when its plans backup cannot be made. It runs inside a gear commit, and refusing would leave gear and plans out of step: gear would carry the new names and subs while rows still held the old ones. The `template_id` repair is different, because it can wait for the next load, so it refuses and leaves both the cache and the file as they were on disk.
- A non-primary profile's pull compares its TS list with gear on every post and adds a note per differing field ("Bar: Ha offset is 10 in TS and -1 in gear"). It writes nothing to gear.

## Assumptions made

1. The raw wheel slot names on Rohan's rig are L, R, G, B, H, O and S, in slots 0 to 6. Confirmed on 13 Oct 2026 from NINA's profile `9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7` on IMAGING-PC and the plugin's log line `filters [L, R, G, B, H, O, S]` from the 15:01 runs on 11 and 12 Oct.
2. The conflict rule is hold, not overwrite (section 8). A field changed in both places is left alone and the gear editor offers Keep ACP's and Take TS's.
3. ACP also pulls a TS-side edit into gear when ACP has not changed that field, for the primary profile only. Rohan's decision names only the push guard. Without a pull, ACP would show and divide by a sub TS no longer uses.
4. Tidy and adoption group templates by ACP filter and sub class (under 60 s is "short"), not by name, so "R Stars" is never merged into "R". The mockup shows one "Stars" group for R, G and B. This spec shows three groups of two.
5. Adoption chooses the same default as Tidy, so the gear shows one template per group straight away and Tidy changes the TS side and offers a different pick. The alternative, listing all 20 in gear, breaks the unique name rule ("B" and "L" repeat).
6. A row's `template_sub_s` is the sub TS shoots, and a template's `sub_s` is a default. A gear sub edit changes only rows that follow the template. Tidy moves a row onto the kept template's sub only when it followed its old template.
7. A stale gear page's `filters` are ignored, not merged. Merging legacy scalars could undo a newer edit, and the cost of ignoring is that the old page's filter edits do not save, which only matters until the tab reloads (PR 115 makes that the next load).
8. The fingerprint gains `profile_id` and `wheel_slots`. Rohan's decision 3 says the wheel comes from the fingerprint. The existing fields cannot do it: `filters` is folded and sorted by ACP, and empty when the wheel is disconnected.
9. A template is created in TS when its filter is on the wheel, even with no plan using it, to match the mockup's note "The next sync creates it".
10. A template cannot move to another filter, and a camera cannot be deleted while a plan uses it. Both are refusals rather than silent cascades.
11. The migration does not touch `template_sub_s`. A mismatch with the gear template is shown as "differs from template" and reported by the push.
12. Gear uses `GEAR_LOCK` taken before `PLANS_LOCK`, and code holding `PLANS_LOCK` never takes `GEAR_LOCK`.
13. With no known slot a template is not inserted at all (its rows are skipped with a note), because the filtername column is not null and a guess would be a wrong filtername. This changes today's push-anyway behaviour for profiles with no wheel data.
14. Today's ACP ignores unknown keys in the template list post, the links post and the fingerprint. I did not check each route, and the plugin-first order depends on it (open question 3).
15. The wording of several decided amendments (the `waiting` definition, the shape of the `wheel_unlinked`, `wheel_ambiguous` and nina-names keys, the Tidy fixtures, the extra tests) was written here from the decision summary, not copied from the reviewers' text.

## Open questions

1. Settled on 13 Oct 2026: the slots are L, R, G, B, H, O and S (see Assumptions, 1).
2. Does TS fail silently or loudly when a template's filtername matches no wheel slot? Ids 15, 17 and 20 have 22 plans each and no frames (`acquiredimage` has none for them), which fits "never runs", but the TS source on that point has not been read. It does not change this design, because the design never writes an off-wheel filtername once the filter has a slot.
3. Do today's `POST /api/ts-templates`, `POST /api/ext/nina-ts-sync/links` and `POST /api/plans/match` accept bodies with extra keys without a 400? `_validate_ts_templates_payload` and `_validate_fingerprint` read named fields only, which suggests yes, but the links route in the extension was not read. Phase 4 depends on it.
4. What filtername does TS need for an OSC camera with no wheel (the ASI294MC Pro has a single filter "NoFilter")? Section 2's rule inserts nothing without a known slot, so such a camera's templates would wait forever. The answer is probably to treat an empty slot list on a colour camera as one implicit slot "NoFilter", but I have not seen what TS writes there.
5. Should the Tidy offset checkbox default to on? Nine of ten originals use offset 25 and Rohan's darks are offset 25, but id 3 (G) is -1, and the checkbox sets 25 on every kept template, including ones whose offset he left at Default for a reason. This spec leaves it off.
6. Is a cap of 5000 plans per template in the template post enough?
7. A secondary profile's adoption records the gear values as its base (section 5). A later gear edit then finds TS different from the base and holds it as a conflict for that profile. Is that acceptable, or should the first push to a secondary profile ask once whether to take its values?
