# NINA Plugin research: Framing Assistant + Target Scheduler

Captured 2026-05-19 from primary sources (NINA core, ninaAPI plugin, AstroPM plugin,
TS plugin source + docs, existing private `nina_ts_sync` ACP extension). This is the
reference for how the ACP NINA Plugin should integrate with both surfaces.

> **Architecture decision (2026-05-19, post-research):** the C# plugin is a thin
> NINA-side UX layer. The existing private Python extension `nina_ts_sync` keeps
> doing all the TS sqlite work. Plugin delegates over localhost HTTP. See
> "Revised plugin architecture" at the bottom of this doc for the why and how.

## Framing Assistant

### The supported API

NINA exposes `IFramingAssistantVM` in `NINA.WPF.Base.Interfaces.ViewModel`. Plugin
accesses it via `AdvancedAPI.Controls.FramingAssistant`. Properties we need:

```csharp
Task<bool> SetCoordinates(DeepSkyObject dso);  // ← clean entry point
int CameraWidth/Height; double CameraPixelSize; double FocalLength;
int HorizontalPanels; int VerticalPanels; double OverlapPercentage;
FramingRectangle Rectangle;                     // .TotalRotation
IAsyncCommand LoadImageCommand;
```

### The supported pattern (use this)

Reference implementation: `christian-photo/ninaAPI` → `Framing.cs`. Battle-tested,
small, no reflection.

```csharp
var framing = AdvancedAPI.Controls.FramingAssistant;

// 1. Coordinates via SetCoordinates() — bypasses H/M/S setters which
//    cross-reference live state. Fresh DeepSkyObject every time.
framing.SetCoordinates(new DeepSkyObject(
    name: targetName,
    coords: new Coordinates(Angle.ByDegree(raDeg), Angle.ByDegree(decDeg), Epoch.J2000),
    imageRepositoryPath: string.Empty,
    customHorizon: null
));

// 2. Optics + camera (FOV depends on these)
framing.CameraWidth = pixelWidth;
framing.CameraHeight = pixelHeight;
framing.CameraPixelSize = pixelSizeUm;
framing.FocalLength = focalLengthMm;

// 3. Mosaic
framing.HorizontalPanels = cols;
framing.VerticalPanels = rows;
framing.OverlapPercentage = overlapPct;

// 4. Rotation — NOTE: NINA stores rotation reversed
framing.Rectangle.TotalRotation = 360 - rotationDeg;

// 5. Optionally trigger image load
await framing.LoadImageCommand.ExecuteAsync(null);
```

**Rotation quirk:** mirror `ninaAPI`'s `360 - rotation`. NINA's `TotalRotation` is
opposite-sense from the deg-east-of-north convention. Our existing `_build_ts_export`
writes `Rotation` to TS without inversion — verify empirically that TS uses the
non-inverted form before plugin v1.0 ships.

### The unsupported pattern (don't use)

`Josh-Jones-76/AstroPM.NINA.Plugin/FramingInjector.cs` does visual-tree injection via
reflection (polls main window every 2s, walks tree by type name, sets ViewModel
properties via `vmType.GetProperty(...).SetValue(...)`). Works today, brittle to
every NINA UI revision. AstroPM probably went hacky to inject their dropdown into
NINA's existing Framing strip; we can match that UX with a NINA dockable plugin
panel without reflection.

## Target Scheduler

### Repos and docs

- **Plugin source:** `tcpalmer/nina.plugin.assistant` — schema under
  `NINA.Plugin.Assistant/Database/Schema/`, namespace
  `Assistant.NINAPlugin.Database.Schema`.
- **Newer TS5 work:** `tcpalmer/nina.plugin.targetscheduler` (per docs link).
- **Docs site:** https://tcpalmer.github.io/nina-scheduler/
- **DB location:** `%localappdata%\NINA\SchedulerPlugin\schedulerdb.sqlite`, with
  auto-backup (3 most recent retained).

### Data model

```
Profile (NINA's, just a GUID we reference)
└── Project (constraints + grading + dithering policy)
    └── Target (RA/Dec/rotation/ROI per panel)
        └── ExposurePlan (desired/acquired/accepted, references template by Id)

ExposureTemplate (filter + camera config; shared across targets)
```

### Project schema (from `Project.cs`)

EF Code-First. DB columns lowercase, exposed via `[NotMapped]` PascalCase properties.

| DB column | .NET property | Type | Notes |
|---|---|---|---|
| `Id` | `Id` | int | PK |
| `ProfileId` | `ProfileId` | string (required) | NINA profile GUID |
| `name` | `Name` | string | |
| `description` | `Description` | string | |
| `state_col` | `State` (enum) | int → ProjectState | 0=Draft, 1=Active, 2=Inactive, 3=Closed |
| `priority_col` | `Priority` (enum) | int → ProjectPriority | 0=Low, 1=Normal, 2=High |
| `createDate/activeDate/inactiveDate` | `CreateDate/...` | long Unix sec → DateTime | |
| `isMosaic` | `IsMosaic` | int 0/1 → bool | |
| `flatsHandling` | `FlatsHandling` | int | 0=Off, 100=TargetCompletion, 200=Immediate |
| `minimumTime` | `MinimumTime` | int (minutes) | default 30 |
| `minimumAltitude` | `MinimumAltitude` | double (deg) | |
| `useCustomHorizon` | `UseCustomHorizon` | int 0/1 → bool | |
| `horizonOffset` | `HorizonOffset` | double | |
| `meridianWindow` | `MeridianWindow` | int (minutes) | 0 = disabled |
| `filterSwitchFrequency` | `FilterSwitchFrequency` | int | 0=sequential, 1=alternate, N=batch |
| `ditherEvery` | `DitherEvery` | int | 0 = disabled |
| `enableGrader` | `EnableGrader` | int 0/1 → bool | default true |

### Target schema

| Field | Type | Notes |
|---|---|---|
| `Id` | int | PK |
| `projectid` | int | FK |
| `guid` | string | |
| `name` | string | mosaic panels include "Panel N (RxCy)" suffix |
| `enabled` | int 0/1 → bool | |
| `ra` | double | **HOURS** (not degrees) |
| `dec` | double | degrees |
| `epoch` | int | 0 = J2000 |
| `rotation` | double | degrees |
| `roi` | double | 0-100, default 100 |

### ExposurePlan schema

| Field | Type | Notes |
|---|---|---|
| `Id` | int | PK; must be unique within an export |
| `targetid` | int | FK |
| `exposureTemplateId` | int | FK |
| `guid` | string | |
| `exposure` | double seconds | sub-exposure time |
| `desired` | int | target count of subs |
| `acquired` | int | progress (live during imaging) |
| `accepted` | int | post-grader count |
| `enabled` | int 0/1 → bool | |

### ExposureTemplate schema

| Field | Type | Notes |
|---|---|---|
| `Id` | int | PK |
| `guid` | string | |
| `name` | string | e.g. `"Ha (ZWO ASI2600MM Pro)"` |
| `filtername` | string | must match filter wheel position |
| `defaultexposure` | double seconds | |
| `gain/offset/readoutmode` | int | -1 = camera default |
| `bin` | int | lowercase even in JSON |
| `twilightlevel` | int | 0=Day, 1=Astronomical, 2=Nautical, 3=Civil, 4=Night |
| `minutesOffset` | int | adjustment to twilight time |
| Moon-avoidance fields | various | enabled, separation, width, relax scale/min/max alt, down-enabled |
| `ditherEvery` | int | 0 = use project default |
| `maximumHumidity` | double | 0 = disabled |

### TS's plugin communication surface

TS uses NINA's plugin pub/sub system with `MessageSenderId =
B4541BA9-7B07-4D71-B8E1-6C73D4933EA0`. **Only outbound publishers**, no inbound
"refresh from disk" topic.

**Topics our plugin can subscribe to:**
- `TargetScheduler-WaitStart` (v2) — planner returned a wait period
- `TargetScheduler-NewTargetStart` (v2) — first/new target this session
- `TargetScheduler-TargetStart` (v2) — every target start (includes exposure metadata)
- `TargetScheduler-ContainerStopped` (v1) — TS container instruction ended
- `TargetScheduler-TargetComplete` (v1) — all exposure plans at 100%

We can subscribe natively from a C# NINA plugin. Event-driven live progress.

### TS's read-only REST API

`http://localhost:{port}/ts/v0/{request}`:

- `/version`
- `/profiles`
- `/profiles/{id}/projects`
- `/projects/{id}/targets`
- `/targets/{id}/statistics` — HFR, FWHM, eccentricity per filter
- `/profiles/{id}/preview` — scheduler preview for current date

**Disabled by default**, must be enabled in profile API preferences. No auth, no
encryption — explicit caveat from the docs. Path includes `v0` because the API will
"almost certainly change rapidly."

For our plugin: useful for diagnostics and as a backup progress source. But not
load-bearing for v1.0 — pub/sub events + our existing `/sync-acquired` are sufficient.

### TS database concurrency (the open question)

TS docs are silent on external writers. Empirically, TS reads its DB:
- When a Target Scheduler Container in a NINA sequence starts (full reload)
- During UI navigation in TS's Project/Target views
- Between scheduling decisions during a container run (re-queries for the next plan)

The existing Python `nina_ts_sync` extension uses `BEGIN IMMEDIATE` transactions
with a pre-write backup, schema-version pinning, and deterministic GUIDs to upsert.
This works because the user's workflow is **plan → sync → image** (writes happen
before TS reads). For mid-imaging writes, TS would need to be restarted or its UI
navigated to pick up changes — not a real issue for the documented push flow.

## The existing Python extension: `nina_ts_sync`

Lives at `%APPDATA%\acp\extensions\nina_ts_sync\` (private — not in the public repo).
Mounted at `/api/ext/nina-ts-sync/` by the Flask host.

### Architecture

| File | Responsibility |
|---|---|
| `db.py` | Connection layer: `open_connection`, `assert_supported_version`, `write_transaction` (BEGIN IMMEDIATE + backup_db + rollback on exception). `SUPPORTED_USER_VERSIONS = (23,)` — fail closed on schema bumps. |
| `schema.py` | Deterministic GUID generators: `project_guid(profile_id, project_name)`, `target_guid(profile_id, project_name, target_name)`, `exposure_plan_guid(profile_id, target_guid, filter_name)`. These are what make upserts work — same logical entity → same GUID → match on retry. |
| `convert.py` | Builds `SyncPayload` from ACP plans + gear (strictest-wins reconciliation, mosaic expansion). |
| `reader.py` | Reads TS state into snapshot objects (`read_all`, `read_acquired`). |
| `upsert.py` | The actual INSERT/UPDATE per table, in FK-safe order. `apply(conn, payload) → SyncReport`. Has matching `preview(conn, payload)` that counts without writing. |
| `from_ts.py` | Reads TS DB back into ACP plan shape, including mosaic grid detection and panel-suffix stripping. |
| `diff.py` | Three-way diff: BASE (last snapshot) vs ACP (local) vs TS (remote). |
| `merge.py` | Applies pull/keep decisions per plan; mosaic sibling-panel handling. |
| `state.py` | Stamps `ts_refs` (TS Ids) and `ts_base_snapshot` (frozen TS state at last sync) onto plans. |
| `paths.py` | DB path resolution (env override + OS default + NINA profile name lookup). |
| `api.py` | Flask blueprint exposing the HTTP surface. |

### HTTP surface (already mounted)

| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | discovered DB path + schema version compatibility |
| POST | `/preview` | dry-run push, returns counts and per-plan diff |
| POST | `/sync` | push ACP → TS (BEGIN IMMEDIATE, backup, upsert, stamp plans) |
| POST | `/import/preview` | dry-run pull, returns counts and sample |
| POST | `/import/apply` | bootstrap import (replaces plans.json wholesale; requires `overwrite=true`) |
| POST | `/import/diff` | three-way diff for conflict resolution UI |
| POST | `/import/resolve` | apply per-plan decisions |
| POST | `/sync-acquired` | light progress poll: refresh `actual_hours` from TS into ACP |
| GET | `/profiles` | list NINA profiles found in DB |
| GET/POST | `/config` | persist active profile_id |

### Safety mechanisms already in place

1. **Schema version pinning** — `SUPPORTED_USER_VERSIONS = (23,)`. Refuses to write
   on any other version. Read paths return an error payload, write paths raise
   `SchemaVersionError` which the API converts to HTTP 409.
2. **Pre-write backup** — `backup_db()` copies the sqlite to a sibling
   `*-acpsync-<utc>-backup.sqlite` before any transaction. Always.
3. **`BEGIN IMMEDIATE` transactions** — acquire write lock at transaction start
   rather than at first write. Avoids "database is locked" mid-transaction.
4. **`isolation_level=None`** — manual transaction control (not Python's autocommit).
5. **Deterministic GUIDs** — same logical entity hashes to same GUID across runs,
   so retries upsert cleanly instead of creating duplicates.
6. **FK enforcement disabled** — matches the TS plugin's own behaviour to avoid
   order-dependent insert failures.
7. **plans.json backup** — `*-acpsync-<utc>-backup.json` on every plans.json write.
   Already visible in `data/` (40+ backups from the 2026-05-11 live walkthrough).

## Revised plugin architecture

**Original sketch (now superseded):** plugin reimplements TS upsert in C#, ACP becomes location-independent.

**Revised:** plugin is a thin NINA-side UX layer over the existing Python extension. ACP and NINA run on the same machine (per `remote-edit-architecture` memory: imaging PC NUC). The extension's HTTP surface is the integration contract.

### Phasing

| Phase | Plugin role | What it calls |
|---|---|---|
| **v1.0** | Framing push + manual TS sync button | Direct `IFramingAssistantVM` for Framing; POST `localhost:5555/api/ext/nina-ts-sync/sync` for TS push |
| **v1.1** | Live progress dock during imaging | Subscribe to TS pub/sub events (`TargetStart`, `TargetComplete`); POST `/api/ext/nina-ts-sync/sync-acquired` to update ACP |
| **v1.2** | Pull / conflict-resolution UI in NINA | POST `/api/ext/nina-ts-sync/import/diff`; render conflicts in NINA dock; POST `/import/resolve` with user's decisions |

### Why this is better

- **Zero algorithm port.** The 1000+ lines of tested Python (deterministic GUIDs, three-way merge, mosaic detection, schema version pinning, transactional writes) keep running unchanged. No risk of behavioural drift between two implementations.
- **Plugin is genuinely thin.** Pure UI + HTTP client + Framing API + pub/sub subscriber. No EF, no transactions, no diff/merge logic. Realistic C# scope.
- **One place to track TS schema compatibility.** Bump `SUPPORTED_USER_VERSIONS` in Python; plugin doesn't care about TS schema at all.
- **Coordination with tcpalmer is simpler.** Ask for early notice of schema-version bumps so we can test the Python extension; nothing else needs to coordinate.

### Constraints this introduces

- ACP must run on the same machine as NINA. Already the target architecture per memory.
- Plugin's TS features are useless without ACP running locally. Plugin should detect ACP availability on startup and degrade gracefully (Framing push still works without ACP — just won't have plans to push).

### Future port to C# (deferred)

If we ever want ACP to run on a separate machine from NINA (NAS-hosted, brutix-hosted), we'd port the Python `nina_ts_sync` to C# at that point. By then we have operational experience with the algorithm and a clear contract (the HTTP surface above). Not a v1.x decision.

## Cross-cutting takeaways

1. **Use `IFramingAssistantVM.SetCoordinates()` directly via `AdvancedAPI.Controls.FramingAssistant`.** No reflection.
2. **TS handoff stays in Python.** The plugin POSTs to the existing extension's `/sync`, `/import/diff`, `/import/resolve`, `/sync-acquired`. C# plugin never opens the sqlite directly.
3. **Live progress is event-driven, not polled.** Subscribe to TS's pub/sub topics (`TargetStart`, `TargetComplete`). Fall back to polling `/sync-acquired` if subscription isn't available.
4. **TS REST API at `localhost:{port}/ts/v0/`** is a useful backup data source but not load-bearing for our design.
5. **Mirror NINA's rotation convention** at the Framing boundary: `Rectangle.TotalRotation = 360 - rotationDeg`. Verify our TS export's `Rotation` field uses the matching convention before v1.0.
6. **Schema version pinning is the right safety mechanism.** Don't add a "force write" override. Force users to update the extension when TS bumps schema.
