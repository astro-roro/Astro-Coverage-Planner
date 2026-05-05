# Extensions

Coverage planning often needs to reach into something ACP doesn't ship — a friend's shared FOV list, a club's target catalogue, a pipeline DB you keep alongside your archive, a curated tile inventory from another tool. Rather than fork the app, drop a Python module into a known directory and ACP will load it at startup.

There are two ways an extension can plug in:

1. **Flask routes** — register a Blueprint to expose new HTTP endpoints (cheap, flexible, fits any custom workflow).
2. **Plugin protocols** — implement one of the three Protocols in `sources.py` to surface data through ACP's existing UI (Sources rail, Inventory rail, Catalogues rail) without writing any frontend code.

- [Where extensions go](#where-extensions-go)
- [The contract](#the-contract)
- [Flask-route example](#flask-route-example-friends-fov-list-as-csv)
- [Plugin protocols](#plugin-protocols)
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
