# Extensions

Coverage planning often needs to reach into something ACP doesn't ship — a friend's shared FOV list, a club's target catalogue, a pipeline DB you keep alongside your archive, a curated tile inventory from another tool. Rather than fork the app, drop a Python module into a known directory and ACP will load it at startup.

There are three ways an extension can plug in:

1. **Flask routes** — register a Blueprint to expose new HTTP endpoints (cheap, flexible, fits any custom workflow).
2. **Plugin protocols** — implement one of the three Protocols in `sources.py` to surface data through ACP's existing UI (Sources rail, Inventory rail, Catalogues rail) without writing any frontend code.
3. **UI manifest** — append an entry to `app.extensions_manifest` describing buttons + toggles. Core ACP renders them in the Extensions rail accordion and can swap a core button for an extension-supplied one (`replaces:`). No frontend code needed.

- [Where extensions go](#where-extensions-go)
- [The contract](#the-contract)
- [Flask-route example](#flask-route-example-friends-fov-list-as-csv)
- [Plugin protocols](#plugin-protocols)
- [UI manifest](#ui-manifest)
- [What kinds of things make sense as extensions](#what-kinds-of-things-make-sense-as-extensions)
- [Trust, failures, debugging](#trust-failures-debugging)

## Where extensions go

`ACP_EXTENSIONS_DIR`, defaulting to:

- **Windows**: `%APPDATA%\acp\extensions`
- **macOS / Linux**: `~/.config/acp/extensions`

Each entry is either a single `.py` file or a package directory.

## The contract

Each extension defines a top-level `register(app)` callable that receives the Flask app. From there you can register blueprints, append to the registries, read `app.config`, attach teardown hooks — anything Flask supports plus the plugin registries below.

Extensions are loaded once at startup; failures in one are logged and don't block the others or the app itself.

## Flask-route example: friend's FOV list as CSV

The simplest pattern — register a Blueprint that exposes a new endpoint. Save as `friend_fovs.py` in your extensions dir:

```python
import json, csv, io
from pathlib import Path
from flask import Blueprint, Response

bp = Blueprint("friend_fovs", __name__)
SHARED = Path.home() / "shared" / "friend_fovs.json"

@bp.route("/api/ext/friend-fovs.csv")
def friend_fovs_csv():
    rows = json.loads(SHARED.read_text())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["name", "ra", "dec", "fov_arcmin"])
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv")

def register(app):
    app.register_blueprint(bp)
```

Drop it in, restart ACP, hit `http://127.0.0.1:5555/api/ext/friend-fovs.csv`.

## Plugin protocols

Three structural types in `sources.py` let you publish data into ACP's UI without writing frontend code. ACP uses [PEP 544 Protocols](https://peps.python.org/pep-0544/) — your class doesn't need to inherit from anything; if it has the right methods, it's compatible.

### `CoverageSource` — coverage layers in the Sources rail

For anything that publishes per-region polygon or MOC coverage with per-filter integration hours: your own manifest, friend manifests, public-survey footprints, custom processing pipelines.

```python
from sources import CoverageSource, SourceMetadata

class MyArchive:
    def id(self) -> str:
        return "my_archive"

    def metadata(self) -> SourceMetadata:
        return {"label": "My Archive", "color": "#7aa", "kind": "manifest",
                "attribution": "private", "enabled_default": True}

    def coverage(self):
        for fov in load_my_fovs():
            yield {
                "kind": "polygon",
                "vertices": fov["corners"],
                "filters": {"Ha": {"hours": 3.2, "files": 12}},
                "name": fov["name"],
                "metadata": {},
            }

    def coverage_moc(self, filter_name):
        return None  # opt out of fast-path; gap-finder will derive one

def register(app):
    app.coverage_sources.append(MyArchive())
```

Yields appear in the Sources rail with their own toggle, contribute to the gap-finder's "Use sources" list, and get shown on the map alongside everything else.

### `PrioritisedTilesSource` — curated cells in the Inventory rail

For ranked sky cells with per-band coverage status — when an upstream tool has done the prioritisation work and ACP just needs to render it.

```python
from sources import PrioritisedTilesSource, TileCell, SourceMetadata

class CuratedTiles:
    def id(self) -> str: return "my_inventory"
    def metadata(self) -> SourceMetadata:
        return {"label": "Curated PNe", "color": "#c80", "kind": "inventory",
                "attribution": "Local curation", "enabled_default": False}
    def tiles(self):
        for cell in my_cells:
            yield {
                "id": cell["id"],
                "ra_deg": cell["ra"], "dec_deg": cell["dec"],
                "footprint": cell["polygon"],
                "priority_level": cell["priority"],
                "score": cell["score"],
                "per_band": {"Ha": {"covered": True, "hours": 2.4, "quality": 4}},
                "category_counts": {"PNe": 3},
                "metadata": {"notes": "..."},
            }

def register(app):
    app.tile_sources.append(CuratedTiles())
```

`priority_level` is an ordinal — 1 is the most urgent — driving the colour bucket on the map. The Inventory rail accordion only appears when at least one tile source is registered, so a stock checkout shows nothing.

### `CategorisedCatalogSource` — point catalogues with category chips

For class-tagged point objects (e.g. "PNe", "HII", "SNR") — gives you the same chip-filter UI the built-in Messier/Sharpless/etc. catalogues get.

```python
from sources import CategorisedCatalogSource

class MyClubCatalog:
    def id(self) -> str: return "my_club"
    def metadata(self): ...
    def categories(self): return ["PNe", "HII", "Other"]
    def objects(self):
        for obj in club_targets:
            yield {"name": obj["name"], "ra_deg": obj["ra"],
                   "dec_deg": obj["dec"], "category": obj["class"],
                   "metadata": {}}

def register(app):
    app.catalog_sources.append(MyClubCatalog())
```

### Declarative catalogues (no Python required)

If you just want to surface an existing catalogue file in the rail without writing code, append an entry to `data/catalog_registry.json`. The full Protocol is only needed when you have logic to run (filtering, derivation, network fetch, etc.).

## UI manifest

Plugin protocols cover *data*. The UI manifest covers *actions* — buttons and toggles that show up in the planning rail (or replace a core button in place). Core ACP knows nothing about the specific actions; it just renders whatever extensions register.

Append a dict to `app.extensions_manifest` from your `register(app)` callback. Minimal example:

```python
def register(app):
    app.extensions_manifest.append({
        "extension": "my_workflow",
        "name": "My Workflow",
        "actions": [
            {
                "id": "ping",
                "kind": "button",
                "label": "Ping pipeline",
                "endpoint": "/api/ext/my-workflow/ping",
                "method": "POST",
            }
        ],
    })
```

The frontend fetches `/api/extensions/manifest` on page load, renders the actions, and wires each one to its endpoint.

### Action kinds

**`"button"`** — user-clicked one-shot. Calls `endpoint` with `method`. Optional fields:
- `replaces: "<core-button-id>"` — swap a core button in place rather than adding a new one. Currently supported: `"sync-to-nina"` (the existing zip-export "Sync to NINA" button in the planner toolbar). The extension's label takes over when the extension is loaded; the original behaviour returns when the extension is removed.
- `preview_endpoint: "..."` — when set, the button opens a modal that calls `preview_endpoint` first and shows the response in a plan-grouped diff before the user confirms. Useful for any action where "here's what I'm about to do" should be reviewable.
- `pull_diff_endpoint: "..."` + `pull_apply_endpoint: "..."` — when both are set, the button switches to the bidirectional flow: the modal calls `preview_endpoint` and `pull_diff_endpoint` in parallel and shows Outgoing / Incoming / Conflicts / New / Notes sections, with per-item radio pickers for incoming changes. Apply calls `pull_apply_endpoint` first (with the user's decisions) then `endpoint`.
- `needs: ["profile_id"]` — config keys the action needs. On first use the frontend prompts via a profile-picker step (using the extension's `config_endpoint` + `profiles_endpoint`, see below). Subsequent uses skip straight to the action.

**`"toggle"`** — opt-in checkbox that auto-runs a background poll. Required fields:
- `interval_s` — poll interval in seconds.
- `endpoint`, `method`, `needs` — same shape as button.
- `max_consecutive_failures` — optional, defaults to 3. After this many failures the status flips red and polling stops until the user clicks Retry.

Toggle state persists across reloads. The frontend repaints the rail and any open plan-detail panel after each successful tick if the response indicates anything changed.

### Per-extension config (the `profile_id` pattern)

Extensions that need a small persisted setting can register top-level endpoints in the manifest:

```python
manifest.append({
    "extension": "my_workflow",
    "name": "My Workflow",
    "config_endpoint": "/api/ext/my-workflow/config",
    "profiles_endpoint": "/api/ext/my-workflow/profiles",
    "actions": [...],
})
```

- `config_endpoint` (GET/POST) — read and write the config dict. POST body is shallow-merged into the persisted state.
- `profiles_endpoint` (GET) — list selectable options for the picker. Response shape: `{"profiles": [{"profile_id": "...", "project_count": N, "sample_projects": [...]}]}`.

The frontend handles the picker UI; you just expose the routes.

### Example: nina_ts_sync

The `acp-nina-ts-sync` extension uses all of the above:
- A `"button"` action with `replaces: "sync-to-nina"` swaps the core Manual Sync button for **Sync with NINA**.
- The same button declares `pull_diff_endpoint` so clicking it opens the bidirectional modal (Outgoing / Incoming / Conflicts / New / Notes) instead of a simple push-preview.
- A separate `"toggle"` action surfaces **Live progress from NINA** with `interval_s: 60`.
- Both actions declare `needs: ["profile_id"]`; the config + profiles endpoints drive the first-time picker.

Source: <https://github.com/astro-roro/Astro-Coverage-Planner> (the extension itself is a separate repo).

## What kinds of things make sense as extensions

A few patterns that fit the model well:

- **Custom coverage sources** — a pipeline DB, an alternative manifest format, a different friend-sharing protocol.
- **Curated inventory tiles** — output from another planning tool published as a ranked cell list.
- **Club / community catalogues** — render a target list maintained outside ACP as a chip-filtered overlay.
- **Workflow integrations** — push session plans to a planning service ACP doesn't natively support (e.g. Voyager, SGP) by reading from `/api/plans` and writing whatever format the target tool expects.
- **One-off CSV exports** — anything where you want to grab data through ACP's API and reshape it for another tool.

If your extension might be useful to others, consider opening a PR to ship it as a built-in feature rather than keeping it private.

## Trust, failures, debugging

**Trust.** Extensions run in-process as your user with no sandbox, so only load code you've read or written yourself. Treat the extensions directory like any other code-execution surface.

**Failures.** Load and registration errors are logged via Python's `logging` under the `acp.extensions` logger — check there if an extension silently doesn't show up. The rest of the app keeps running regardless.

**Debugging.** Set `LOG_LEVEL=DEBUG` to see the registration sequence on startup; each Protocol implementation gets a one-line confirmation when it's accepted into the registry.
