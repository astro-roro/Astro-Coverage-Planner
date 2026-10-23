"""Keep tests away from the developer's own data/ directory.

app.migrate_gear_at_startup() runs when the module is imported; tests point
GEAR_PATH at a temp file afterwards, so the default path would be the real
one. The migration itself is tested through the lazy path.

The Activity store opens beside plans.json by default, and many tests save
plans without redirecting that. Point it at a throwaway path for the whole
session so no test can write activity.sqlite into data/.
"""
import atexit
import os
import shutil
import tempfile

os.environ.setdefault("ACP_SKIP_STARTUP_MIGRATION", "1")
# The bundled Target Scheduler sync would otherwise register its routes and
# start its upload worker inside every test that imports app. Its own tests
# (tests/ts_sync) drive it through a stand-in host instead.
os.environ.setdefault("ACP_DISABLE_BUNDLED_EXTENSIONS", "1")

_dir = tempfile.mkdtemp(prefix="acp-activity-tests-")
atexit.register(shutil.rmtree, _dir, ignore_errors=True)
os.environ["ACP_ACTIVITY_DB"] = os.path.join(_dir, "activity.sqlite")
