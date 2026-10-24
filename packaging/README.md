# Packaging

Everything that builds the downloads. The design is in [the spec](../docs/specs/windows-download-and-setup.md). Phase 2 covers the Windows zip only.

| Path | What it is |
|---|---|
| `pins.txt` | The Python version and the SHA-256 of the python.org download. Fill in the hash before the first build |
| `constraints.txt` | An exact `==` pin for every dependency. Dependabot updates it |
| `windows/build.ps1` | Builds `ACP-windows-x64.zip` and `SHA256SUMS-windows.txt` on `windows-latest` |
| `windows/Start ACP.bat`, `windows/README.txt` | The files at the top of the zip |
| `windows/smoke-start.ps1`, `windows/make_test_links.py` | The CI check that ACP starts through the `.bat` and a shortcut with no console window |
| `smoke.py` | The smoke test, run with the zip's own Python (stdlib only) |
| `check_tree.py` | The build checks: tag equals `VERSION`, size, longest path, required files |
| `zip_tree.py` | Zips a folder quickly with the folder as the top entry |
| `icons/make_icons.py` | Draws the icons from the shapes in `static/favicon.svg` into the package. Not committed |

## Releasing

1. Bump `VERSION` in `app.py` and commit.
2. Tag `v<VERSION>` (the first release is `v1.0.0`) and push the tag.
3. `release-windows.yml` builds, smoke-tests, then creates the GitHub release if it does not exist and uploads the zip and `SHA256SUMS-windows.txt`. The tag must equal `VERSION` or the build fails.

To try a build without releasing, push a branch named `packaging/<anything>` or run the workflow from the Actions tab. Both build and smoke-test, and neither publishes.

## Before the first build

- Put the SHA-256 of `python-3.12.10-embed-amd64.zip` in `pins.txt`. The build refuses to run while it says `TODO` and prints the hash it downloaded, which you must check against the python.org release page before trusting it.
- `constraints.txt` was drafted from the maintainer's environment, not resolved for Python 3.12 on Windows. If the install step reports a pin with no wheel, move that line to the nearest version that has one.

## Running the checks by hand

    python packaging/smoke.py --package <unzipped ACP folder> --version <VERSION> --tray --windows-checks

Run it with the package's own `python\python.exe`, from outside the package. Against a source checkout, add `--python <python> --launcher launcher.py`.
