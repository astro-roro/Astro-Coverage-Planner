"""A dummy extension that owns three plan fields.

Used by tests/test_ext_plan_fields.py and tests/test_ext_project_keys.py.

- ``rating``: 1 to 5, set by the client, checked by ``validate``.
- ``rated_at``: kept from the stored plan, so a client can never set it.
- ``rating_label``: derived by ``view`` for display, never stored.
- ``rating_group``: a project-wide key (only when the project_keys hook is
  present), spread across every plan in a project by the settings route.
"""
LABELS = {1: "poor", 2: "fair", 3: "good", 4: "very good", 5: "excellent"}


def validate(payload):
    if "rating" in payload and payload["rating"] is not None:
        r = payload["rating"]
        if isinstance(r, bool) or not isinstance(r, int) or not 1 <= r <= 5:
            return "rating must be a whole number from 1 to 5"
    if "rating_group" in payload and payload["rating_group"] not in (None, "a", "b"):
        return "rating_group must be a or b"
    return None


def keep(payload, existing):
    payload.pop("rating_label", None)
    existing = existing or {}
    if "rated_at" in existing:
        payload["rated_at"] = existing["rated_at"]
    else:
        payload.pop("rated_at", None)


def view(plan):
    if isinstance(plan.get("rating"), int):
        plan["rating_label"] = LABELS.get(plan["rating"], "")
    return plan


ENTRY = {"extension": "plan_rating", "keys": ["rating", "rated_at", "rating_group"],
         "validate": validate, "keep": keep, "view": view}


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "plan_fields" not in app.acp_extension_hooks:
        return
    entry = dict(ENTRY)
    if "project_keys" in app.acp_extension_hooks:
        entry["project_keys"] = ["rating_group"]
    app.plan_fields.append(entry)
