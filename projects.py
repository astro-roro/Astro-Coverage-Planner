"""Which project a plan belongs to.

ACP has no project entity. A Target Scheduler project is the group of plans
that share a project name, the same grouping ``_build_ts_export`` uses. The
project settings route and ``plans_api.project_key`` both group plans with
``project_key``.
"""
from __future__ import annotations


def project_key(plan: dict) -> str:
    """The TS project a plan belongs to. Its project name, else its target's
    name (each unnamed plan is its own project), else its id."""
    explicit = str(plan.get("project_name") or "").strip()
    if explicit:
        return explicit
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    return str(target.get("name") or "").strip() or str(plan.get("id") or "") or "Untitled"
