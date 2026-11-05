"""Plan access for extensions (docs/extensions.md, "Plan reads and writes").

An extension that changes plans must do it under the same lock every core
route that writes plans.json holds, or a save from the editor and a write
from the extension can overwrite each other. This module is the supported
way in. app.py binds it at startup with ``bind()`` and sets ``app.plans_api``
to it, before any extension loads.

    def register(app):
        api = app.plans_api
        def mark(plans):
            for p in plans:
                p["my_note"] = "seen"
            return len(plans)
        n = api.transact(mark)

Everything handed to an extension is a copy. ``transact`` saves only when the
function returns without raising and something changed.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable

__all__ = ["bind", "read", "transact", "apply_project_settings", "project_key"]

_load: Callable[[], dict] | None = None
_save: Callable[[dict], None] | None = None
_lock: Any = None
_apply_project_settings: Callable[..., dict] | None = None
_project_key: Callable[[dict], str] | None = None
_now: Callable[[], str] | None = None


def bind(*, load, save, lock, apply_project_settings, project_key, now) -> None:
    """Called once by app.py. Each argument is a callable (or the lock) the
    module uses; they are looked up when called, not when bound."""
    global _load, _save, _lock, _apply_project_settings, _project_key, _now
    _load, _save, _lock = load, save, lock
    _apply_project_settings, _project_key, _now = apply_project_settings, project_key, now


def _need_bound() -> None:
    if _load is None:
        raise RuntimeError("plans_api is not bound to an ACP app")


def read() -> dict:
    """A deep copy of the plans document, ``{"version": ..., "plans": [...]}``."""
    _need_bound()
    with _lock:
        return copy.deepcopy(_load())


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, default=str)


def transact(fn: Callable[[list], Any]) -> Any:
    """Run ``fn(plans)`` under the plans lock and save what it leaves.

    ``plans`` is a deep copy of the stored list. Change it in place: edit
    plans, append new ones or remove them. When ``fn`` returns, the list is
    saved if it differs from what was stored, and ``fn``'s return value is
    passed back. When ``fn`` raises, nothing is saved and the exception
    propagates. Core does not validate what the function writes, so keep to
    fields your extension owns or core's documented fields.
    """
    _need_bound()
    with _lock:
        doc = _load()
        before = _canonical(doc.get("plans", []))
        plans = copy.deepcopy(doc.get("plans", []))
        result = fn(plans)
        if _canonical(plans) != before:
            _save({**doc, "version": doc.get("version", 1), "plans": plans})
        return result


def apply_project_settings(plans: list, project_name: str, wanted: dict) -> dict:
    """Set every key in ``wanted`` on every plan in the project named
    ``project_name``, exactly as the project settings route does, and return
    ``{key: {"from": old, "to": new}}`` for the keys that changed. Use it
    inside ``transact`` on the list it hands you."""
    _need_bound()
    return _apply_project_settings(plans, project_name, wanted, _now())


def project_key(plan: dict) -> str:
    """The project a plan belongs to: its project name, else its target's
    name, else its id. The same grouping the Target Scheduler export uses."""
    _need_bound()
    return _project_key(plan)
