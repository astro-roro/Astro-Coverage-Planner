# Spec: the Activity tab

This spec adds a fourth side-panel tab to Astro Coverage Planner (ACP) that shows what happened, night by night: plans changed, syncs, imaging, data landing, processing, and masters built. It also flags the few things that need a person to act. The user approved the design in the mockup at https://claude.ai/artifact/3hLxXU4WbX5i7VCEAe9nLG (version 2).

The work is built in three phases, and each ships on its own. Phase 1 is ACP plus the Target Scheduler sync extension (`acp-nina-ts-sync`), because the TS upload and rig-link routes live in the extension. Phase 2 adds events from the NINA plugin (`ACP.NINA.Plugin`). Phase 3 adds events from Astro Auto Calibration (AAC).

## Terms

- **Event**: one record of something that happened, stored by ACP and shown as one row in the timeline.
- **Night**: the observing night an event belongs to, written as the date the evening started, `YYYY-MM-DD`. The cut-over is local noon in `ACP_ACTIVITY_TZ`, so 10:00 on 14 Oct belongs to night `2026-10-13`.
- **Source**: who reported the event. ACP's own events are `acp`. Posted events must be `plugin`, `aac` or `external`.
- **Needs you**: an event that asks a person to act. It is open until it is resolved, dismissed or expires.
- **Badge**: the count on the Activity rail button. It counts open needs-you events and nothing else.
- **Dot**: a small marker on the rail button with no number. It shows when events have arrived or changed since the tab was last opened.

## Event model

ACP fills the fields marked "server".

| Field | Type | Notes |
|---|---|---|
| `id` | int | server. Row id, stable across updates. |
| `source` | string | `acp` for ACP's own events. POST accepts only `plugin`, `aac`, `external`. |
| `key` | string, max 200 | Idempotency key. Unique together with `source`. |
| `seq` | int | Optional. A sender's counter for this key. An update carrying a lower `seq` than the stored one is ignored. |
| `type` | string | One of the types in the chip table. Unknown types are stored as `other`. |
| `at` | ISO 8601 with offset | When it happened. Missing means the time ACP received it. |
| `night` | `YYYY-MM-DD` | The plugin and AAC always send it. ACP derives it from `at` only when it is absent. |
| `title` | string, max 200 | One plain-text line. Longer is rejected. |
| `detail` | string | Optional plain text, cut to 2000 characters. |
| `severity` | `info`, `warn`, `error` | Default `info`. Anything else is rejected. |
| `refs` | object | Optional. Allowed keys only: `plan_id`, `project`, `target`, `bucket`, `filter`, `ra_deg`, `dec_deg`, `aac_job`, `upload_id`, `profile`, `master`. Others are dropped. |
| `data` | object | Optional numbers for the UI. Over 8 KB serialised is rejected. |
| `needs_you` | bool | Default false. |
| `resolve_key` | string | Required when `needs_you` is true. |
| `resolves` | list of strings | Keys of needs-you events this event closes. |
| `expires_at` | ISO | server. Set for the types with an expiry rule below. |
| `received_at`, `updated_at` | ISO | server. |
| `resolved_at`, `resolved_by`, `dismissed_at` | ISO / event id | server. |

`refs.master` arrives as `{ "path", "frames", "hours", "filter" }`. ACP adds `mac_folder`, `windows_folder` and `file_name` from the path map in "Master paths".

### Updating an event by key

Posting an event whose `(source, key)` exists updates it in place: `title`, `detail`, `severity`, `data`, `refs`, `needs_you`, `resolve_key` and `resolves` all take the new values, `updated_at` moves, and `id`, `night` and `received_at` stay. An update with a lower `seq` than the stored one is ignored and counted as `stale`.

ACP compares content as canonical JSON (sorted keys, numbers as sent). Identical content changes nothing, and `updated_at` stays put. When a needs-you event's content changes, ACP clears `resolved_at`, `resolved_by` and `dismissed_at`, which reopens it. An identical replay of a closed event leaves it closed.

### Resolving

Every key named in any event's `resolves` is written to a `resolutions` table as `(resolve_key, resolved_by, at)`. A needs-you event is closed when a resolution for its `resolve_key` exists with `at` later than the event's last content change. This works in either arrival order: a resolve that arrives before its item still closes it, provided both carry their real `at`. So a posted event with `needs_you` true or a non-empty `resolves` must include `at`, and POST rejects it otherwise with an error naming `at`. ACP's own events always set `at`. Resolving a key with no matching item stores the resolution and changes nothing else.

A needs-you event is open while it has no qualifying resolution, no `dismissed_at`, and `expires_at` is empty or in the future. Dismiss sets `dismissed_at`; restore clears it.

### Types and chips

| Chip id | Label | Types |
|---|---|---|
| `plans` | Plans | `plan_created`, `plan_edited`, `plan_deleted`, `project_settings`, `gear_changed`, `counts_corrected`, `other` |
| `syncs` | Syncs | `sync_for_tonight`, `rig_synced`, `ts_upload_received`, `ts_upload_applied`, `ts_upload_discarded`, `ts_push` |
| `imaging` | Imaging | `imaging_target`, `goal_reached`, `progress_report`, `progress_unknown`, `session_start`, `session_end` |
| `site` | Site and focus | `safety`, `meridian_flip`, `autofocus_summary`, `park` |
| `data` | Data landed | `transfer_landed`, `ingest` |
| `processing` | Processing | `calibration`, `threshold_decision`, `master_queued`, `accepted_counts`, `darks_built`, `aac_alert`, `aac_recovered` |
| `masters` | Masters | `master_built`, `master_ready` |
| `coverage` | Coverage | `scan_finished`, `scan_error`, `coverage_changed` |

`site` is off by default and every other chip is on. An event with `severity: "error"` shows whatever the chips say.

### Needs-you items

| Item type | `resolve_key` | Closed by | Expires |
|---|---|---|---|
| `ts_upload_received` | `upload:<upload_id>` | `ts_upload_applied` or `ts_upload_discarded` for that upload | 7 days after posting |
| `master_queued` | `master:<target>:<bucket>:<filter>` | `master_built` for the same target, bucket and filter | no |
| `master_ready` | `edit:<master path>` | dismiss | 7 days after posting. A rebuild of the same master changes its content, which reopens it. |
| `scan_error` | `scan` | the next successful `scan_finished` | no |
| `aac_alert` | the AAC alert key | `aac_recovered` for the same key | no |
| `progress_unknown` | `progress:<plan_id>` | a later report for that plan with no unknown filters | no |
| `counts_corrected` | `counts:<night>` | dismiss | no |

## Storage

Events live in SQLite at `ACP_ACTIVITY_DB`, default `activity.sqlite` beside `plans.json`. The tables are `events` (unique index on `(source, key)`, index on `(night, at, id)`), `resolutions`, `tombstones` (`source`, `key` of deleted events) and `state` (`last_seen`, `prefs`).

The connection uses `check_same_thread=False` behind one module lock, `busy_timeout` 250 ms, and `journal_mode=DELETE`, because the data directory may sit on a network share where WAL is unsafe. `PRAGMA user_version` holds the schema version, and startup migrates forward from it.

`record()` runs after the caller's save has succeeded and outside `PLANS_LOCK`. It catches and logs every exception and returns, so recording can never fail a save. Add `activity.sqlite` to the data backup notes in `docs/install.md`.

History is kept forever by default, at well under 1 KB an event. `ACP_ACTIVITY_KEEP_NIGHTS`, unset by default, deletes nights older than that count. Deleted events leave their `(source, key)` in `tombstones`, and a replay of a tombstoned key is ignored and counted as `stale`.

## Night

`ACP_ACTIVITY_TZ` is an IANA zone name, default `Australia/Sydney`, not the container's time zone. When `night` is absent, ACP converts `at` into that zone. A local time before 12:00 belongs to the previous date, and noon or later to the same date. Daylight saving changes nothing in the rule, because the cut-over is local noon on each day.

## Master paths

A browser cannot open Finder or Explorer, so master rows show copyable text. AAC reports the path it sees in its container. `ACP_ACTIVITY_PATH_MAP` is a JSON list of `{ "from", "mac", "windows" }` entries, and the first entry whose `from` matches wins. A match must end on a path segment boundary, so `/mnt/a` matches `/mnt/a/x.fit` but not `/mnt/ab/x.fit`.

For `/mnt/remotes/NAS/Astro/Library/Example_Target/RIG_CAMERA/masters/full_master_Ha.fit` with the map `/mnt/remotes/NAS` to `/Volumes/NAS` and `\\NAS\share`, ACP produces:

- `mac_folder`: `/Volumes/NAS/Astro/Library/Example_Target/RIG_CAMERA/masters/`
- `windows_folder`: `\\NAS\share\Astro\Library\Example_Target\RIG_CAMERA\masters\`
- `file_name`: `full_master_Ha.fit`

A path matching no entry is shown as `(unmapped) <basename>` in both folder fields, with `file_name` set to the basename. The Windows share name is an assumption to confirm in Explorer on IMAGING-PC before phase 3 ships.

## Redaction

`record()` and the ingest route both scrub text fields and string values in `data` and `refs` before storing them. They replace bearer tokens, `token=`, `password=` and `user:pass@` in URLs with `[redacted]`. They strip Windows home prefixes (`C:\Users\<name>\`) and `/Users/<name>/` to `~\` and `~/`. Paths outside `refs.master.path` are not mapped. The plugin sends path fields as basenames only.

## API

All routes sit behind ACP's existing access gate: a bearer `ACP_API_TOKEN` or the session cookie. GET routes follow the same gate as every other `/api` GET. One shared token for the plugin, AAC and the browser is an accepted risk on a home LAN. Browser POSTs (`seen`, `dismiss`, `restore`, `prefs`) also check that `Origin`, when present, matches the request's `Host` or `X-Forwarded-Host`, since production sits behind a reverse proxy. ACP had no such guard before this feature; this check is new.

- `POST /api/activity/events` with `{ "events": [ ... ] }`. The route's body limit is 2 MiB and the batch limit is 100 events; either over is 413 and stores nothing. It answers `{ "stored", "updated", "unchanged", "stale", "rejected": [ { "key", "error" } ] }`. A bad event is rejected alone and the rest are stored. Clients never retry rejected events. The route answers 403 while `ACP_API_TOKEN` is unset, and rejects `source: "acp"`.
- `GET /api/activity?nights=5&before=<night>` answers `{ "nights": [ { "night", "events": [...], "more": n } ], "next_before", "total_nights", "last_seen" }`. Nights are newest first and skip nights with no events. `nights` defaults to 5, is capped at 30, and a non-integer is 400. Within a night, events are newest first, by `(at, id)` descending, and capped at 200, with `more` counting the rest.
- `GET /api/activity/night/<night>?offset=200` returns the next 200 events of one night.
- `GET /api/activity/summary` answers `{ "needs_you", "has_new", "max_updated_at", "last_night", "open": [...], "open_total" }`. `open` is capped at 50, oldest first. `last_night` is the most recent night with `imaging_target` or `progress_report` events, with hours per filter against goal per target.
- `POST /api/activity/seen` sets `last_seen` to the server's time. It ignores any time the client sends and never moves backwards.
- `POST /api/activity/events/<id>/dismiss` and `/restore`.
- `GET /api/activity/prefs` and `PUT /api/activity/prefs` with `{ "chips": { "<chip id>": bool } }`. Unknown chip ids are dropped and missing ones take their defaults.

`has_new` and row highlighting both use `max(received_at, updated_at)` against `last_seen`. `last_seen` is shared across browsers on purpose, so opening the tab on the Mac clears the dot on IMAGING-PC too.

## Phase 1: ACP and the sync extension

ACP exposes `app.record_activity(**fields)`, which calls `record()` with `source: "acp"`. The sync extension calls `getattr(app, "record_activity", None)` and skips when it is absent, so a newer extension still works on an older ACP.

- **Plan created, deleted**: one event each, key `plan_created:<plan_id>:<night>` or `plan_deleted:<plan_id>:<night>`, so a plan deleted and recreated on a later night records again.
- **Plan edited**: type `plan_edited`, key `plan_edited:<plan_id>:<night>`. ACP diffs the stored plan before and after the PUT, ignoring `updated_at`, `guid`, `created_at`, `last_synced_at` and anything only a load-time heal or migration changed. The night's cumulative change set lives in `data.changes` (field to `{ from, to }`, where `from` stays the first value of the night) and merges on each save. The title is built from that set, such as "min altitude 20 to 30, priority Normal to High". A save that changes nothing records nothing. In the UI, consecutive rows with the same change across one project collapse into one line, such as "46 PNe Survey plans: min altitude 20 to 30".
- **Project settings and gear**: `project_settings` and `gear_changed`, coalesced per night the same way.
- **Send TS to ACP** (extension, `nina_ts_sync/upload_routes.py`): `ts_upload_received` when an upload arrives, then `ts_upload_applied` with the number of plans changed, or `ts_upload_discarded`.
- **Rig synced** (extension, the `/links` route): `rig_synced`, key `rig_synced:<profile>:<night>`, one row per rig per night.
- **Progress reports**: `progress_report`, key `progress_report:<plan_id>:<night>`, updated with hours per filter. Unknown filters in a report post a separate `progress_unknown`, key `progress_unknown:<plan_id>:<night>`, resolve key `progress:<plan_id>`. A later report with none unknown resolves it.
- **Goal reached**: when a progress report or `POST /api/plans/<id>/accepted` takes a row to its goal, ACP records `goal_reached`, key `goal:<plan_id>:<row_id>:<target_hours as %g>`, so two rows of the same filter each record. Raising the goal later makes a new key, so reaching the new goal records again.
- **AAC accepted counts**: `accepted_counts`, key `accepted_counts:<plan_id>:<night>`.
- **Manifest scan**: on success, `scan_finished` (key `scan_finished:<night>`) with targets added and removed, resolving `scan`. Each target and filter whose hours moved by more than 0.01 h updates `coverage_changed`, key `coverage:<target id>:<filter>:<night>` (the manifest target id; `refs.target` carries the name), whose `data.delta_h` accumulates over the night, so two scans of +0.25 h read as one row of "+0.50 h". On failure, `scan_error` (key `scan_error:<night>`, needs you, resolve key `scan`) carries the error kind ACP already classifies.

The tab, its API, storage and UI all ship in phase 1. Before phase 2, "Last night" builds its hours from `progress_report` events.

## Phase 2: the NINA plugin

The plugin posts with `source: "plugin"`, an explicit `night`, and a `seq` that increases per key. It queues events in memory and sends batches every 60 s and when the Target Scheduler container ends, retrying a failed batch on the next tick. After the first 404 or 403 it stops posting for the rest of the NINA session, with one log line.

- `sync_for_tonight`: the outcome lines the plugin already logs, including plan count and the backup file's basename.
- `imaging_target`: key `img:<profile>:<ts target id>:<night>`, updated as frames arrive. `data.frames` is a list of `{ filter, exposure_s, count, first_at, last_at }`.
- `session_start`, `session_end` with the reason the session ended.
- `meridian_flip` with whether recentring succeeded.
- `autofocus_summary`: one per night, updated per run, with run count, first and last focuser position, and temperature range.
- `park`, and `safety` changes to safe or unsafe.

## Phase 3: AAC

AAC gets a client, `auto_pix/acp_activity.py`, that posts with `source: "aac"`, explicit `night`, and `seq`. A failed post is logged and dropped after three tries. After the first 404 or 403 the client disables itself for the process with one log line.

`target` is the AAC Library target folder name (`Example_Target`) and `bucket` is the Library bucket folder name (`RIG_CAMERA`), both sent as-is. ACP maps them to a plan with the matching it already uses for AAC's accepted reports.

- `transfer_landed`: night id, file count, bytes, start and finish from `ingest_result.json`.
- `ingest`: lights, flats and darks promoted.
- `calibration`: per job, frames calibrated, accepted and rejected.
- `threshold_decision`: only rows that crossed their goal.
- `master_queued` and `master_built`. A build posts `master_built` with `refs.master` and `resolves: ["master:<target>:<bucket>:<filter>"]`, plus `master_ready` (needs you, resolve key `edit:<path>`).
- `darks_built`: master darks made from a darks night.
- `aac_alert`: every `notify_failure` call also posts an `aac_alert` (severity error, needs you) whose resolve key is its alert key.
- `aac_recovered`: posted on the first success after a failure for a key, resolving it. `FailureStreakTracker.record` gains a return value that says "recovered" when a success ends a streak.

Alert keys take the form `aac:<component>:<check or job class>`:

| Call site | Alert key |
|---|---|
| `nightwatch.run_cycle`, pre-flight check failed | `aac:nightwatch:<check name>` (`siril_container`, `job_queue`, disk) |
| `nightwatch`, master dark refresh failed or darks failed to build | `aac:nightwatch:master_darks` |
| `nightwatch`, interrupted jobs requeued at start | `aac:nightwatch:requeue` (informational, severity warn, not needs you) |
| `watcher.run_scanner_iteration` | `aac:watcher:scanner` |
| `night_marker`, night ingest failed | `aac:ingest:<night_id>` |
| `orchestrator`, calibration failed | `aac:calibration:<job_id>` |
| `orchestrator`, library filing failed | `aac:filing:<job_id>` |
| `orchestrator`, no matching flats | `aac:flats:<job_id>` |
| `orchestrator`, tile export held | `aac:tiles:<target>` |
| `master_thresholds`, master build failed | `aac:master:<target>:<bucket>:<filter>` |
| `master_thresholds`, master gates failed or WCS not confirmed | `aac:master_gates:<target>:<bucket>:<filter>` |
| `master_thresholds`, tile export failed | `aac:tiles:<target>` |
| `survey_worker`, permanent failure | `aac:survey:<job_id>` |
| `pixinsight_runner`, job failed | `aac:pixinsight:<job_id>` |

## The tab

The layout follows the mockup. The rendering logic lives in `static/activity.mjs` as pure functions so it can be tested without a browser.

- **Rail button**: "Activity", fourth after Plans, Filters and Library. `railState(summary)` gives the badge when `needs_you` is above 0, otherwise the dot when `has_new` is true, otherwise nothing.
- **Panel width**: 560 px where the screen allows. The other tabs stay 380 px. Below 760 px the panel takes the full width, like the others.
- **Last night**: one line per target with hours per filter against goal, a finished mark from `goal_reached`, and a link to the plan.
- **Needs you**: open items, oldest first, each with its link and a × to dismiss. Beyond 50, a line says how many more.
- **Timeline**: nights newest first, five on first load. Scrolling near the end loads five more, and a "Load older nights" button does the same. `pageState` gives the "Showing 5 of 7 nights" text and whether the button shows. A night with more than 200 events shows "N more" that fetches the rest of that night.
- **Rows**: icon by chip, time, title, and links to the plan, the target on the map, the AAC job or the upload review page. Rows whose `max(received_at, updated_at)` is after the previous `last_seen` are highlighted.
- **Live updates**: the client polls the summary every 60 s. When `max_updated_at` moves, it refetches the loaded nights and merges events by `id`, so late events and updates appear in nights already on screen.
- **Master rows**: `masterRowStrings` gives the Mac folder, Windows folder and file name, each with a Copy button. If the clipboard call fails, the text is selected instead.
- **Chips**: `visibleRows` filters the timeline at once. Changes save to `PUT /api/activity/prefs` after a 500 ms pause.
- **Seen**: on opening the tab, the UI keeps the `last_seen` it was given for highlighting, then posts `/seen`.
- **Text safety**: `renderRowHtml` escapes every event string.

## Acceptance tests

Phase 1, ACP (pytest, in `tests/test_activity.py` unless named):

1. Posting two events with the same `(source, key)` and identical content stores one event, and the second call reports `unchanged: 1`.
2. Posting the same `(source, key)` with a new `title` keeps `id`, `night` and `received_at`, changes the title, and bumps `updated_at`.
3. With `ACP_ACTIVITY_TZ=Australia/Sydney` and no `night`: `2026-10-14T10:00+11:00` is stored under `2026-10-13`, and `2026-10-14T12:00+11:00` under `2026-10-14`.
4. With the same zone, `2026-10-14T00:30:00Z` (11:30 local) is stored under `2026-10-13`.
5. On a daylight saving change day (`2026-10-04`, Sydney), `2026-10-04T11:59+11:00` is stored under `2026-10-03` and `2026-10-04T12:00+11:00` under `2026-10-04`.
6. An event posted with `night: "2026-10-13"` keeps that night whatever its `at` is.
7. One batch covers decision 5's validation: missing `key` rejected, `source: "acp"` rejected, `source: "bogus"` rejected, a 201-character title rejected, `data` over 8 KB rejected, `severity: "fatal"` rejected, unknown `type` stored as `other`, a 3000-character `detail` stored cut to 2000, and missing `at` stored with the receipt time. Valid events in the same batch are stored.
8. A batch of 101 events is refused with 413 and stores nothing, and so is a body over 2 MiB.
9. `POST /api/activity/events` answers 403 while `ACP_API_TOKEN` is unset, and 401 without the token when it is set.
10. `needs_you: true` without `resolve_key` is rejected.
11. Events with `(plugin, k1)` and `(aac, k1)` are stored as two events.
12. An update with `seq: 3` after one with `seq: 5` for the same `(source, key)` is ignored and reported as `stale`.
13. A `master_built` posted with `resolves: ["master:Dolphin:110GTX:OIII"]` closes the open `master_queued` with that key and sets `resolved_by`. The summary's `needs_you` drops by one.
14. Out of order: a `master_built` resolving a key, then the `master_queued` with that key, leaves the item closed.
15. Reopen: a closed `master_ready` re-posted with different content (a new frame count) is open again. Re-posting it with identical content leaves it closed.
16. Dismiss sets `dismissed_at` and drops `needs_you` by one, and restore puts it back.
17. A `ts_upload_received` or `master_ready` posted 8 days ago is not open and not counted, and one posted 6 days ago is.
18. With events on nights 10, 11, 12 and 13 Oct and none on 9 Oct, `GET /api/activity?nights=2` returns 13 and 12 Oct with `next_before: "2026-10-12"`. Following with `before=2026-10-12` returns 11 and 10 Oct and `next_before: null`. `total_nights` is 4.
19. `nights=31` is capped to 30, and `nights=abc` is 400.
20. A night with 250 events returns 200 ordered by `(at, id)` with `more: 50`, and `GET /api/activity/night/<night>?offset=200` returns the other 50.
21. `has_new` is true after an event is stored, false after `POST /seen`, and true again after that event is updated with new content.
22. `POST /seen` with a client `at` in the past stores the server's time, and a second call never moves it backwards.
23. `GET /api/activity/prefs` with nothing stored returns exactly `{"chips": {"plans": true, "syncs": true, "imaging": true, "site": false, "data": true, "processing": true, "masters": true, "coverage": true}}`. A `PUT` of `{"chips": {"imaging": false, "bogus": true}}` followed by a `GET` returns the same body with `imaging: false` and no `bogus`.
24. A browser POST to `/api/activity/seen` with a foreign `Origin` is refused, and with a matching one succeeds.
25. Editing a plan's min altitude from 20 to 30 records one `plan_edited` event titled with "min altitude 20 to 30". A second edit that night changing priority leaves one event whose `data.changes` holds both, with min altitude still from 20.
26. A plan PUT that changes only `updated_at`, `guid`, `created_at` or `last_synced_at`, or where only a load-time heal differs, records nothing.
27. A progress report naming a filter the plan lacks posts `progress_unknown` keyed `progress_unknown:<plan_id>:<night>` with resolve key `progress:<plan_id>`, and `summary.open` contains it. A later clean report resolves it.
28. A progress report taking a row to its goal records one `goal_reached` keyed `goal:<plan_id>:<row_id>:<target_hours as %g>`, and a repeat records nothing. The same through `POST /api/plans/<id>/accepted`, in the fixture style of `tests/test_aac_accepted.py`.
29. Two successful scans each adding 0.25 h of Ha to a target, with `_run_scan_subprocess` and `run_scan_now` monkeypatched as in `tests/test_scan_schedule.py`, leave one `coverage_changed` keyed `coverage:<target id>:Ha:<night>` reading "+0.50 h".
30. A failed scan records `scan_error` keyed `scan_error:<night>`, distinct from `scan_finished`, and the next successful scan resolves it.
31. If the activity database cannot be opened, plan saves still return 200 and the failure is logged.
32. Path map: the Dolphin `full_master_Ha.fit` path gives the three exact strings in "Master paths". An unmapped path gives `(unmapped) full_master_Ha.fit` in both folders. With two matching entries, the first wins. `/mnt/remotes/NAS2/x.fit` does not match the map.
33. Redaction: `Authorization: Bearer abc123`, `token=abc`, `password=hunter2` and `https://u:p@host/` in a title or `data` string are stored with `[redacted]`. `C:\Users\Astro\Pictures\x.fits` is stored as `~\Pictures\x.fits`. A `refs` key outside the allowlist is dropped.
34. A deleted night's keys go to `tombstones`, and replaying one of those events is reported as `stale` and not stored.
35. With `ACP_ACTIVITY=off`: every activity route answers 404, a plan PUT stores no event, and the page shell has no Activity rail button, in the style of `tests/test_ts_sync_banner.py`.

Phase 1, sync extension (`acp-nina-ts-sync/tests/test_activity_hooks.py`, with a `FakeHost` whose `record_activity` appends to a list):

36. An upload records `ts_upload_received` with resolve key `upload:<id>`. Applying records `ts_upload_applied` resolving it, and discarding instead records `ts_upload_discarded` resolving it.
37. A `/links` push records `rig_synced` keyed `rig_synced:<profile>:<night>`, and three pushes in one night record the same key.
38. On a host with no `record_activity`, the upload and `/links` routes still answer 200.

Phase 1, frontend (`tests/frontend/activity.test.mjs` against `static/activity.mjs`, `node --test`):

39. `renderRowHtml` with a title of `<img src=x onerror=alert(1)>` returns escaped text and no `<img` element.
40. `railState({needs_you: 2, has_new: true})` is `{badge: 2}`, `railState({needs_you: 0, has_new: true})` is `{dot: true}`, and `railState({needs_you: 0, has_new: false})` is `null`.
41. `visibleRows` with `imaging: false` hides imaging rows and keeps an imaging row whose severity is `error`.
42. `pageState({shown: 5, total: 7})` gives "Showing 5 of 7 nights" with `showLoadOlder: true`, and `pageState({shown: 7, total: 7})` gives `showLoadOlder: false`.
43. `masterRowStrings` returns `[mac_folder, windows_folder, file_name]` in that order.

The clipboard buttons are checked by hand, through an entry in `notes/qa/queue.md`.

Phase 2, plugin (xunit on blakphoenix):

44. Two frames on the same target, filter and exposure produce one `imaging_target` with count 2 under one key, the second post carrying a higher `seq`.
45. A failed batch is retried with the same keys, and a 404 or 403 stops posting for the session after one log line.
46. Sync for tonight posts one `sync_for_tonight` with the plan count and the backup file's basename, not its full path.

Phase 3, AAC (pytest):

47. An ingest result for night `2026-10-13` posts `transfer_landed` with that night, the file count and the byte total.
48. A master build posts `master_built` with `refs.master.path` and `resolves: ["master:<target>:<bucket>:<filter>"]`, and a `master_ready` with `needs_you: true` and resolve key `edit:<path>`.
49. A calibration failure posts `aac_alert` keyed `aac:calibration:<job_id>`. For a streak key, `FailureStreakTracker.record` reports "recovered" on the first success after failure, and the client posts `aac_recovered` resolving it.
50. With ACP unreachable, processing finishes normally and the client logs one warning per event after three tries. After a 404 it posts nothing more for the process.

## Rollback

The feature only adds things. `ACP_ACTIVITY=off` makes every activity route answer 404, hides the rail button and turns `record()` into a no-op. The plugin and AAC clients disable themselves after the first 404 or 403. Deleting `activity.sqlite` removes the history and nothing else. Phases 2 and 3 roll back by reverting their own repos.

## Open questions, with defaults

- **Master previews.** Default: none in any phase of this spec. AAC writing a small stretched JPEG for ACP to serve would be a separate spec.
- **Where failures appear.** Default: inline in the timeline as error rows that ignore the chips, and in the Needs you box until resolved, dismissed or expired. No separate failures panel.
- **Windows share name.** Default: `\\NAS\share\`, to confirm in Explorer on IMAGING-PC before phase 3.

## Out of scope

- Notifications outside ACP. The user decided against a morning digest.
- More than one user, or per-user state.
- Searching or editing events.
- Opening Finder or Explorer from the browser.
- Master previews.
