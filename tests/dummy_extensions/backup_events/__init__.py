"""A dummy extension that adds Activity types and a source.

Used by tests/test_ext_activity_types.py. A backup tool on the same machine
posts ``backup_done`` and ``backup_failed`` events as source ``backup``; the
extension records ``backup_checked`` itself. They sit under a new chip.
"""
import activity


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "activity_types" not in app.acp_extension_hooks:
        return
    activity.register_types("backups", ["backup_done", "backup_failed", "backup_checked"],
                            label="Backups")
    activity.register_types("processing", ["frames_rejected"])
    activity.register_source("backup")
