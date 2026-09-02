# Spec: the "what I'm shooting" public page

Status: draft, 2026-09-02. Part one of the public sharing work. Part two (friends and family voting on targets) is a separate spec that builds on this one.

## Goal

A page at astrowithroro.com/shooting that shows which projects Rohan is currently working on, how far along each one is, and when it was last imaged. ACP is the only source. NINA is not involved. Only plans Rohan has explicitly marked public appear.

## What the reader sees

One page with a short list of project cards, most recent activity first. Each card shows:

- the project name and target name
- a one or two sentence blurb, written by Rohan, on why he is shooting it
- a "current" badge on the project he has marked as current
- per-filter progress, as hours done of hours planned, with a simple bar
- "last imaged N nights ago" (or "not started yet")
- a thumbnail of the field, fetched by the browser from the CDS hips2fits service using the plan's centre and field of view, so no images are stored or pushed

Above the cards, one line: "Updated <date>, from a scan on <date>". The page never claims to be live. Below the cards, one line linking back to the ACP GitHub repo.

## Plan fields added

Three optional fields on a plan, all validated in `_validate_plan_payload` and editable in the plan edit panel:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `visibility` | `"private"` or `"public"` | `"private"` | Only `"public"` plans are published. Any other value is rejected with 400. |
| `is_current` | bool | `false` | Shows the "current" badge. More than one current plan is allowed. |
| `public_blurb` | string, max 500 chars | `""` | Rohan's "why I'm shooting this" text. Plain text, no markup. |

A plan with no `visibility` field is private. The UI shows a visibility toggle and the blurb field only when the plan is public, so the default state of every existing plan is unchanged and nothing leaks from plans created before this feature.

## The published JSON

`GET /api/public/shooting` returns the exact document that will be published, so Rohan can preview it in the browser. Shape:

```jsonc
{
  "version": 1,
  "generated_at": "2026-09-03T07:00:00+10:00",
  "data_as_of": "2026-09-03",            // manifest scan_date, date only
  "projects": [
    {
      "project_name": "Vela SNR",
      "target_name": "Vela Supernova Remnant",
      "blurb": "Huge, faint, and I have never had enough OIII on it.",
      "is_current": true,
      "center_ra_deg": 128.5, "center_dec_deg": -45.0,
      "fov_arcmin": [220.0, 165.0],       // whole mosaic extent, not one panel
      "mosaic": {"rows": 2, "cols": 2},   // omitted for single frames
      "telescope": "51mm refractor",      // aperture class only, via the sanitiser rule
      "filters": {
        "Ha":   {"target_hours": 12.0, "done_hours": 8.3},
        "OIII": {"target_hours": 12.0, "done_hours": 2.1}
      },
      "last_imaged": "2026-09-01",        // date only, or null
      "last_imaged_nights_ago": 2         // relative to generated_at, or null
    }
  ]
}
```

Rules:

- Only plans with `visibility == "public"` are included. `is_current` on a private plan does not publish it.
- Coordinates are rounded to 0.01 degrees. Hours are rounded to 0.1. Dates are dates, never datetimes.
- No plan id, guid, gear ids, camera model, sub-exposure, site, or timestamps other than the ones above.
- The document is run through `validate_no_paths` from `scripts/sanitise_manifest.py` before it is written. A leak raises and nothing is published.
- Sorted by `last_imaged` descending, nulls last, then by `project_name`.

## Matching a plan to logged data

Plans do not know which manifest targets are theirs. The publisher matches them by sky position:

- A manifest target belongs to a plan when the angular separation between their centres is at most half the larger of the two diagonal extents. The plan's extent is the full mosaic footprint, computed from the plan's telescope and camera via the existing `_fov_arcmin` and mosaic geometry.
- `done_hours` per filter is the sum of `filters.<f>.total_hours` across matched targets. If the plan's own `filter_goals.<f>.actual_hours` is higher, use that instead, since it came from a fresher source.
- `last_imaged` is the latest `date_range[1]` across matched targets, or null if none match.
- Filter names are matched after the same canonicalisation the scanner uses, so `Ha`, `H-alpha` and `HA` agree.

## Publishing

`scripts/publish_shooting.py` does the whole run from the command line:

```
python scripts/publish_shooting.py --rescan   # rebuild manifest first, then publish
python scripts/publish_shooting.py            # publish from the current manifest
python scripts/publish_shooting.py --dry-run  # write to data/shooting/ and stop
```

What it does:

1. With `--rescan`, runs the manifest builder with the same arguments as the normal scan.
2. Builds the JSON document via the same code path as `/api/public/shooting`.
3. Writes `data/shooting/shooting.json` locally.
4. Pushes that one file to the destination with rsync over SSH.

The page that reads the file lives in the site repo, `astro-roro/astrowithroro`, under `shooting/`. ACP owns the data and the contract above; the site owns the presentation. The site repo gitignores `shooting/shooting.json` so the pushed file never shows up as drift there.

Configuration, all environment variables, matching the rest of ACP:

| Variable | Meaning |
|---|---|
| `ACP_PUBLISH_DEST` | rsync target, for example `linuxuser@100.106.46.47:/home/astrowithroro.com/public_html/shooting/shooting.json`. Publishing refuses to run when unset. |
| `ACP_PUBLISH_SSH_KEY` | Optional path to the key. Defaults to whatever SSH would use. |

The push is initiated from the ACP machine, never from Canopus, which keeps to the rule that Canopus only ever receives. The web root on Canopus is owned by `astro2735` and the SSH user is `linuxuser`, so a one-off setup step creates `public_html/shooting/` owned by `linuxuser` with mode 755. After that rsync writes the JSON directly with no sudo. That setup step is documented in the site repo's README, not automated. Scheduling is a cron or Task Scheduler entry on the machine that runs ACP, documented in `docs/sharing.md`, not built into the app. Suggested cadence is once each morning after the rig has finished.

There is also `POST /api/publish/shooting`, which runs steps 2 to 4 without a rescan and returns the rsync result, so a "Publish now" button in the plan rail works without a terminal.

## The page itself

`shooting/index.html` and `shooting/shooting.js` in the site repo, plain HTML and vanilla JS, no build step, one small stylesheet inlined. The page fetches `shooting.json` relative to itself. It renders the cards described above, handles a missing or malformed JSON with a single "nothing public right now" line, and uses hips2fits with the DSS2 colour survey for thumbnails. The page is built as part of this work and deployed the same way as the rest of the site. Matching the site's existing look is done once, at build time, by copying the header and colours from `index.html`.

## Out of scope

- Live status from NINA.
- Votes, suggestions, or anything that writes back. That is part two.
- Any change to the sanitiser's friend-manifest behaviour.
- Lightbucket.

## Tests

`tests/test_shooting_publish.py`, unittest style like the rest of the suite:

- `visibility` accepts only `"private"` and `"public"`; a plan without it is treated as private; the blurb is capped at 500 characters.
- A private plan with `is_current` set is not in the output. A public plan is.
- The output passes `validate_no_paths` and contains none of the forbidden keys listed above.
- A manifest target inside the plan's extent contributes its hours and date; one outside does not.
- `done_hours` takes the larger of manifest hours and `actual_hours`.
- `last_imaged` is null and `last_imaged_nights_ago` is null when nothing matches.
- Sort order is as specified.
- `publish_shooting.py --dry-run` writes the JSON and does not call rsync. Without `ACP_PUBLISH_DEST` and without `--dry-run` it exits non-zero with a clear message.
