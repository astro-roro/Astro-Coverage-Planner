# Spec: import Target Scheduler projects by upload from the NINA plugin

Status: draft, 2026-09-24. Not built. It touches three repos:

- this one, ACP core
- the extension, `acp-nina-ts-sync`
- the plugin, `ACP.NINA.Plugin`

## Goal

Get the projects in Voyager's Target Scheduler (TS) into ACP on Brutix. The TS import already exists in the extension (`/import/preview` and `/import/apply`), but it reads the TS database from a path on the ACP host. The database is on Voyager, so the import cannot see it.

This reverses the direction the plugin already uses. The plugin pulls plans from ACP over HTTP. Now it also sends TS's database file to ACP, and ACP runs the existing import on it. Rohan reviews the result in ACP and applies it with a separate click.

The first version sends the whole database file. Sending only the rows is a later clean-up, described near the end.

## What Rohan sees

1. In NINA's ACP dock, he clicks "Send TS to ACP".
2. The dock shows progress: copying, uploading with a percentage, then "ACP is reading it".
3. The dock shows a one-line result and an "Open in ACP" button. The result reads like "ACP has a preview: 16 projects, 41 targets, 3 loss notes".
4. The button opens the review page in his browser. It lists the profile, the counts, a sample of plans, and every loss note grouped by project.
5. He clicks Apply on that page. Nothing changes in ACP before that click, and nothing ever changes in TS.

## Which repo does what

| Piece | Repo | Why there |
|---|---|---|
| Button, consistent copy, upload, progress, link | Plugin | Only the plugin runs on the machine that has the file. |
| Upload endpoint, worker, temp storage, review page, apply by upload id | Extension | The reader, the version check, the importer and its loss notes all live there. ACP core knows nothing about TS by design. |
| Per-request body limit, 413 message | ACP core | Core sets a 1 MB limit on every request (`MAX_BODY_BYTES`, `app.py:159`). Only core can let one route go higher. |
| Token on the daily publish job, `ACP_API_TOKEN` on Brutix | `homelab-stacks` and Brutix | Deployment settings, not code in either app. |

ACP core's access gate (`_api_gate`, `app.py:265`) already covers every path, including the extension's `/api/ext/...` routes. The extension needs no auth code of its own beyond the check in "Auth" below.

## Part A: the plugin

### The button

A new button, "Send TS to ACP", in `AcpDockableTemplate.xaml` beside "Sync All to TS" (row 4, around line 134). It binds to a new `SendTsToAcpCommand` on `AcpDockableVM`. Its tooltip says: "Sends a read-only copy of Target Scheduler's database to ACP for review. Nothing in TS changes."

It asks for no confirmation, because it writes nothing on Voyager. It is disabled while any other ACP action in the dock is running.

### The consistent copy

The plugin never copies the file with a plain file copy. TS may have the database open, and a raw copy can miss committed changes still held in the WAL file, or catch a page mid-write.

Instead it uses SQLite's online backup API, through `Microsoft.Data.Sqlite`'s `SqliteConnection.BackupDatabase(destination)`:

1. Resolve the path with the existing `TsPaths.ResolveDbPath` (`%LOCALAPPDATA%\NINA\SchedulerPlugin\schedulerdb.sqlite`, or `ACP_TS_DB_PATH`).
2. Open the source through `TargetSchedulerDb` in read-only mode. That already sets a 10 second busy timeout and checks `PRAGMA user_version` is 23 to 28.
3. Back it up into a new file, `%TEMP%\acp-ts-upload-<guid>.sqlite`.
4. Open the copy and read its `user_version` again, to send with the upload.
5. Compute the copy's SHA-256.
6. Delete the copy in a `finally` block, whether the upload worked or not.

The backup API is preferred over `VACUUM INTO` because it copies pages exactly. Row ids stay the same, and the import stamps those ids into each plan's `ts_refs`. The writer from plugin PR #11 then finds TS rows by those ids.

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
  "machine": "VOYAGER"
}
```

`AcpApiClient` gains an upload method. It uses the same base URL and the same `Authorization: Bearer` header from `TokenStore` as every other call. There is no multipart code in the plugin today, so this is new.

The shared `HttpClient` has a 30 second timeout (`AcpApiClient.cs:30`). That is too short for a large file over a VPN. The upload uses a second static client with a 10 minute timeout. Progress comes from wrapping the file stream in a small `HttpContent` subclass that reports bytes sent to `ProgressStatusLine`.

### After the upload

ACP answers `202` with an upload id, a status URL and a review URL. The plugin polls the status URL every two seconds for up to two minutes:

- `ready`: the dock shows the counts and the loss note total. It also shows the "Open in ACP" button. The button opens the review URL with the default browser.
- `failed`: the dock shows ACP's message after a cross, and no button.
- Still checking after two minutes: the dock says "ACP is still reading it" and shows the button anyway.

The review URL is an ordinary ACP page. If the browser is not signed in, ACP shows its login form and returns to the page after sign-in.

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
| `GET /import/uploads/<id>` | Status, and the preview once ready. |
| `POST /import/uploads/<id>/preview` | Re-runs the preview for another `profile_id` in the file. Returns `202`. |
| `POST /import/uploads/<id>/apply` | Body `{profile_id, sha256, overwrite}`. Applies, then deletes the upload. |
| `DELETE /import/uploads/<id>` | Discards the upload. |

The review page is `GET /ext/nina-ts-sync/import/uploads/<id>`, a small HTML page served by the extension's blueprint. It reads the status endpoint and calls apply.

The upload id is a random `uuid4` hex. Nothing the client sends is ever used as a file name or a path.

### What the upload route checks, in order

Before the worker sees anything, the route checks:

1. `ACP_API_TOKEN` is set on the server. If not, it answers `403` with "ACP needs an access token set before it accepts uploads". See Decision 1.
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
7. Runs the preview for `meta.profile_id`, and saves the result.

The file is only ever read by SQLite. It is never executed, imported, unpacked, or passed to a shell.

To share code, the bodies of the existing `/import/preview` and `/import/apply` routes move into two functions that take a path: `preview_from(path, profile_id)` and `apply_from(path, profile_id, overwrite)`. The existing routes call them with `resolve_db_path(...)`. The upload routes call them with the stored file. The existing routes behave exactly as before.

### Storage and lifetime

Uploads live in `ACP_TS_UPLOAD_DIR`, default `<tempfile.gettempdir()>/acp-ts-uploads`, created with mode 0700. Each upload is two files:

- `<id>.sqlite`: the database copy.
- `<id>.json`: state, times, size, SHA-256, `meta`, the profile list, and the saved preview.

The state is one of `checking`, `ready`, `failed`, `superseded`, `applied`.

An upload lives 24 hours (`ACP_TS_UPLOAD_TTL_HOURS`). Expired files are swept when the extension registers and on every new upload. A successful apply deletes both files straight away.

In the Brutix container the temp directory is inside the container, not on the archive share. A Watchtower redeploy wipes it. The review page then says the upload has expired and to send it again from NINA. Nothing is lost except the upload itself.

### The review page and apply

The page shows:

- which machine and profile the upload came from, and when
- a profile picker, when the file holds more than one profile
- the counts and first five plans from the preview
- every note, grouped by project, with `loss` notes first and marked as losses
- how many plans ACP has now, and that Apply replaces them all
- the backup path of `plans.json` after apply

Apply is a button, and it needs a second tick box, "Replace the N plans in ACP", when ACP already has plans. That maps to `overwrite`. Apply sends the upload's SHA-256 back, and the route refuses with `409` if it does not match the stored file.

Apply runs `apply_from` on the stored file. That is the existing code path: it rereads the file, imports, stamps `ts_refs` and `ts_base_snapshot` on each plan, and writes `plans.json` with a timestamped backup. It is quick, so it runs inside the request, not in the worker.

## Auth

The token is ACP's existing `ACP_API_TOKEN`. It fits as it is:

- When set, ACP's gate requires `Authorization: Bearer <token>` or a session cookie on every path.
- The plugin already sends the bearer header on every call when a token is stored.
- The plugin stores the token in Windows Credential Manager under `ACP.NINA.Plugin:AcpApiToken`, entered in the NINA Options page. It is never in `settings.json`.
- The browser signs in once at `/login` and keeps a 30 day cookie.

No new token and no new header are needed.

### What breaks when the token goes on

When `ACP_API_TOKEN` is unset, ACP ignores any bearer header. So a plugin with a stored token keeps working against an open server. That makes the safe order: token on the plugin first, then on the server.

These callers exist today:

- The plugin on Voyager. That covers its sync and push buttons, the dock's version poll, and hours sent back. All use `AcpApiClient`, so all carry the token once it is stored. A missing or wrong token shows "ACP rejected the token" in the dock.
- The Brutix User Script `acp-live-publish`, which POSTs `/api/publish/shooting` at 07:15 daily. It sends no token today. It will get `401` and the live page will stop updating until it sends the header.
- Rohan's browser, which will get the login form once.

Rohan sets the token in this order:

1. Generate a token of at least 32 ASCII characters.
2. On Voyager, paste it into NINA's Options page for the ACP plugin. Press Test; it still passes.
3. On Brutix, add `-H "Authorization: Bearer <token>"` to the `acp-live-publish` script, reading the token from a file rather than the script text.
4. On Brutix, put `ACP_API_TOKEN` in the ACP stack's `.env`. Reference it from `docker-compose.yml` as `ACP_API_TOKEN: ${ACP_API_TOKEN}`. Recreate the container.
5. Check the container log says "API auth: ON". Sign in once in the browser. Press Test on Voyager again.

The compose file in `homelab-stacks` gets the `${ACP_API_TOKEN}` reference. The token itself is never committed.

## Profiles

A TS database holds rows for every NINA profile that has used TS. TS's `profileId` is the NINA profile's GUID, and the plugin already uses `ActiveProfile.Id` as the TS profile when it writes (`AcpDockableVM.cs:701`).

The upload imports one profile at a time, because the importer takes one `profile_id`.

- By default it previews the profile NINA has active when the button is pressed. The dock names it: "Sending profile Voyager main".
- The review page lists every profile found in the file, named from the plugin's list, with a project count each. A profile in the file but not on Voyager shows its GUID.
- Picking another profile re-runs the preview for it. Apply uses whichever profile the page shows.
- A profile with no projects gives an empty preview and disables Apply.

## Failure cases

| Case | What happens |
|---|---|
| Copy fails on Voyager: file missing, locked past the busy timeout, or disk full | Dock shows the error. Nothing is sent. The temp copy is deleted. |
| Local `user_version` outside 23 to 28 | The plugin refuses before uploading, with the same words it uses to refuse a sync. |
| Upload interrupted: network drop, NINA closed, timeout | ACP never renames the part file, so no upload exists. The dock says "Upload interrupted. Nothing changed in ACP. Try again." |
| Body over the limit | `413` naming the limit. The dock shows it. |
| No token on the server | `403`. The dock shows ACP's message, which says to set `ACP_API_TOKEN`. |
| Wrong or missing token | `401`. The dock shows "ACP rejected the token", as today. |
| Not a SQLite file | `415`. |
| SQLite, but not TS, or damaged | The worker marks it `failed` with the reason. The dock and page show it. |
| TS schema version ACP does not know | The worker marks it `failed` with the extension's version message. This happens when the plugin supports a newer TS than the extension on Brutix. The message names both. |
| A second upload while one is pending | The new upload replaces the old. The old one is marked `superseded` and its file deleted. Its review page says a newer upload replaced it and links to the new one. The worker drops a superseded job without saving a preview. |
| Apply from a stale tab | The SHA-256 no longer matches, or the upload is gone. `409` or `404`, and the page says to reload. |
| ACP restarts between preview and apply | The upload is gone. The page says it expired. Send again. |
| TS changes between upload and sync | The import stamped TS row ids from the copy. Rows that still exist are found by id. A row deleted and recreated in TS since the upload is not. The live run order below keeps that window short. |

## Later: send rows as JSON instead of the file

This first version sends the whole database because the importer already reads one. Rohan asked for a cleaner version later. It is in `notes/todo.md` under "TS import: send rows instead of the whole database file".

That version would change four things:

- The plugin reads `project`, `target`, `exposureplan` and `exposuretemplate` itself and sends them as JSON. It already has the reader and schema knowledge from the push code.
- The endpoint takes JSON under the normal 1 MB limit, so the core limit change and the temp files go away.
- The importer reads from a snapshot built from JSON instead of from SQLite, so `read_all` gets a second front end.
- The row format becomes a contract kept in step across both repos, with golden tests like the push parity tests.

No SQLite file crosses the network, and ACP stops depending on TS's file layout.

## Acceptance tests

### ACP core (unittest, in `tests/`)

- `test_request_limits.py`: a route that raises `request.max_content_length` accepts a 5 MB body. Every other route still refuses 1.1 MB with `413`. The upload route's `413` body names its own limit.
- `test_api_gate.py`: with `ACP_API_TOKEN` set, `POST /api/ext/...` with no token returns `401` JSON. With the token it passes the gate.

### Extension (pytest, test databases built by `tests/make_db.py`)

- A version 28 database with two profiles uploads with `202`. The status reaches `ready` with the preview for the named profile. Counts equal those from `/import/preview` on the same file.
- Upload with no `ACP_API_TOKEN` set returns `403` and writes nothing to the upload directory.
- A text file returns `415`. A SQLite file with no `project` table ends `failed` with the "not a Target Scheduler database" message. A file where `project` is a view also fails.
- A truncated file whose SHA-256 does not match returns `400` and leaves no `.part` or `.sqlite` file.
- A version 22 database ends `failed` with the extension's unsupported version message.
- A second upload marks the first `superseded` and deletes its file. Apply on the first returns `404`.
- `POST /preview` with the second profile returns that profile's counts.
- Apply with the right SHA-256 writes `plans.json` with a backup. Every plan carries `ts_refs` whose ids exist in the uploaded file. The upload files are deleted.
- Apply with a wrong SHA-256 returns `409` and leaves `plans.json` unchanged.
- Apply when `plans.json` has plans and `overwrite` is false returns `409`.
- An upload older than the lifetime is swept on the next upload.
- The existing `/import/preview` and `/import/apply` tests pass unchanged after the refactor.

### Plugin (unit tests in `ACP.NINA.Plugin.Tests`)

- A database in WAL mode has committed rows still in the WAL. Its backup copy contains those rows.
- The copy's row ids equal the source's for every TS table.
- A version 22 database is refused before any HTTP call.
- The upload request is multipart with `file` and `meta` parts, carries the bearer header, and `meta.sha256` matches the file.
- `401`, `403`, `413`, `415` and a dropped connection each give their dock message. The temp copy is deleted in every case.

### By hand on Voyager

1. Press "Send TS to ACP" while NINA is running with TS open. The dock shows progress, then counts and the button.
2. The button opens the review page. The loss notes match the preview's `loss_count`.
3. Press it again with a wrong token stored. The dock says ACP rejected the token.

## Live run order

- [ ] Merge and deploy the ACP core change (a push to main redeploys Brutix).
- [ ] Copy the updated extension into `/mnt/user/appdata/acp/extensions` on Brutix and recreate the `acp` container.
- [ ] Build and install the plugin on Voyager, and restart NINA.
- [ ] Set the token in the order under "Auth".
- [ ] In NINA, press "Send TS to ACP" and wait for the button.
- [ ] Review in ACP: check the profile, the counts, and every loss note.
- [ ] Press Apply. Note the `plans.json` backup path it shows.
- [ ] Back up TS on Voyager: with NINA closed, copy `schedulerdb.sqlite` somewhere outside `SchedulerPlugin`.
- [ ] Open NINA and press "Sync All to TS". Check TS's project list shows the same projects, with no duplicates.

Do the last four without editing anything in TS in between.

## Decisions for Rohan

1. Refuse uploads when no token is set. Recommended: yes. The cost is that the token must be switched on before the first upload. Without it, anyone on the LAN can write files into ACP's container.
2. Apply replaces all plans in ACP, as the existing import does. Recommended: yes for this first run, since that path is built and tested. The cost: a plan that exists only in ACP is dropped. It stays in the backup file, and the page shows how many plans ACP has before you apply.
3. The review page is served by the extension, not built into ACP's main page. Recommended: extension. It looks plainer than the main app, but it needs no change to ACP's frontend and keeps TS out of core.
