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
    ACP_DISABLE_BUNDLED_EXTENSIONS
                        Set to ``1`` to skip the extensions that ship inside
                        ACP (``bundled_extensions/``, currently the Target
                        Scheduler sync). They load by default, after the
                        user's directory, and a same-named entry there wins.

Extensions are independent — a failure in one does not stop the others, and
nothing happens at all when the directory is absent (vanilla setup).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

__all__ = ["load_extensions", "load_bundled_extensions", "default_extensions_dir",
           "sanitise_rail_tabs"]

logger = logging.getLogger("acp.extensions")

#: Extensions that ship inside ACP and load by default.
BUNDLED_EXTENSIONS_DIR = Path(__file__).resolve().parent / "bundled_extensions"
BUNDLED_DISABLE_ENV = "ACP_DISABLE_BUNDLED_EXTENSIONS"


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


def _rule_paths(app: "Flask") -> set[str]:
    return {str(rule) for rule in app.url_map.iter_rules()}


def routes_outside_api(before: set[str], after: set[str]) -> list[str]:
    """Routes an extension added that do not sit under ``/api/``.

    Loading arbitrary Python from a configured directory is the feature working
    as designed, so this is a warning and not a restriction. The reason to say
    it out loud is that the gate an operator reasons about is the one named
    after the API. An extension is free to mount ``/backdoor`` beside the
    pages, and with no token set that route answers anyone on the LAN. Sorted
    so the warning reads the same on every start.
    """
    return sorted(r for r in (after - before) if not r.startswith("/api/"))


_RAIL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_warned_rail_tabs: set[tuple[str, str, str]] = set()


def rail_page_prefixes(extension_id: str) -> list[str]:
    """URL prefixes a rail tab page of *extension_id* may live under.

    The extension id as written, and the same id with underscores turned
    into hyphens (``nina_ts_sync`` and ``nina-ts-sync``), since extensions
    name their routes in either style. Empty when the id is not a plain
    slug, which also rules out ids that could smuggle path characters.
    """
    if not isinstance(extension_id, str) or not _RAIL_ID_RE.match(extension_id):
        return []
    forms = [extension_id, extension_id.replace("_", "-")]
    return [f"/api/ext/{f}" for f in dict.fromkeys(forms)]


def rail_page_ok(extension_id: str, page: object) -> bool:
    """True when *page* is a same-origin path inside the extension's prefix."""
    if not isinstance(page, str) or not page.startswith("/"):
        return False
    if page.startswith("//") or "\\" in page or any(ord(c) < 0x20 or c == "\x7f" for c in page):
        return False
    path = page.split("?", 1)[0].split("#", 1)[0]
    if re.search(r"%(2e|2f|5c)", path, re.IGNORECASE):
        return False
    if any(seg in ("..", ".") for seg in path.split("/")):
        return False
    return any(path == p or path.startswith(p + "/")
               for p in rail_page_prefixes(extension_id))


def sanitise_rail_tabs(entry: dict) -> dict:
    """Return *entry* with only its valid ``rail_tabs`` kept.

    A rail tab is ``{"id", "label", "icon", "page"}`` plus an optional ``width`` of
    ``"normal"`` or ``"wide"`` (anything else is dropped). ``id`` is a short
    slug, ``label`` a non-empty string, ``icon`` an optional name, and
    ``page`` a relative path under ``/api/ext/<the extension's prefix>``
    (see ``rail_page_ok``). Bad tabs are dropped with one warning each; the
    rest of the entry passes through untouched. Entries without ``rail_tabs``
    are returned as they are.
    """
    if not isinstance(entry, dict) or "rail_tabs" not in entry:
        return entry
    ext = entry.get("extension")
    raw = entry.get("rail_tabs")
    kept: list[dict] = []
    seen: set[str] = set()
    for tab in raw if isinstance(raw, list) else []:
        tid = tab.get("id") if isinstance(tab, dict) else None
        label = tab.get("label") if isinstance(tab, dict) else None
        page = tab.get("page") if isinstance(tab, dict) else None
        reason = None
        if not isinstance(tid, str) or not _RAIL_ID_RE.match(tid):
            reason = "id must be a short slug"
        elif tid in seen:
            reason = "duplicate id"
        elif not isinstance(label, str) or not label.strip():
            reason = "label must be a non-empty string"
        elif not rail_page_ok(ext, page):
            reason = (f"page must be a relative path under "
                      f"{' or '.join(rail_page_prefixes(ext)) or '/api/ext/<extension>'}")
        if reason:
            key = (str(ext), str(tid), str(page))
            if key not in _warned_rail_tabs:
                _warned_rail_tabs.add(key)
                logger.warning("Extension %r rail tab %r rejected: %s (page=%r)",
                               ext, tid, reason, page)
            continue
        seen.add(tid)
        icon = tab.get("icon")
        kept_tab = {"id": tid, "label": label.strip(),
                    "icon": icon if isinstance(icon, str) else "",
                    "page": page}
        if tab.get("width") in ("normal", "wide"):
            kept_tab["width"] = tab["width"]
        kept.append(kept_tab)
    out = dict(entry)
    out["rail_tabs"] = kept
    return out


def _user_extensions_dir() -> Path:
    raw = os.environ.get("ACP_EXTENSIONS_DIR", "").strip()
    return Path(raw).expanduser() if raw else default_extensions_dir()


def load_extensions(app: "Flask") -> list[str]:
    """Discover and register the user's extensions on *app*.

    Returns the names (directory entry name) of extensions that registered
    successfully. Nothing happens when the directory is absent.
    """
    ext_dir = _user_extensions_dir()
    if not ext_dir.is_dir():
        return []
    return _load_dir(app, ext_dir)


def bundled_extensions_disabled() -> bool:
    """True when ``ACP_DISABLE_BUNDLED_EXTENSIONS`` switches the bundled set off."""
    return (os.environ.get(BUNDLED_DISABLE_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


def load_bundled_extensions(app: "Flask") -> list[str]:
    """Register the extensions that ship inside ACP (``bundled_extensions/``).

    They load the same way as user extensions and stay separate packages that
    core never imports. Call this after ``load_extensions``: when the user's
    extensions directory already holds an entry with the same name (a newer
    standalone copy, say), that copy is the one that runs and the bundled one
    is skipped, so the same routes are never registered twice.
    ``ACP_DISABLE_BUNDLED_EXTENSIONS=1`` skips the whole folder.
    """
    if bundled_extensions_disabled():
        logger.info("Bundled extensions disabled by %s", BUNDLED_DISABLE_ENV)
        return []
    if not BUNDLED_EXTENSIONS_DIR.is_dir():
        return []
    user_dir = _user_extensions_dir()
    shadowed: set[str] = set()
    if user_dir.is_dir():
        shadowed = {(p.stem if p.is_file() else p.name) for p in user_dir.iterdir()}
    for path in sorted(BUNDLED_EXTENSIONS_DIR.iterdir()):
        stem = path.stem if path.is_file() else path.name
        if stem in shadowed and not stem.startswith((".", "_")):
            logger.info("Bundled extension %s skipped: %s has a copy of its own",
                        stem, user_dir)
    return _load_dir(app, BUNDLED_EXTENSIONS_DIR, frozenset(shadowed))


def _load_dir(app: "Flask", ext_dir: Path,
              skip_stems: frozenset[str] = frozenset()) -> list[str]:
    """Load every extension found directly under *ext_dir*.

    Entries whose stem is in *skip_stems* are passed over. Names starting with
    ``.`` or ``_`` are skipped too, which also skips ``__pycache__`` and any
    package an author intentionally hides.
    """
    loaded: list[str] = []
    seen_names: set[str] = set()
    for path in sorted(ext_dir.iterdir()):
        if path.name.startswith((".", "_")):
            continue
        # Stem (without .py for files) determines the synthetic module name;
        # collisions between e.g. `foo.py` and `foo/` would silently shadow.
        stem = path.stem if path.is_file() else path.name
        if stem in skip_stems:
            continue
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
        before = _rule_paths(app)
        try:
            register(app)
        except Exception:
            logger.exception("Extension %s register() raised", path.name)
            continue
        loaded.append(path.name)
        logger.info("Loaded extension: %s", path.name)
        outside = routes_outside_api(before, _rule_paths(app))
        if outside:
            logger.warning(
                "Extension %s mounted %d route(s) outside /api/: %s. Those are "
                "served like any page. With ACP_API_TOKEN set they sit behind "
                "the same gate as everything else; with no token set they "
                "answer anyone who can reach this port.",
                path.name, len(outside), ", ".join(outside))

    return loaded
