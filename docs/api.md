# API and manifest reference

ACP is a Flask app that exposes a small JSON API. This doc covers every public endpoint, the shape of the manifest JSON the app reads, and the data contracts the gap-finder and the plugin platform rely on.

- [HTTP API](#http-api)
- [Manifest schema](#manifest-schema)
- [Sites and visibility](#sites-and-visibility)
- [Coverage sources, tiles, catalogues](#coverage-sources-tiles-catalogues)
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
| `GET /api/moc/<source_id>` | FITS MOC blob for a survey source — lazy-fetched, cached |
| `GET /api/tile-sources` | Metadata for every registered `PrioritisedTilesSource` |
| `GET /api/tiles/<source_id>` | Tile list for one source (server-side filter optional) |
| `GET /api/saved-searches`, `POST` | CRUD for saved Inventory filter bundles |
| `DELETE /api/saved-searches/<id>` | Remove a saved search |
| `GET /api/observability?lat=&lon=&time=` | Altaz for every target at one moment |
| `GET /api/visibility` | 12-month per-target visibility bins for a site |
| `GET /api/visibility/point?ra=&dec=` | Same bins for an arbitrary (RA, Dec) point |
| `GET /api/sites`, `POST` | CRUD for saved observing sites |
| `GET /api/gaps` | Multi-source gap-finder JSON response |
| `GET /api/gaps/moc.fits` | Gap MOC as raw FITS bytes |
| `GET /api/export/priority` | Legacy CSV of Hα-but-no-SII candidates |
| `GET /api/gear`, `POST` | Telescopes + cameras |
| `POST /api/gear/seed` | Merge manifest-derived gear into `gear.json` |
| `GET /api/plans`, `POST` | Session plan CRUD |
| `GET /api/plans/<id>`, `PUT`, `DELETE` | Single-plan operations |
| `GET /api/target-overrides`, `POST` | Per-target metadata overrides |
| `GET /api/ts-templates` | NINA TS plugin's exposure templates (if installed) |
| `POST /api/sync` | Build NINA Target Scheduler import zip |
| `GET /api/ext/...` | Routes registered by [extensions](extensions.md) |

The API is unauthenticated and binds to `127.0.0.1` by default — see the [security notes in the archive setup guide](setup-archive.md#security-and-deployment-notes) before exposing it on a network.

## Manifest schema

The manifest is the JSON file ACP reads at startup (default path `data/manifest.json`). It's produced by `scripts/build_archive_manifest.py` from your FITS/XISF archive — see [setting up your own archive](setup-archive.md) for how to build it.

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

### Sites — `/api/sites`

`GET /api/sites` returns the saved sites plus the active one:

```json
{
  "active_id": "sydney",
  "sites": [
    {"id": "sydney", "label": "Sydney", "lat_deg": -33.87, "lon_deg": 151.21, "altitude_m": 50, "min_alt_deg": 30}
  ]
}
```

`POST /api/sites` accepts `{"action": "create" | "update" | "delete" | "set_active", ...}` for full CRUD plus active-site selection. Sites are persisted to `data/sites.json`.

### Per-target visibility — `/api/visibility`

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

### Arbitrary point — `/api/visibility/point`

`GET /api/visibility/point?ra=161.26&dec=-59.68&site_id=sydney` returns the same 12-month bin shape for one (RA, Dec) point — useful for the Inventory tile-detail panel.

## Coverage sources, tiles, catalogues

ACP's plugin platform lets extensions publish coverage data in three flavours, each with its own endpoint. The data contracts are defined as PEP 544 Protocols in `sources.py` — see [extensions](extensions.md) for how to author them.

### `/api/sources`

Lists every registered `CoverageSource` (the manifest, friend manifests, public-survey MOCs, anything an extension registers) plus its metadata. Used by the gap-finder's "Use sources" checkboxes and the legend.

### `/api/tile-sources` and `/api/tiles/<id>`

Lists every registered `PrioritisedTilesSource` (curated, ranked sky cells with per-band coverage status). These are the inputs to the **Inventory** rail. The accordion is hidden when no tile sources are registered, so a stock checkout doesn't show it.

`GET /api/tiles/<source_id>` returns the tile list for one source. Optional query params filter server-side: `?priority=1,2`, `?missing_band=Ha`, etc.

### `/api/catalog-registry` and `/api/catalogs`

`/api/catalog-registry` returns the declarative list of catalogues to surface in the right rail (loaded from `data/catalog_registry.json` plus anything `app.extra_catalogues` extensions register). `/api/catalogs` returns the actual catalogue *payloads* (the points themselves).

### `/api/saved-searches`

CRUD for saved Inventory filter bundles. The Inventory rail lets users name a combination of priority/missing-band/category filters and recall it later. State persists to `data/saved_searches.json`.

## Gap-finder

When planning narrowband sessions you usually want to know where one filter has been imaged but another hasn't yet — so a follow-up session adds new data instead of duplicating coverage. ACP unions every coverage source you've enabled (your manifest, friend manifests, public-survey MOCs), intersects that union with public catalogue candidates, and gives you a CSV you can paste into NINA.

### How to use it from the UI

The control lives in the **Catalogues** rail:

- **Have** / **Missing** dropdowns — pick the two filters. Defaults to `Hα` and `SII`.
- **Two hour thresholds** — `≥ N h` for the *have* side (a region only counts as covered if at least one source has stacked at least this many hours), `< N h` for the *missing* side. Defaults: `1.0` and `0.5`.
- **Use sources** — checkbox per registered source. All checked by default.
- **Find gaps** — fetches `/api/gaps`, mounts a yellow MOC over the gap region on the map, scatters catalogue candidates that fall inside it, and writes a one-line summary (`sky 0.84% • 1808 candidates • from manifest, iphas_ha`) under the buttons. Click again to hide.

### API endpoints

```
GET /api/gaps?have=Ha&missing=SII&sources=manifest,iphas_ha&min_have_hours=1&max_missing_hours=0.5
GET /api/gaps/moc.fits?<same query>
```

The JSON response carries `gap_sky_fraction`, `candidates`, the resolved `have_sources` / `missing_sources` lists, any sources skipped (with reasons), and a `moc_url` pointing at the FITS MOC for the same query. The FITS endpoint serves raw bytes — useful if you'd rather load the gap into Aladin desktop or `mocpy` directly.

### Legacy CSV route

The legacy `/api/export/priority` CSV route stays for backwards compatibility — same shape and headers as before, hardcoded to "Hα but no SII over the manifest source only". The new gap-finder doesn't replace it; pick whichever fits your workflow.

### Without `mocpy`

`/api/gaps` and `/api/gaps/moc.fits` return `503` (MOC algebra is the whole point of these routes). `/api/export/priority` still works — it falls back to the original inline implementation. `pip install mocpy` to enable the full gap-finder UI.
