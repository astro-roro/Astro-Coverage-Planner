# Astro Coverage Planner

A web-based sky-coverage viewer for astrophotographers. Feed it a manifest of
what you've imaged — which targets, which filters, how many hours — and it
renders every field of view on an Aladin Lite sky map, with filter/telescope
toggles, depth filtering, altitude/observability for your site, and optional
catalog overlays (SNRs, HII regions) for finding gap targets.

Built originally against Rohan's Ha/SII/OIII archive to find targets where
Ha data exists but SII doesn't — spun out here so others can point it at
their own manifests.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/make_demo_manifest.py       # writes data/manifest.json (demo)
python app.py                              # http://127.0.0.1:5555
```

Open `http://127.0.0.1:5555/` in your browser. The demo has five
well-known southern-sky targets so you can see what the viewer does.

## Pointing it at your own FITS archive

Replace the demo manifest by scanning your imaging folder:

```bash
python scripts/build_archive_manifest.py --scan-root /path/to/your/archive
```

This walks the folder tree, opens every master FITS/XISF file to read its
WCS, clusters results into targets by sky position, and writes
`data/manifest.json`. On a ~100k-file archive it takes ~5 minutes. The
webapp hot-reloads on mtime, so you can rebuild without restarting.

Multiple roots (e.g. NAS + local masters):

```bash
python scripts/build_archive_manifest.py \
    --scan-root /mnt/nas/Astro/Images \
    --scan-root ./my_masters
```

Output location:

```bash
python scripts/build_archive_manifest.py --scan-root /data -o custom.json
MANIFEST_PATH=custom.json python app.py
```

The scanner expects one FITS/XISF file per master (stacked frame) with
standard WCS keywords (CRVAL1/CRVAL2/CD1_1 etc. or CDELT1/CDELT2). Filter
is read from the `FILTER` header; telescope from `TELESCOP`; object name
from `OBJECT`. Files classified as "sub" (unstacked individual exposures)
are counted but not plotted — only masters with WCS become FOV polygons.

### Optional: sub-integration hours from a pipeline DB

If you keep a SQLite DB tracking every captured sub-exposure with a
`frames` table shaped like:

```sql
CREATE TABLE frames (
    object_name  TEXT,
    filter_name  TEXT,
    exptime      REAL,      -- seconds
    captured_at  TEXT,      -- ISO date
    path         TEXT
);
```

you can feed it in and the manifest will also report per-target sub-hours
(useful when you have far more subs than you've stacked):

```bash
python scripts/build_archive_manifest.py --scan-root /data --db pipeline.db
```

Without `--db`, only master integration (NCOMBINE × EXPTIME from master
headers) is counted.

### Env vars (alternative to CLI)

| Var                       | Equivalent flag       |
|---------------------------|-----------------------|
| `MANIFEST_SCAN_ROOTS`     | `--scan-root` (`;`- or `:`-separated) |
| `MANIFEST_DB_PATH`        | `--db`                |
| `MANIFEST_PATH`           | `--output` + tells the webapp where to read |

## Configuration (env vars)

| Var              | Default              | Purpose                       |
|------------------|----------------------|-------------------------------|
| `MANIFEST_PATH`  | `./data/manifest.json`  | Path to manifest JSON       |
| `CATALOGS_PATH`  | `./data/catalogs.json`  | Path to overlay catalogs    |
| `HOST`           | `127.0.0.1`          | Bind host (use `0.0.0.0` only on trusted networks) |
| `PORT`           | `5555`               | Bind port                     |

## Optional: catalog overlays

To enable the Green SNR / WISE HII / SMGPS / EMU overlays:

```bash
python scripts/fetch_catalogs.py           # writes data/catalogs.json
```

Requires `astroquery` (already in `requirements.txt`). Data is pulled from
VizieR; first run takes ~30s and is cached.

## Features

### Sky map
- **Aitoff / Mollweide / Orthographic / Gnomonic** projections.
- **Equatorial or Galactic** coordinate frames.
- FOV polygons color-coded by telescope; a per-FOV badge shows which filters
  cover that field.

### Filter controls
- Per-filter toggles (`Ha SII OIII L R G B V`).
- Logic modes: ANY, ALL, or `Have Ha but NOT SII` (gap-finder).
- Depth slider: minimum hours/filter (0–10h).

### Site + observability
- Presets for Sydney / Victoria, or enter custom lat/lon.
- Status bar shows how many targets are above 30° / 60° altitude right now.

### Catalog overlays
- Green 2019 SNRs (confirmed Galactic SNRs).
- SMGPS SNR candidates.
- EMU SNR candidates.
- WISE HII regions (Anderson 2014).

### Export
- CSV of overlay candidates where Ha ≥ 1h but SII < 0.5h (validation-gap bucket).

## Manifest schema

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

Corner order for `corners_icrs`: `[SW, NW, NE, SE]` (the frontend places the
filter badge on the NW corner and rotates it with the FOV).

See `scripts/make_demo_manifest.py` for a runnable example.

## API

| Endpoint                           | Purpose                              |
|------------------------------------|--------------------------------------|
| `GET /`                            | Frontend HTML                        |
| `GET /api/manifest`                | Slim manifest JSON                   |
| `GET /api/target/<id>`             | Full target detail                   |
| `GET /api/catalogs`                | Overlay catalogs                     |
| `GET /api/observability?lat=&lon=&time=` | Altaz for every target         |
| `GET /api/export/priority`         | CSV of Ha-but-no-SII candidates      |

## Security notes

- `app.py` binds to `127.0.0.1` by default. Only switch to `0.0.0.0` on a
  trusted LAN — the server has no authentication and exposes your manifest
  (including file paths) over HTTP.
- The server is Flask's dev server. For anything beyond local use, front it
  with gunicorn/uwsgi behind a reverse proxy.

## License

MIT — see `LICENSE`.
