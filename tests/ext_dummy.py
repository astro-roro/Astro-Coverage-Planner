"""Load one of the dummy extensions in tests/dummy_extensions by name.

The dummies are ordinary extensions. Tests load them through the same import
the loader uses and call their register(app) on whatever app they pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extensions import _import_module_from_path  # noqa: E402

DUMMIES = Path(__file__).resolve().parent / "dummy_extensions"


def load_dummy(name: str):
    """Import tests/dummy_extensions/<name> as ``acp_ext_test_<name>``."""
    module = _import_module_from_path(DUMMIES / name, f"acp_ext_test_{name}")
    if module is None:
        raise FileNotFoundError(DUMMIES / name)
    return module
