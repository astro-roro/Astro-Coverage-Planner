"""ACP extension: write directly into NINA Target Scheduler's SQLite DB.

Replaces the manual "Sync to NINA" zip-and-Import-Profile flow with a direct
upsert against `schedulerdb.sqlite`. Avoids the duplicate-on-resync bug in
TS's Import Profile (always mints fresh ExposureTemplate rows; no GUID dedup).

Strict version pinning: refuses to run unless `PRAGMA user_version` is in the
SUPPORTED_USER_VERSIONS allowlist. See README for the compat matrix.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

__version__ = "0.1.0-dev"


def register(app: "Flask") -> None:
    """Called by ACP's extension loader at startup."""
    from .api import blueprint
    # ACP's own routes that read, change and write plans.json (the AAC
    # /accepted write, progress, plan saves) hold the host's `plans_lock`.
    # Share it, so they and this extension's routes exclude one another.
    # A host that predates it leaves this extension's own lock in place.
    from . import import_merge
    host_lock = getattr(app, "plans_lock", None)
    if host_lock is not None:
        import_merge.PLANS_LOCK = host_lock
    # Importing upload_routes adds the upload and /links routes to the API
    # blueprint, so it has to happen before the blueprint is registered.
    from . import upload_routes, uploads
    app.register_blueprint(blueprint)
    app.register_blueprint(upload_routes.pages)

    # Uploads older than their lifetime go now, and the worker that reads
    # new ones starts.
    try:
        uploads.sweep()
    except OSError:
        pass
    uploads.start_worker()

    # Surface our user-facing actions on core ACP's UI. Core renders these
    # as buttons / toggles via /api/extensions/manifest; the ``replaces``
    # key on the "push" action swaps the existing zip-export button for our
    # direct-write equivalent. If core ACP is older and doesn't expose
    # ``app.extensions_manifest``, we silently no-op — the routes still work,
    # the user just won't see them in the UI.
    manifest = getattr(app, "extensions_manifest", None)
    if manifest is not None:
        manifest.append({
            "extension": "nina_ts_sync",
            "name": "NINA Target Scheduler (direct write)",
            "config_endpoint": "/api/ext/nina-ts-sync/config",
            "profiles_endpoint": "/api/ext/nina-ts-sync/profiles",
            "actions": [
                {
                    "id": "sync",
                    "kind": "button",
                    "label": "Sync with NINA",
                    "endpoint": "/api/ext/nina-ts-sync/sync",
                    "method": "POST",
                    "needs": ["profile_id"],
                    "replaces": "sync-to-nina",
                    "preview_endpoint": "/api/ext/nina-ts-sync/preview",
                    # Plain-text info banner shown above the preview body.
                    # Previously hardcoded in templates/index.html; moved
                    # here once core started honouring action.preview_hint.
                    "preview_hint": (
                        "Sync works while NINA is imaging. May take a "
                        "moment if TS is mid-write — we'll retry "
                        "automatically."
                    ),
                    "apply_label": "Sync",
                    "applying_label": "Syncing…",
                    "result_title": "✓ Sync complete",
                    # When these two are present the frontend opens the
                    # bidirectional modal: outgoing (ACP→TS) from /preview,
                    # incoming + conflicts (TS→ACP) from /import/diff. Apply
                    # then runs /sync then /import/resolve in sequence.
                    "pull_diff_endpoint": "/api/ext/nina-ts-sync/import/diff",
                    "pull_apply_endpoint": "/api/ext/nina-ts-sync/import/resolve",
                },
                {
                    "id": "live-progress",
                    "kind": "toggle",
                    "label": "Live progress from NINA",
                    "endpoint": "/api/ext/nina-ts-sync/sync-acquired",
                    "method": "POST",
                    "needs": ["profile_id"],
                    "interval_s": 60,
                    "max_consecutive_failures": 3,
                },
            ],
        })
