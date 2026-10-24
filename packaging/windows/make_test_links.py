"""Make the ACP shortcuts in two throwaway folders, for smoke-start.ps1.

    python make_test_links.py <package folder> <start menu folder> <desktop folder>

Run with the package's own Python, so it exercises the same shortcuts.py the app
uses. Nothing here touches the real Start menu or desktop.
"""
import sys
from pathlib import Path

package, start_menu, desktop = sys.argv[1:4]
sys.path.insert(0, str(Path(package) / "app"))

import shortcuts  # noqa: E402

for path in shortcuts.create(package, start_menu_dir=start_menu, desktop_dir=desktop):
    print(path)
