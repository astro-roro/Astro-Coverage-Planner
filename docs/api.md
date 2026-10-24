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
| `POST /api/visibility/panels/batch` | The same for several plans at once: `{"plans": [{"id", "panels"}]}` in, `{"results": {id: ...}}` out. At most 400 panels in total |
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
| `POST /api/plans/<id>/accepted` | Store Astro Auto Calibration's accepted counts per filter row |
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
| `GET /api/settings` | Where each setting came from, whether the environment locks it, `first_run`, `writable` and the version. Folder and manifest paths go to an authorised client only |
| `PUT /api/settings` | Save `fits_roots` (and `shortcuts` where available). Authorised client only, JSON body |
| `POST /api/settings/adopt-env` | Copy the folders in `FITS_ROOTS` into the settings file. Authorised client only |
| `POST /api/settings/check-path` | Body `{"path": "..."}`. Checks one folder against the saved list and writes nothing. Returns `{code, warnings}`. Authorised client only |
| `GET /setup` | The first-run and settings page |
| `POST /api/scan/start` | Start one scan now. 202, or 409 `scan_running`, or 400 `no_archive_roots`. Authorised client only |
| `GET /api/scan/status` | Scan state, with `progress` (`{stage, files_seen}`, never a path) and `elapsed_seconds` while running |
| `POST /api/catalogues/fetch` | Download the catalogue overlays in the background. 202, or 409 `fetch_running`. Authorised client only |
| `GET /api/catalogues/status` | `{running, finished, error_kind, last_finish, present}` |

The API is unauthenticated by default and binds to `127.0.0.1`, see the [security notes in the archive setup guide](setup-archive.md#security-and-deployment-notes) before exposing it on a network, and [Optional bearer-token auth](#optional-bearer-token-auth) below if you do.

### Optional bearer-token auth

Set `ACP_API_TOKEN` to require a credential on every request. Leave it unset (the default) and every request passes, same as before this existed. It is meant for the case where you have put ACP on a LAN or a NUC that the NINA plugin reaches over the network, not for a stock loopback install. ACP logs one line at startup saying whether auth is on, and warns if the token is under 16 characters or contains non-ASCII characters.

Two credentials are accepted, and both are compared with `hmac.compare_digest` rather than `==`.

**`Authorization: Bearer <token>`** is what the NINA plugin and any script should send. A request with a missing or wrong token gets `401 {"error": "unauthorized"}`.

**A session cookie** is what a browser uses. Navigating to any page without a credential returns the sign-in form with status 401. Posting the token to `POST /login` sets `acp_session`, which is `HttpOnly`, `SameSite=Strict`, `Secure` when the request arrived over https, and valid for 30 days. `POST /logout` clears it. The cookie value is derived from the token rather than being the token, so what sits in the cookie jar is not the string you paste into the plugin.

Until 2026-09-07 the gate covered `/api/*` only, and the page and static files were served to anyone. The effect was that turning the token on gave you the app shell and a 401 on every fetch it made, so the browser UI was dead and nobody would enable the one setting that closes the LAN exposure. The gate now covers every path except `/login` and `/favicon.ico`.

The API sends no CORS headers and grants no preflight, deliberately. It used to send `Access-Control-Allow-Origin: *` on reads and answer the preflight, on the reasoning that withholding the header from write responses kept writes closed. That is half right: it stops an attacker reading the reply, not sending the request. Since the preflight listed `PUT` and `DELETE` as allowed, any web page a user visited could delete their plans or publish one, silently, because ACP normally runs on the same machine as their browser.

Nothing needs it. The NINA plugin is a desktop HTTP client and never sees CORS, and ACP's own page is same-origin. If a browser-based client is ever wanted, allow that single origin by name, never `*`, and never on a preflight for a method that writes.

### Settings: `/api/settings`

The routes behind the settings file described in [the archive setup guide](setup-archive.md#the-settings-file).

`GET /api/settings` answers every client with `version`, `first_run` (no folders set and no manifest yet), `writable` (false when ACP has no folder it can write a settings file to, and always false inside a container), `error` (`null`, or `settings_file_corrupt`), `shortcuts` (`{available, value}`) and `keys`. Each entry in `keys` has `source` (`env`, `file` or `default`) and `locked` (true when the environment sets it), and a `value` except that `fits_roots` and `manifest_path` carry no `value` for a client that is not authorised. An authorised client also gets `data_dir`.

An authorised client is one that passed the access gate with the bearer token or session cookie, or, when `ACP_API_TOKEN` is not set, one connecting from the same machine (`127.0.0.1`, `::1`) with no `X-Forwarded-For` header and a `Host` of `localhost`, `127.0.0.1` or `[::1]`. The `Host` rule stops a web page on another site from reaching ACP through a DNS name it has pointed at your machine.

`PUT /api/settings` and `POST /api/settings/adopt-env` need an authorised client and `Content-Type: application/json`, and refuse a cross-site `Origin`. Errors are `{"error": <code>}`:

| Status | Code | Meaning |
|---|---|---|
| 400 | `unknown_key` | The body has a key other than `fits_roots` or `shortcuts`. `port`, `manifest_path` and `scan_cron` are edited in the file. |
| 400 | `not_absolute`, `invalid`, `duplicate`, `too_many` | A folder failed the syntax check. Nothing is saved. |
| 400 | `not_found`, `not_a_directory`, `not_readable`, `timed_out` | A folder failed the disk check. The reply also has `index`, the position of the folder in the list. Nothing is saved. A folder that does not answer within 5 seconds gives `timed_out`. |
| 400 | `unavailable` | `shortcuts` where shortcuts are not supported. |
| 400 | `nothing_to_adopt` | `adopt-env` with no `FITS_ROOTS` set. |
| 400 | `bad_host` | The `Host` header does not name this machine. |
| 403 | `forbidden` | Not an authorised client. |
| 409 | `locked_by_env` | The environment sets that key. |
| 409 | `scan_running` | `fits_roots` cannot change during a scan. |
| 409 | `not_writable` | There is no writable settings folder. |
| 415 | `unsupported_media_type` | Not a JSON body. |

A successful `PUT` of `fits_roots` returns the settings view plus `warnings`, a list of `{index, code}` where `code` is `overlaps`, `very_large` (the top of a drive) or `no_images_seen`. Warnings never block a save.

`POST /api/settings/check-path` answers 200 with `{"code": "ok" | <blocking code>, "warnings": [...]}` for any string, using the codes above plus `duplicate` and `too_many` measured against the saved list. It judges the path's syntax before it touches the disk, and it never returns the path or an operating system message.

`POST /api/scan/start` and `POST /api/catalogues/fetch` follow the same rules as `PUT`: an authorised client, a JSON body (`{}` will do) and a matching `Origin`. The scan claims its slot before it answers, so two quick requests give one 202 and one 409. Scan failures show up in `GET /api/scan/status` as `last_error_kind` with a fixed message.

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
      "rotation_deg": 32.4,
      "rotation_source": "wcs",
      "footprint_arcmin": [120.0, 90.0],
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

**Corner order** for `corners_icrs`: `[SW, NW, NE, SE]` of the box before it is turned. The frontend places the filter badge on whichever corner ends up furthest north-west and rotates it with the FOV.

**Rotation.** `rotation_deg` is the angle the target was shot at: the position angle of the image's +Y axis, in degrees east of north, folded into 0 to 180 (a rectangle looks the same turned half a circle). It is the same convention as a plan's `target.rotation_deg`. The scanner reads it from each master's plate solve and stores the unfolded angle, 0 to 360, on each `per_master_fov` entry. `corners_icrs` and `corners_galactic` are the box turned to that angle, and `footprint_arcmin` is its size, which can be larger than `fov_arcmin` when the masters differ.

`rotation_source` says where the angle came from:

- `wcs`: every master has a plate solve and they agree within 3 degrees.
- `wcs_partial`: they agree, but some masters had no usable angle.
- `wcs_mixed`: the masters were shot at different angles. The angle with the most integration wins, the box grows to hold every frame, and `rotation_alternates` lists the other angles with their hours.
- `none`: no master gave an angle, so the box is drawn north up.

A manifest written before these fields existed has none of them and draws north up, as it always did.

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

A plan's optional `state` field is one of `"draft"`, `"active"`, `"inactive"` or `"closed"`, the four Target Scheduler project states; any other value is a 400. Plans with no `state` field at all (anything written before this field existed) are treated as `"active"`. `minimum_time_min` is an optional non-negative integer, minutes, defaulting to 0 when absent. Both fields sync both ways with TS: see `docs/specs/ts-project-settings.md`. Only `"closed"` plans are excluded from `/api/sync` (see below); draft and inactive plans still sync, because TS needs the project row to exist so a later push can pause or unpause it.

`PUT /api/projects/<project_name>/settings` saves `state`, `priority` and `minimum_time_min` on every plan that shares that `project_name`, in one write, so a group of plans in one TS project can never end up half-saved. Body: `{"state": "inactive", "priority": "high", "minimum_time_min": 30}`; any subset of the three keys can be given. An unknown `project_name` (no plan carries it) is a 404.

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

`supports_state: true` tells this endpoint the caller (the NINA plugin's "Sync for tonight") understands plan state. With it, every plan comes back, including draft, inactive and closed ones; only `"active"` plans get a real verdict and count in `summary`, the rest get `"match": {"verdict": "held", "state": "<state>"}`. Without it (an older plugin, or ACP's own planning rail), only active plans are returned at all, same as before this field existed.

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

A mosaic's goals are per panel, so the plan's total goal for a filter is the row goal times rows x cols, and the planner works out hours left against that total.

A mosaic can also report per panel. Add `panels` to an entry, a map from panel key to the hours acquired on that panel. The key is `"row,col"` counted from 1, the same key `ts_links.refs.target_ids_by_panel` uses (`"1,1"`, `"2,1"`):

```json
{"entries": [{"filter": "L", "template": "L", "acquired_hours": 3.5, "panels": {"1,1": 2.0, "2,1": 1.5}}]}
```

The map is stored on the row as `panels`, and the row's `actual_hours` becomes the sum of the map, whatever `acquired_hours` said. Reported panels merge into the stored map and each panel only goes up unless `force` is set, which replaces the whole map. A panel that would have gone down is named in `not_lowered` as `"<template>#<key>"`. Keys must look like `"1,1"` and values must be numbers from 0 up to the same cap as `acquired_hours`, or the whole request is a 400. Keys outside the mosaic's grid are kept but ignored when totalling.

An empty `panels` map is treated as no `panels` at all. A report without `panels` (every existing caller) behaves as before on a row with no map. On a row that already has a map it cannot say which panel the figure belongs to, so it is ignored and the row is listed in `panel_rows_kept`; with `force` it overwrites `actual_hours` and drops the map.

On `/api/sync`, a mosaic row with a `panels` map gives each exported panel target its own `Acquired` and `Accepted` (a panel missing from the map gets 0). Without a map every panel gets the plan's `actual_hours` as before, and a single target never reads the map. A mosaic row with no map is in a transitional state: its single figure came from a caller that read one panel, the planner counts it against the plan total, and the next `/sync-acquired` from the NINA Target Scheduler extension replaces it with a per-panel map.

`acquired_count` is validated but not stored: the plan schema carries hours, and `/api/sync` derives the acquired sub count from hours and sub exposure. Filters the plan has no goal for are ignored rather than invented.

```json
{"ok": true, "plan": {"...": "the updated plan"},
 "updated": {"Ha": 1.5}, "unknown_filters": ["SII"], "not_lowered": []}
```

`updated` is what changed, `unknown_filters` is what was dropped, and `not_lowered` names goals whose stored value was higher than what was reported and `force` was not set. An unknown plan id is a 404; a bad payload is a 400 and nothing is written.

### Accepted counts: `POST /api/plans/<id>/accepted`

Astro Auto Calibration (AAC) is the only caller. It sends the frames it accepted, per filter row, so the editor can show done against what AAC kept and the Target Scheduler sync can write the shortfall (docs/specs/goal-panel-and-ts-order.md section 6). The bearer token is checked like every other `/api` route, against `ACP_API_TOKEN`.

```json
{"source": "aac", "at": "2026-10-12T10:05:00Z", "filed_through_night": "2026-10-11", "min_total_frames": 10,
 "rows": [{"id": "<row id>", "filter": "Ha", "template": "Ha 300",
           "accepted_seconds": 20700.0, "accepted_frames": 69, "seen_seconds": 21600.0, "seen_frames": 72,
           "complete": false, "estimated_hours": null, "measurement": null}]}
```

A row is stored only when its `id` is on the plan, its `filter` reaches the row's filter (`Antlia Ha` is `Ha`), and its `template` equals the row's. It replaces the row's `aac` block, and values may go down. `actual_hours` is never changed. The plan gets `aac.filed_through_night`, `aac.min_total_frames` and `aac.received_at`. A save from the plan editor cannot change any of these.

```json
{"ok": true, "unknown_rows": [{"id": "<row id>", "reason": "row now has filter G and template G Stars"}],
 "filed_through_night": "2026-10-11"}
```

`unknown_rows` lists the rows that were not stored, with `no such row` or the row's current filter and template as the reason. A missing or wrong token is a 401, an unknown plan id a 404, and a malformed body a 400 with nothing written.

A plan also carries `target_class`, one of `survey`, `deep` and `pretty`, which AAC reads from `GET /api/plans`. A plan with none reads as `survey`, and any other value is refused with a 400.

### Destinations: `/api/destinations`

Multi-rig setups (more than one NUC/rig each running its own NINA + Target Scheduler) can register a *destination* per rig instead of relying on the single global TS database path. `GET /api/destinations` returns `{version, destinations: [...]}`, empty when `destinations.json` doesn't exist (single-rig users never see the concept). `POST /api/destinations` replaces the full list.

Each destination is:

```json
{
  "id": "remote",
  "label": "Remote Observatory",
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
  "skipped_closed_count": 1,
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

Only closed plans (`state: "closed"`) are excluded; `skipped_closed_count` reports how many so the UI can surface it. TS imports this zip as new projects every time, so a closed plan re-syncing here would recreate a project the user already finished and deleted from TS. Active, inactive and draft plans are all included, each project's `State` set from the plans sharing its `project_name`: the most-running value wins in the order active, inactive, draft (a project with plans in more than one state gets a warning, same as a priority or minimum-time disagreement). Plans are grouped into TS Projects by `project_name` (or, when blank, one Project per plan named after its target); a mosaic plan's `rows`/`cols` expand into per-panel Targets and the Project's `IsMosaic` flag is set accordingly. `MinimumTime` is the group's largest `minimum_time_min`, the same strictest-wins rule as `MinimumAltitude`. RA is normalised with `% 360` on export as a defence for any plan written before RA-range validation existed.

By default (no `destination_id`) every non-closed plan is bundled and the zip is written under `ZIP_OUTPUT_DIR`, downloadable via `download_url`. This is the pre-multi-rig behaviour and is what the existing NINA plugin integration expects.

Pass an optional `destination_id` (JSON body `{"destination_id": "remote"}` or query param `?destination_id=remote`) to scope the sync to one rig: only plans whose `destination_id` matches are bundled, and that destination's own configured path is used instead of the global one. That's `ts_db_path` for the `PRAGMA user_version` probe on a `local_db` destination, or `export_path` as the zip's own output path (no `download_url`, no `zip_path` under `ZIP_OUTPUT_DIR`) on a `shared_file` destination. An unknown `destination_id` returns 400; a destination with no matching plans (after closed exclusion) returns the same "no plans to sync" 400 as an empty sync. `destinations.json` isn't validated on load (only `POST /api/destinations` validates), so a hand-edited entry with a `kind` other than `local_db`/`shared_file` also returns 400 naming the bad kind, rather than silently falling back to the global paths.

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

## Activity: `/api/activity`

The Activity tab's events, in `docs/specs/activity-tab.md`. `POST /api/activity/events` takes `{"events": [...]}` (up to 100, 2 MiB) from the plugin, AAC or anything else with the token, and answers `{stored, updated, unchanged, stale, rejected}`; it answers 403 while `ACP_API_TOKEN` is unset. `GET /api/activity?nights=5&before=<night>`, `GET /api/activity/night/<night>?offset=200`, `GET /api/activity/summary`, `POST /api/activity/seen`, `POST /api/activity/events/<id>/dismiss` and `/restore`, and `GET`/`PUT /api/activity/prefs` serve the tab. With `ACP_ACTIVITY=off` all of them answer 404.
