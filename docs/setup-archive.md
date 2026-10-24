# Setting up your own archive

This guide picks up where the [Quickstart](../README.md#get-it-running) left off. By now ACP should be running with the demo data, we'll swap that out for a manifest built from your real FITS/XISF library.

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

    FITS_ROOTS="/Volumes/Astro/Images;/Volumes/Archive" python scripts/build_archive_manifest.py

**Windows PowerShell:**

    $env:FITS_ROOTS="D:/Astro/Images;E:/Archive"
    python scripts/build_archive_manifest.py

Use **`;`** as the separator on every platform. A colon is a legal character in a macOS or Linux path, so the builder does not split on it.

Both of these are set for the current terminal session only. Close the window or open a new tab and it's gone, so if you also start `app.py` from a different terminal (or a different session later on) it won't see `FITS_ROOTS` unless you set it there too. On macOS/Linux, the `FITS_ROOTS="..." python ...` form above only applies to that one command anyway, which is usually what you want. On Windows, if you'd rather not retype `$env:FITS_ROOTS=...` every session, set it permanently with:

    [Environment]::SetEnvironmentVariable("FITS_ROOTS", "D:/Astro/Images;E:/Archive", "User")

That writes it to your user profile, so it's there in every new PowerShell window from then on (you'll need to open a fresh window for it to take effect).

The script walks every folder under each root, opens every FITS/XISF master to read its WCS (sky position) and gear headers (`TELESCOP`, `INSTRUME`, `FOCALLEN`, `XPIXSZ`, `APTDIA`, `GAIN`, `OFFSET`, `XBINNING`), clusters results into targets by sky position, and writes `data/manifest.json`. On a ~100,000-file archive it takes around 5 minutes.

When it finishes, the app will pick up the new manifest automatically, no restart needed. Refresh the browser tab.

## The settings file

ACP can also read a few settings from a file, `settings.json`, so you do not have to set environment variables. An environment variable always wins over the file when it is set and not empty. A blank variable counts as unset.

| OS | Settings file |
|---|---|
| Windows | `%APPDATA%\ACP\settings.json` |
| macOS | `~/Library/Application Support/ACP/settings.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/acp/settings.json` |

`ACP_HOME` replaces that folder on every OS. The file holds these keys:

| Key | Variable that overrides it | Default |
|---|---|---|
| `fits_roots` | `FITS_ROOTS` | none |
| `manifest_path` | `MANIFEST_PATH` | `<data folder>/manifest.json` |
| `port` | `PORT` | `5555` |
| `scan_cron` | `ACP_SCAN_CRON` | unset |
| `shortcuts` | none | `null` (reserved, no effect yet) |

`fits_roots` is a list of folder paths. `manifest_path` and `port` need a restart to take effect. The bind address is never read from the file: `HOST` stays an environment variable so no file or page can open ACP to the network. A scan started by the app, and `python scripts/build_archive_manifest.py` run by hand from a source checkout, both read `fits_roots` from the file.

If the file cannot be parsed, ACP starts on the defaults and saves the unreadable file once as `settings.json.bak`. A second unreadable file does not replace that copy. `GET /api/settings` reports the problem as `"error": "settings_file_corrupt"`.

The installed Docker image does not need a file. It sets `FITS_ROOTS`, and the environment keeps winning.

### The data folder

Every file ACP writes, such as the manifest, plans, gear, sites, saved searches, the scan cache and `catalogs.json`, lives in `./data` in a source install. Set `ACP_DATA_DIR` to move all of them at once. A per-file variable such as `PLANS_PATH` still beats it. Three files that ship with ACP stay in `./data` of the app folder and are never moved: `catalog_registry.json`, `filter_catalogue.json` and `surveys.json`. `ACP_DATA_DIR` is an environment variable only, because the settings file cannot say where the settings file is.

### Settings routes

`GET /api/settings` reports each key's value, where it came from (`env`, `file` or `default`) and whether the environment locks it. Folder and manifest paths go only to an authorised client: one that sent the bearer token or session cookie, or, when `ACP_API_TOKEN` is not set, one on the same machine addressing ACP as `localhost` or `127.0.0.1`.

`PUT /api/settings` saves `fits_roots` (and `shortcuts`, once that is available). It takes JSON, answers 409 `locked_by_env` for a key the environment sets, and 409 `scan_running` for `fits_roots` while a scan runs. `POST /api/settings/adopt-env` copies the folders in `FITS_ROOTS` into the file. See [api.md](api.md) for the details.

## Configuration variables

Both the build script and the webapp read these environment variables. Set whatever you need before launching either; defaults are sensible for most setups.

| Var | Default | Purpose |
|---|---|---|
| `FITS_ROOTS` | (none, required for build) | One or more image roots for the manifest builder. Semicolon-separated on every platform. |
| `MANIFEST_PATH` | `./data/manifest.json` | Where the manifest is read (by the app) and written (by the builder). |
| `CATALOGS_PATH` | `./data/catalogs.json` | Path to overlay catalogues. |
| `GEAR_PATH` | `./data/gear.json` | Telescopes + cameras for the planner. |
| `PLANS_PATH` | `./data/plans.json` | Saved session plans. |
| `FULL_MASTERS` | `./state/full_masters` (if exists) | Extra root of stacked masters to scan in addition to `FITS_ROOTS`. |
| `PIPELINE_DB` | `./state/job_queue.db` (if exists) | Optional SQLite DB with a `frames` table for sub-exposure hours. See below. |
| `TS_DB_PATH` | `%LOCALAPPDATA%/NINA/SchedulerPlugin/schedulerdb.sqlite` | NINA Target Scheduler DB (Windows only, optional). |
| `ZIP_OUTPUT_DIR` | `./data/exports` | Where TS-sync zips are written. |
| `HOST` | `127.0.0.1` | Bind host. Use `0.0.0.0` only on a trusted LAN, ACP has no authentication. |
| `ACP_BIND_HOST` | unset | Bind host for `python app.py`, checked before `HOST`. A launcher sets it to the address it really binds. |
| `ACP_DATA_DIR` | `./data` | Moves every file ACP writes. See [the data folder](#the-data-folder). Environment only. |
| `ACP_HOME` | per-OS folder | Replaces the folder that holds `settings.json`. |
| `PORT` | `5555` | Bind port. |
| `ACP_EXTENSIONS_DIR` | `%APPDATA%/acp/extensions` (Win) / `~/.config/acp/extensions` | Directory of extension modules. |
| `ACP_DISABLE_BUNDLED_EXTENSIONS` | unset | Set to `1` to skip the extensions that ship inside ACP (the Target Scheduler sync). |
| `ACP_FRIEND_MANIFESTS` | unset | Semicolon-separated paths to sanitised friend manifests. |
| `ACP_SURVEYS_PATH` | `./data/surveys.json` | Path to the public-survey registry. |
| `ACP_CATALOG_REGISTRY` | `./data/catalog_registry.json` | Catalogue registry read by the catalogue loader. |
| `ACP_MOC_CACHE_DIR` | `./data/moc_cache` | Cache for downloaded survey coverage (MOC) files. |
| `SITES_PATH` | `./data/sites.json` | Observing sites. |
| `DESTINATIONS_PATH` | `./data/destinations.json` | Export destinations. |
| `SAVED_SEARCHES_PATH` | `./data/saved_searches.json` | Saved target searches. |
| `TARGET_OVERRIDES_PATH` | `./data/target_overrides.json` | Per-target overrides. |
| `ACP_SCAN_CACHE` | `./data/scan_cache.json` | Where the builder caches per-file header metadata between scans. |
| `ACP_SCAN_CRON` | unset | Cron expression for an automatic rescan run by the app itself. See below. |
| `ACP_STATIC_MAX_AGE_S` | `0` | Cache lifetime for static files. At 0 the browser checks each file on every load and gets a short "not modified" reply when nothing changed. A longer lifetime can leave an open browser running old scripts after an upgrade. |
| `ACP_API_TOKEN` | unset | When set, requires `Authorization: Bearer <token>` on `/api/*` (401 otherwise). Unset means no auth, matching pre-existing loopback behaviour. See [docs/api.md](api.md#optional-bearer-token-auth). |
| `ACP_PUBLISH_DEST`, `ACP_PUBLISH_SSH_KEY`, `ACP_LIVE_OUT_DIR` | unset | Live-page publishing, see [sharing.md](sharing.md). |
| `ACP_MAX_BODY_BYTES` | `1048576` | Largest request body accepted. Anything bigger gets a 413. The largest legitimate body is a 400-panel mosaic at roughly 25 KB, so raise this only if you hit the limit. |
| `ACP_MAX_FINGERPRINT_PROFILES` | `50` | How many NINA profiles the gear fingerprint store keeps. Oldest report is dropped first. |
| `NAS_PREFIX`, `PIPELINE_DB_ALT` | unset | Manifest builder only: NAS path prefix and an alternate pipeline DB, see `scripts/build_archive_manifest.py`. |

A typical "everything in one custom location" invocation:

    FITS_ROOTS="D:/Astro/Images;E:/Archive" \
    MANIFEST_PATH=./data/custom_manifest.json \
    PIPELINE_DB=./my_pipeline.db \
        python scripts/build_archive_manifest.py

    MANIFEST_PATH=./data/custom_manifest.json python app.py

## Optional: sub-exposure hours from a pipeline DB

If you keep a SQLite database tracking every captured sub-exposure (not just stacked masters), ACP can include those sub-hours in the per-target totals. This is useful when you've captured far more subs than you've stacked, without it, hours come solely from master headers (`NCOMBINE × EXPTIME`).

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
- **Counted for hours but not plotted:** files classified as "subs" (unstacked individual exposures), only if you've wired up a `PIPELINE_DB`. Otherwise they're skipped entirely.
- **Skipped silently:** any file without WCS, any non-FITS/XISF file, anything the loader can't parse.

The result is that your map shows clean polygons for everything you've actually processed, while the per-target hours still reflect total time on sky if you've kept the optional DB.

### Flattened archives

The builder de-duplicates the multiple pipeline stages WBPP writes (`calibrated/`, `registered/`, `master/`, `og/`, `starless/`, `stars/`) so a frame processed through several stages is only counted once. This works best on **WBPP's default layout**, where each stage lives in its own subfolder, that's the supported path.

If you've **manually flattened** your archive, for example dragging the raw `.fits` lights into the same folder as their calibrated/registered `.xisf` siblings, the builder still pairs an `X.fits` with its `X_c.xisf` / `X_c_r.xisf` (etc.) sibling and counts them as one physical frame, printing a `collapsed N files to M physical frames` line when it does. Pairing is anchored to WBPP's stage-suffix naming (`_c`, `_cc`, `_r`, `_d` and their ordered combinations); frames that have been renamed away from that convention can't be paired and may double-count. Renamed stage folders (e.g. `cal/`, `reg/`, `masters/`, `original_fits/`) are recognised as aliases. To stay on the well-tested path, keep WBPP's default staged output rather than reorganising stages into one directory.

## Re-running after new captures

After a new imaging session, just re-run the manifest builder:

    FITS_ROOTS="..." python scripts/build_archive_manifest.py

The webapp watches the manifest file and reloads automatically when its mtime changes. You don't need to restart `app.py`, just rebuild and refresh your browser.

If you've added new gear (a new scope or camera), open Planning mode in the app and hit "Scan coverage" in the gear editor, the planner re-merges manifest-derived gear into your `data/gear.json` without overwriting your manual edits.

### The scan cache

Almost all of a scan is spent opening one header per file, and almost nothing in an archive changes between one night and the next. The builder therefore remembers what it read from each file in `data/scan_cache.json` and skips the header read when the file's path, size and modification time are all unchanged. The manifest comes out exactly the same either way; only the time taken changes. On a large archive with a handful of new subs a rescan finishes in seconds instead of minutes.

The end of a run prints what the cache did:

    Header cache:      98412 hits, 341 misses, 12 dropped

Hits were served from the cache, misses were read for real (new or changed files), and drops are entries for files that are no longer in the archive, which are removed when the cache is rewritten.

You never have to manage the cache by hand. It is rewritten in full at the end of every run, a missing or damaged file simply means a normal cold scan, and it is invalidated automatically when you upgrade ACP to a version whose header readers changed. Move it with `ACP_SCAN_CACHE` if `data/` is on slow or read-only storage, and pass `--no-cache` to force every header to be read again:

    python scripts/build_archive_manifest.py --no-cache

The first scan after upgrading to the version that records each master's rotation is one of those cold scans, because the header readers changed. On a big archive that can take hours. If your last scan used the version just before it, you can skip most of that:

    python scripts/build_archive_manifest.py --refresh-rotation

This keeps the old cache and opens only the plate-solved masters again, since they are the only files the angle is read from. Everything else comes from the cache. Use it once, straight after the upgrade. A cache from an older version than that can hold other stale answers, so run a normal scan instead.

If you keep two archives with different `FITS_ROOTS` and run both regularly, give each its own `ACP_SCAN_CACHE` path. Sharing one is harmless but each run drops the other's entries, so neither ever gets a warm scan.

### Rescanning on a schedule

Set `ACP_SCAN_CRON` to a five-field cron expression and the app runs the builder for you on that schedule, in the same process, with no cron daemon or Task Scheduler entry to set up. It is off unless you set it. A nightly rescan at 03:00:

    ACP_SCAN_CRON="0 3 * * *"

Or in Docker, along with the roots the builder needs:

    docker run -d \
      -e FITS_ROOTS=/data/images \
      -e ACP_SCAN_CRON="0 3 * * *" \
      -e TZ=Australia/Sydney \
      -v /mnt/nas/images:/data/images:ro \
      -v acp-data:/app/data \
      -p 5555:5555 astro-coverage-planner

Times are the local time of the machine or container running ACP, so set `TZ` if you want a particular midnight. Keep `/app/data` on a volume as above, otherwise the scan cache is thrown away with the container and every scheduled run is a cold one.

Each run is a separate process, so a builder that crashes leaves the web app serving the previous manifest. A scheduled scan is skipped if the previous one is still running, which matters on a big archive where a cold scan can outlast the gap between runs. `GET /api/scan/status` reports what happened:

    {"cron": "0 3 * * *", "scheduled": true, "running": false,
     "last_start": "2026-09-04T03:00:00", "last_finish": "2026-09-04T03:00:24",
     "last_exit_code": 0, "last_trigger": "cron", "last_error": null}

`last_exit_code` is 0 for a clean run. Anything else means the builder failed, and the reason is in the app's log output.

## Security and deployment notes

ACP is intended to run on your own machine, behind your own firewall, against your own data. Two things to be aware of before exposing it to anything beyond `localhost`:

- **Default bind is `127.0.0.1`.** Only switch to `HOST=0.0.0.0` on a trusted LAN. ACP has no authentication by default, so anyone who can reach the port can read every target you have shot, its coordinates and its hours. Since 2026-09-07 the `/api/manifest` and `/api/target/<id>` endpoints report file paths relative to your scan roots rather than in full, so your username and mount points no longer cross the wire, but the folder structure inside the archive still does. Set `ACP_API_TOKEN` (see [docs/api.md](api.md#optional-bearer-token-auth)) if you do put ACP on a LAN, e.g. so a NINA plugin on another machine can reach it.
- **`python3 app.py` runs Flask's dev server.** Fine for local single-user use; not built to handle public internet traffic. The Docker image runs `waitress` instead, which is a production WSGI server, but it still has no TLS or authentication. For anything beyond your own network, put either behind a reverse proxy (nginx, caddy) that adds TLS and authentication.

If you need genuinely shared access for a small group, the [friend manifests](sharing.md) feature is the intended path, each person runs their own copy of ACP locally and consumes sanitised exports from the others, rather than pointing multiple browsers at one shared instance.
