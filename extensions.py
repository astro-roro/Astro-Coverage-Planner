"""Extension loading for Astro Coverage Planner.

ACP can load out-of-tree Python extensions to add custom data sources,
overlays, archive bridges, etc. without modifying the core repo. Drop a `.py`
file or package directory into the configured extensions directory; if it
defines a top-level ``register(app)`` callable, it gets called once at
startup with the Flask app instance.

Configuration:
    ACP_EXTENSIONS_DIR  Directory to scan. Defaults to
                        ``%APPDATA%\\acp\\extensions`` on Windows or
                        ``~/.config/acp/extensions`` elsewhere.

Extensions are independent — a failure in one does not stop the others, and
nothing happens at all when the directory is absent (vanilla setup).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

__all__ = ["load_extensions", "default_extensions_dir"]

logger = logging.getLogger("acp.extensions")


def default_extensions_dir() -> Path:
    """Per-OS default location ACP scans when ``ACP_EXTENSIONS_DIR`` is unset."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "acp" / "extensions"
    return Path.home() / ".config" / "acp" / "extensions"


def _import_module_from_path(path: Path, module_name: str) -> ModuleType | None:
    """Import *path* (a ``.py`` file or package directory) under *module_name*.

    Cleans up ``sys.modules`` if execution fails, so partial imports never
    leak a half-initialised module under the chosen name.
    """
    if path.is_file() and path.suffix == ".py":
        spec = importlib.util.spec_from_file_location(module_name, path)
    elif path.is_dir() and (path / "__init__.py").is_file():
        spec = importlib.util.spec_from_file_location(
            module_name,
            path / "__init__.py",
            submodule_search_locations=[str(path)],
        )
    else:
        return None
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def load_extensions(app: "Flask") -> list[str]:
    """Discover and register extensions on *app*.

    Returns the names (directory entry name) of extensions that registered
    successfully. Names starting with ``.`` or ``_`` are skipped — this also
    skips ``__pycache__`` and any package an author intentionally hides.
    """
    raw = os.environ.get("ACP_EXTENSIONS_DIR", "").strip()
    ext_dir = Path(raw).expanduser() if raw else default_extensions_dir()

    if not ext_dir.is_dir():
        return []

    loaded: list[str] = []
    seen_names: set[str] = set()
    for path in sorted(ext_dir.iterdir()):
        if path.name.startswith((".", "_")):
            continue
        # Stem (without .py for files) determines the synthetic module name;
        # collisions between e.g. `foo.py` and `foo/` would silently shadow.
        stem = path.stem if path.is_file() else path.name
        module_name = f"acp_ext_{stem}"
        if module_name in seen_names:
            logger.warning(
                "Duplicate extension name %r — skipping %s", stem, path
            )
            continue
        seen_names.add(module_name)

        try:
            module = _import_module_from_path(path, module_name)
        except Exception:
            logger.exception("Failed to import extension: %s", path.name)
            continue
        if module is None:
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        try:
            register(app)
        except Exception:
            logger.exception("Extension %s register() raised", path.name)
            continue
        loaded.append(path.name)
        logger.info("Loaded extension: %s", path.name)

    return loaded
