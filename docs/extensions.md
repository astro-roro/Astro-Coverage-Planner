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

Two optional extras make tiles carry more than coverage:

- `fill_opacity` in the source's `metadata()` (a number from 0 to 1). When set, ACP draws that source's footprints filled at that opacity, in each tile's usual colour. When absent, or not a number in range, footprints stay as they were. The value is reported as `fill_opacity` by `GET /api/tile-sources` (`null` when unset).
- `plans` in a tile's `metadata`, a list of `{"plan_id": str | null, "name": str, "detail": str}`. The tile panel shows a Plans list. A name is a link when `plan_id` is set and matches a plan ACP has loaded (by plan `id` or `guid`), and clicking it switches to Planning mode and opens that plan. With `plan_id` null, or an id ACP doesn't know, the name is plain text. `detail` appears as muted text under the name. All of it is escaped. Other metadata keys are not displayed, as before.

- `style` in a tile's `metadata`, an object that overrides how that one tile is drawn. `color` is a hex colour (`#rgb` or `#rrggbb`) used in place of the colour ACP picks from the facet or priority. `fill_opacity` is a number from 0 to 1 that replaces the source's `fill_opacity` for this tile, and `null` draws the tile as an outline even when the source fills its tiles. `line_width` is a number from 0.5 to 6 (the default is 0.5). Each key is checked on its own, and one that is missing or invalid is ignored.
- A footprint can be a line: list the vertices out along the path and back the same way (three or more vertices). ACP does not check polygon area or winding, so it draws the polygon as given. On the all-sky projections (Aitoff and Mollweide) ACP cuts any footprint that crosses the map's seam (the line where the map's left and right edges meet, which turns as the view is moved and rolled) into one part per side, so footprints near it are not drawn across the whole map; lines, and tiles drawn as outlines only, are cut into separate open lines. Clicks and hover still belong to the original tile. A zero-area footprint can't be clicked on the map, because the click test needs an inside, but its hover text still shows.
- `panel_html` in a tile's `metadata` (a string). ACP puts it in the tile panel inside a container with the class `ext-tile-panel`, under the title. It is inserted as written, because extensions are trusted server-side code, so escape any user-supplied text yourself. ACP styles links inside that container with its own link colour. Prefix your own CSS classes (for example `nights-`) so they do not clash with ACP's.
- `tile_panel` in the source's `metadata()`, either `"replace"` or `"append"`. With `"replace"`, tiles of that source show only the Back link, the title and `panel_html`: ACP leaves out the Position table, facet chips, per-band coverage, the Plans list, the bands note and the "Create new plan from this tile" button. With `"append"`, or when unset, `panel_html` follows the standard sections. Any other value counts as unset. `GET /api/tile-sources` reports it as `tile_panel` (`null` when unset).
- The tile panel title is the tile `id`, with the source label as a small muted line above it.

Elements inside `panel_html` can trigger ACP actions through data attributes. Clicking the element (or anything inside it) runs the action.

- `data-acp-plan-id="<id>"` opens the loaded plan whose `id` or `guid` matches, as the Plans list links do: it switches to Planning, pans the map and opens the editor. If no loaded plan matches, a short note appears at the top of the panel and nothing else happens.
- `data-acp-new-plan` with `data-ra` and `data-dec` in degrees, and an optional `data-name`, opens the same new-plan editor that "Create new plan from this tile" uses, centred on that position and named from `data-name`.
- `data-acp-goto` with `data-ra` and `data-dec` in degrees, and an optional `data-fov` in degrees, pans the sky map there and sets the field of view when `data-fov` is given.

- `data-acp-post="<path>"` with an optional `data-acp-body='<json>'` POSTs that JSON body (`{}` when absent) to the path as `application/json`. The path must be a same-origin relative path under `/api/ext/`, with no host, `..` or `.` segments, backslashes or encoded dots and slashes. On a 2xx reply ACP refreshes tile sources and tiles as it does after an extension Apply, redraws the map, re-renders the open tile panel and sends `acp:layers` to the rail iframes. On any other status a short note with the status appears at the top of the panel. A bad path or a body that is not valid JSON sends nothing and shows a note.

An action with a missing or invalid `data-ra` or `data-dec` (not a number, or a declination outside -90 to 90) does nothing.

- `status` in the source's `metadata()`. Return `"loading"` while the source is still starting up and ACP lists it as loading and asks again (see below). Any other value is ignored.
- `extension` in the source's `metadata()`, the id of the extension that owns the source. When absent, ACP takes it from the module the source class was loaded from. It only decides which extension a source is listed under in the Extensions block.

`GET /api/tile-sources` lists every registered source, including ones that fail. Each entry has a `status` of `"ready"`, `"loading"`, `"empty"` (ready with no tiles) or `"error"`, plus `error` (a short message, or `null`) and `extension`. A source whose `id()`, `metadata()` or `tiles()` raises is reported as `"error"` with the exception text instead of being left out.

The Inventory rail has an Extensions block that shows each extension and the state of its tile sources, with a Retry button on errors. When everything has loaded it shrinks to one line, such as "Extensions: 2 loaded". While any source is loading or in error, the page asks for the summary again after 2, 4, 8, 8 and 8 seconds, then stops. A source that becomes ready is drawn if it is switched on.

After any extension action's apply step succeeds, ACP refetches tile sources and redraws the enabled ones, so the map doesn't show layers from before the change. ACP doesn't know which extension owns which source, so it refreshes all of them.

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
- `preview_hint: "..."` — optional plain-text banner shown above the preview body. Use it for "what the user should know before they look at the preview" — e.g. timing constraints, side effects, retry behaviour. No HTML — it's escaped on render.
- `input_schema: [...]` — when set, the modal collects user input *before* calling the preview/apply endpoints. Each entry is `{name, type, label, default, required, min, max, options}`; collected values are spread into the JSON payload alongside `profile_id` (if any). See [Input schema](#input-schema) below.
- `pull_diff_endpoint: "..."` + `pull_apply_endpoint: "..."` — when both are set, the button switches to the bidirectional flow: the modal calls `preview_endpoint` and `pull_diff_endpoint` in parallel and shows Outgoing / Incoming / Conflicts / New / Notes sections, with per-item radio pickers for incoming changes. Apply calls `pull_apply_endpoint` first (with the user's decisions) then `endpoint`.
- `needs: ["profile_id"]` — config keys the action needs. On first use the frontend prompts via a profile-picker step (using the extension's `config_endpoint` + `profiles_endpoint`, see below). Subsequent uses skip straight to the action.

#### Input schema

Use `input_schema` when your button needs runtime parameters from the user (tile count, project name, per-filter hours, a toggle, etc.). The frontend renders one labelled field per schema entry, validates client-side, and only POSTs to the preview endpoint once all required fields are filled.

Each entry is a dict:

| Key        | Type                              | Notes |
| ---------- | --------------------------------- | ----- |
| `name`     | str                               | Payload key. Flat — extensions reshape nested structures server-side. |
| `type`     | `"string"` / `"int"` / `"float"` / `"bool"` / `"select"` | Required. |
| `label`    | str                               | User-visible field label. Defaults to `name`. |
| `default`  | str / number / bool               | Prefills the field. String defaults support `{today}` / `{tomorrow}` substitution. |
| `required` | bool                              | Default `false`. Required fields block Continue when empty. |
| `min`/`max`| number                            | For `int` / `float`. Enforced client-side. |
| `options`  | list of `{value, label}`          | For `select`. |
| `help`     | str                               | Optional grey helper text shown beneath the field. |

The frontend remembers the last-submitted values per `(extension, action)` in localStorage and prefills on next open, so iterating on a long form is cheap.

**Example** — a hypothetical batch-renamer extension:

```python
manifest.append({
    "extension": "batch_renamer",
    "name": "Batch renamer",
    "actions": [{
        "id": "rename",
        "kind": "button",
        "label": "Rename plans",
        "endpoint": "/api/ext/batch-renamer/apply",
        "preview_endpoint": "/api/ext/batch-renamer/preview",
        "preview_hint": "Renames every plan matching the prefix below. Preview first.",
        "input_schema": [
            {"name": "prefix",  "type": "string", "label": "Old prefix", "required": True},
            {"name": "replace", "type": "string", "label": "New prefix", "required": True,
             "default": "Survey {today}"},
            {"name": "dry_run", "type": "bool",   "label": "Dry run (no writes)", "default": True},
        ],
    }],
})
```

When the user clicks the button the modal shows three fields, the Continue button stays disabled until both string fields have values, and the payload posted to `/preview` is `{prefix, replace, dry_run}`.

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

### Rail tabs

An entry can add its own tab to the right-hand rail, next to Plans, Filters and Library, by declaring `rail_tabs`:

```python
manifest.append({
    "extension": "my_workflow",
    "name": "My Workflow",
    "rail_tabs": [
        {"id": "overview", "label": "Overview", "icon": "calendar",
         "page": "/api/ext/my-workflow/ui"},
    ],
    "actions": [...],
})
```

- `id` is a short slug (letters, digits, `_`, `-`, up to 40 characters), unique within the extension.
- `label` is the text under the rail icon and the panel title.
- `icon` is optional. Known names are `list`, `plans`, `targets`, `filter`, `filters`, `library`, `catalogue`, `catalog`, `layers`, `activity`, `pulse`, `chart` and `calendar`. Anything else gets a generic icon.
- `page` is the page to show. It must be a relative path that starts with `/api/ext/<prefix>`, where the prefix is the entry's `extension` value, or the same value with underscores written as hyphens (`my_workflow` may use `/api/ext/my_workflow/` or `/api/ext/my-workflow/`). The path must be that prefix or continue after a `/`, and it can carry a query string. Full URLs, `//host/...` paths, `..` segments and encoded slashes or dots are rejected.

ACP shows the page in a same-origin iframe that fills the panel, so the browser's session cookie authenticates it exactly as it does for the rest of ACP. The iframe is created the first time the tab is opened and kept afterwards. A tab that fails validation is dropped and a warning is logged once on the `acp.extensions` logger. The other tabs and the rest of the entry are unaffected.

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
- **UI workflows** — register manifest actions to surface custom buttons or auto-pollers in the planning rail, or to swap a core button for an extension-supplied one (e.g. replacing the zip-export sync with a direct-DB-write sync, with a preview-and-confirm modal). See [UI manifest](#ui-manifest) above.

If your extension might be useful to others, consider opening a PR to ship it as a built-in feature rather than keeping it private.

## Trust, failures, debugging

**Trust.** Extensions run in-process as your user with no sandbox, so only load code you've read or written yourself. Treat the extensions directory like any other code-execution surface.

**Failures.** Load and registration errors are logged via Python's `logging` under the `acp.extensions` logger — check there if an extension silently doesn't show up. The rest of the app keeps running regardless.

**Routes outside `/api/`.** An extension can mount a route anywhere, including beside the pages. The loader logs a warning at startup naming any route it added outside `/api/`, because the setting an operator reasons about is called `ACP_API_TOKEN` and it would be easy to read the gate as covering the API alone. It does not: with a token set, every path is behind it. With no token set, every path answers anyone who can reach the port, and a route called `/backdoor` is no different from the coverage page. Keep extension routes under `/api/ext/<name>/` unless you have a reason not to.

**Debugging.** Set `LOG_LEVEL=DEBUG` to see the registration sequence on startup; each Protocol implementation gets a one-line confirmation when it's accepted into the registry.


## Rail tab width and the layer bridge

A rail tab can be widened with the arrows button in its panel header. Wide is about three times the normal panel width, held to 70% of the window, and full width on a phone. ACP remembers the choice for each tab in the browser. A tab in `rail_tabs` may set `"width": "wide"` to start wide until the user picks otherwise; `"normal"` is the default and any other value is ignored.

A rail tab page can read and change the map's tile layers and time-aware mode with `window.postMessage`. ACP accepts a message only if its origin is ACP's own and it came from one of ACP's rail iframes. Messages that are malformed are ignored without a reply. Send to `window.parent` with `window.location.origin` as the target origin.

- `{type: "acp:layers:get"}` asks for the current state. ACP replies to that iframe.
- `{type: "acp:layer:set", id: "<source id>", enabled: true | false}` switches a tile source on or off exactly as its rail checkbox does, including remembering it. Sources that are loading or in error are ignored.
- `{type: "acp:time-aware:set", enabled: true | false}` switches time-aware mode as the existing control does.

- `{type: "acp:tiles:refresh"}` makes ACP refresh tile sources and tiles, as after an Apply. Use it when the page has changed something server-side by its own means.

ACP sends `{type: "acp:layers", timeAware: bool, layers: [{id, label, enabled, color, kind, extension}]}` in reply to a get, and to every open rail iframe whenever a layer or time-aware mode changes, whoever changed it. `kind` is always `"tiles"`. Only tile sources are included: the built-in map overlays (catalogues, survey layers and the like) are toggled through a different mechanism and are not part of the bridge.

```js
window.addEventListener("message", e => {
  if (e.origin !== location.origin || e.data?.type !== "acp:layers") return;
  render(e.data.layers, e.data.timeAware);
});
window.parent.postMessage({type: "acp:layers:get"}, location.origin);
```
