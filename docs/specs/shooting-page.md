# Spec: the live page, "what I'm shooting"

Status: draft, 2026-09-02. Part one of the public sharing work. Part two (friends and family voting on targets) is a separate spec that builds on this one.

## Goal

A page at astrowithroro.com/live that shows which projects the user is currently working on, how far along each one is, and when it was last imaged. ACP is the only source. NINA is not involved. Only plans the user has explicitly marked public appear.

## What the reader sees

One page with a short list of project cards, most recent activity first. Each card shows:

- the project name and target name
- a one or two sentence blurb, written by the user, on why they are shooting it
- a "current" badge on the project he has marked as current
- per-filter progress, as hours done of hours planned, with a simple bar
- "last imaged N nights ago" (or "not started yet")
- a thumbnail of the field. The publisher fetches it from the CDS hips2fits service (DSS2 colour, 480 by 270, the plan's centre and field of view), keeps it in `data/live/thumbs/<key>.jpg` and uploads it beside `shooting.json`, so a CDS outage does not blank the page. `<key>` is a 12 character hash of the published centre and field, so a changed pointing gets a new file. Each project carries `thumb` (`thumbs/<key>.jpg`) when the file exists and omits it otherwise. A file is fetched only when missing (10 second timeout, 2 attempts), a failed fetch never fails the publish and never deletes an existing file, and after one target fails both attempts the rest are skipped for that run. Only new or changed files are uploaded, tracked in `thumbs/.pushed.json`, and they go up before `shooting.json` moves into place. A dry run lists existing thumbnails and fetches none.

Above the cards, one line: "Updated <date>, from a scan on <date>". The page is called live because that is as live as astrophotography gets, but it never claims more than its dates show. Below the cards, one line linking back to the ACP GitHub repo.

## Plan fields added

Three optional fields on a plan, all validated in `_validate_plan_payload` and editable in the plan edit panel:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `visibility` | `"private"` or `"public"` | `"private"` | Only `"public"` plans are published. Any other value is rejected with 400. |
| `is_current` | bool | `false` | Shows the "current" badge. More than one current plan is allowed. |
| `public_blurb` | string, max 500 chars | `""` | The user's "why I'm shooting this" text. Plain text, no markup. |

A plan with no `visibility` field is private. The whole section is shown only when the server reports publishing is configured (`GET /api/publish/config`, true when `ACP_PUBLISH_DEST` is set), and within it the blurb and current fields appear only when the plan is public. A stock install looks unchanged, every existing plan stays private, and the wording is generic: the option is just "Public", with no site named.

### Vote section fields

Added 2026-10-23 for the monthly vote. All optional and absent on old plans, validated in `_validate_plan_payload`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `public_section` | `"current"` or `"vote"` | `"current"` | Which section of the live page the plan appears in. Anything else is rejected with 400. |
| `vote_goals` | object, filter name to hours | none | The hours each filter has in the vote option. Hours must be positive numbers. Only used when `public_section` is `"vote"`. A filter left out is not part of the option. |
| `vote_baseline` | object, filter name to hours | none | Set by the server, never by the UI. The hours the plan already had per filter when it entered the vote, counted the same way the page counts a current project (the larger of the archive hours and the plan's credited hours). Cleared when the plan goes back to `"current"`. A client can send it but the server overwrites it. |

The server sets the baseline when `public_section` changes to `"vote"` (a fresh snapshot each time the plan enters the vote), and tops it up for any goal filter that has no baseline entry yet. Saving a plan that is already in the vote leaves the baseline alone. This is what stops a plan with earlier data, such as 2023 RGB on NGC 300, looking nearly done: the page shows only hours added after the plan entered the vote.

In the plan editor, the Public page section has a Section choice. "Up for the vote" shows a table of the plan's filters with an hours box each (blank means not in the vote), a running total, and a warning above 7 hours, since each option is meant to be about 6. A "Publish now" button in the same section calls `POST /api/publish/shooting` and shows the project count or the error.

### Status, default visibility and finished fields

Added 2026-10-23. The page is a public log of recent work, so which projects appear is decided by activity, not by a "current" flag.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `finished_at` | date `YYYY-MM-DD`, or empty | none | The day the project was finished. Anything that is not a real calendar date is rejected with 400. An empty string clears it. |
| `astrobin_url` | https URL, or empty | none | Link to the finished image. Must be https on `astrobin.com` or a subdomain, no credentials, no whitespace, 300 characters or fewer. Anything else is rejected with 400. An empty string clears it. |

Visibility by class. An explicit `visibility` always wins. With no `visibility` field, a plan whose `target_class` is `"pretty"` counts as public, and every other class (survey, deep, none) counts as private. In the plan editor the Public page section shows the effective state, so a pretty plan with no field reads "Public (default for pretty plans)", and the user can set it to Private, which is then stored. The Public page section also has a Finished date field with a Today button and an AstroBin link field.

Status, worked out at publish time and published as `status`. A public plan gets the first that applies:

| Status | When |
|---|---|
| `vote` | `public_section == "vote"` and the plan has no current commitment. Always shown, whatever the activity. |
| `vote_winner` | The plan has a current vote commitment that is not met (see Vote winner below). Always shown, whatever the activity. Checked before everything else. |
| `finished` | `finished_at` is set and no more than 30 days ago. A plan with `finished_at` older than that is left out, even if frames were added since. |
| `imaging_now` | Last imaged within the last 3 nights. |
| `recent` | Last imaged within the last 14 days. |

A public plan with none of these is left out of the document. A plan never imaged and not finished is left out. The three windows (3 nights, 14 days, 30 days) are the `LIVE_STATUS_WINDOWS` block at the top of `shooting_publish.py`, inclusive at the boundary. A `finished_at` in the future counts as finished.

`section` and `is_current` are still published for one release so the old page keeps rendering. They are deprecated: the new page should group by `status`. They go in a later release, with a bump of `version` if the page needs it.

### Vote winner

Added 2026-10-23, from `docs/specs/imaging-balancer.md`. A plan that won the monthly vote carries a commitment (`commitments` on the plan, made by the "Won the vote for" action in the editor). Commitments are never published.

The publish date is Sydney's (`activity.tz()`), not the container clock, so the page and the balancer agree on the month. A met commitment is current only in its own month. An unmet one from this month or an earlier month stays current until it is met. A met one from an earlier month is not current, so the plan can be put back in the vote section for a later round. It is met when `met_at` is set or every goal filter the plan can count is at its goal, counted from the commitment's own baseline. A commitment naming no filter the plan can count is ignored. A lapsed commitment (`lapsed_at` set by the balancer, see `imaging-balancer.md` "Lapsing") is ignored too: the plan shows no vote winner chip and falls back to the normal chips.

- **Unmet current commitment.** The plan publishes with `status: "vote_winner"`, `vote_month` (for example `"2026-11"`) and `vote_label` (for example `"November vote winner"`), whatever its last imaged date, as long as it is public. Vote winners are pretty plans, so they are public by default. `filters`, `done_hours` and `target_hours` use the commitment's `goals` and `baseline` the same way the vote section uses `vote_goals` and `vote_baseline`. `section` is `"current"`, so the old page renders it as a current project. With several unmet commitments the earliest month is the one shown, so a carried-over goal comes first.
- **Met.** Once its month has passed, the plan falls back to the normal rules (`imaging_now`, `recent`, `finished`, or left out) with its full-plan numbers, and has no `vote_month` or `vote_label`.
- **Vote section.** A plan with a current commitment is not in the vote section, even with `public_section: "vote"`: the commitment wins. A commitment for a later month is ignored until its month starts, so the plan stays in the vote section until then.
- **End of a round.** The "Won the vote for" action offers to move the other vote plans out of the vote section in one step. See the balancer spec, "The vote winner on the live page".

Example (a winner in the middle of its month):

```jsonc
{
  "project_name": "NGC 300",
  "target_name": "NGC 300",
  "blurb": "...",
  "is_current": false,
  "status": "vote_winner",
  "section": "current",
  "vote_month": "2026-11",
  "vote_label": "November vote winner",
  "center_ra_deg": 13.72, "center_dec_deg": -37.68,
  "fov_arcmin": [220.0, 165.0],
  "telescope": "51mm refractor",
  "filters": {
    "Ha":   {"target_hours": 3.0, "done_hours": 1.5},
    "OIII": {"target_hours": 3.0, "done_hours": 0.0}
  },
  "done_hours": 1.5,
  "target_hours": 6.0,
  "last_imaged": "2026-11-09",
  "last_imaged_nights_ago": 1
}
```

## The published JSON

`GET /api/public/shooting` returns the exact document that will be published, so the user can preview it in the browser. Shape:

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
      "status": "imaging_now",            // "imaging_now", "recent", "finished", "vote" or "vote_winner"
      "section": "current",               // "current" or "vote", deprecated, see below
      "center_ra_deg": 128.5, "center_dec_deg": -45.0,
      "fov_arcmin": [220.0, 165.0],       // whole mosaic extent, not one panel
      "mosaic": {"rows": 2, "cols": 2},   // omitted for single frames
      "telescope": "51mm refractor",      // aperture class only, via the sanitiser rule
      "filters": {
        "Ha":   {"target_hours": 12.0, "done_hours": 8.3},
        "OIII": {"target_hours": 12.0, "done_hours": 2.1}
      },
      "done_hours": 10.4,                 // sum over filters, each done capped at its target
      "target_hours": 24.0,               // sum of the filters' targets
      "last_imaged": "2026-09-01",        // date only, or null
      "last_imaged_nights_ago": 2         // relative to generated_at, or null
      // finished projects only: "finished_at": "2026-10-20", and "astrobin_url" when set
    }
  ]
}
```

Rules:

- Only plans that are public are included (explicitly, or by default for a pretty plan), and of those only plans with a status above. `is_current` on a private plan does not publish it.
- `done_hours` and `target_hours` on the project are totals over the published `filters` (the vote filters for a vote project), rounded to 0.1, with each filter's done hours capped at its target. A finished project keeps its final per-filter progress.
- `finished_at` and `astrobin_url` appear only on `finished` projects, and `astrobin_url` only when set and valid.
- Coordinates are rounded to 0.01 degrees. Hours are rounded to 0.1. Dates are dates, never datetimes.
- No plan id, guid, gear ids, camera model, sub-exposure, site, or timestamps other than the ones above.
- The document is run through `validate_no_paths` from `scripts/sanitise_manifest.py` before it is written, and the three free-text fields (project name, target name, blurb) get a stricter unanchored scan for drive letters, system directories, UNC paths and image file extensions anywhere in the string. A hit raises and nothing is published.
- A trailing site tag on the project name or target name is dropped from the published text: a final parenthetical ending in "Skies", case-insensitive, such as " (Dark Skies)" or " (Any Skies)". The plan and ACP's own views keep the tag, since it tells the user where a target can be imaged from.
- `section` is `"current"` or `"vote"`. The `version` stays 1 because the field is additive: a page that ignores it still renders every project.
- For a `"vote"` project, `filters` lists only the filters in `vote_goals`. `target_hours` is the vote goal. `done_hours` is the hours now (counted as for a current project) minus `vote_baseline`, floored at 0. A missing baseline counts as 0. `last_imaged` and `last_imaged_nights_ago` are unchanged, so a vote card can still say when the target was last imaged. A vote project can have `is_current` set, but the page should group by `section`. `vote_goals` and `vote_baseline` are never in the document.
- Current projects are published as before, plus the new fields above.
- Sorted by `last_imaged` descending, nulls last, then by `project_name`.

## Matching a plan to logged data

Plans do not know which manifest targets are theirs. The publisher matches them by sky position:

- A manifest target belongs to a plan when the angular separation between their centres is at most half the larger of the two diagonal extents. The plan's extent is the full mosaic footprint, computed from the plan's telescope and camera via the existing `_fov_arcmin` and mosaic geometry.
- `done_hours` per filter is the sum of `filters.<f>.total_hours` across matched targets, less the `headline_hours` of any `rigs` row whose telescope or camera name differs from the plan's (a side that either the row or the plan does not name never excludes it). If the plan's own `filter_goals.<f>.actual_hours` is higher, use that instead, since it came from a fresher source.
- `last_imaged` is the latest `date_range[1]` across matched targets, or null if none match.
- Filter names are matched after the same canonicalisation the scanner uses, so `Ha`, `H-alpha` and `HA` agree.

## Publishing

`scripts/publish_shooting.py` does the whole run from the command line:

```
python scripts/publish_shooting.py --rescan   # rebuild manifest first, then publish
python scripts/publish_shooting.py            # publish from the current manifest
python scripts/publish_shooting.py --dry-run  # write to data/live/ and stop
```

What it does:

1. With `--rescan`, runs the manifest builder with the same arguments as the normal scan.
2. Builds the JSON document via the same code path as `/api/public/shooting`.
3. Writes `data/live/shooting.json` locally.
4. Uploads that one file to the destination over SFTP, using paramiko so no ssh binary is needed in Docker.

The page that reads the file lives in the site repo, `astro-roro/astrowithroro`, under `live/`. ACP owns the data and the contract above; the site owns the presentation. The site repo gitignores `live/shooting.json` so the pushed file never shows up as drift there.

Configuration, all environment variables, matching the rest of ACP:

| Variable | Meaning |
|---|---|
| `ACP_PUBLISH_DEST` | SFTP destination, for example `user@your-host:/var/www/site/live/shooting.json`. Publishing refuses to run when unset. |
| `ACP_PUBLISH_SSH_KEY` | Optional path to the key. Defaults to whatever SSH would use. |

The push is initiated from the ACP machine, never from the web host, which keeps to the rule that the web host only ever receives. The web root is owned by a different account from the SSH user, so a one-off setup step creates `live/` owned by the SSH user with mode 755. After that the upload writes the JSON directly with no sudo. That setup step is documented in the site repo's README, not automated. Scheduling is a cron or Task Scheduler entry on the machine that runs ACP, documented in `docs/sharing.md`, not built into the app. Suggested cadence is once each morning after the rig has finished.

There is also `POST /api/publish/shooting`, which runs steps 2 to 4 without a rescan and returns the upload result, so a "Publish now" button in the plan rail works without a terminal.

## The page itself

`live/index.html` and `live/shooting.js` in the site repo, plain HTML and vanilla JS, no build step, one small stylesheet inlined. The page fetches `shooting.json` relative to itself. It renders the cards described above, handles a missing or malformed JSON with a single "nothing public right now" line, and uses hips2fits with the DSS2 colour survey for thumbnails. The page is built as part of this work and deployed the same way as the rest of the site. Matching the site's existing look is done once, at build time, by copying the header and colours from `index.html`.

## Out of scope

- Live status from NINA.
- Votes, suggestions, or anything that writes back. That is part two.
- Any change to the sanitiser's friend-manifest behaviour.
- Lightbucket.

## Tests

`tests/test_shooting_publish.py`, unittest style like the rest of the suite:

- `visibility` accepts only `"private"` and `"public"`; a plan without it is treated as private; the blurb is capped at 500 characters.
- A private plan with `is_current` set is not in the output. A public plan is.
- Site tags are stripped from published names and left on the plan.
- `section` defaults to `"current"`. A vote project lists only its goal filters, with `done_hours` since the baseline, floored at 0, and an old plan publishes as before.
- The server sets the baseline on entering the vote, keeps it on later saves, ignores a client-sent value, and clears it on leaving. A bad `public_section` or a non-positive goal is a 400.
- Status boundaries: imaging_now at 3 nights and recent at 4, recent at 14 and out at 15, finished at 30 days and out at 31. A project older than 14 days with no finish drops out. A vote project stays.
- A pretty plan with no `visibility` publishes; explicit private wins; survey, deep and unclassed plans stay private unless explicitly public.
- Finished with and without an AstroBin link; an unsafe stored link is not published. An invalid `astrobin_url` or `finished_at` is a 400.
- Overall totals cap each filter at its target.
- Vote winner: a plan with an unmet current commitment publishes as `vote_winner` with `vote_month`, `vote_label` and the commitment's goals and baseline as its numbers; it shows whatever its last imaged date; it beats `public_section: "vote"`; a met commitment falls back to the normal rules with full-plan numbers; a later month's commitment and one with no countable filter are ignored; commitments never appear in the document.
- The other vote plans leave the vote section when "Won the vote for" is sent with `clear_other_votes` (`tests/test_balancer_wiring.py`).
- The output passes `validate_no_paths` and contains none of the forbidden keys listed above.
- A manifest target inside the plan's extent contributes its hours and date; one outside does not.
- `done_hours` takes the larger of manifest hours and `actual_hours`.
- `last_imaged` is null and `last_imaged_nights_ago` is null when nothing matches.
- Sort order is as specified.
- `publish_shooting.py --dry-run` writes the JSON and does not connect anywhere. Without `ACP_PUBLISH_DEST` and without `--dry-run` it exits non-zero with a clear message.
