# Setting up your own archive

This guide picks up where the [Quickstart](../README.md#get-it-running) left off. By now ACP should be running with the demo data — we'll swap that out for a manifest built from your real FITS/XISF library.

- [Build the manifest](#build-the-manifest)
- [Configuration variables](#configuration-variables)
- [Optional: sub-exposure hours from a pipeline DB](#optional-sub-exposure-hours-from-a-pipeline-db)
- [Optional: download catalogue overlays](#optional-download-catalogue-overlays)
- [What gets scanned vs skipped](#what-gets-scanned-vs-skipped)
- [Re-running after new captures](#re-running-after-new-captures)

## Build the manifest

The manifest is the JSON file at `data/manifest.json` that ACP reads on startup. It contains every field of view in your archive, clustered by sky position, with per-filter integration hours and the gear used.

To build it, point `FITS_ROOTS` at one or more folders of stacked FITS/XISF masters and run the build script:

**macOS / Linux:**

    FITS_ROOTS="/Volumes/Astro/Images:/Volumes/Archive" python scripts/build_archive_manifest.py

**Windows PowerShell:**

    $env:FITS_ROOTS="D:/Astro/Images;E:/Archive"
    python scripts/build_archive_manifest.py

Use **`;`** as the separator on Windows and **`:`** on macOS/Linux (matching each shell's PATH convention).

Both of these are set for the current terminal session only. Close the window or open a new tab and it's gone, so if you also start `app.py` from a different terminal (or a different session later on) it won't see `FITS_ROOTS` unless you set it there too. On macOS/Linux, the `FITS_ROOTS="..." python ...` form above only applies to that one command anyway, which is usually what you want. On Windows, if you'd rather not retype `$env:FITS_ROOTS=...` every session, set it permanently with:

    [Environment]::SetEnvironmentVariable("FITS_ROOTS", "D:/Astro/Images;E:/Archive", "User")

That writes it to your user profile, so it's there in every new PowerShell window from then on (you'll need to open a fresh window for it to take effect).

The script walks every folder under each root, opens every FITS/XISF master to read its WCS (sky position) and gear headers (`TELESCOP`, `INSTRUME`, `FOCALLEN`, `XPIXSZ`, `APTDIA`, `GAIN`, `OFFSET`, `XBINNING`), clusters results into targets by sky position, and writes `data/manifest.json`. On a ~100,000-file archive it takes around 5 minutes.

When it finishes, the app will pick up the new manifest automatically — no restart needed. Refresh the browser tab.

## Configuration variables

Both the build script and the webapp read these environment variables. Set whatever you need before launching either; defaults are sensible for most setups.

| Var | Default | Purpose |
|---|---|---|
| `FITS_ROOTS` | (none — required for build) | One or more image roots for the manifest builder. Semicolon-separated on Windows, colon-separated on macOS/Linux. |
| `MANIFEST_PATH` | `./data/manifest.json` | Where the manifest is read (by the app) and written (by the builder). |
| `CATALOGS_PATH` | `./data/catalogs.json` | Path to overlay catalogues. |
| `GEAR_PATH` | `./data/gear.json` | Telescopes + cameras for the planner. |
| `PLANS_PATH` | `./data/plans.json` | Saved session plans. |
| `FULL_MASTERS` | `./state/full_masters` (if exists) | Extra root of stacked masters to scan in addition to `FITS_ROOTS`. |
| `PIPELINE_DB` | `./state/job_queue.db` (if exists) | Optional SQLite DB with a `frames` table for sub-exposure hours. See below. |
| `TS_DB_PATH` | `%LOCALAPPDATA%/NINA/SchedulerPlugin/schedulerdb.sqlite` | NINA Target Scheduler DB (Windows only, optional). |
| `ZIP_OUTPUT_DIR` | `./data/exports` | Where TS-sync zips are written. |
| `HOST` | `127.0.0.1` | Bind host. Use `0.0.0.0` only on a trusted LAN — ACP has no authentication. |
| `PORT` | `5555` | Bind port. |
| `ACP_EXTENSIONS_DIR` | `%APPDATA%/acp/extensions` (Win) / `~/.config/acp/extensions` | Directory of extension modules. |
| `ACP_FRIEND_MANIFESTS` | unset | Semicolon-separated paths to sanitised friend manifests. |
| `ACP_SURVEYS_PATH` | `./data/surveys.json` | Path to the public-survey registry. |
| `ACP_CATALOG_REGISTRY` | `./data/catalog_registry.json` | Catalogue registry read by the catalogue loader. |
| `ACP_MOC_CACHE_DIR` | `./data/moc_cache` | Cache for downloaded survey coverage (MOC) files. |
| `SITES_PATH` | `./data/sites.json` | Observing sites. |
| `DESTINATIONS_PATH` | `./data/destinations.json` | Export destinations. |
| `SAVED_SEARCHES_PATH` | `./data/saved_searches.json` | Saved target searches. |
| `TARGET_OVERRIDES_PATH` | `./data/target_overrides.json` | Per-target overrides. |
| `ACP_STATIC_MAX_AGE_S` | `3600` | Cache lifetime for static files; set `0` in development. |
| `ACP_PUBLISH_DEST`, `ACP_PUBLISH_SSH_KEY`, `ACP_LIVE_OUT_DIR` | unset | Live-page publishing, see [sharing.md](sharing.md). |
| `NAS_PREFIX`, `PIPELINE_DB_ALT` | unset | Manifest builder only: NAS path prefix and an alternate pipeline DB, see `scripts/build_archive_manifest.py`. |

A typical "everything in one custom location" invocation:

    FITS_ROOTS="D:/Astro/Images;E:/Archive" \
    MANIFEST_PATH=./data/custom_manifest.json \
    PIPELINE_DB=./my_pipeline.db \
        python scripts/build_archive_manifest.py

    MANIFEST_PATH=./data/custom_manifest.json python app.py

## Optional: sub-exposure hours from a pipeline DB

If you keep a SQLite database tracking every captured sub-exposure (not just stacked masters), ACP can include those sub-hours in the per-target totals. This is useful when you've captured far more subs than you've stacked — without it, hours come solely from master headers (`NCOMBINE × EXPTIME`).

The DB needs a `frames` table shaped like this:

    CREATE TABLE frames (
        object_name  TEXT,
        filter_name  TEXT,
        exptime      REAL,      -- seconds
        captured_at  TEXT,      -- ISO date
        path         TEXT
    );

Point `PIPELINE_DB` at it before running the manifest builder and the per-target sub-hours will be merged in.

## Optional: download catalogue overlays

To enable the Green SNR, WISE HII, SMGPS, Messier, Sharpless, and ESO PNe overlays in the right rail:

    python scripts/fetch_catalogs.py

This writes `data/catalogs.json` by querying VizieR via `astroquery` (already in `requirements.txt`). First run takes around 30 seconds; the result is cached so subsequent runs are instant.

## What gets scanned vs skipped

**Colour cameras and multi band filters.** Coverage is tracked per band (L, R, G, B, Ha, OIII, SII). A frame credits every band its filter covers, decided from the FITS `FILTER` keyword plus whether the header carries a Bayer pattern (`BAYERPAT`). No filter on a mono camera counts as L. No filter on a colour camera is labelled OSC and counts as R, G and B at once. A dual band filter such as L-eXtreme, L-eNhance, NBZ or ALP-T counts as Ha and OIII. Broadband light pollution filters such as L-Pro behave like no filter. Any other name (IR, sodium, a maker's own label) is kept as its own band and shown in grey. The target panel says which real filter fed each band, and an RGB chip in the filter bar picks out colour camera data.

- **Plotted as FOV polygons:** any FITS or XISF file with standard WCS keywords (`CRVAL1/CRVAL2/CD1_1`, etc., or `CDELT1/CDELT2`). These are typically your stacked masters.
- **Counted for hours but not plotted:** files classified as "subs" (unstacked individual exposures) — only if you've wired up a `PIPELINE_DB`. Otherwise they're skipped entirely.
- **Skipped silently:** any file without WCS, any non-FITS/XISF file, anything the loader can't parse.

The result is that your map shows clean polygons for everything you've actually processed, while the per-target hours still reflect total time on sky if you've kept the optional DB.

### Flattened archives

The builder de-duplicates the multiple pipeline stages WBPP writes (`calibrated/`, `registered/`, `master/`, `og/`, `starless/`, `stars/`) so a frame processed through several stages is only counted once. This works best on **WBPP's default layout**, where each stage lives in its own subfolder — that's the supported path.

If you've **manually flattened** your archive — for example dragging the raw `.fits` lights into the same folder as their calibrated/registered `.xisf` siblings — the builder still pairs an `X.fits` with its `X_c.xisf` / `X_c_r.xisf` (etc.) sibling and counts them as one physical frame, printing a `collapsed N files to M physical frames` line when it does. Pairing is anchored to WBPP's stage-suffix naming (`_c`, `_cc`, `_r`, `_d` and their ordered combinations); frames that have been renamed away from that convention can't be paired and may double-count. Renamed stage folders (e.g. `cal/`, `reg/`, `masters/`, `original_fits/`) are recognised as aliases. To stay on the well-tested path, keep WBPP's default staged output rather than reorganising stages into one directory.

## Re-running after new captures

After a new imaging session, just re-run the manifest builder:

    FITS_ROOTS="..." python scripts/build_archive_manifest.py

The webapp watches the manifest file and reloads automatically when its mtime changes. You don't need to restart `app.py` — just rebuild and refresh your browser.

If you've added new gear (a new scope or camera), open Planning mode in the app and hit "Scan coverage" in the gear editor — the planner re-merges manifest-derived gear into your `data/gear.json` without overwriting your manual edits.

## Security and deployment notes

ACP is intended to run on your own machine, behind your own firewall, against your own data. Two things to be aware of before exposing it to anything beyond `localhost`:

- **Default bind is `127.0.0.1`.** Only switch to `HOST=0.0.0.0` on a trusted LAN. ACP has no authentication and the `/api/manifest` and `/api/target/<id>` endpoints expose your archive's full file paths over HTTP — anyone who can reach the port can enumerate where your imaging data lives on disk.
- **`python3 app.py` runs Flask's dev server.** Fine for local single-user use; not built to handle public internet traffic. The Docker image runs `waitress` instead, which is a production WSGI server, but it still has no TLS or authentication. For anything beyond your own network, put either behind a reverse proxy (nginx, caddy) that adds TLS and authentication.

If you need genuinely shared access for a small group, the [friend manifests](sharing.md) feature is the intended path — each person runs their own copy of ACP locally and consumes sanitised exports from the others, rather than pointing multiple browsers at one shared instance.
