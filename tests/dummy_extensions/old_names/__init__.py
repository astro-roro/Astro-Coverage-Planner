"""A dummy extension that registers old names after a rename.

Used by tests/test_ext_name_aliases.py. A tool that used to post as
``legacy_src`` with type ``legacy_check`` and ref ``legacy_job``, and that
used to store its plan block as ``legacy_block``, keeps working after ACP
renamed them.
"""
import activity
import filter_rows

ALIASES = {
    "source": {"legacy_src": "external"},
    "type": {"legacy_check": "calibration"},
    "ref": {"legacy_job": "bucket"},
}
BLOCKS = {"legacy_block": "grader"}


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "name_aliases" not in app.acp_extension_hooks:
        return
    for kind, table in ALIASES.items():
        for old, new in table.items():
            activity.register_alias(kind, old, new)
    for old, new in BLOCKS.items():
        filter_rows.register_block_alias(old, new)
