# API and manifest reference

ACP is a Flask app that exposes a small JSON API. This doc covers every public endpoint, the shape of the manifest JSON the app reads, and the data contracts the gap-finder and the plugin platform rely on.

- [HTTP API](#http-api)
- [Manifest schema](#manifest-schema)
- [Sites and visibility](#sites-and-visibility)
- [Coverage sources, tiles, catalogues](#coverage-sources-tiles-catalogues)
- [Plans, gear, and NINA Target Scheduler sync](#plans-gear-and-nina-target-scheduler-sync)
- [Gap-finder](#gap-finder)

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /` | Frontend HTML |
| `GET /api/manifest` | Slim manifest JSON (no large path lists) |
| `GET /api/target/<id>` | Full per-target detail |
| `GET /api/catalogs` | Overlay catalogue payloads |
| `GET /api/catalog-registry` | Declarative list of catalogues to surface in the rail |
| `GET /api/sources` | All registered coverage sources |
| `GET /api/moc/<source_id>` | FITS MOC blob for a survey source, lazy-fetched, cached |
| `GET /api/tile-sources` | Metadata for every registered `PrioritisedTilesSource` |
| `GET /api/tiles/<source_id>` | Tile list for one source (server-side filter optional) |
| `GET /api/saved-searches`, `POST` | CRUD for saved Inventory filter bundles |
| `DELETE /api/saved-searches/<id>` | Remove a saved search |
| `GET /api/observability?lat=&lon=&time=` | Altaz for every target at one moment |
| `GET /api/visibility` | 12-month per-target visibility bins for a site |
| `GET /api/visibility/point?ra=&dec=` | Same bins for an arbitrary (RA, Dec) point |
| `POST /api/visibility/panels` | Aggregated visibility bins for a list of mosaic panel centres |
| `GET /api/sites`, `POST` | CRUD for saved observing sites |
| `GET /api/gaps` | Multi-source gap-finder JSON response |
| `GET /api/gaps/moc.fits` | Gap MOC as raw FITS bytes |
| `GET /api/export/priority` | Legacy CSV of Hα-but-no-SII candidates |
| `GET /api/gear`, `POST` | Telescopes + cameras |
| `POST /api/gear/seed` | Merge manifest-derived gear into `gear.json` |
| `GET /api/plans`, `POST` | Session plan CRUD |
| `GET /api/plans/<id>`, `PUT`, `DELETE` | Single-plan operations |
| `POST /api/plans/match` | Score every plan against a connected rig's gear fingerprint |
| `POST /api/plans/<id>/progress` | Record acquired hours against a plan's filter goals |
| `GET /api/fingerprints` | Last gear fingerprint each NINA profile reported |
| `GET /api/target-overrides`, `POST` | Per-target metadata overrides |
| `GET /api/destinations`, `POST` | CRUD for multi-rig sync destinations |
| `GET /api/ts-templates` | NINA TS plugin's exposure templates (if installed) |
| `POST /api/sync` | Build a NINA Target Scheduler import zip from current plans |
| `GET /api/sync/download/<filename>` | Download a zip previously built by `/api/sync` |
| `GET /api/publish/config` | `{"live_page_enabled": bool}`, true when `ACP_PUBLISH_DEST` is set; the plan editor shows its Public page section only then |
| `GET /api/public/shooting` | Sanitised document of public plans for the live page at astrowithroro.com/live ([spec](specs/shooting-page.md)) |
| `POST /api/publish/shooting` | Write that document to `data/live/` and upload it over SFTP to `ACP_PUBLISH_DEST` |
| `GET /api/extensions/manifest` | UI-action manifest registered by [extensions](extensions.md#ui-manifest), drives the Extensions rail accordion and the core-button swap mechanism |
| `GET /api/ext/...` | Routes registered by [extensions](extensions.md) |
| `GET /api/version` | `{version, plans_last_modified, manifest_last_modified}`, lets a client poll cheaply instead of refetching plans on a timer |

The API is unauthenticated by default and binds to `127.0.0.1`, see the [security notes in the archive setup guide](setup-archive.md#security-and-deployment-notes) before exposing it on a network, and [Optional bearer-token auth](#optional-bearer-token-auth) below if you do.

### Optional bearer-token auth

Set `ACP_API_TOKEN` to require `Authorization: Bearer <token>` on every request under `/api/*`. Leave it unset (the default) and every request passes, same as before this existed, this is meant for the case where you've put ACP on a LAN or a NUC that the NINA plugin reaches over the network, not for a stock loopback install. A request to `/api/*` with a missing or wrong token gets `401 {"error": "unauthorized"}`; the token is compared with `hmac.compare_digest`, not `==`. The HTML page (`GET /`) and static files are never gated, so a browser can always load the UI, only the JSON API is behind the token. ACP logs one line at startup saying whether API auth is on.

The API sends no CORS headers and grants no preflight, deliberately. It used to send `Access-Control-Allow-Origin: *` on reads and answer the preflight, on the reasoning that withholding the header from write responses kept writes closed. That is half right: it stops an attacker reading the reply, not sending the request. Since the preflight listed `PUT` and `DELETE` as allowed, any web page a user visited could delete their plans or publish one, silently, because ACP normally runs on the same machine as their browser.

Nothing needs it. The NINA plugin is a desktop HTTP client and never sees CORS, and ACP's own page is same-origin. If a browser-based client is ever wanted, allow that single origin by name, never `*`, and never on a preflight for a method that writes.

## Manifest schema

The manifest is the JSON file ACP reads at startup (default path `data/manifest.json`). It's produced by `scripts/build_archive_manifest.py` from your FITS/XISF archive, see [setting up your own archive](setup-archive.md) for how to build it.

Minimal shape:

```jsonc
{
  "scan_date": "2026-04-19T10:30:00",
  "total_targets": 5,
  "total_integration_hours": 28.9,
  "targets": [
    {
      "target_id": 1,
      "objects": ["Eta Carinae"],
      "center_ra_deg": 161.26,
      "center_dec_deg": -59.68,
      "center_l_deg": 287.60,
      "center_b_deg": -0.63,
      "fov_arcmin": [120.0, 90.0],
      "pix_arcsec": 1.5,
      "corners_icrs":     [[ra,dec], [ra,dec], [ra,dec], [ra,dec]],
      "corners_galactic": [[l,b],    [l,b],    [l,b],    [l,b]   ],
      "telescopes": ["RedCat 51"],
      "cameras": ["ZWO ASI2600MM Pro"],
      "date_range": ["2025-01-01", "2025-12-31"],
      "filters": {
        "Ha":  {"total_hours": 3.2, "files": 12},
        "OIII":{"total_hours": 1.4, "files": 6}
      },
      "master_files": ["/path/to/master_H.fit"]
    }
  ]
}
```

**Corner order** for `corners_icrs`: `[SW, NW, NE, SE]`. The frontend places the filter badge on the NW corner and rotates it with the FOV.

See `scripts/make_demo_manifest.py` for a runnable example that produces a valid minimal manifest.

## Sites and visibility

The viewer can compute year-long observability for any saved site or arbitrary sky point.

### Sites, `/api/sites`

`GET /api/sites` returns the saved sites plus the active one:

```json
{
  "active_id": "mauna_kea",
  "sites": [
    {"id": "mauna_kea", "name": "Mauna Kea, Hawaii", "lat": 19.82, "lon": -155.47, "elev_m": 4205, "min_alt_deg": 30}
  ]
}
```

`POST /api/sites` accepts `{"action": "create" | "update" | "delete" | "set_active", ...}` for full CRUD plus active-site selection. Sites are persisted to `data/sites.json`.

### Per-target visibility, `/api/visibility`

`GET /api/visibility?site_id=sydney` returns a 12-month bin for every target in the manifest:

```json
{
  "site": { "...resolved site..." },
  "bins": {
    "1": {"target_id": 1, "monthly": ["good", "good", "fair", "partial", "..."]},
    "2": { "..." }
  }
}
```

Each `monthly` array has 12 entries (one per month, January first), each one of `not_visible`, `partial`, `fair`, `good`, `great`. Computation runs astropy `AltAz` against a sun-darkness mask; results are cached per (site, manifest mtime).

### Arbitrary point, `/api/visibility/point`

`GET /api/visibility/point?ra=161.26&dec=-59.68&site_id=sydney` returns the same 12-month bin shape for one (RA, Dec) point, useful for the Inventory tile-detail panel.

## Coverage sources, tiles, catalogues

ACP's plugin platform lets extensions publish coverage data in three flavours, each with its own endpoint. The data contracts are defined as PEP 544 Protocols in `sources.py`, see [extensions](extensions.md) for how to author them.

### `/api/sources`

Lists every registered `CoverageSource` (the manifest, friend manifests, public-survey MOCs, anything an extension registers) plus its metadata. Used by the gap-finder's "Use sources" checkboxes and the legend.

### `/api/tile-sources` and `/api/tiles/<id>`

Lists every registered `PrioritisedTilesSource` (curated, ranked sky cells with per-band coverage status). These are the inputs to the **Inventory** rail. The accordion is hidden when no tile sources are registered, so a stock checkout doesn't show it.

`GET /api/tiles/<source_id>` returns the tile list for one source. Optional query params filter server-side: `?priority=1,2`, `?missing_band=Ha`, etc.

### `/api/catalog-registry` and `/api/catalogs`

`/api/catalog-registry` returns the declarative list of catalogues to surface in the right rail (loaded from `data/catalog_registry.json` plus anything `app.extra_catalogues` extensions register). `/api/catalogs` returns the actual catalogue *payloads* (the points themselves).

### `/api/saved-searches`

CRUD for saved Inventory filter bundles. The Inventory rail lets users name a combination of priority/missing-band/category filters and recall it later. State persists to `data/saved_searches.json`.

## Plans, gear, and NINA Target Scheduler sync

ACP lets you sketch imaging plans against the sky map, then export them as a NINA Target Scheduler (TS) import zip. This section covers the gear registry, plan CRUD, multi-rig destinations, and the sync endpoint itself.

### Gear: `/api/gear`

`GET /api/gear` returns `{version, telescopes: [...], cameras: [...]}`. Each camera carries `sensor_px: [width, height]` (the shape ACP's own frontend uses) and, when `sensor_px` is present, the equivalent `sensor_width_px` / `sensor_height_px` scalar fields (the NINA companion plugin reads the scalars and ignores `sensor_px`). The scalars are derived on every read, not stored separately, so they can't drift out of sync with `sensor_px`.

`POST /api/gear` replaces the full telescopes + cameras arrays. `POST /api/gear/seed` merges telescope/camera metadata observed in the manifest into `gear.json`, skipping anything that fuzzy-matches an existing entry by name.

### Plans: `/api/plans`

`GET /api/plans` returns `{version, plans: [...]}`. `POST /api/plans` creates or replaces a plan (matched by `id`); `PUT /api/plans/<id>` and `DELETE /api/plans/<id>` operate on one plan. A plan's `target.center_ra_deg` is normalised into `[0, 360)` (so -0.5 becomes 359.5 and 360.0 becomes 0.0, matching Aladin seam-drag and manual entry) rather than rejected; `target.center_dec_deg` must be in `[-90, 90]`. Every numeric field (RA, Dec, rotation, mosaic overlap, filter goal hours and exposures) must be finite: NaN and Infinity are rejected with a 400 rather than being written to `plans.json`. A `plans.json` written before this healing existed and containing legacy NaN/Infinity values is healed on load (the poisoned fields become `null`, logged as a warning) rather than 500ing every subsequent write.

`GET /api/plans?expand=gear,site,panels` is an opt-in enrichment for the NINA plugin: `gear` resolves `telescope_id` / `camera_id` against `gear.json` and adds the pair's `fov_arcmin` and `pixel_scale_arcsec`, `site` is the observing site (the plan's `site_id` if it has one, otherwise the first site), and `panels` is the mosaic's per-panel centres. Any subset of the names can be given. Without the parameter the response is unchanged. The response always carries a `Last-Modified` header taken from `plans.json`, so a client can poll cheaply.

A plan's optional `state` field controls whether it's eligible for sync. `state: "draft"` marks a plan as still being worked on, and draft plans are excluded from `/api/sync` (see below). Plans with no `state` field at all (anything written before this field existed) are treated as committed and keep syncing.

### Matching plans to connected gear: `POST /api/plans/match`

The companion NINA plugin posts a *fingerprint* of whatever gear is connected right now and gets back every plan with a verdict on whether that rig can shoot it. The rules live here rather than in the plugin so the two can't drift, and so ACP's own UI can show the same answers.

```json
{
  "profile_name": "Travel rig",
  "mode": "fit",
  "camera": {"name": "QHY268M", "sensor_px": [6252, 4176], "pixel_size_um": 3.76, "colour": false, "bin": 1},
  "filters": ["L", "R", "G", "B", "Ha", "OIII", "SII"],
  "mount": {"name": "EQ6-R Pro"},
  "site": {"lat": -33.87, "lon": 151.21, "elev_m": 40},
  "focal_length_mm": {"profile": 250.0, "solved": 540.4, "source": "solved"},
  "pixel_scale_arcsec": 1.436,
  "rotation_deg": 12.3,
  "nina_version": "3.3.0.1041"
}
```

Only `camera` (with `sensor_px` and `pixel_size_um`) and `focal_length_mm` are required: a rig with no filter wheel, no mount driver and no site set still gets an answer. `focal_length_mm` can be a plain number or the `{profile, solved}` pair, in which case the solved value wins, because a reducer, a different back focus or simply a stale profile entry all show up in the plate solve and nowhere else. `pixel_scale_arcsec` is used when given and derived from pixel size, bin and focal length otherwise. `mode` is `"fit"` or `"everything"`; it is echoed back and stored with the fingerprint but never changes a verdict, since all verdicts are returned and the caller decides what to show.

The response wraps each plan (in the same shape as `GET /api/plans?expand=gear,panels`) with a `match` block:

```json
{
  "fingerprint_id": "8f1c...",
  "mode": "fit",
  "plans": [
    {"id": "m42-ha", "...": "...",
     "match": {"verdict": "fit_with_warnings", "pixel_scale_ratio": 1.02,
               "fov_ratio": [1.1, 0.85], "filters_missing": [],
               "reasons": ["Field of view is 85% of the plan's, ..."]}}
  ],
  "summary": {"fit": 12, "fit_with_warnings": 2, "no_fit": 30, "unconstrained": 3}
}
```

The verdict is one of:

- `unconstrained`: the plan has no telescope or camera set (or points at gear no longer in `gear.json`), so there is nothing to match against.
- `no_fit`: the connected pixel scale is more than 15% off the plan's own pixel scale, or the field of view is below 80% of the plan's in either axis, or the plan has a goal for a filter the rig can't produce. Missing filters are listed in `filters_missing`.
- `fit_with_warnings`: the pixel scale is within 15% and nothing is missing, but the field of view is between 80% and 90% of the plan's (the framing will be tighter than planned), or a filter is only reachable through a dual band filter (so its hours can't be shot independently). One string per warning in `reasons`.
- `fit`: all three tests pass. A larger field of view than the plan's is always fine.

`pixel_scale_ratio` is connected divided by plan, and `fov_ratio` is the same ratio per axis. Both are `null` on an `unconstrained` plan.

Filter names are canonicalised with the same rules the archive scanner uses, so "Antlia Ha" and "Ha" are the same filter here and in the manifest, a colour camera with no filter wheel credits R, G and B, and a dual band filter credits Ha and OIII (a quad band adds SII). A plan goal with `target_hours` of zero is not something the rig has to be able to shoot.

### Reported rigs: `GET /api/fingerprints`

Every call to `/api/plans/match` stores the fingerprint in `data/fingerprints.json` under its `profile_name` (falling back to the fingerprint id when the profile has no name), along with the id, the time it arrived, the `mode` and the summary counts. `GET /api/fingerprints` returns `{version, profiles: {...}}`. The endpoint is read-only; `/api/plans/match` is the only writer, and a failed write is logged rather than costing the caller its match results. The file carries your observing site's latitude and longitude, so it is gitignored along with the rest of the private data under `data/`.

The Planning rail shows a read-only "NINA rigs" accordion built from this endpoint: one line per profile with its camera, solved focal length, fit count and when it last reported. The accordion stays hidden until at least one NINA install has reported.

### Progress: `POST /api/plans/<id>/progress`

Records what a session actually acquired against a plan's per-filter goals:

```json
{"filters": {"Ha": {"acquired_hours": 1.5, "acquired_count": 18}}, "source": "ts", "at": "2026-09-04T11:00:00+00:00"}
```

Each entry updates `filter_goals.<f>.actual_hours`. Hours only ever go up: NINA's own acquired count drops legitimately when frames are culled or a project is reset, and silently rewinding a plan the user has watched fill up is worse than being one session stale. Pass `"force": true` to overwrite downward on purpose. Filter names are canonicalised, so a plugin reporting "Antlia Ha" updates the plan's `Ha` goal.

`acquired_count` is validated but not stored: the plan schema carries hours, and `/api/sync` derives the acquired sub count from hours and sub exposure. Filters the plan has no goal for are ignored rather than invented.

```json
{"ok": true, "plan": {"...": "the updated plan"},
 "updated": {"Ha": 1.5}, "unknown_filters": ["SII"], "not_lowered": []}
```

`updated` is what changed, `unknown_filters` is what was dropped, and `not_lowered` names goals whose stored value was higher than what was reported and `force` was not set. An unknown plan id is a 404; a bad payload is a 400 and nothing is written.

### Destinations: `/api/destinations`

Multi-rig setups (more than one NUC/rig each running its own NINA + Target Scheduler) can register a *destination* per rig instead of relying on the single global TS database path. `GET /api/destinations` returns `{version, destinations: [...]}`, empty when `destinations.json` doesn't exist (single-rig users never see the concept). `POST /api/destinations` replaces the full list.

Each destination is:

```json
{
  "id": "victoria",
  "label": "Remote Victoria Observatory",
  "kind": "local_db",
  "ts_db_path": "C:/Users/me/AppData/Local/NINA/SchedulerPlugin/schedulerdb.sqlite"
}
```

`kind` is `"local_db"` (requires `ts_db_path`, the TS sqlite ACP reads `PRAGMA user_version` from when syncing) or `"shared_file"` (requires `export_path`, the file `/api/sync` writes to directly; `acquired_path` is optional and is where a NUC-side daemon can write acquisition progress back for ACP to poll).

A plan opts into a destination via its own `destination_id` field. The first time destinations are declared, any existing plan without a `destination_id` is backfilled to the first destination, once: the migration is flagged in `destinations.json` so it never re-runs after a plan is explicitly unassigned.

### Sync: `POST /api/sync`

Builds a Target Scheduler import zip (`metadata.json`, `exposureTemplates.json`, `projects.json`) from the current plans and returns the result:

```json
{
  "ok": true,
  "plan_count": 3,
  "skipped_draft_count": 1,
  "destination_id": null,
  "project_count": 2,
  "template_count": 4,
  "zip_path": "/path/to/acp-sync-20260710T120000Z.zip",
  "zip_filename": "acp-sync-20260710T120000Z.zip",
  "download_url": "/api/sync/download/acp-sync-20260710T120000Z.zip",
  "warnings": [ "...strictest-wins conflicts, mosaic FOV issues..." ],
  "conflicts": [ "...same list, alias..." ]
}
```

Draft plans (`state: "draft"`) are always excluded; `skipped_draft_count` reports how many so the UI can surface it. Plans are grouped into TS Projects by `project_name` (or, when blank, one Project per plan named after its target); a mosaic plan's `rows`/`cols` expand into per-panel Targets and the Project's `IsMosaic` flag is set accordingly. RA is normalised with `% 360` on export as a defence for any plan written before RA-range validation existed.

By default (no `destination_id`) every non-draft plan is bundled and the zip is written under `ZIP_OUTPUT_DIR`, downloadable via `download_url`. This is the pre-multi-rig behaviour and is what the existing NINA plugin integration expects.

Pass an optional `destination_id` (JSON body `{"destination_id": "victoria"}` or query param `?destination_id=victoria`) to scope the sync to one rig: only plans whose `destination_id` matches are bundled, and that destination's own configured path is used instead of the global one. That's `ts_db_path` for the `PRAGMA user_version` probe on a `local_db` destination, or `export_path` as the zip's own output path (no `download_url`, no `zip_path` under `ZIP_OUTPUT_DIR`) on a `shared_file` destination. An unknown `destination_id` returns 400; a destination with no matching plans (after draft exclusion) returns the same "no plans to sync" 400 as an empty sync. `destinations.json` isn't validated on load (only `POST /api/destinations` validates), so a hand-edited entry with a `kind` other than `local_db`/`shared_file` also returns 400 naming the bad kind, rather than silently falling back to the global paths.

### Download: `GET /api/sync/download/<filename>`

Serves a zip previously built by `/api/sync` from `ZIP_OUTPUT_DIR`. `filename` must match the `acp-sync-<UTC timestamp>.zip` pattern `/api/sync` produces; anything else is rejected before `send_from_directory` is even called, as defence in depth against path traversal.

## Gap-finder

When planning narrowband sessions you usually want to know where one filter has been imaged but another hasn't yet, so a follow-up session adds new data instead of duplicating coverage. ACP unions every coverage source you've enabled (your manifest, friend manifests, public-survey MOCs), intersects that union with public catalogue candidates, and gives you a CSV you can paste into NINA.

### How to use it from the UI

The control lives in the **Catalogues** rail:

- **Have** / **Missing** dropdowns, pick the two filters. Defaults to `Hα` and `SII`.
- **Two hour thresholds**, `≥ N h` for the *have* side (a region only counts as covered if at least one source has stacked at least this many hours), `< N h` for the *missing* side. Defaults: `1.0` and `0.5`.
- **Use sources**, checkbox per registered source. All checked by default.
- **Find gaps**, fetches `/api/gaps`, mounts a yellow MOC over the gap region on the map, scatters catalogue candidates that fall inside it, and writes a one-line summary (`sky 0.84% • 1808 candidates • from manifest, iphas_ha`) under the buttons. Click again to hide.

### API endpoints

```
GET /api/gaps?have=Ha&missing=SII&sources=manifest,iphas_ha&min_have_hours=1&max_missing_hours=0.5
GET /api/gaps/moc.fits?<same query>
```

The JSON response carries `gap_sky_fraction`, `candidates`, the resolved `have_sources` / `missing_sources` lists, any sources skipped (with reasons), and a `moc_url` pointing at the FITS MOC for the same query. The FITS endpoint serves raw bytes, useful if you'd rather load the gap into Aladin desktop or `mocpy` directly.

### Legacy CSV route

The legacy `/api/export/priority` CSV route stays for backwards compatibility, same shape and headers as before, hardcoded to "Hα but no SII over the manifest source only". The new gap-finder doesn't replace it; pick whichever fits your workflow.

### Without `mocpy`

`/api/gaps` and `/api/gaps/moc.fits` return `503` (MOC algebra is the whole point of these routes). `/api/export/priority` still works, it falls back to the original inline implementation. `pip install mocpy` to enable the full gap-finder UI.
