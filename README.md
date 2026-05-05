# Astro Coverage Planner

**See what you've already imaged. Plan what's next. Hand it to NINA.**

![Coverage map hero](docs/images/hero-coverage-map.png)

It's your astrophotography catalogue, visualised. ACP reads your entire FITS library and draws every field you've ever shot onto one sky map — coloured by telescope, badged by filter, with integration hours on every target. Useful from your first handful of targets and only gets more valuable as your library grows, especially if you're shooting narrowband or multi-night projects and want to see at a glance which targets are finished, which still owe you an OIII pass, and where your different rigs overlap. Plan your next session, next month, or next whole *year* in the same view — and export it straight to NINA Target Scheduler.

## Who this is for

ACP is built around a few specific imaging patterns. You'll get the most out of it if any of these sound like you:

- You shoot **with filters** (narrowband, LRGB, mixed) and want to see which targets are missing which channel.
- You build up integration **across multiple nights or multiple sessions** and want a clear picture of which projects are actually finished vs. half-done.
- You use **multiple telescopes** and want each rig's footprints visually distinguished on the same sky map (colour-coded, with filter badges per polygon).
- You plan sessions with **mosaics** and want the panel layout, rotation, and per-filter exposure goals captured in one place — and synced to NINA Target Scheduler so you don't re-enter targets manually.
- You want **one place** to answer "what have I shot, where are the gaps, what's next" without juggling spreadsheets or scrolling through folders.

You don't need a huge library for this to start paying off — even a couple of dozen targets is enough to surface projects you'd half-forgotten about. Most people wish they'd started this kind of catalogue years earlier.

## See it in action

Four moments that show what this is actually like to use.

### Your coverage on every sky survey

![Survey swap](docs/images/survey-swap.gif)

Same FOV polygons, same telescope colours — flip the background between optical, narrowband Hα, infrared, and radio surveys with one click. Hunting bright Hα regions you haven't shot? Switch to the Hα survey and scan for big patches with no polygons on them. Curious how a region looks in radio? One dropdown and you're there. ACP exposes Aladin Lite's full survey catalogue, so you've got dozens of professional astronomy datasets behind your map at all times.

### Smart search across your archive

![Smart search](docs/images/smart-search.gif)

The right-rail search supports a small grammar that combines with AND. Bare words match object names; everything else is `key:value`.

<details>
<summary>All search tokens</summary>

| Token | Effect |
|---|---|
| `eta carinae` | substring match across object names |
| `"north america"` | quoted phrases preserve spaces |
| `object:M42` / `name:rosette` | restrict to object/target name |
| `filter:Ha` | only targets with Hα data |
| `telescope:RedCat` / `tel:edge` | by telescope (fuzzy) |
| `camera:2600MM` / `cam:294` | by camera |
| `hours>5` / `hours<2` | total integration hours |
| `fov>60` / `fov<10` | FOV size in arcmin |

</details>

Use it for things like `filter:Ha -filter:SII tel:redcat` ("RedCat-shot Hα targets without SII") or `hours>5 fov<60` ("targets I've put serious time into with a tight rig"). The filtered list updates live as you type.

### Catalogue overlays for finding new targets

![Catalogue overlays](docs/images/feature-catalogues.gif)

Toggle the Green 2019 SNR catalogue, SMGPS / EMU SNR candidates, WISE HII regions, the Messier and Sharpless lists, and ESO's planetary nebulae. Each one lights up its objects as markers across the sky. Combined with the survey-background swap above, you can pick a catalogue (say, confirmed SNRs), switch to the Hα survey, and scan for objects with bright emission you haven't shot yet — that's your shortlist for the next dark night.

### Plan your next session in the same map

![Planning mode](docs/images/planning-mode.gif)

Switch to **Planning mode** in the topbar. Click the sky to drop a target centre, pick telescope + camera (FOV box auto-derives from your gear), set per-filter target hours and sub-exposure. Doing a mosaic? Set rows × columns × overlap %, drag the rotation handle to align the panel grid. Solid borders mean data already logged; dashed borders mean not yet started. When the plan's ready, ACP can also sync it to NINA Target Scheduler so you're not re-typing targets.

## Quickstart (demo data)

```bash
pip install -r requirements.txt
python scripts/make_demo_manifest.py       # writes data/manifest.json (demo)
python app.py                              # http://127.0.0.1:5555
```

Open `http://127.0.0.1:5555/` in your browser. The demo has five well-known southern-sky targets so you can see what the viewer does.

## Full setup (your own FITS archive)

1. **Install dependencies.**
   ```bash
   pip install -r requirements.txt
   ```

2. **Build the manifest** from your FITS/XISF archive. Set `FITS_ROOTS` (one or more image roots, semicolon-separated) before running:
   ```bash
   FITS_ROOTS="D:/Astro/Images;E:/Archive" python scripts/build_archive_manifest.py
   ```
   On Windows PowerShell:
   ```powershell
   $env:FITS_ROOTS="D:/Astro/Images;E:/Archive"
   python scripts/build_archive_manifest.py
   ```
   This walks the folder tree, opens every master FITS/XISF to read its WCS + gear headers (TELESCOP, INSTRUME, FOCALLEN, XPIXSZ, APTDIA, GAIN, OFFSET, XBINNING), clusters results into targets by sky position, and writes `data/manifest.json`. On a ~100k-file archive it takes ~5 minutes.

3. **Launch the webapp.**
   ```bash
   python app.py
   ```
   The app hot-reloads on manifest mtime, so you can rebuild without restarting.

4. **Open Planning mode → gear auto-seeds.** Switch to the "Planning" tab in the top bar. On first load, the planner scans your manifest and auto-imports every telescope + camera it finds (names, focal length, aperture, pixel size, sensor size, observed filters) into `data/gear.json`. Open the gear editor (pencil/"Edit gear" button in the plan editor) to review, fill in anything the headers didn't carry (e.g. `gain`/`offset` if your FITS didn't record them), and hit "Scan coverage" to re-merge later after rebuilding the manifest. The planner uses fuzzy name matching so "RedCat 51 APO" in your gear matches "RedCat 51" from a FITS header.

5. **Plan a session.** Click "+ New plan", pick a target (by name or by clicking the sky), select telescope + camera, set filter goals and mosaic geometry if needed. Footprints update live on the map — solid borders for plans with data logged, dashed for not-yet-started. Border color matches the telescope you selected.

6. **Sync to NINA Target Scheduler** (optional). Click "Sync" to export a TS plugin zip (metadata.json, profilePreference.json, exposureTemplates.json, projects.json) that imports directly into NINA. Mosaics expand to per-panel TS targets. See the Planner section below.

### Multiple image roots / output path / pipeline DB

The manifest builder takes its paths from env vars:

| Env var         | Default                            | Purpose                                                 |
|-----------------|------------------------------------|---------------------------------------------------------|
| `FITS_ROOTS`    | hardcoded list at top of script    | Semicolon-separated list of image roots                 |
| `MANIFEST_PATH` | `./data/manifest.json`             | Where to write (and where the webapp reads from)        |
| `FULL_MASTERS`  | `./state/full_masters` (if exists) | Extra root of stacked masters                           |
| `PIPELINE_DB`   | `./state/job_queue.db` (if exists) | Optional sqlite DB with `frames` table for sub-hours    |

```bash
FITS_ROOTS="D:/Astro/Images;E:/Archive" \
MANIFEST_PATH=./data/custom_manifest.json \
PIPELINE_DB=./my_pipeline.db \
    python scripts/build_archive_manifest.py

MANIFEST_PATH=./data/custom_manifest.json python app.py
```

The scanner expects one FITS/XISF file per master (stacked frame) with standard WCS keywords (CRVAL1/CRVAL2/CD1_1 etc. or CDELT1/CDELT2). Files classified as "sub" (unstacked individual exposures) are counted for hours but not plotted — only masters with WCS become FOV polygons.

### Optional: sub-integration hours from a pipeline DB

If you keep a SQLite DB tracking every captured sub-exposure with a `frames` table shaped like:

```sql
CREATE TABLE frames (
    object_name  TEXT,
    filter_name  TEXT,
    exptime      REAL,      -- seconds
    captured_at  TEXT,      -- ISO date
    path         TEXT
);
```

point `PIPELINE_DB` at it and the manifest will include per-target sub-hours (useful when you have far more subs captured than you've stacked). Without it, hours come solely from master headers (NCOMBINE × EXPTIME).

## Configuration (env vars)

| Var               | Default                                                     | Purpose                                              |
|-------------------|-------------------------------------------------------------|------------------------------------------------------|
| `MANIFEST_PATH`   | `./data/manifest.json`                                      | Path to manifest JSON (read by app; written by builder) |
| `CATALOGS_PATH`   | `./data/catalogs.json`                                      | Path to overlay catalogs                             |
| `GEAR_PATH`       | `./data/gear.json`                                          | Telescopes + cameras for the planner                 |
| `PLANS_PATH`      | `./data/plans.json`                                         | Saved session plans                                  |
| `TS_DB_PATH`      | `%LOCALAPPDATA%/NINA/SchedulerPlugin/schedulerdb.sqlite`    | NINA Target Scheduler DB (optional)                  |
| `ZIP_OUTPUT_DIR`  | `./data/exports`                                            | Where TS-sync zips are written                       |
| `HOST`            | `127.0.0.1`                                                 | Bind host (use `0.0.0.0` only on trusted networks)   |
| `PORT`            | `5555`                                                      | Bind port                                            |
| `ACP_EXTENSIONS_DIR` | `%APPDATA%/acp/extensions` (Win) / `~/.config/acp/extensions` | Directory of extension modules — see [Extensions](#extensions) |
| `ACP_FRIEND_MANIFESTS` | unset | Semicolon-separated paths to sanitised friend manifests — see [Sharing](#sharing-coverage-with-friends) |
| `ACP_SURVEYS_PATH` | `./data/surveys.json` | Path to the survey registry — see [Public surveys](#public-surveys) |

## Optional: catalog overlays

To enable the Green SNR / WISE HII / SMGPS / EMU overlays:

```bash
python scripts/fetch_catalogs.py           # writes data/catalogs.json
```

Requires `astroquery` (already in `requirements.txt`). Data is pulled from VizieR; first run takes ~30s and is cached.

## Features

### Sky map
- **Aitoff / Mollweide / Orthographic / Gnomonic** projections.
- **Equatorial or Galactic** coordinate frames.
- FOV polygons color-coded by telescope; a per-FOV badge shows which filters cover that field.

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

### Search grammar (right rail)
- Bare words match object names: `eta carinae`.
- Quoted phrases preserve spaces: `"north america"`.
- Key:value: `object:M42`, `name:rosette`, `filter:Ha`, `telescope:RedCat`, `tel:edge`, `camera:2600MM`, `cam:294`.
- Numeric comparators: `hours>5`, `hours<2`, `fov>60`, `fov<10`.
- Tokens combine with AND.

### Export
- CSV of overlay candidates where Ha ≥ 1h but SII < 0.5h (validation-gap bucket).

### Planner (Planning mode)
Switch to the "Planning" tab in the top bar.

- **Gear auto-seed from coverage.** On first load the planner scans your manifest and adds every telescope + camera it finds to `data/gear.json`. Re-run any time via the "Scan coverage" button in the gear editor after rebuilding the manifest.
- **Plan editor.** Pick a target (name, coordinates, or click on the sky), select telescope + camera, set filter goals (target hours + sub-exposure), priority, min altitude, meridian window.
- **Mosaics.** Set rows × columns × overlap % and the planner tiles panels around your center RA/Dec. Rotate the whole mosaic by dragging the handle. Default overlap is 15%, which is the sweet spot for gradient blending.
- **Live footprint previews.** Footprint color matches the selected telescope (same colors as your coverage legend; fuzzy name match handles "RedCat 51 APO" vs "RedCat 51"). Dashed borders for plans with no data yet; solid once you've logged actual hours.
- **NINA Target Scheduler sync.** "Sync" builds a TS plugin zip with projects.json / targets / exposureTemplates, merging plans by project, applying strictest-wins for conflicting altitude/priority/meridian settings. Templates map to your existing TS templates by name (set per filter in the gear editor) or are generated from camera gain/offset/bin.
- **TS template mapping.** If NINA Target Scheduler is installed locally, the planner reads its sqlite DB (`%LOCALAPPDATA%/NINA/SchedulerPlugin/schedulerdb.sqlite`) to offer existing templates in a dropdown.

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

Corner order for `corners_icrs`: `[SW, NW, NE, SE]` (the frontend places the filter badge on the NW corner and rotates it with the FOV).

See `scripts/make_demo_manifest.py` for a runnable example.

## API

| Endpoint                                 | Purpose                                   |
|------------------------------------------|-------------------------------------------|
| `GET /`                                  | Frontend HTML                             |
| `GET /api/manifest`                      | Slim manifest JSON                        |
| `GET /api/target/<id>`                   | Full target detail                        |
| `GET /api/catalogs`                      | Overlay catalogs                          |
| `GET /api/observability?lat=&lon=&time=` | Altaz for every target                    |
| `GET /api/export/priority`               | CSV of Ha-but-no-SII candidates           |
| `GET  /api/gear`                         | Current gear (telescopes + cameras)       |
| `POST /api/gear`                         | Persist gear edits                        |
| `POST /api/gear/seed`                    | Merge manifest-derived gear into gear.json|
| `GET  /api/plans`, `POST`, `PUT`, `DELETE` | CRUD for session plans                  |
| `GET  /api/ts-templates`                 | TS plugin's exposure templates (if installed) |
| `POST /api/sync`                         | Build NINA Target Scheduler import zip    |

## Extensions

Coverage planning often needs to reach into something ACP doesn't ship — a friend's shared FOV list, a club's target catalog, a small SQLite DB you keep alongside your archive. Rather than fork the app, drop a Python module into a known directory and ACP will load it at startup.

**Where they go.** `ACP_EXTENSIONS_DIR`, defaulting to `%APPDATA%\acp\extensions` on Windows and `~/.config/acp/extensions` elsewhere. Each entry is either a single `.py` file or a package directory.

**The contract.** Each extension defines a top-level `register(app)` callable that receives the Flask app. From there you can register blueprints, add routes, read `app.config`, attach teardown hooks — anything Flask supports. Extensions are loaded once at startup; failures in one are logged and don't block the others or the app itself.

**Example — expose a friend's shared FOV list as CSV.** Save as `friend_fovs.py` in your extensions dir:

```python
import json, csv, io
from pathlib import Path
from flask import Blueprint, Response

bp = Blueprint("friend_fovs", __name__)
SHARED = Path.home() / "shared" / "friend_fovs.json"

@bp.route("/api/ext/friend-fovs.csv")
def friend_fovs_csv():
    rows = json.loads(SHARED.read_text())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["name", "ra", "dec", "fov_arcmin"])
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv")

def register(app):
    app.register_blueprint(bp)
```

Drop the file in, restart, hit `http://127.0.0.1:5555/api/ext/friend-fovs.csv`.

**Trust.** Extensions run in-process as your user with no sandbox, so only load code you've read or written yourself.

**Failures.** Load and registration errors are logged via Python's `logging` under the `acp.extensions` logger — check there if an extension silently doesn't show up.

## Sharing coverage with friends

Planning narrowband sessions is easier when you can see your imaging buddies' coverage gaps next to your own — split the sky, agree who's chasing OIII on a given target, avoid duplicating Ha hours someone else already has stacked. ACP can load any number of *sanitised* friend manifests as additional toggleable layers in the Sources rail.

**What gets stripped vs kept.** The sanitiser rebuilds the manifest from a small whitelist:

- **Stripped:** `master_files` arrays, all on-disk paths, telescope/camera serials and exact model strings, exact dates (truncated to month), per-frame file lists, `scan_roots`, anything else that fingerprints the imager's setup.
- **Kept:** polygon footprints (`corners_icrs`), per-filter integration hours (rounded to 0.1h), aperture-class telescope info (e.g. `"110mm refractor"`), public catalog target names (`"M 42"`, `"Eta Carinae"`), month of last activity.
- **Tripwire:** the loader refuses any file missing `"sanitised": true` so you can't accidentally share an unsanitised manifest.

**How to produce one.** Two paths:

```bash
# Option A: re-run the scanner with --sanitise alongside the regular write
python scripts/build_archive_manifest.py --sanitise dave_to_share.json --label "Dave"

# Option B: sanitise an existing manifest in place
python scripts/sanitise_manifest.py data/manifest.json dave_to_share.json --label "Dave"
```

Inspect the output before sending — it's plain JSON.

**How to consume one.** Point `ACP_FRIEND_MANIFESTS` at a semicolon-separated list of paths before launching:

```bash
# Linux/macOS
ACP_FRIEND_MANIFESTS="/path/to/dave.json;/path/to/sara.json" python app.py

# Windows PowerShell
$env:ACP_FRIEND_MANIFESTS = "C:\shared\dave.json;C:\shared\sara.json"
python app.py
```

Each manifest becomes its own toggleable layer in the **Sources** rail with a distinct color from the palette.

**Failure modes.** Rejected manifests log a `WARNING` to Python's `logging` channel and are skipped; other friends still load and the app still starts. Hard caps applied during validation: 10,000 targets, 64 polygons per target, 64 vertices per polygon.

## Public surveys

ACP can pull public-survey footprints (IPHAS, VPHAS+, etc.) from CDS as MOCs and overlay them on the map alongside your own coverage. Useful for spotting where a survey already has Hα coverage so you can prioritise the gaps. Ships with one survey wired up; adding more is a one-line PR to `data/surveys.json`.

**What ships out of the box.** IPHAS DR2 Hα (northern Galactic plane). Toggleable in the **Sources** rail, off by default.

**Adding a survey.** Append an entry to `data/surveys.json`:

```json
{
  "id": "vphas_ha",
  "label": "VPHAS+ Hα",
  "color": "#5b9bc2",
  "filter": "Ha",
  "moc_url": "https://alasky.cds.unistra.fr/...",
  "attribution": "VPHAS+ DR4 (Drew et al. 2014)",
  "enabled_default": false
}
```

The loader enforces an HTTPS-only hostname allowlist (currently `alasky.cds.unistra.fr` and `alasky.u-strasbg.fr`). Adding other CDS mirrors or other survey hosts means editing the allowlist constant in `app.py` — flag this as the right place to push back in PR review.

**How fetching works.** Lazy on first `/api/moc/<id>` hit. Cached at `data/moc_cache/<id>.fits` with a 30-day TTL and content-hash invalidation. Subsequent toggles re-use the cache.

**Hard limits enforced.** 10MB per MOC, 30s fetch timeout, response must parse as a FITS MOC via `mocpy` before being cached, otherwise `502`.

**Without `mocpy`.** ACP runs fine. Sources still appear in the rail but `/api/moc/<id>` returns `503`. `pip install mocpy` to enable.

**`ACP_SURVEYS_PATH`.** Point at a custom JSON file to override the bundled registry — handy for testing or per-machine survey curation.

## Finding coverage gaps

When planning narrowband sessions you usually want to know where one filter has been imaged but another hasn't yet — so a follow-up session adds new data instead of duplicating coverage. ACP unions every coverage source you've enabled (your manifest, friend manifests, public-survey MOCs), intersects that union with public catalog candidates, and gives you a CSV you can paste into NINA.

The control lives in the **Catalogues** rail:

- **Have** / **Missing** dropdowns — pick the two filters. Defaults to `Ha` and `SII`.
- Two hour thresholds — `≥ N h` for the *have* side (a region only counts as covered if at least one source has stacked at least this many hours), `< N h` for the *missing* side. Defaults: `1.0` and `0.5`.
- **Use sources** — checkbox per registered source. All checked by default.
- **Find gaps** — fetches `/api/gaps`, mounts a yellow MOC over the gap region on the map, scatters catalog candidates that fall inside it, and writes a one-line summary (`sky 0.84% • 1808 candidates • from manifest, iphas_ha`) under the buttons. Click again to hide.

Endpoints:

```
GET /api/gaps?have=Ha&missing=SII&sources=manifest,iphas_ha&min_have_hours=1&max_missing_hours=0.5
GET /api/gaps/moc.fits?<same query>
```

The JSON response carries `gap_sky_fraction`, `candidates`, the resolved `have_sources` / `missing_sources` lists, any sources skipped (with reasons), and a `moc_url` pointing at the FITS MOC for the same query. The FITS endpoint serves raw bytes — useful if you'd rather load the gap into Aladin desktop or `mocpy` directly.

The legacy `/api/export/priority` CSV route stays for back-compat — same shape and headers as before, hardcoded to "Ha but no SII over the manifest source only". The new gap-finder doesn't replace it; pick whichever fits your workflow.

**Without `mocpy`.** `/api/gaps` and `/api/gaps/moc.fits` return `503` (MOC algebra is the whole point of the route). `/api/export/priority` still works — it falls back to the original inline implementation. `pip install mocpy` to enable the gap-finder UI.

## Security notes

- `app.py` binds to `127.0.0.1` by default. Only switch to `0.0.0.0` on a trusted LAN — the server has no authentication and exposes your manifest (including file paths) over HTTP.
- The server is Flask's dev server. For anything beyond local use, front it with gunicorn/uwsgi behind a reverse proxy.

## License

MIT — see `LICENSE`.
