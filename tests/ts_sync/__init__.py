"""Tests for the bundled Target Scheduler sync extension.

The extension lives in ``bundled_extensions/nina_ts_sync`` and ACP loads it by
file path, so nothing puts it on ``sys.path`` by name. These tests import it as
``nina_ts_sync``, so the folder holding it goes on the path here, which covers
both ``python -m unittest discover -s tests`` and pytest.
"""
import sys
from pathlib import Path

_BUNDLED = str(Path(__file__).resolve().parent.parent.parent / "bundled_extensions")
if _BUNDLED not in sys.path:
    sys.path.insert(0, _BUNDLED)
