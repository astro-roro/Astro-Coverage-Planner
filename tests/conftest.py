"""Keep an import of `app` from migrating the developer's own data/gear.json.

app.migrate_gear_at_startup() runs when the module is imported; tests point
GEAR_PATH at a temp file afterwards, so the default path would be the real
one. The migration itself is tested through the lazy path.
"""
import os

os.environ.setdefault("ACP_SKIP_STARTUP_MIGRATION", "1")
