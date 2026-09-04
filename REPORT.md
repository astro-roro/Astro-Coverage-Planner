# Incremental scan cache and scheduled rescan

Branch: `feat/incremental-scan`. Four commits on top of `main`.

## What changed

### 1. Per-file header cache (`scripts/build_archive_manifest.py`)

Repeat scans spent nearly all their time reopening headers that had not changed. The builder now keeps a cache at `data/scan_cache.json` (override with `ACP_SCAN_CACHE`) with one entry per header-read file:

    {"schema": 1, "reader_hash": "<sha256>", "written": "<iso>",
     "entries": {"<path>": {"size": 123, "mtime": 1756948800.0,
                            "meta": {...}, "unrecognised": []}}}

- A file is a hit when path, size and mtime all match. A hit skips the header read entirely and the cached dict is fed into exactly the same place the reader's return value went, so nothing downstream can tell the difference.
- The cache is read once at the start and written once at the end, atomically (temp file plus `os.replace`, the same pattern `app.py` already uses for its JSON stores). A write failure warns and does not fail a run that already produced a manifest.
- The document is rebuilt from scratch each run, so files that have vanished are simply not carried over. The summary prints `Header cache: N hits, N misses, N dropped`.
- A missing, unparseable, non-object, wrong-schema or wrong-hash cache means a cold scan and never a crash.
- `--no-cache` skips reading the cache but still writes it, so a forced cold scan leaves the next run warm.

**Invalidation choice.** As the task suggested, the cache header carries `sha256(schema version + inspect.getsource(read_fits_meta) + inspect.getsource(read_xisf_meta))` and any mismatch is a cold scan. That covers both the hand-bumped schema and, automatically, any edit to either reader, including edits made in a release the user upgrades into. If source is unavailable (a frozen build) the fingerprint falls back to a value that forces a cold scan rather than trusting a stale cache. `SCAN_CACHE_SCHEMA` is still there for changes to the cache file's own shape.

**Portability.** The key is the path exactly as scanned, with backslashes folded to forward slashes so the same file keys identically whether it was reached as `D:\Astro\x.fit` or `D:/Astro/x.fit`. Nothing else is derived from the path, and the absolute path is deliberately not stored inside the cached metadata (`meta["path"]` is stripped on write and set from the scanned path on read). If a NAS is remapped to a different drive letter every key misses, which is a cold scan rather than a silent mismatch. mtimes are rounded to microseconds before storing and comparing so a JSON round trip compares equal, which is well below the resolution of any filesystem in play (NTFS, SMB, ext4, and the two second granularity of FAT-style exports).

**One subtlety worth knowing about.** `read_fits_meta` counts unrecognised filter names into a module-level counter that ends up in the manifest's `integrity_flags`. A skipped read would have silently under-counted them on a warm scan, so each read now records the names it counted (per thread, since reads run in a pool), the cache stores them, and a hit replays the same increments. That is what makes the two manifests byte-identical rather than nearly so.

`glob_archive` now returns `(path, size, mtime)` triples, taking the mtime from the stat it already did, so the cache costs no extra stat per file on a NAS.

### 2. Scheduled rescan (`app.py`)

`ACP_SCAN_CRON`, when set to a cron expression, starts a daemon scheduler thread at import (so it works under `waitress-serve app:app` in Docker, not just `python app.py`). Unset means today's behaviour, with no thread and nothing started. `croniter>=6.0` was not in `requirements.txt` and has been added; if it is missing at runtime the app logs an error and schedules nothing rather than failing to start.

- Each scan runs as a subprocess (`sys.executable scripts/build_archive_manifest.py`), so a builder crash or a MemoryError on a huge archive cannot take the web app down.
- `run_scan_now()` refuses to start while a scan is running, under a lock. Two builders would race over the same manifest and the same scan cache, and on a large archive an overlap is likely rather than theoretical.
- `start_scan_scheduler()` refuses to start a second scheduler thread, and an invalid expression is logged and ignored.
- The wait loop wakes every 30 seconds rather than sleeping until the fire time, so shutdown stays responsive and a machine that suspends across the scheduled minute re-reads the clock instead of firing hours late.
- `GET /api/scan/status` returns `cron`, `scheduled`, `running`, `last_start`, `last_finish`, `last_exit_code`, `last_trigger`, `last_error`.

Times are the local time of the machine or container, so `TZ` matters in Docker; that is documented.

### 3. Docs

`docs/setup-archive.md` gains `ACP_SCAN_CACHE` and `ACP_SCAN_CRON` in the configuration table, a section on what the cache does and how to move, read or bypass it, and a section on scheduled rescans with a nightly `0 3 * * *` example both bare and as a `docker run` with a `/app/data` volume so the cache survives the container.

`data/scan_cache.json` is gitignored: it is derived from the user's private archive and holds absolute paths.

## Timing

Fixture tree of 2250 synthetic 64x64 FITS lights (three targets by three filters by 250 frames), local disk, same machine, cache on:

| Run | Header phase | Total |
|---|---|---|
| Cold (2250 reads) | 4.4 s | 4.5 s |
| Warm (2250 hits, 0 reads) | 0.05 s | 0.2 s |

About 23x end to end, and the warm run's remaining time is globbing plus clustering, not header reads. The manifests were identical apart from `scan_date` and `scan_duration_sec`. A real archive with much larger headers and a NAS in the path should gain more, since the saving is per file open rather than per byte.

## Tests

`tests/test_scan_cache.py` (10 tests) runs the real builder as a subprocess over a synthetic tree built the way `tests/test_scanner_folder_sampling.py` builds its fixtures: cold then warm manifest equality, cache file shape, changed mtime forces one re-read, changed content re-reads and shows up in the cache, a deleted file is dropped, a stale reader hash and a bumped schema both force a cold scan, a corrupt cache falls back to cold and is repaired, a missing cache is not an error, and `--no-cache` ignores an existing cache while still leaving a usable one behind.

`tests/test_scan_schedule.py` (11 tests) covers the status endpoint shape before a scan, after a clean scan, after a failed scan and during a running scan; that a second scan is refused while one runs and allowed once it finishes; that a second scheduler thread is not started; that the loop actually fires a scan; that `0 3 * * *` resolves to the next 3 am; and that with the variable unset nothing is scheduled and the app serves its normal endpoints.

Full suite, foreground, explicit timeout:

    timeout 590 python3 -m pytest tests -q --ignore=tests/frontend
    406 passed, 10 warnings, 118 subtests passed in 17.35s

Also run the way CI runs it (`python -m unittest discover -s tests`): 393 tests, OK.

## Notes

- Cache size on a very large archive: roughly 600 bytes per entry, so about 60 MB for 100,000 files, read and written once per run. That is well under the cost of the header reads it replaces, but it is worth knowing before pointing `ACP_SCAN_CACHE` at slow storage.
- Nothing in target clustering, hours counting, the manifest schema or `static/` was touched.
