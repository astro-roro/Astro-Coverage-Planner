"""A dummy extension that adds a card to the top of the Activity tab.

Used by tests/test_ext_activity_cards.py. The card is a small page showing
free space on the disk that holds ACP's data folder.
"""
import shutil

from flask import Blueprint

bp = Blueprint("disk_space_card", __name__)


@bp.route("/api/ext/disk-space-card/card")
def card():
    usage = shutil.disk_usage(".")
    pct = round(100 * usage.free / usage.total) if usage.total else 0
    return (f'<!doctype html><meta charset="utf-8"><title>Disk</title>'
            f'<p class="disk">Free space: {pct}%</p>')


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "activity_cards" not in app.acp_extension_hooks:
        return
    app.register_blueprint(bp)
    app.extensions_manifest.append({
        "extension": "disk_space_card",
        "name": "Disk space",
        "activity_cards": [{"id": "disk", "label": "Disk space",
                            "page": "/api/ext/disk-space-card/card", "height": 60}],
    })
