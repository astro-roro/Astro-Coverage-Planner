# Spec: the Activity tab

This spec adds a fourth side-panel tab to Astro Coverage Planner (ACP) that shows what happened, night by night: plans changed, syncs, imaging, data landing, processing, and masters built. It also flags the few things that need a person to act. Rohan approved the design in the mockup at https://claude.ai/artifact/3hLxXU4WbX5i7VCEAe9nLG (version 2).

The work spans three repos and is built in three phases. Phase 1 is ACP alone and records what ACP already sees. Phase 2 adds events from the NINA plugin (`ACP.NINA.Plugin`). Phase 3 adds events from Astro Auto Calibration (AAC). Each phase ships on its own and is useful without the next.

## Terms

- **Event**: one record of something that happened, stored by ACP and shown as one row in the timeline.
- **Night**: the observing night an event belongs to, written as the date the evening started, `YYYY-MM-DD`. An event at 10:00 on 14 Oct belongs to night `2026-10-13`. The cut-over is local noon.
- **Source**: who reported the event: `acp`, `plugin` or `aac`. Anything else that posts is stored as `external`.
- **Needs you**: an event that asks a person to act. It stays open until something resolves it or the person dismisses it.
- **Badge**: the count on the Activity rail button. It counts open needs-you events and nothing else.
- **Dot**: a small marker on the rail button with no number. It shows when events have arrived since the tab was last opened.

## Event model

Every event has these fields. ACP fills the ones marked "server".

| Field | Type | Notes |
|---|---|---|
| `id` | int | server. Row id, stable. |
| `key` | string, max 200 | Idempotency key, unique. Required on POST. ACP's own events build it from what they describe, for example `plan_edit:<plan_id>:<night>`. |
| `type` | string | One of the types in the table below. Unknown types are stored as `other`. |
| `source` | string | `acp`, `plugin`, `aac` or `external`. |
| `at` | ISO 8601 with offset | When it happened. Defaults to the time ACP received it. |
| `night` | `YYYY-MM-DD` | Optional on POST. When absent, ACP derives it from `at` with the noon cut-over in `ACP_ACTIVITY_TZ` (default: the container's local time zone). AAC sends it explicitly, because it processes a night's data the next afternoon. |
| `title` | string, max 200 | One line, plain text, shown as the row's main text. |
| `detail` | string, max 2000 | Optional, plain text. Shown when the row is expanded. |
| `severity` | `info`, `warn`, `error` | Default `info`. |
| `refs` | object | Optional links: `plan_id`, `project`, `target` (name), `ra_deg`/`dec_deg`, `aac_job`, `upload_id`, `master` (see below). |
| `data` | object, max 8 KB serialised | Optional numbers the UI uses: hours per filter, frame counts, coverage deltas. |
| `needs_you` | bool | Default false. |
| `resolve_key` | string | Required when `needs_you` is true. Another event naming this key in `resolves` closes it. |
| `resolves` | list of strings | Keys of needs-you events this event closes. |
| `received_at`, `updated_at` | ISO | server. |
| `resolved_at`, `resolved_by`, `dismissed_at` | ISO / event id | server. |

`refs.master` is `{ "path": "<path as the sender sees it>", "frames": int, "hours": float, "filter": "Ha" }`. ACP turns `path` into the three strings the UI shows (Mac folder, Windows folder, file name) using the path map in "Master paths" below.

Posting an event whose `key` already exists replaces that event's `title`, `detail`, `severity`, `data` and `refs`, and bumps `updated_at`, but keeps its `id`, `night` and `received_at`. If the content is identical, nothing changes and `updated_at` stays put. This makes every report safe to retry, and it lets a running total (hours shot on a target tonight) update one row instead of adding a new one each time.

### Types and the chips they fall under

| Chip | Types |
|---|---|
| Plans | `plan_created`, `plan_edited`, `plan_deleted`, `project_settings`, `gear_changed`, `counts_corrected` |
| Syncs | `sync_for_tonight`, `rig_synced`, `ts_upload_received`, `ts_upload_applied`, `ts_upload_discarded`, `ts_push` |
| Imaging | `imaging_target`, `goal_reached`, `progress_report`, `session_start`, `session_end` |
| Site and focus | `safety`, `meridian_flip`, `autofocus_summary`, `park` |
| Data landed | `transfer_landed`, `ingest` |
| Processing | `calibration`, `threshold_decision`, `master_queued`, `accepted_counts`, `darks_built` |
| Masters | `master_built` |
| Coverage | `scan_finished`, `coverage_changed` |
| (every chip) | `error` events of any type stay visible whatever the chips say |

"Site and focus" is off by default. Every other chip is on.

### Needs-you rules

A needs-you event is open while `resolved_at` and `dismissed_at` are both empty. It closes in one of two ways:

- **Resolved**: a later event lists its `resolve_key` in `resolves`. ACP sets `resolved_at` and `resolved_by`. Resolving a key that is not open does nothing.
- **Dismissed**: the person clicks × on it. ACP sets `dismissed_at`. A dismissed event can be restored from the row's menu.

The resolution pairs ACP knows about in this spec:

| Opens | `resolve_key` | Closed by |
|---|---|---|
| `ts_upload_received` | `upload:<upload_id>` | `ts_upload_applied` or `ts_upload_discarded` for that upload |
| `master_queued` | `master:<target>:<bucket>:<filter>` | `master_built` for the same target, bucket and filter |
| `master_built` (ready to edit) | `edit:<master path>` | dismiss only |
| any `error` from a scan | `scan` | the next `scan_finished` that succeeds |
| any `error` from AAC | the alert key AAC already uses for ntfy | AAC's next success for the same key |
| `progress_report` with unknown filters | `progress:<plan_id>` | dismiss, or a later report for the plan with none unknown |
| `counts_corrected` | `counts:<night>` | dismiss only |

## Storage

Events live in a SQLite file, `activity.sqlite`, next to `plans.json` (path from `ACP_ACTIVITY_DB`, default `<dir of PLANS_PATH>/activity.sqlite`). SQLite gives paging by night, a unique index on `key`, and safe appends without rewriting a growing JSON file. It runs in WAL mode, and one module-level lock serialises writes.

Tables: `events` (the fields above, `refs` and `data` as JSON text, unique index on `key`, index on `(night, at)`), and `state` (one row per key: `last_seen`, `prefs` as JSON). The directive to keep state in a separate `activity_state.json` is folded into the `state` table, so the tab has one file to back up and one lock. If a reviewer prefers the JSON file, nothing else in this spec changes.

History is kept forever. A busy night produces about 50 to 150 events at under 1 KB each, so ten years is tens of megabytes. There is no rotation. `ACP_ACTIVITY_MAX_EVENTS`, unset by default, deletes the oldest nights beyond that count if it is ever needed.

Lock order: a route that already holds `PLANS_LOCK` may record an event. Code holding the activity lock never takes `PLANS_LOCK`. Recording an event must never fail the caller: `record()` catches and logs every exception and returns.

## Master paths

AAC reports a master at the path it sees inside its container, for example `/mnt/remotes/SINGULARITY.ERKO_Singularity/Astro With RoRo/Library/Sh2_308_Dolphin/110GTX_ASI6200MM/masters/full_master_Ha.fit`. A browser cannot open Finder or Explorer, so the UI shows copyable text instead:

- **Mac folder**, for Finder's Go to Folder: `/Volumes/Singularity/Astro With RoRo/Library/Sh2_308_Dolphin/110GTX_ASI6200MM/masters/`
- **Windows folder**, for the Explorer address bar: `\\Singularity\Singularity\Astro With RoRo\Library\Sh2_308_Dolphin\110GTX_ASI6200MM\masters\`
- **File name**, to search for: `full_master_Ha.fit`

`ACP_ACTIVITY_PATH_MAP` is a JSON list of `{ "from": "<prefix>", "mac": "<prefix>", "windows": "<prefix>" }`. The first entry whose `from` prefixes the path wins. With no match, both folders show the path as sent. Rohan's value maps `/mnt/remotes/SINGULARITY.ERKO_Singularity/` to `/Volumes/Singularity/` and `\\Singularity\Singularity\`. The Windows share name is an assumption to confirm on Voyager before phase 3 ships.

## API

All routes sit behind ACP's existing access gate (bearer `ACP_API_TOKEN` or the session cookie). Bodies are JSON, and errors are JSON with an `error` string.

- `POST /api/activity/events` with `{ "events": [ ... ] }`, at most 200 per call. Answers `{ "stored": n, "updated": n, "unchanged": n, "rejected": [ { "key", "error" } ] }`. A bad event is rejected alone; the rest are stored. Refused with 403 while `ACP_API_TOKEN` is unset, the same rule the TS upload route uses, because an open server would take writes from anyone on the network. ACP's own events do not go through this route.
- `GET /api/activity?nights=5&before=<night>` answers `{ "nights": [ { "night", "events": [ ... ] } ], "next_before": "<night>" | null, "total_nights": n, "last_seen": "<iso>" }`. Nights are newest first. `nights` defaults to 5 and is capped at 30. `before` is exclusive. Nights with no events are skipped, so five nights of results can span more than five calendar days.
- `GET /api/activity/summary` answers `{ "needs_you": n, "has_new": bool, "last_night": { "night", "targets": [ { "name", "plan_id", "hours_by_filter", "goal_by_filter", "finished": bool } ] }, "open": [ needs-you events ] }`. The UI polls it every 60 s while the page is open.
- `POST /api/activity/seen` with optional `{ "at": "<iso>" }` sets `last_seen` to that time or now. It never moves `last_seen` backwards.
- `POST /api/activity/events/<id>/dismiss` and `POST /api/activity/events/<id>/restore`.
- `GET /api/activity/prefs` and `PUT /api/activity/prefs` with `{ "chips": { "<chip>": bool } }`. Unknown chips are dropped. Missing chips take their defaults.

Prefs, `last_seen` and dismissals are stored in ACP, not in the browser, so the Mac and Voyager show the same state. ACP has one user, so there is one set of state.

## Phase 1: what ACP records itself

ACP calls `activity.record(...)` from these places. Edits are coalesced so a burst of saves makes one row.

- **Plan created, deleted**: one event each, `title` like "Added plan OIII-T 7.50 -13.75 (PNe Survey)".
- **Plan edited**: a field-level summary of what changed, built by comparing the plan before and after the save. It names filter rows added, removed or reordered, goal hours or frames changed, `actual_hours` changed, and changes to min altitude, priority, state, target class, meridian window and minimum time. `updated_at` is ignored. Key `plan_edit:<plan_id>:<night>`, so later edits that night replace the row's text with the combined summary of every change since the night began. In the UI, consecutive rows of the same change across one project collapse into one line, such as "46 PNe Survey plans: min altitude 20 to 30".
- **Project settings and gear**: one event per save, coalesced per night like plan edits.
- **Send TS to ACP**: `ts_upload_received` when an upload arrives (needs you), then `ts_upload_applied` with the number of plans changed, or `ts_upload_discarded`. These hooks sit in the sync extension's upload routes, which call ACP's `record()` through the app object, the same way the extension already adopts `PLANS_LOCK`.
- **Rig synced**: when the plugin posts `/api/ext/nina-ts-sync/links`, ACP records `rig_synced` with the profile and plan count. Key `rig_synced:<profile>:<night>`, so it is one row per rig per night. Phase 2 adds the plugin's own, richer `sync_for_tonight` event.
- **Progress reports**: each `POST /api/plans/<id>/progress` updates one `progress_report` row per plan per night, carrying hours per filter. Unknown filters in a report open a needs-you item.
- **Goal reached**: when a progress report or an AAC accepted report takes every row of a plan to its goal, ACP records `goal_reached` once. Key `goal:<plan_id>`.
- **AAC accepted counts**: each `POST /api/plans/<id>/accepted` updates one `accepted_counts` row per night.
- **Manifest scan**: when a scan finishes, ACP compares the manifest before and after. It records `scan_finished` with targets added and removed, plus `coverage_changed` for each target whose hours per filter went up or down by more than 0.01 h. A failed scan records an `error` with the error kind ACP already classifies.

The tab, its API, storage and the UI below all ship in phase 1. Before phase 2, the "Last night" box builds its hours from `progress_report` events.

## Phase 2: the NINA plugin

The plugin posts to `/api/activity/events` with `source: "plugin"`. It queues events in memory, sends them in batches every 60 s and at the end of the Target Scheduler container, and retries a failed batch on the next tick. Keys make the retries harmless. A 404 from an older ACP turns the feature off for that NINA session, with one log line.

- `sync_for_tonight`: the outcome lines the plugin already logs: gear used, plans loaded, projects set inactive, inserted and updated counts, and the backup path.
- `imaging_target`: one per target per night, key `img:<profile>:<ts target id>:<night>`, replaced as frames arrive. It carries `data.frames` as `[ { filter, exposure_s, count, first_at, last_at } ]` and hours per filter.
- `session_start` and `session_end`: when the sequence starts and finishes, with the reason it ended.
- `meridian_flip`: time, and whether recentring succeeded.
- `autofocus_summary`: one per night, replaced as runs complete: run count, first and last focuser position, temperature range.
- `park`, and `safety` changes to safe or unsafe.
- `error`: sequence failures the plugin catches, such as a failed plate solve that stops a target. Severity `error`, not needs-you, unless it stopped the night.

## Phase 3: AAC

AAC gets a small client (`auto_pix/acp_activity.py`) that posts with `source: "aac"` and explicit `night` values. It reads the ACP URL and token from config. A failed post is logged and dropped after three tries, because the timeline is a convenience and must never block processing.

- `transfer_landed`: night id, file count, bytes, and start and finish times from `ingest_result.json`.
- `ingest`: lights, flats and darks promoted.
- `calibration`: per job, frames calibrated, accepted and rejected, with rejection reasons summarised.
- `threshold_decision`: only rows that crossed their goal.
- `master_queued` (needs you, until built) and `master_built` with `refs.master`, the gate result, frames and hours. A successful build also opens an "ready to edit" needs-you item, and it closes the matching `master_queued`.
- `darks_built`: master darks made from a darks night.
- `error`: every alert AAC sends to ntfy is also posted here, with the same key, as a needs-you event. The next success for that key resolves it.

## The tab

The layout follows the mockup.

- **Rail button**: "Activity" is the fourth button, after Plans, Filters and Library. It shows the badge when open needs-you events exist, otherwise the dot when `has_new` is true, otherwise nothing.
- **Panel width**: 560 px on screens wide enough for it. The other tabs stay at 380 px. Below 760 px wide, the panel takes the full width like the others.
- **Last night**: the most recent night with events. One line per target with hours per filter against its goal, a finished mark for `goal_reached`, and a link to the plan.
- **Needs you**: every open needs-you event, oldest first, each with its link and a × to dismiss.
- **Timeline**: nights newest first, five on first load. Scrolling near the end loads the next five, and a "Load older nights" button does the same for anyone whose scrolling does not trigger it. A line reads "Showing 5 of 21 nights". Each night header shows its date and a one-line total.
- **Rows**: icon by chip, time, title, and links to the plan, the target on the map, the AAC job or the upload review page. Error rows are red and always shown. Rows that arrived after the previous `last_seen` are highlighted.
- **Master rows**: the Mac folder, the Windows folder and the file name, each with a Copy button. If the clipboard call fails, the text is selected instead.
- **Chips**: toggling a chip filters the timeline at once and saves to `PUT /api/activity/prefs` after a 500 ms pause. On reload, the chips come back as saved.
- **Seen**: when the tab opens, the UI keeps the `last_seen` it was given for highlighting, then posts `/seen`, which clears the dot.
- **Text safety**: every string from an event is inserted as text, never as HTML.

## Acceptance tests

Phase 1, ACP (pytest unless marked UI):

1. Posting two events with the same `key` and the same content stores one event, and the second call reports `unchanged: 1`.
2. Posting the same `key` with a new `title` keeps the `id`, `night` and `received_at`, changes the title, and bumps `updated_at`.
3. An event at `2026-10-14T10:00+11:00` with no `night` is stored under `2026-10-13`. One at `2026-10-14T12:00+11:00` is stored under `2026-10-14`.
4. An event posted with `night: "2026-10-13"` keeps that night whatever its `at` is.
5. A batch of three events where one has no `key` stores two and rejects one by key, with an error naming the missing field.
6. A batch of 201 events is refused with 400 and stores nothing.
7. `POST /api/activity/events` answers 403 while `ACP_API_TOKEN` is unset, and 401 without the token when it is set.
8. `needs_you: true` without `resolve_key` is rejected.
9. A `master_built` event with `resolves: ["master:Dolphin:110GTX:OIII"]` closes the open `master_queued` with that key and sets `resolved_by` to its own id. The summary's `needs_you` count drops by one.
10. Resolving a key with no open event changes nothing and raises no error.
11. Dismiss sets `dismissed_at`, drops the summary count by one, and restore puts it back.
12. With events on nights 10, 11, 12 and 13 Oct and none on 9 Oct, `GET /api/activity?nights=2` returns 13 and 12 Oct with `next_before: "2026-10-12"`. A follow-up with `before=2026-10-12` returns 11 and 10 Oct and `next_before: null`.
13. `total_nights` counts only nights that have events.
14. `has_new` is true when an event's `received_at` is after `last_seen`, and false after `POST /seen`.
15. `POST /seen` with an `at` earlier than the stored `last_seen` leaves it unchanged.
16. `PUT /api/activity/prefs` with `{"chips": {"imaging": false, "bogus": true}}` stores `imaging: false`, drops `bogus`, and `GET` returns every other chip at its default, with "Site and focus" off.
17. Editing a plan's min altitude from 20 to 30 records one `plan_edited` event whose title names "min altitude 20 to 30". A second edit that night, changing priority, replaces that event's text with both changes, and there is still one event.
18. A plan PUT that changes only `updated_at` records nothing.
19. Changing `actual_hours` on a row records it in the plan-edited summary with the old and new hours.
20. A progress report naming a filter the plan does not have opens a needs-you event. A later report for the same plan with no unknown filters resolves it.
21. A progress report that takes every row to its goal records exactly one `goal_reached`, and a repeat report does not add a second.
22. A TS upload records `ts_upload_received` as needs you. Applying it records `ts_upload_applied` and closes the first. Discarding instead records `ts_upload_discarded` and also closes it.
23. A plugin links push records one `rig_synced` per profile per night, however many pushes arrive.
24. A manifest scan that adds 0.5 h of Ha to a target records `coverage_changed` naming the target, the filter and "+0.50 h". A scan with no change above 0.01 h records `scan_finished` only.
25. A failed scan records an `error` with resolve key `scan`, and the next successful scan resolves it.
26. If the activity database cannot be written (the path is a directory), plan saves still succeed and the failure is logged once per call.
27. A path under the configured `from` prefix maps to the Mac folder, the Windows folder with backslashes, and the file name. A path matching no entry returns the path as sent for both folders.
28. A title of `<img src=x onerror=alert(1)>` is stored as sent and rendered as visible text. (UI, node test on the row renderer.)
29. The rail button shows the badge count when needs-you events are open, the dot when there are none but `has_new` is true, and nothing otherwise. (UI)
30. Turning the Imaging chip off hides imaging rows, keeps error rows visible, and persists across a page reload. (UI, against a test server)
31. With 7 nights of events, the timeline first shows 5 and "Showing 5 of 7 nights". Clicking "Load older nights" shows all 7 and hides the button. (UI)
32. A master row's three Copy buttons put the Mac folder, the Windows folder and the file name on the clipboard. (UI)

Phase 2, plugin (xunit on blakphoenix):

33. Two frames on the same target, filter and exposure produce one `imaging_target` event whose `data.frames` count is 2, posted under one key.
34. A failed batch is retried on the next tick with the same keys, and a 404 stops further posting for the session after one log line.
35. A sync for tonight posts one `sync_for_tonight` event carrying the plan count and the backup path from the outcome lines.

Phase 3, AAC (pytest):

36. An ingest result for night `2026-10-13` posts `transfer_landed` with `night: "2026-10-13"`, the file count and the byte total.
37. A master build posts `master_built` with `refs.master.path` set to the built file, and `resolves` naming `master:<target>:<bucket>:<filter>`.
38. An ntfy alert also posts an `error` event with the alert's key as `resolve_key`, and the next success for that key posts an event that resolves it.
39. With ACP unreachable, processing finishes normally and the client logs one warning per event after three tries.

## Rollback

The feature only adds things. `ACP_ACTIVITY=off` hides the tab, turns `record()` into a no-op, and makes the activity routes answer 404, which the plugin and AAC already treat as "feature off". Deleting `activity.sqlite` removes the history and nothing else. Phases 2 and 3 roll back by reverting their own repos, and ACP keeps working with events from ACP alone.

## Open questions, with proposed defaults

- **Master previews.** Proposed: no preview in phase 3. Later, AAC can write a small stretched JPEG next to each master, and ACP can serve it behind the access gate as a thumbnail on master rows. That is a separate spec.
- **Where failures appear.** Proposed: in the timeline as red rows that ignore the chips, and in the Needs you box until resolved or dismissed. There is no separate failures panel.
- **Windows share name.** Proposed: `\\Singularity\Singularity\`, to confirm in Explorer on Voyager before phase 3.

## Out of scope

- Notifications outside ACP (ntfy, Discord, email). Rohan decided against a morning digest.
- More than one user, or per-user state.
- Searching or editing events.
- Opening Finder or Explorer from the browser.
- Previews of masters (see open questions).
