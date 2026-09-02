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

## Publishing a live page

Separate from friend manifests: this pushes a small public summary of your own current projects to a web page you host, so people can see what you are shooting. Spec and data contract: [specs/shooting-page.md](specs/shooting-page.md). Only plans you mark public are ever included, and the output goes through the same path tripwire as the friend sanitiser.

### Marking a plan public

Once `ACP_PUBLISH_DEST` is set (below) and ACP restarted, the plan editor gains a "Public page" section with three fields. Without it, nothing in the UI changes and nothing is ever published.

- **Visibility**: private (default) or public. Nothing leaves your machine unless this is public.
- **Current project**: shows a "current" badge on the page. More than one is fine.
- **Why I'm shooting this**: a short blurb, up to 500 characters, shown on the card.

`GET /api/public/shooting` returns exactly what would be published, so you can check it in a browser first.

### Configuration

| Variable | Meaning |
|---|---|
| `ACP_PUBLISH_DEST` | Where to upload the JSON over SFTP, as `user@host:/var/www/site/live/shooting.json`. Publishing refuses to run when unset. |
| `ACP_PUBLISH_SSH_KEY` | Optional path to an SSH key. Defaults to whatever SSH would use. |
| `ACP_LIVE_OUT_DIR` | Where the JSON is written locally before pushing. Defaults to `data/live/`. |

The destination folder on the server must be writable by the SSH user, since the upload is a plain SFTP put with no sudo. ACP only ever pushes; the server never connects back. The upload uses paramiko, so no ssh or rsync binary is needed, which matters in Docker where the container user often has no passwd entry and OpenSSH refuses to run.

Host keys are kept in `known_hosts` next to the JSON in the output folder. The first contact records the server's key and a changed key is refused after that. Without `ACP_PUBLISH_SSH_KEY`, the usual keys and agent are tried. In Docker, mount a dedicated key read-only and point the variable at it:

```yaml
    environment:
      ACP_PUBLISH_DEST: "user@your-web-host:/var/www/site/live/shooting.json"
      ACP_PUBLISH_SSH_KEY: "/run/secrets/acp_publish_key"
    volumes:
      - "/path/on/host/acp_publish_key:/run/secrets/acp_publish_key:ro"
```

The key file must be readable by the container user and no one else, or ssh refuses it.

### Running it

```bash
python scripts/publish_shooting.py --rescan    # rebuild the manifest, then publish
python scripts/publish_shooting.py             # publish from the current manifest
python scripts/publish_shooting.py --dry-run   # write data/live/shooting.json and stop
```

The page can only be as current as your last archive scan, so run it after the rig has finished for the night. A cron entry on the machine that runs ACP:

```
15 7 * * * cd /path/to/acp && .venv/bin/python scripts/publish_shooting.py --rescan >> data/live/publish.log 2>&1
```

On Windows, the same command in Task Scheduler. `POST /api/publish/shooting` does the same thing without a rescan, for a "publish now" from anything that can reach the API.

### The page itself

ACP ships the data, not the page. A reference page (plain HTML and JS, no build step) lives in the `astro-roro/astrowithroro` repo under `live/`. It reads `shooting.json` from its own folder and fetches field thumbnails from CDS hips2fits in the browser, so no images are stored or pushed.
