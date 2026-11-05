"""A dummy extension that keeps a JSON summary of each clean scan.

Used by tests/test_ext_scan_hooks.py. A real one might post the summary
somewhere; this one keeps the last few in memory and serves them.
"""
from flask import Blueprint, jsonify

bp = Blueprint("scan_summary", __name__)
received = []


def on_scan(summary):
    received.append(summary)
    del received[:-5]


@bp.route("/api/ext/scan-summary/last")
def last():
    return jsonify(received[-1] if received else None)


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "scan_hooks" not in app.acp_extension_hooks:
        return
    app.register_blueprint(bp)
    app.scan_hooks.append(on_scan)
