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

Separate from friend manifests: this pushes a small public summary of your own projects to a web page you host, so people can see what you are shooting, what is up for the vote and what you have finished. Spec and data contract: [specs/shooting-page.md](specs/shooting-page.md). The output goes through the same path tripwire as the friend sanitiser, and free text you type is scanned for file paths too.

### Which plans appear

A plan is only a candidate if it is public. Private plans are never published, and they never reach the file at all.

- **Visibility** in the plan editor is private or public. If you set nothing, a plan with target class "pretty" is public and every other class is private. Choosing either value overrides that default.
- A public plan then needs a status (below). With no status it is left out, so a public plan you have not shot for a while quietly drops off the page.
- Projects are listed with the most recently imaged first.

### Status chips

Each published project gets one chip. ACP checks them in this order and stops at the first match.

| Chip | When |
|---|---|
| Vote winner | The plan has a vote commitment for this month or an earlier one that is not yet met. The chip reads "October vote winner" (the commitment's month). |
| Vote | The plan's section is "Up for the vote" and it has no such commitment. |
| Finished | You set a Finished date, and it is within the last 30 days. |
| Imaging now | The last imaged night is within 3 nights. |
| Recent | The last imaged night is within 14 days. |

Some details that decide edge cases:

- A plan with an unmet commitment never shows in the vote section, even if its section still says vote.
- A commitment is met when you mark it met, or when every filter in it has reached its goal. A met commitment stops being the winner, and the plan falls back to the other chips.
- A finished plan is judged by its Finished date alone. After 30 days it drops off, even if frames were added later.
- The last imaged night comes from the archive scan. It is the latest end date of the logged targets that sit on the plan's field.
- The windows (3 nights, 14 days, 30 days) are in `LIVE_STATUS_WINDOWS` at the top of `shooting_publish.py`. Boundaries are inclusive.

### What each project shows

- Project name and target name, with a trailing site tag such as "(Dark Skies)" removed.
- Your blurb, the field position and size (including a mosaic's rows and columns), and the telescope as an aperture class such as "110mm refractor".
- Hours done and hours wanted per filter, and an overall total. Done is capped at the goal for each filter.
- The last imaged date.
- Finished projects also show the finish date and the AstroBin link, if it is a valid https link on astrobin.com.

The hours depend on the chip.

- Imaging now, recent and finished: every filter on the plan, with its full goal and the hours logged so far.
- Vote: only the filters you gave hours for, and "done" counts only frames added since the plan entered the vote. ACP records that starting point itself when you move the plan into the vote section.
- Vote winner: the filters and goals of the commitment, with done counted since the commitment's starting point.

Hours count only frames from the plan's own telescope and camera. The archive scan records the telescope and camera names from the FITS headers, and ACP compares them with the plan's gear by name. A frame from a different scope or camera on the same sky position is left out. If the scan or the plan does not name a side, that side never excludes anything. Hours with no rig record, such as an older manifest, still count. If the Target Scheduler extension reports more hours than the scan for a filter, ACP uses the higher number.

### Fields in the plan editor

Once `ACP_PUBLISH_DEST` is set (below) and ACP is restarted, the plan editor gains a "Public page" section. Without it, nothing in the UI changes and nothing is ever published. The fields below the visibility choice show only when the plan is public.

- **Visibility**: private or public. A pretty plan with no choice made reads "Public (default for pretty plans)".
- **Finished**: the date you finished the project, with a Today button. Setting it gives the Finished chip for 30 days.
- **AstroBin link**: shown on the Finished card. Must be https on astrobin.com, at most 300 characters.
- **Current project**: a checkbox. It no longer changes the chip, because the chip comes from imaging activity. The flag is still written to the file for one release so the old page renders.
- **Section**: "Currently imaging" or "Up for the vote".
- **Vote hours**: appears when the section is "Up for the vote". One box per filter, blank meaning the filter is not part of the vote option. The editor warns above 7 hours in total, since an option is meant to be about 6.
- **Won the vote for**: appears on a saved plan in the vote section. Pick a month and press the button. It adds a vote commitment from the vote hours, sets the project's balancer role to pretty, and offers to move every other vote plan back to the current section, which ends the round.
- **Why I'm shooting this**: a short blurb, up to 500 characters, shown on the card.
- **Publish now (saved plans)**: builds and uploads the page straight away. It publishes saved plans, so save first.

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

### When it publishes

Nothing publishes by itself inside ACP. There are two ways to trigger it.

- A scheduled run on the machine that runs ACP, normally once a morning.
- The **Publish now** button in the plan editor, or `POST /api/publish/shooting`. Neither rescans the archive.

```bash
python scripts/publish_shooting.py --rescan    # rebuild the manifest, then publish
python scripts/publish_shooting.py             # publish from the current manifest
python scripts/publish_shooting.py --dry-run   # write data/live/shooting.json and stop
```

The page can only be as current as your last archive scan, so schedule the run after the rig has finished for the night. A cron entry:

```
15 7 * * * cd /path/to/acp && .venv/bin/python scripts/publish_shooting.py --rescan >> data/live/publish.log 2>&1
```

On Windows, the same command in Task Scheduler. The button publishes whatever the last scan found, so a frame logged after the scan does not appear until the next scan.

### The page itself

ACP ships the data, not the page. A reference page (plain HTML and JS, no build step) lives in the `astro-roro/astrowithroro` repo under `live/`. It reads `shooting.json` from its own folder and fetches field thumbnails from CDS hips2fits in the browser, so no images are stored or pushed.
