#!/usr/bin/env python
"""Publish the public live document to a static web page.

    python scripts/publish_shooting.py --rescan    rebuild the manifest first, then publish
    python scripts/publish_shooting.py             publish from the current manifest
    python scripts/publish_shooting.py --dry-run   write data/live/shooting.json and stop

The SFTP destination comes from ACP_PUBLISH_DEST and an optional key from
ACP_PUBLISH_SSH_KEY. See docs/specs/shooting-page.md and docs/sharing.md.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rescan", action="store_true", help="run scripts/build_archive_manifest.py first")
    ap.add_argument("--dry-run", action="store_true", help="write the JSON locally and do not push")
    args = ap.parse_args(argv)

    if args.rescan:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_archive_manifest.py")])
        if r.returncode != 0:
            print("rescan failed, not publishing", file=sys.stderr)
            return r.returncode

    import shooting_publish as sp
    import app as app_module  # defines the Flask app, does not start a server

    dest = None
    if not args.dry_run:
        try:
            dest = sp.resolve_dest()
        except sp.PublishConfigError as e:
            print(str(e), file=sys.stderr)
            return 2

    plans = app_module.load_plans().get("plans", [])
    doc = sp.build_shooting_document(plans, app_module.load_manifest(), app_module.load_gear())
    path = sp.write_document(doc, app_module.LIVE_OUT_DIR)
    print(f"wrote {path} ({len(doc['projects'])} public project(s))")
    if args.dry_run:
        return 0
    result = sp.push_document(path, dest, os.environ.get("ACP_PUBLISH_SSH_KEY") or None)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode or 1
    print(f"pushed to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
