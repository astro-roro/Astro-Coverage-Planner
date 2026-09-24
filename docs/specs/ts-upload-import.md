# Spec: import Target Scheduler projects by upload from the NINA plugin

Status: draft, revised 2026-09-24 so an upload merges and never replaces. Not built. It touches three repos:

- this one, ACP core
- the extension, `acp-nina-ts-sync`
- the plugin, `ACP.NINA.Plugin`

## Goal

Get plans made or changed in a rig's Target Scheduler (TS) back into ACP on Brutix. The TS readers already exist in the extension, but they read the TS database from a path on the ACP host. The database is on the rig, so ACP cannot see it.

This reverses the direction the plugin already uses. The plugin pulls plans from ACP over HTTP. Now it also sends TS's database file to ACP. ACP compares it with its own plans and shows what would change. Rohan reviews that in ACP and applies it with a separate click.

An upload is a merge. It adds plans that are new to ACP and updates plans it matches. It never deletes anything, and it never touches a plan that belongs to another rig. The use case: Rohan adds a plan to a rig's TS while out at a dark site, presses the button, and that plan appears in ACP with everything else left as it was.

The first version sends the whole database file. Sending only the rows is a later clean-up, described near the end.

## What Rohan sees

1. In NINA's ACP dock, he clicks "Send TS to ACP".
2. The dock shows progress: copying, uploading with a percentage, then "ACP is reading it".
3. The dock shows a one-line result and an "Open in ACP" button. The result reads like "Voyager main: 2 new, 5 updated, 1 needs a choice".
4. The button opens the review page in his browser. It lists new plans, updated plans, conflicts, and loss notes.
5. He picks an answer for each conflict and clicks Apply. Nothing changes in ACP before that click, and nothing ever changes in TS.

## Which repo does what

| Piece | Repo | Why there |
|---|---|---|
| Button, consistent copy, upload, progress, link | Plugin | Only the plugin runs on the machine that has the file. |
| Push state sent back to ACP after a sync | Plugin | Only the plugin knows which rows it just wrote. See "Per-rig links". |
| Upload endpoint, worker, temp storage, merge, review page | Extension | The reader, the version check, the importer, the three-way diff and its loss notes all live there. ACP core knows nothing about TS by design. |
| Per-request body limit, 413 message | ACP core | Core sets a 1 MB limit on every request (`MAX_BODY_BYTES`, `app.py:159`). Only core can let one route go higher. |
| Token on the daily publish job, `ACP_API_TOKEN` on Brutix | `homelab-stacks` and Brutix | Deployment settings, not code in either app. |

ACP core's access gate (`_api_gate`, `app.py:265`) already covers every path, including the extension's `/api/ext/...` routes. The extension needs no auth code of its own beyond the check in "Auth" below.

## Part A: the plugin

### The button

A new button, "Send TS to ACP", in `AcpDockableTemplate.xaml` beside "Sync All to TS" (row 4, around line 134). It binds to a new `SendTsToAcpCommand` on `AcpDockableVM`. Its tooltip says: "Sends a read-only copy of Target Scheduler's database to ACP. ACP shows what would change before anything does. Nothing in TS changes."

It asks for no confirmation, because it writes nothing on the rig. It is disabled while any other ACP action in the dock is running.

### The consistent copy

The plugin never copies the file with a plain file copy. TS may have the database open, and a raw copy can miss committed changes still held in the WAL file, or catch a page mid-write.

Instead it uses SQLite's online backup API, through `Microsoft.Data.Sqlite`'s `SqliteConnection.BackupDatabase(destination)`:

1. Resolve the path with the existing `TsPaths.ResolveDbPath` (`%LOCALAPPDATA%\NINA\SchedulerPlugin\schedulerdb.sqlite`, or `ACP_TS_DB_PATH`).
2. Open the source through `TargetSchedulerDb` in read-only mode. That already sets a 10 second busy timeout and checks `PRAGMA user_version` is 23 to 28.
3. Back it up into a new file, `%TEMP%\acp-ts-upload-<guid>.sqlite`.
4. Open the copy and read its `user_version` again, to send with the upload.
5. Compute the copy's SHA-256.
6. Delete the copy in a `finally` block, whether the upload worked or not.

The backup API is preferred over `VACUUM INTO` because it copies pages exactly. Row ids stay the same. The merge stamps those ids into each plan's links, and the writer from plugin PR #11 finds TS rows by them.

The existing `TargetSchedulerDb.BackupTo` uses a raw `FileStream` copy, with the same WAL risk. It is out of scope here but worth a follow-up.

### The upload

`POST /api/ext/nina-ts-sync/import/uploads` on the configured ACP server, as `multipart/form-data` with two parts:

- `file`: the copy, content type `application/vnd.sqlite3`.
- `meta`: JSON, described below.

```jsonc
{
  "profile_id": "3f2c...",          // ActiveProfile.Id, which TS uses as profileId
  "profiles": [                     // every NINA profile on this machine, so ACP can name them
    {"id": "3f2c...", "name": "Voyager main"},
    {"id": "9a01...", "name": "Travel rig"}
  ],
  "sha256": "e3b0...",
  "user_version": 28,
  "plugin_version": "3.3.0.0",
  "machine": "VOYAGER"               // Environment.MachineName
}
```

`AcpApiClient` gains an upload method. It uses the same base URL and the same `Authorization: Bearer` header from `TokenStore` as every other call. There is no multipart code in the plugin today, so this is new.

The shared `HttpClient` has a 30 second timeout (`AcpApiClient.cs:30`). That is too short for a large file over a VPN. The upload uses a second static client with a 10 minute timeout. Progress comes from wrapping the file stream in a small `HttpContent` subclass that reports bytes sent to `ProgressStatusLine`.

### After the upload

ACP answers `202` with an upload id, a status URL and a review URL. The plugin polls the status URL every two seconds for up to two minutes:

- `ready`: the dock shows the counts of new, updated and conflicting plans, and the "Open in ACP" button. The button opens the review URL with the default browser.
- `failed`: the dock shows ACP's message after a cross, and no button.
- Still checking after two minutes: the dock says "ACP is still reading it" and shows the button anyway.

The review URL is an ordinary ACP page. If the browser is not signed in, ACP shows its login form and returns to the page after sign-in.

### Push state sent back after a sync

Today the plugin works out each plan's TS row ids and snapshot during a push (`TsPushResult.PlanStates`), then drops them. `TsPlanRefsSource.cs` says so in its header comment. That leaves ACP's record of the last sync stale after every plugin push, which makes the merge report false conflicts. See "How changed since last sync is detected".

After a successful "Sync for tonight" or "Sync All to TS", the plugin posts those states to `POST /api/ext/nina-ts-sync/links`, with the profile id and machine name. It is a small JSON body, under the 1 MB limit. A failure here does not fail the sync. The dock adds "ACP was not told about this sync, so the next upload may ask about more plans" and the sync result stands.

## Part B: ACP and the extension

### Core change: a bigger limit for one route

Flask 3.1 lets a route raise its own body limit by setting `request.max_content_length` before reading the body. The extension's upload route does that. Core changes are:

- `requirements.txt`: `flask>=3.1` (the venv has 3.1.3; the pin is `>=3.0` today).
- The 413 handler (`app.py:169`) reports `request.max_content_length` instead of the global constant, so the upload's error names the right limit.

The upload limit is `ACP_TS_UPLOAD_MAX_BYTES`, default 64 MB. It is read by the extension. Every other route keeps the 1 MB limit.

### Endpoints

All under `/api/ext/nina-ts-sync`, in the extension.

| Method and path | Does |
|---|---|
| `POST /import/uploads` | Takes the upload, stores it, queues it for the worker. Returns `202` with `upload_id`, `status_url`, `review_url`. |
| `GET /import/uploads/<id>` | Status, and the merge preview once ready. |
| `POST /import/uploads/<id>/preview` | Re-runs the preview for another `profile_id` in the file. Returns `202`. |
| `POST /import/uploads/<id>/apply` | Body `{profile_id, sha256, preview_hash, decisions, add, new_plan_gear}`. Merges, then deletes the upload. |
| `DELETE /import/uploads/<id>` | Discards the upload. |
| `POST /links` | Stores push state from the plugin for one profile. See "Per-rig links". |

The review page is `GET /ext/nina-ts-sync/import/uploads/<id>`, a small HTML page served by the extension's blueprint. It reads the status endpoint and calls apply.

The upload id is a random `uuid4` hex. Nothing the client sends is ever used as a file name or a path.

### What the upload route checks, in order

Before the worker sees anything, the route checks:

1. `ACP_API_TOKEN` is set on the server. If not, it answers `403` with "ACP needs an access token set before it accepts uploads".
2. The body fits the limit, else `413`.
3. Both parts are present and `meta` parses, else `400`.
4. The file starts with the 16 bytes `SQLite format 3\0`, else `415` "not a SQLite database".
5. The SHA-256 of the received bytes matches `meta.sha256`, else `400` "upload incomplete or damaged".

The file is streamed to `<id>.part` and renamed to `<id>.sqlite` only after step 5 passes. A failed check deletes the part file.

### The worker

One background thread, started when the extension registers, takes upload ids from a queue. ACP runs under waitress as a single process, so one in-process thread is enough and no job system is needed. For each upload it:

1. Opens the file with `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)`. `immutable=1` is safe here because nothing else ever writes this copy.
2. Sets `PRAGMA trusted_schema = OFF`, so a crafted view or trigger cannot call functions with side effects. Python's `sqlite3` never loads extensions unless asked, and this code never asks.
3. Runs `PRAGMA quick_check`. A damaged file fails here.
4. Checks that `project`, `target`, `exposureplan` and `exposuretemplate` exist in `sqlite_schema` with type `table`, not `view`. If not, it fails with "a SQLite file, but not a Target Scheduler database".
5. Runs `assert_supported_version` (`db.py`), which accepts `user_version` 23 to 28.
6. Lists the profiles in the file, the same way `GET /profiles` does, from `project.profileId` and `profilepreference.profileId`. Names come from `meta.profiles`, because Brutix has no NINA profile files to read.
7. Runs the merge preview for `meta.profile_id` (below), and saves the result.

The file is only ever read by SQLite. It is never executed, imported, unpacked, or passed to a shell.

### Storage and lifetime

Uploads live in `ACP_TS_UPLOAD_DIR`, default `<tempfile.gettempdir()>/acp-ts-uploads`, created with mode 0700. Each upload is two files:

- `<id>.sqlite`: the database copy.
- `<id>.json`: state, times, size, SHA-256, `meta`, the profile list, and the saved preview.

The state is one of `checking`, `ready`, `failed`, `superseded`, `applied`.

An upload lives 24 hours (`ACP_TS_UPLOAD_TTL_HOURS`). Expired files are swept when the extension registers and on every new upload. A successful apply deletes both files straight away.

In the Brutix container the temp directory is inside the container, not on the archive share. A Watchtower redeploy wipes it. The review page then says the upload has expired and to send it again from NINA. Nothing is lost except the upload itself.

## The merge

### Rigs and scope

A rig is a NINA profile. TS stores every row under the NINA profile's GUID, and the plugin already writes TS as `ActiveProfile.Id` (`AcpDockableVM.cs:701`). So an upload names its rig with `meta.profile_id`, and the preview is always for one profile.

An ACP plan belongs to a profile when it has a link for that profile (next section). An upload for profile P only ever reads or changes:

- plans linked to P, and
- plans with no link to any profile, found through the guid match below.

A plan linked only to another profile is out of scope. It is not read, not diffed, not restamped, and not listed. This is what stops one rig's upload from changing another rig's plans. It also covers two rigs with a project of the same name, which today's name matching would cross.

The link also records the machine name. If P's link was made from a different machine than the upload's, the link's row ids are not trusted, because they belong to a different database file. The plan falls back to the guid match, and the preview says "Profile Voyager main was last linked from HAYABUSA; matched by name stamp instead".

### Per-rig links

Today each plan has one `ts_refs` block and one `ts_base_snapshot`, and `ts_refs` holds a single `profile_id`. A plan pushed to two rigs keeps only the last one's ids. When the other rig next syncs, its pinned rows fail the check in `TsUpsert.ResolvePins`, and it falls back to guid and name. Rows TS created by hand carry NINA's own random guid and are not claimed, so they get duplicated.

The merge needs one link per profile. Each plan gains:

```jsonc
"ts_links": {
  "<profile_id>": {
    "machine": "VOYAGER",
    "refs": { /* the same shape as ts_refs today */ },
    "base_snapshot": { /* the same shape as ts_base_snapshot today */ },
    "updated_iso": "2026-09-24T10:31:00Z"
  }
}
```

`ts_refs` and `ts_base_snapshot` stay, written as a copy of the most recent link, so older readers keep working. The plugin's `TsConvert.RefsFor(plan, profileId)` reads `ts_links[profileId].refs` first, then falls back to `ts_refs` when its `profile_id` matches, as now.

The link for P is written by:

- an upload apply for P, for every plan it adds or matches
- the extension's own `/sync`, `/import/apply` and `/import/resolve`, for their profile
- `POST /links`, from the plugin after each push

`POST /links` takes `{profile_id, machine, states: [{plan_id, refs, base_snapshot}]}`. It only writes `ts_links[profile_id]` on plans that exist, and ignores unknown plan ids. It touches no other field.

### Matching a TS plan to an ACP plan

The worker builds TS-side plans from the file with the existing `import_to_acp_plans(snap, gear)`, which also returns the row ids each came from. It then matches them to in-scope ACP plans in the plugin's order (PR #11, `TsUpsert.cs:253`):

1. By link. The ACP plan's `ts_links[P].refs` name a project id and target ids, and a TS plan came from those same rows.
2. By ACP's guid. ACP's deterministic `target_guid(P, project_name, anchor target name)` equals the guid on a TS target row in the plan. Rows the plugin or extension pushed carry these, so this finds every plan ACP has pushed to this rig, linked or not.
3. By name, as a proposal only. An unmatched TS plan whose project name and base target name equal an unmatched in-scope ACP plan's is shown as "Looks like ACP's M31, link them?". It is never linked without a tick.

Each ACP plan matches at most one TS plan. Sibling panels of a mosaic that the importer split into separate plans are claimed with the plan, as `_claim_sibling_panels` does today.

A TS plan left unmatched after these steps is new to ACP. An in-scope ACP plan left unmatched is listed as "not in this upload" and left alone. A TS row deleted on the rig therefore never deletes anything in ACP.

### How "changed since last sync" is detected

The diff is the existing three-way diff in `diff.py`. For each matched plan it compares three versions of each field it knows:

- base: `ts_links[P].base_snapshot`, the TS state as of the last sync on this rig
- ACP: the plan as it is in ACP now
- TS: the plan as the upload reads it

A field that only TS changed is taken from TS. A field that only ACP changed keeps ACP's value, and the next sync writes it to TS. A field changed on both sides to different values makes the whole plan a conflict. That matches `_classify` today.

Two rules change for uploads:

- No base snapshot for P. Today `diff_plan` treats that as a first sync and silently takes TS's value for every difference. For an upload, a difference with no base is a conflict instead, marked "no sync record for this rig". Otherwise an ACP edit made since the last push would be overwritten without a word.
- Base captured before a plugin push. Without `POST /links`, the base is only as fresh as the last extension sync. An ACP edit pushed by the plugin, then a TS edit on the rig, looks like both sides changed. `POST /links` keeps the base current and removes these false conflicts.

### Conflicts and defaults

| Case | Default | Choice on the page |
|---|---|---|
| Only TS changed | Take TS's fields | Can switch to "Keep ACP" |
| Only ACP changed | Keep ACP | None needed |
| Both changed, or no sync record | No default | "Take TS", "Keep ACP", or "Leave for now" |
| New in TS | Add | Can untick |
| Name proposal | Not linked, and not added | Tick to link, or tick "Add as new" |

Apply stays disabled until every conflict has a choice. "Leave for now" changes nothing on that plan, including its link. Each conflict row shows the base, ACP and TS values side by side for the fields that differ.

### New plans and gear

A new plan gets a fresh ACP id. The importer's `acp-imp-<guid or name>` id is kept only when no existing plan uses it. It gets a link for P at once, so the next upload matches it by link.

TS keeps no record of gear. For a mosaic, the importer already finds the telescope and camera that rebuild its panels (`_gear_that_rebuilds`), and that stays. For a single target it uses the first telescope and camera in ACP's gear list, which is wrong for most rigs. The review page shows a "Gear for new plans on this rig" picker. It defaults to the pair most used by plans already linked to P, and the choice is saved per profile in the extension's config. It applies to new single-target plans only.

### What must change in the existing code

The extension already has most of this. `POST /import/diff` and `POST /import/resolve` do a three-way diff, take per-plan decisions, add new plans only when asked, and never delete. `POST /import/apply` is the one that replaces `plans.json`, and uploads never call it.

The changes, all in the extension unless noted:

- `_match_remote` matches by project name only, and falls back to the first candidate. It is replaced by the three-step match above.
- `import_diff_route` and `import_resolve_route` loop over every ACP plan. They get the profile scope above, so a plan linked only to another profile is skipped without a notice.
- Their bodies move into `diff_from(path, profile_id, machine)` and `resolve_from(path, profile_id, machine, decisions, add, new_plan_gear)`, which take a file path. The existing routes call them with `resolve_db_path(...)`. The upload routes call them with the stored file.
- `import_ts_only_new` is keyed by project name, which is ambiguous. New plans are keyed by the TS project id and anchor target id instead.
- `diff_plan` gets a flag for the "no base means conflict" rule. The upload path sets it. The existing routes keep today's behaviour unless the flag is set.
- `_restamp` and the stamping in `/sync` and `/import/apply` write `ts_links[P]` as well as `ts_refs` and `ts_base_snapshot`.
- `resolve_from` refuses with `409` when the in-scope ACP plans changed after the preview. It compares a hash of those plans taken at preview time (`preview_hash`) with one taken at apply time.
- `plans.json` is written through the existing `_write_plans_json(..., overwrite=True)`, which keeps a timestamped backup. Out-of-scope plans pass through untouched.
- Plugin: `RefsFor` reads `ts_links` first, and the push posts its states to `POST /links`.

### The review page and apply

The page shows:

- which machine and profile the upload came from, and when
- a profile picker, when the file holds more than one profile
- new plans, with a tick each, and the gear picker for them
- updated plans, with the fields that will change
- conflicts, with the three values and the three choices
- name proposals, with their ticks
- in-scope plans not in this upload, as a count and a collapsed list
- every loss note, grouped by project, losses first
- after apply, the backup path of `plans.json`

Apply sends the upload's SHA-256, the preview hash and the choices. The route refuses with `409` when the SHA-256 does not match the stored file, or when ACP's in-scope plans changed since the preview. Apply is quick, so it runs inside the request, not in the worker.

## Auth

The token is ACP's existing `ACP_API_TOKEN`. It fits as it is:

- When set, ACP's gate requires `Authorization: Bearer <token>` or a session cookie on every path.
- The plugin already sends the bearer header on every call when a token is stored.
- The plugin stores the token in Windows Credential Manager under `ACP.NINA.Plugin:AcpApiToken`, entered in the NINA Options page. It is never in `settings.json`.
- The browser signs in once at `/login` and keeps a 30 day cookie.

No new token and no new header are needed. Uploads are refused while no token is set on the server.

### What breaks when the token goes on

When `ACP_API_TOKEN` is unset, ACP ignores any bearer header. So a plugin with a stored token keeps working against an open server. That makes the safe order: token on every plugin first, then on the server.

These callers exist today:

- The plugin on each rig. That covers its sync and push buttons, the dock's version poll, and hours sent back. All use `AcpApiClient`, so all carry the token once it is stored. A missing or wrong token shows "ACP rejected the token" in the dock.
- The Brutix User Script `acp-live-publish`, which POSTs `/api/publish/shooting` at 07:15 daily. It sends no token today. It will get `401` and the live page will stop updating until it sends the header.
- Rohan's browser, which will get the login form once.

Rohan sets the token in this order:

1. Generate a token of at least 32 ASCII characters.
2. On each rig (Voyager, and any other NINA machine), paste it into NINA's Options page for the ACP plugin. Press Test; it still passes.
3. On Brutix, add `-H "Authorization: Bearer <token>"` to the `acp-live-publish` script, reading the token from a file rather than the script text.
4. On Brutix, put `ACP_API_TOKEN` in the ACP stack's `.env`. Reference it from `docker-compose.yml` as `ACP_API_TOKEN: ${ACP_API_TOKEN}`. Recreate the container.
5. Check the container log says "API auth: ON". Sign in once in the browser. Press Test on each rig again.

The compose file in `homelab-stacks` gets the `${ACP_API_TOKEN}` reference. The token itself is never committed.

## Profiles in one file

A TS database holds rows for every NINA profile on that machine that has used TS. One machine can run several rigs as separate profiles.

- By default the upload previews the profile NINA has active when the button is pressed. The dock names it: "Sending profile Voyager main".
- The review page lists every profile found in the file, named from the plugin's list, with a project count each. A profile in the file but not on this machine shows its GUID.
- Picking another profile re-runs the preview for it, with that profile's scope. Apply uses whichever profile the page shows.
- A profile with no projects gives an empty preview and disables Apply.

## Failure cases

| Case | What happens |
|---|---|
| Copy fails on the rig: file missing, locked past the busy timeout, or disk full | Dock shows the error. Nothing is sent. The temp copy is deleted. |
| Local `user_version` outside 23 to 28 | The plugin refuses before uploading, with the same words it uses to refuse a sync. |
| Upload interrupted: network drop, NINA closed, timeout | ACP never renames the part file, so no upload exists. The dock says "Upload interrupted. Nothing changed in ACP. Try again." |
| Body over the limit | `413` naming the limit. The dock shows it. |
| No token on the server | `403`. The dock shows ACP's message, which says to set `ACP_API_TOKEN`. |
| Wrong or missing token | `401`. The dock shows "ACP rejected the token", as today. |
| Not a SQLite file | `415`. |
| SQLite, but not TS, or damaged | The worker marks it `failed` with the reason. The dock and page show it. |
| TS schema version ACP does not know | The worker marks it `failed` with the extension's version message. This happens when the plugin supports a newer TS than the extension on Brutix. The message names both. |
| A second upload for the same profile while one is pending | The new upload replaces the old. The old one is marked `superseded` and its file deleted. Its review page says a newer upload replaced it and links to the new one. |
| A second upload for a different profile | Both stay pending. Each has its own review page and its own scope. Applying one does not affect the other, because their scopes do not overlap. |
| ACP plans edited between preview and apply | `409`, "ACP changed since this preview". The page offers to re-run the preview on the same upload. |
| Apply from a stale tab | The SHA-256 no longer matches, or the upload is gone. `409` or `404`, and the page says to reload. |
| ACP restarts between preview and apply | The upload is gone. The page says it expired. Send again. |
| TS changes between upload and sync | The merge stamped TS row ids from the copy. Rows that still exist are found by id. A row deleted and recreated in TS since the upload is not. The live run order below keeps that window short. |
| Link from another machine for the same profile | Row ids not trusted. Matched by guid instead, with a note on the page. |
| `POST /links` fails after a push | The sync still counts. The dock warns. The next upload may show more conflicts than it should. |

## Later: send rows as JSON instead of the file

This first version sends the whole database because the importer already reads one. Rohan asked for a cleaner version later. It is in `notes/todo.md` under "TS import: send rows instead of the whole database file".

That version would change four things:

- The plugin reads `project`, `target`, `exposureplan` and `exposuretemplate` itself and sends them as JSON. It already has the reader and schema knowledge from the push code.
- The endpoint takes JSON under the normal 1 MB limit, so the core limit change and the temp files go away.
- The importer reads from a snapshot built from JSON instead of from SQLite, so `read_all` gets a second front end.
- The row format becomes a contract kept in step across both repos, with golden tests like the push parity tests.

The merge rules above do not change. Only the way the TS rows reach them does.

## Acceptance tests

### ACP core (unittest, in `tests/`)

- `test_request_limits.py`: a route that raises `request.max_content_length` accepts a 5 MB body. Every other route still refuses 1.1 MB with `413`. The upload route's `413` body names its own limit.
- `test_api_gate.py`: with `ACP_API_TOKEN` set, `POST /api/ext/...` with no token returns `401` JSON. With the token it passes the gate.

### Extension: upload handling (pytest, test databases built by `tests/make_db.py`)

- A version 28 database with two profiles uploads with `202`. The status reaches `ready` with a merge preview for the named profile.
- Upload with no `ACP_API_TOKEN` set returns `403` and writes nothing to the upload directory.
- A text file returns `415`. A SQLite file with no `project` table ends `failed` with the "not a Target Scheduler database" message. A file where `project` is a view also fails.
- A truncated file whose SHA-256 does not match returns `400` and leaves no `.part` or `.sqlite` file.
- A version 22 database ends `failed` with the extension's unsupported version message.
- A second upload for the same profile marks the first `superseded` and deletes its file. Apply on the first returns `404`.
- An upload for a second profile leaves the first one `ready`.
- An upload older than the lifetime is swept on the next upload.

### Extension: the merge

Each test starts from a `plans.json` and a TS file built for it. "Unchanged" means the plan's JSON is equal before and after, field for field.

- Add only. ACP has plans A and B linked to profile P. The upload holds A, B, and a new plan C. After apply, C exists with a link for P. A and B are unchanged. The count of plans is three.
- Nothing deleted. ACP has A, B and D linked to P. The upload holds only A and B. After apply, D is unchanged. The preview lists D under "not in this upload".
- Other rig untouched. ACP has X linked to profile Q and Y linked to P. Both have project name "M31". The upload for P changes M31's hours. After apply, Y has the new hours and X is unchanged. X does not appear in the preview.
- ACP-only plans untouched. A plan with no link, whose guid matches no row in the file, is unchanged and not listed.
- Match by link beats name. An ACP plan linked to P was renamed in TS. The upload matches it by link, and the TS name comes in as a TS-only change.
- Match by guid. A plan with no link, whose deterministic guid is on a TS target row, is matched and gets a link for P.
- Name proposal. A TS project with NINA's random guid has the same name as an unlinked ACP plan. The preview shows a proposal. Applying without ticking it neither links nor adds it. Ticking "link" links it.
- TS-only change. The base and ACP agree on 10 hours of Ha, and TS has 12. Apply with no choices gives 12.
- ACP-only change. The base and TS agree on 10, and ACP has 14. Apply keeps 14.
- Conflict. The base has 10, ACP 14, TS 12. The preview lists it as a conflict. Apply without a choice for it returns `400`. "Take TS" gives 12, "Keep ACP" gives 14, and "Leave for now" leaves the plan and its link unchanged.
- No base. A matched plan with no base snapshot for P, where ACP and TS differ, is a conflict marked "no sync record for this rig".
- Stale preview. A plan in scope is edited through ACP's API after the preview. Apply returns `409` and `plans.json` is unchanged.
- Apply with a wrong SHA-256 returns `409` and leaves `plans.json` unchanged.
- Every apply writes a timestamped backup of `plans.json`.
- Gear. A new single-target plan gets the gear chosen on the page. With no choice made, it gets the pair most used by plans linked to P.
- Links. After apply, every added or matched plan has `ts_links[P]`, and `ts_refs` equals `ts_links[P].refs`. A plan's link for Q is unchanged.
- `POST /links` writes only `ts_links[P]` on the named plans and ignores unknown plan ids.
- The existing tests for `/import/preview`, `/import/apply`, `/import/diff` and `/import/resolve` pass after the refactor. Tests that relied on name matching across profiles are rewritten for the new scope, and the change is noted in the commit.

### Plugin (unit tests in `ACP.NINA.Plugin.Tests`)

- A database in WAL mode has committed rows still in the WAL. Its backup copy contains those rows.
- The copy's row ids equal the source's for every TS table.
- A version 22 database is refused before any HTTP call.
- The upload request is multipart with `file` and `meta` parts, carries the bearer header, and `meta.sha256` matches the file.
- `401`, `403`, `413`, `415` and a dropped connection each give their dock message. The temp copy is deleted in every case.
- `RefsFor` returns `ts_links[profile].refs` when present, then `ts_refs` when its profile matches, else nothing.
- After a successful push, one `POST /links` is sent with a state per pushed plan. When it fails, the sync still reports success, with the warning line.

### By hand on Voyager

1. Add a new project in TS by hand. Press "Send TS to ACP" while NINA is running. The dock shows progress, then "1 new" and the button.
2. The review page lists that project as new, and plans for other rigs appear nowhere on it.
3. Apply. The new plan is in ACP, and the plan count went up by one.

## Live run order

- [ ] Merge and deploy the ACP core change (a push to main redeploys Brutix).
- [ ] Copy the updated extension into `/mnt/user/appdata/acp/extensions` on Brutix and recreate the `acp` container.
- [ ] Build and install the plugin on Voyager, and restart NINA.
- [ ] Set the token in the order under "Auth".
- [ ] Back up TS on Voyager: with NINA closed, copy `schedulerdb.sqlite` somewhere outside `SchedulerPlugin`. Then open NINA.
- [ ] In NINA, press "Send TS to ACP" and wait for the button.
- [ ] Review in ACP. Check the profile, the new plans, the gear for them, every conflict, and every loss note. Check no plan from another rig is listed.
- [ ] Press Apply. Note the `plans.json` backup path it shows.
- [ ] In NINA, run "Sync for tonight" or "Sync All to TS". Check TS's project list has no duplicates. The sync posts its links back to ACP.
- [ ] Press "Send TS to ACP" again. The preview should show nothing new, nothing updated, and no conflicts.

Do the steps from the upload to the sync without editing anything in TS in between. The last step proves the round trip is stable.

"Sync All to TS" writes every ACP plan into this rig's TS, including plans meant for other rigs, unless the plugin is set to "Only what fits". That is today's behaviour and outside this spec, but it matters once more than one rig uploads.

## Decisions for Rohan

1. Store one link per rig on each plan, and have the plugin send its push state back to ACP after every sync. Recommended: yes. The cost is changes in both the plugin and the extension, and a new field in `plans.json`. Without it, a plan used on two rigs belongs to whichever uploads first, and every plugin push makes the next upload ask about plans that did not really conflict.
2. When a TS project has no ACP stamp but has the same name as an ACP plan, propose the match and let you tick it. Recommended: yes. The cost is one more kind of row on the review page. Without it, projects you made by hand in TS with an existing ACP name come in as duplicates.
3. New single-target plans get a per-rig default gear, picked on the review page and remembered. Recommended: yes. The cost is one picker to set on the first upload from each rig. Without it, they get the first telescope and camera in ACP's list, which is wrong for most rigs.
