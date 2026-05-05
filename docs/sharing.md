# Sharing coverage with friends

Planning narrowband sessions is easier when you can see your imaging buddies' coverage gaps next to your own — split the sky, agree who's chasing OIII on a given target, avoid duplicating Hα hours someone else already has stacked. ACP can load any number of *sanitised* friend manifests as additional toggleable layers in the Sources rail.

## What gets stripped vs kept

The sanitiser rebuilds the manifest from a small whitelist:

- **Stripped**: `master_files` arrays, all on-disk paths, telescope/camera serials and exact model strings, exact dates (truncated to month), per-frame file lists, `scan_roots`, anything else that fingerprints the imager's setup.
- **Kept**: polygon footprints (`corners_icrs`), per-filter integration hours (rounded to 0.1h), aperture-class telescope info (e.g. `"110mm refractor"`), public catalogue target names (`"M 42"`, `"Eta Carinae"`), month of last activity.
- **Tripwire**: the loader refuses any file missing `"sanitised": true`, so you can't accidentally share an unsanitised manifest.

## How to produce one

Two paths:

```bash
# Option A: re-run the scanner with --sanitise alongside the regular write
python scripts/build_archive_manifest.py --sanitise dave_to_share.json --label "Dave"

# Option B: sanitise an existing manifest in place
python scripts/sanitise_manifest.py data/manifest.json dave_to_share.json --label "Dave"
```

Inspect the output before sending — it's plain JSON, you can read it.

## How to consume one

Point `ACP_FRIEND_MANIFESTS` at a semicolon-separated list of paths before launching:

**macOS / Linux:**

```bash
ACP_FRIEND_MANIFESTS="/path/to/dave.json;/path/to/sara.json" python app.py
```

**Windows PowerShell:**

```powershell
$env:ACP_FRIEND_MANIFESTS = "C:\shared\dave.json;C:\shared\sara.json"
python app.py
```

Each manifest becomes its own toggleable layer in the **Sources** rail with a distinct colour from the palette.

## Failure modes

Rejected manifests log a `WARNING` to Python's `logging` channel and are skipped — other friends still load and the app still starts. Hard caps are applied during validation:

- 10,000 targets per manifest
- 64 polygons per target
- 64 vertices per polygon

These exist mostly to bound memory usage if a malformed manifest somehow slips through.

## Workflow tips

- Agree on a labelling convention with your group (`dave`, `sara`, `astrocollective_dave`) so the legend stays readable when you have several friends loaded.
- The sanitiser is deterministic — re-running it on an updated manifest produces a stable diff, useful if you're versioning shared manifests in a private git repo.
- Friend manifests participate in the [gap-finder](api.md#gap-finder) — their hours count toward the "have" side, so you can find regions *your group* hasn't covered together.
