# `/api/plans` audit for the ACP NINA Plugin

Snapshot: 2026-05-19. This document records what the plugin (planned across v1.0
Framing Wizard, v1.1 per-filter UI, v2.0 Target Scheduler) needs from ACP's HTTP
API, what's already there, and the concrete gaps to close before plugin work
starts.

## Current plan schema (from data/plans.json, n=31)

Fields present on every plan:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Required. |
| `guid` | string | Auto-assigned. |
| `created_at`, `updated_at` | ISO string | |
| `last_synced_at` | ISO string or null | Set by `/api/sync`. |
| `project_name` | string | TS uses this as Project grouping key. |
| `state` | string | `"draft"` etc — vocabulary not formalised. |
| `priority` | string | `"low"` / `"normal"` / `"high"`. |
| `min_altitude_deg` | int/float | |
| `meridian_window_min` | int | Present on 1/31 (defaults to 0 elsewhere). |
| `telescope_id`, `camera_id` | string | Opaque IDs into `/api/gear`. |
| `target.name` | string | |
| `target.center_ra_deg` | float | **DEG, not hours.** TS needs hours, plugin will need to convert. |
| `target.center_dec_deg` | float | DEG. |
| `target.rotation_deg` | int/float | |
| `target.target_id` | always null | **Vestigial — drop or populate.** |
| `target.mosaic.rows` | int | |
| `target.mosaic.cols` | int | |
| `target.mosaic.overlap_pct` | int/float | |
| `filter_goals.<f>.target_hours` | int/float | Per-filter integration goal. |

Fields present on a subset of plans:

| Field | Coverage | Notes |
|---|---|---|
| `filter_goals.<f>.sub_exposure_s` | **0/31** | Validated by the API but never populated. Falls back to `ts_base_snapshot.templates_by_filter.<f>.defaultexposure` or hardcoded 300s. |
| `filter_goals.<f>.actual_hours` | 1/31 | Live progress; usually missing. |
| `ts_base_snapshot` | 1/31 | Whole TS-state snapshot at last sync. |
| `ts_refs` | 1/31 | TS row IDs from last sync (project_id, target_ids_by_panel, etc.). |

## What each consumer needs

### v1.0 Framing Wizard push

Reference: `Josh-Jones-76/AstroPM.NINA.Plugin/Models/ProjectTarget.cs` (MIT). Fields they pass to Framing Wizard:

| Field (in plugin) | Source in ACP |
|---|---|
| `ra_hours` | `target.center_ra_deg / 15` |
| `dec_degrees` | `target.center_dec_deg` |
| `rotation_deg` | `target.rotation_deg` |
| `panel_rows` / `panel_columns` | `target.mosaic.rows` / `.cols` |
| `panel_overlap_percent` | `target.mosaic.overlap_pct` |
| `target_name` | `target.name` |
| `project_name` | `project_name` |
| `telescope_name` | `/api/gear` lookup via `telescope_id` |
| `telescope_focal_length_mm` | `/api/gear` |
| `camera_name` | `/api/gear` via `camera_id` |
| `camera_pixel_width` / `_height` | `/api/gear` |
| `camera_pixel_size_um` | `/api/gear` |
| `location_name` | `/api/sites` (active site) |
| `panels[]` | computed in `_mosaic_panel_centers` (currently server-side only) |

### v1.1 Per-filter exposure UI (display only)

| Field | Source |
|---|---|
| filter names | keys of `filter_goals` |
| target hours | `filter_goals.<f>.target_hours` |
| sub exposure seconds | **GAP** — see below |
| gain / offset / bin | `/api/gear` → `cameras[].filters.<f>.gain/offset/bin` |
| default sub seconds | `/api/gear` → `cameras[].filters.<f>.default_sub_s` |
| TS template name | `/api/gear` → `cameras[].filters.<f>.ts_template_name` |
| desired sub count | derived: `target_hours * 3600 / sub_s` |
| acquired sub count | derived: `actual_hours * 3600 / sub_s` |
| actual hours | `filter_goals.<f>.actual_hours` (often missing) |

### v2.0 Target Scheduler push

Today this lives in `app.py:_build_ts_export()`. Key insight: **TS sync is a ZIP-based Import Profile generator, not a direct sqlite write.** The only sqlite touch is `PRAGMA user_version` to set the metadata's `DatabaseVersion`.

`_build_ts_export()` reads from each plan:
- everything in v1.0 above
- `min_altitude_deg`, `meridian_window_min`, `priority`
- `filter_goals.<f>.target_hours`, `sub_exposure_s` (with 300s fallback), `actual_hours` (with 0 fallback)
- joins with `/api/gear` for FOV calc and filter template metadata

And produces TS-shape JSON (PascalCase, Newtonsoft.Json compatible) with `Project`, `Target`, `ExposurePlan`, `ExposureTemplate` schemas. Mosaic expansion (`_mosaic_panel_centers`) and strictest-wins grouping all happen server-side.

## Gap analysis

### (a) Present and good

- All geometry fields (RA/Dec/rotation/mosaic).
- Project grouping key (`project_name`).
- Constraint fields (`min_altitude_deg`, `priority`).
- Gear references via opaque IDs + `/api/gear` for resolution.
- Sites via `/api/sites` + active-site concept.
- The existing TS export logic — well-factored, plugin can fetch a "compiled" payload rather than re-implementing.

### (b) Present but shaped wrong / weakly typed

1. **Numeric fields typed as `float | int`.** `target_hours`, `rotation_deg`, `overlap_pct`, `min_altitude_deg`, `target_hours` are all mixed-type across plans. C# JSON deserialisation handles this but it requires `double` everywhere defensively. **Recommendation: normalise on write (always `float`)** so C# can use simpler types.
2. **`priority` as string vs TS's int enum.** ACP stores `"high"`, TS expects `"High"`. Already handled in `_build_ts_export` via `_TS_PRIORITY_NAME`. If the plugin handles its own TS payload generation later, this mapping needs to live somewhere central.
3. **`state` vocabulary not formalised.** Currently `"draft"` is the only observed value. **Recommendation: document the allowed values** (draft, active, complete, archived?) and validate on POST.

### (c) Missing from /api/plans but available elsewhere

1. **Gear details for plugin display.** Plugin needs to do two HTTP calls (`/api/plans` + `/api/gear`) and join. **Recommendation: add `GET /api/plans?expand=gear,site,panels` that returns enriched plans server-side.** Optional opt-in; default `/api/plans` stays raw for compatibility.
2. **Mosaic panel coordinates.** `_mosaic_panel_centers()` is server-side only. **Recommendation: include panel array under `expand=panels`** with `[{row, col, ra_deg, dec_deg}, ...]`. Avoids reimplementing the spherical math in C#.
3. **TS-ready payload.** When v2.0 lands, the plugin shouldn't reimplement `_build_ts_export`. **Recommendation: expose `GET /api/ts-export?plan_ids=…` that returns the same payload `/api/sync` writes to the ZIP** — i.e. `{projects: […], exposureTemplates: […]}` — without writing a file. The plugin can ship that straight to TS as the user's local sqlite write.

### (d) Missing from ACP entirely (or near-entirely)

1. **`sub_exposure_s` never persisted on plans.** Currently relies on TS snapshot fallback or hardcoded 300s. Plugin UI shows "blank" or "300s" for every plan without a TS snapshot. **Recommendation: when a plan is saved, persist `sub_exposure_s` per filter, defaulting from `cameras[].filters.<f>.default_sub_s`.** Cheap one-line write in the UI's plan save flow.
2. **`actual_hours` per filter.** Set on 1/31 plans. The "actuals" pipeline isn't really wired up — `/api/sync` doesn't write it; nothing else populates it. **Recommendation: defer.** Actuals come from re-scanning the manifest and matching to the plan's project_name. Not blocking for plugin v1.0/v1.1. v2.0 may want it for "Acquired/Accepted" counts in the TS payload.
3. **Plan version / cache validation.** The plugin will be polling `/api/plans` for updates. Today there's no `If-Modified-Since` / ETag / version int. **Recommendation: add `/api/version` and a top-level `last_modified` on the plans response.** Lets the plugin poll cheaply.
4. **CORS headers.** NINA runs as a desktop app and makes HTTP calls — usually fine without CORS, but worth confirming. **Recommendation: add `Access-Control-Allow-Origin: *` for read-only endpoints (`/api/plans`, `/api/gear`, `/api/sites`, `/api/version`)** to remove any ambiguity.
5. **`target.target_id`.** Always null, type-annotated as such. **Recommendation: either populate it (link to manifest target ID) or drop the field.** Dead schema noise.

## Recommended ACP-side changes before plugin work starts

In priority order:

| # | Change | Effort | Blocks plugin? |
|---|---|---|---|
| 1 | Add `GET /api/version` returning ACP version + plans `last_modified` | tiny | No, but plugin will want it day one |
| 2 | Add `?expand=gear,site,panels` mode on `/api/plans` returning enriched plans | small | No, plugin can do client-side join; this is convenience |
| 3 | Persist `sub_exposure_s` on plan save (default from gear) | small | v1.1 yes (per-filter UI) |
| 4 | Add CORS headers to read-only endpoints | trivial | Confirm-only |
| 5 | Document allowed `state` values; validate on POST | trivial | No |
| 6 | Normalise numeric types on write (always float) | small | No |
| 7 | Add `GET /api/ts-export` JSON endpoint mirroring `/api/sync`'s ZIP contents | medium | v2.0 yes |
| 8 | Decide fate of `target.target_id` (populate or drop) | trivial | No |

**Items 1, 3, 4 are pre-plugin-work fixes.** Items 2 and 7 land alongside plugin development as ACP-side support for v1.0 and v2.0 respectively.

## Decisions to lock before writing C#

1. **Does the plugin do server-side joins or client-side?** Recommend server (item 2 above) — keeps the C# thin.
2. **Does the plugin generate its own TS payload, or fetch the compiled one from ACP?** Recommend fetch (item 7 above) — single source of truth for TS schema. ACP version controls TS schema compatibility, plugin doesn't have to track it.
3. **Polling vs. push?** Plugin should poll `/api/plans` (with version check from item 1). Push would require ACP to know about the plugin, which fights the architecture.
4. **Auth?** None for v1.0. ACP listens on localhost by default. If users open it on a LAN, that's their call; plugin assumes it can reach the configured URL.
