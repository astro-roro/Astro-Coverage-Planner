# Spec: downloads for Windows, Mac and Linux, and an in-app first-run setup

This spec removes the terminal from installing ACP. A first-run page takes the image folders and runs the scan. A zip for Windows, a signed app for Mac and a tar.gz for Linux each bundle Python, so nobody installs it. The work ships in four phases that each merge alone.

## Decisions

| Question | Choice |
|---|---|
| Where settings live | A JSON file in a per-OS folder. Environment variables still win. |
| How a user picks a folder | A typed or pasted path, checked by the server, with on-page help for copying a path. No folder browser. |
| Packaging, all three | A bundled, relocatable Python plus the app. Not PyInstaller or py2app. |
| What the user starts | `Start ACP.bat` the first time on Windows, then a Start menu shortcut. `ACP.app` on Mac. `start-acp` on Linux, then a launcher entry. |
| Running state | A tray icon (menu bar on Mac) with Open ACP, a status line and Quit ACP. Windows shows no console window. |
| Shortcuts | Offered once with a tick box on the setup page. Never created silently. |
| Icon | Drawn at build time from the shapes in `static/favicon.svg`. |
| Mac | Apple Silicon only, signed and notarised. Intel later if asked. |
| Linux | x86_64 and arm64 (Raspberry Pi), built to run on distros back to glibc 2.28. |
| Bind address in packaged builds | `127.0.0.1` only. |
| Catalogue overlays | A "Download catalogues" button, not shipped data. |
| First release | 1.0.0. |
| Updating | Replace the app. Data lives elsewhere and stays. No automatic update check. |

## Terms

- **Settings file**: `settings.json`, the file the app reads and writes for the keys in section 1.
- **Config folder**: the per-OS folder that holds the settings file.
- **Data directory**: the folder for files the user creates, such as the manifest, plans and gear.
- **Packaged mode**: ACP started by a launcher. The launcher sets `ACP_PACKAGED=1`.
- **Tray icon**: the icon in the Windows notification area, the Mac menu bar or a Linux system tray, with a small menu.
- **Shortcut**: a Start menu and desktop link on Windows, or an applications-menu entry on Linux, that starts ACP.
- **Effective value**: the value ACP uses after applying precedence in section 1.3.
- **Authorised client**: a request that passes the existing access gate with a bearer header or the session cookie from `/login`, or, when `ACP_API_TOKEN` is not set, one that comes from a loopback address (`127.0.0.1`, `::1` or `::ffff:127.0.0.1`) and carries no `X-Forwarded-For` header.
- **Restricted route**: a route that reveals a local path, changes state or contacts the file system. Section 6 lists them.

## 1. Settings file

### 1.1 Where files live

| OS | Settings file | Data directory in packaged mode | Data directory in a source install |
|---|---|---|---|
| Windows | `%APPDATA%\ACP\settings.json` | `%APPDATA%\ACP\data` | `<repo>\data` |
| macOS | `~/Library/Application Support/ACP/settings.json` | `~/Library/Application Support/ACP/data` | `<repo>/data` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/acp/settings.json` | `${XDG_CONFIG_HOME:-~/.config}/acp/data` | `<repo>/data` |

`ACP_HOME` replaces the config folder on every OS. The smoke tests use it to stay out of the real profile. Packaged mode also writes `logs/acp.log` (1 MB, three files) in the config folder.

Resolving the config folder never raises. A container running as a user with no home folder makes `Path.home()` raise, and that case means "no settings file", so ACP starts on environment values.

The extension folder default (`~/.config/acp/extensions` outside Windows) does not move. A Mac user can therefore have two ACP folders. That is accepted.

### 1.2 Keys

| Key | Environment variable | Default | Notes |
|---|---|---|---|
| `fits_roots` | `FITS_ROOTS` | none | List of folder paths. The environment form is semicolon-separated, as today. |
| `manifest_path` | `MANIFEST_PATH` | `<data directory>/manifest.json` | Shown on the setup page, edited in the file only. |
| `port` | `PORT` | 5555 | Shown on the setup page, edited in the file only. Needs a restart. |
| `scan_cron` | `ACP_SCAN_CRON` | unset | File and environment only. No page control in this change. |
| `shortcuts` | none | `null` | `null` means not offered yet, `true` means the user wants shortcuts, `false` means declined. File only. |

The file also carries `"version": 1`. `HOST` stays environment-only on purpose, so no file or page can open ACP to the network. `ACP_DATA_DIR` is environment-only too, because the file cannot say where the file is.

### 1.3 Precedence

The effective value is the environment variable if it is set and not empty, else the settings file, else the default. An empty variable counts as unset, because compose files often declare blank ones.

`acp_settings.py` implements this. It sits at the repo root with the other app modules, takes the platform and environment as parameters so every OS branch runs on any CI machine, and uses only the standard library. `app.py` imports it. The builder inserts the repo root into `sys.path` beside its existing `scripts/` entry and imports it too. A source user who runs `python scripts/build_archive_manifest.py` by hand therefore sees roots from the settings file.

When the app starts a scan it also passes the effective values to the builder as environment variables. The builder keeps its built-in fallback root for the maintainer's setup. In packaged mode, and for a scan started from the page, `run_scan_now` refuses to start when no roots are effective, so the fallback root can never be scanned by accident. Source and Docker scheduled scans behave as today.

The app reads its bind host from `ACP_BIND_HOST`, then `HOST`, then `127.0.0.1`. The launcher sets `ACP_BIND_HOST` to what it actually binds, because it ignores `HOST`. Under `waitress-serve` in Docker the main block never runs, and `HOST` supplies the value.

### 1.4 The data directory

`ACP_DATA_DIR` moves every file ACP writes. That covers the manifest, plans, gear and sites, and also the destinations, saved searches and hidden lists. It covers target overrides, Target Scheduler templates and fingerprints, and it covers exports, live output and the MOC cache. The activity database, scan cache, target ids and `catalogs.json` move too. A helper, `data_path(name)`, replaces each `REPO_ROOT / "data" / name` default in `app.py` and the builder. The per-file variables such as `PLANS_PATH` still win.

Three tracked files stay in the app folder and are read-only: `catalog_registry.json`, `filter_catalogue.json` and `surveys.json`.

### 1.5 Existing installs and Docker

With no settings file and none of the new variables, every path and the port resolve to today's values. Phase 1 proves this with a test that lists the values as literal strings (S6). The Dockerfile does not change.

A Docker deployment sets `FITS_ROOTS`, so the page shows the folders as set by the environment and locks them. A deployment with no `FITS_ROOTS` and no manifest is first run. Its page is read-only, as section 2.3 describes.

A Windows source user may already have `FITS_ROOTS` set in their profile, as `docs/setup-archive.md` told them to. Their page shows the folders locked, with a "Copy to settings file" button that writes the same list to `settings.json` and tells them to remove the variable under Windows Settings, "Edit environment variables for your account". The file takes effect once the variable is gone.

### 1.6 Writing

- The write is atomic, using the existing `target_ids.atomic_write_json`, and keeps keys it does not know.
- A corrupt file is not overwritten. ACP starts on defaults, shows an error on the setup page, and saves the bad file once as `settings.json.bak`. A second corrupt file does not replace the first copy.
- A write to a key set by the environment is refused with 409 and `locked_by_env`.
- A write to `fits_roots` while a scan runs is refused with 409. It uses the same lock that guards the scan claim in section 2.5.

## 2. The setup page

### 2.1 Routes

| Route | Authorised client only | Purpose |
|---|---|---|
| `GET /setup` | no | The page. It contains no local paths. Scripts fill them in. |
| `GET /api/settings` | for paths | Sources, locked flags, `first_run`, `writable` and the version go to every client, with a `shortcuts` object (`available`, `value`). Folder, manifest and data paths go to authorised clients only. |
| `PUT /api/settings` | yes | Saves `fits_roots` and `shortcuts`. Any other key returns 400. Setting `shortcuts` creates or removes the shortcut files (section 3.5), and returns 400 `unavailable` where they are not supported. |
| `POST /api/settings/check-path` | yes | Validates one path and returns codes. Writes nothing. |
| `POST /api/settings/adopt-env` | yes | Copies environment roots into the file (section 1.5). |
| `POST /api/scan/start` | yes | Starts one scan. Returns 202, or 409 if one is running. |
| `GET /api/scan/status` | no | Existing route, gains a `progress` object with no paths. |
| `POST /api/catalogues/fetch` | yes | Runs `scripts/fetch_catalogs.py` in the background (section 2.6). |
| `GET /api/catalogues/status` | no | Running, finished, and an error kind. |
| `POST /api/quit` | yes | Packaged mode only (section 3.3). |

### 2.2 First run

First run is true when there are no effective roots and no manifest file at the effective manifest path. A browser `GET /` then redirects to `/setup`, unless the address carries `?setup=skip`. A browser is a request whose `Accept` header contains `text/html`, as `_wants_html()` decides. The skip link on the setup page shows today's empty map with its banner.

A user with a manifest but no roots is not first run. They see the onboarding banner, which now links to `/setup`. API routes never redirect.

### 2.3 The page

- A list of folders, each with a Remove button.
- One text box and an Add button. Add checks the path and, if it passes, saves the whole list at once through `PUT`. The box strips surrounding quotes, because Windows "Copy as path" adds them.
- A line beside the box that tells the user how to get a path: on Windows 11 right-click the folder and choose Copy as path, on Windows 10 hold Shift while right-clicking, on Mac right-click in Finder, hold Option and choose Copy as Pathname.
- A Scan button, enabled with at least one folder.
- A progress panel with the stage, files seen and elapsed time.
- On success, the number of targets and a button to the map. The page continues to `/` after three seconds.
- On failure, the message for the error kind and a Change folders button.
- A "Download catalogues" button with its status, because the overlays are empty until it runs once.
- A tick box, ticked by default, beside the Scan button: "Add ACP to the Start menu and desktop" on Windows, "Add ACP to the applications menu" on Linux. It shows only when `shortcuts.available` is true and the value is `null`. Pressing Scan or Skip sends the box's value, and either value ends the offer.

The same URL is the settings page afterwards. A "Settings" link in the header opens it. It shows the data folder, the manifest path, the port and the version, with a link to the GitHub releases page that opens in a new tab. In packaged mode it shows a Quit ACP button, which stays because a tray can be hidden or missing. The same page offers the shortcuts to a user who skipped setup, and afterwards shows a Shortcuts row with Create and Remove buttons.

When `writable` is false, the page hides every control and shows one paragraph. The paragraph says this ACP is configured by environment variables, links to `docs/setup-archive.md`, and says to set `FITS_ROOTS` and restart, then run a scan with `docker exec` or schedule one with `ACP_SCAN_CRON`.

### 2.4 Path checks

`PUT` runs every check below on every path, after quote stripping and `~` expansion. It refuses the whole write with 400 and the first blocking code. `check-path` returns the same codes for one path, compared against the saved list. It returns a code, never operating-system error text.

| Code | Meaning |
|---|---|
| `ok` | Absolute, exists, is a folder, can be listed. |
| `not_absolute` | Relative paths are refused. |
| `not_found` | Nothing at that path. |
| `not_a_directory` | A file. |
| `not_readable` | The folder cannot be listed. On macOS the message points to System Settings, Privacy and Security. |
| `timed_out` | The folder did not answer in 5 seconds. The message says it could not be reached. |
| `invalid` | Contains a NUL or control character, or is longer than 1,000 characters. |
| `duplicate` | Equal to a saved path after `os.path.normcase(os.path.normpath(...))`. The path is stored as typed. |
| `too_many` | More than 32 folders. |

Two warnings do not block: `overlaps` (inside or containing another saved folder) and `no_images_seen`. A drive root gets `very_large`. UNC paths such as `\\nas\astro` are accepted, since many users scan a NAS.

The folder read runs in a worker thread with a 5-second limit, so a sleeping NAS cannot hold up the four waitress threads. `no_images_seen` walks the folder breadth-first for up to 5,000 entries or 2 seconds, looking for a `.fit`, `.fits`, `.fts` or `.xisf` file. Its message says the folder was accepted but no image files were found, so the user may want the parent folder.

### 2.5 Running the scan

The route claims the scan under `SCAN_STATE_LOCK` before it answers. `run_scan_now` splits into a claim step and a run step. The route answers 202 or 409 from the claim, then hands the claimed run to a worker thread. Two quick clicks therefore cannot both get 202. The run step already starts the builder as a subprocess, so a builder crash cannot take the app down. If no roots are effective, the route answers 400 `no_archive_roots` and claims nothing.

The builder gains a progress writer. It writes `scan_progress.json` beside the manifest, atomically, at each stage change and every two seconds. A failed write is logged and never fails the scan, because on Windows `os.replace` fails while a reader holds the file. The file contains the stage, `files_seen` and a timestamp, and never a path. Stages follow the builder's printed steps.

| Stage | Starts at | `files_seen` counts |
|---|---|---|
| `listing` | Globbing and pre-filtering (steps 1 and 2) | Files found so far, counted inside the walk loop |
| `reading` | Reading headers | Headers read so far |
| `matching` | Classification, dedup, clustering and target numbering | Headers read, held constant |
| `writing` | Writing the manifest | Held constant |

I did not trace the builder's step numbers. The build confirms the boundaries against its output.

`/api/scan/status` returns the stage, the count and the existing fields. It returns no paths and no builder output, because the status route is unauthenticated in the stock configuration. The error text for `no_archive_roots` changes from "check FITS_ROOTS" to "check the image folders in Settings".

### 2.6 Catalogue overlays

`data/catalogs.json` is gitignored and made by `scripts/fetch_catalogs.py` from four VizieR tables (Green 2019 supernova remnants, Anderson 2014 WISE HII regions, Sharpless 2, and the Strasbourg-ESO planetary nebulae) plus a hard-coded Messier list. A packaged build has none, so the overlays would be empty.

The repo records no licence for these tables, and I did not verify the terms of the four tables at CDS. The release therefore does not ship the file. `POST /api/catalogues/fetch` runs the script the way a scan runs: one at a time, as a subprocess, status without paths. It writes to `catalogs.json` in the data directory. The button is the first outbound call a user starts from the page, and the page says so. If the terms are later confirmed to allow redistribution, shipping the file is a small change.

## 3. Windows download

### 3.1 Embeddable package or PyInstaller

| | python.org embeddable package | PyInstaller one-folder |
|---|---|---|
| The scan | `sys.executable` is a real Python, so `_run_scan_subprocess` works unchanged. | `sys.executable` is the ACP exe. The scan, which runs `sys.executable build_archive_manifest.py`, needs a rewrite. |
| Extensions | `ACP_EXTENSIONS_DIR` loads user modules as it does now. | Dynamic imports need hidden-import rules. |
| Heavy dependencies | `pip install` of normal wheels. | astropy data files and mocpy need hand-written hooks, and each upgrade can break them. |
| Antivirus | Unsigned `python.exe` from python.org. Seldom flagged. | An unsigned PyInstaller bootloader is a common false positive. |
| Files | Roughly 15,000 to 25,000. Slow to unzip. | Fewer. |
| Fix in place | Edit a `.py` file. | Rebuild. |

The embeddable package wins on the first row alone. PyInstaller would force changes to the scan design that Docker and source installs rely on.

### 3.2 Zip layout

The release asset is `ACP-windows-x64.zip`. It has no version in its name, so the README can link to `https://github.com/astro-roro/Astro-Coverage-Planner/releases/latest/download/ACP-windows-x64.zip`. The version is in `VERSION.txt` and on the settings page.

```
ACP\
  Start ACP.bat
  README.txt            how to start, how to stop, where data lives
  VERSION.txt
  python\               embeddable runtime, with python312._pth edited
  site-packages\        dependencies
  app\                  every file `git ls-files` returns, minus tests/, docs/, .github/, notes/ and packaging/
```

`app\` includes the modules `app.py` imports (`activity.py`, `balancer.py`, `extensions.py`, `filter_rows.py`, `gaps.py`, `gear_templates.py`, `shooting_publish.py`, `sources.py`) and `bundled_extensions\nina_ts_sync`, the Target Scheduler sync. `python312._pth` lists `..\site-packages` and `..\app`, because a `._pth` file also makes the embeddable Python ignore `PYTHON*` environment variables.

`Start ACP.bat` does these things, with every path built from `%~dp0` and quoted:

1. Check that `%~dp0python\pythonw.exe` exists. If not, print "Extract the whole zip first, then run Start ACP.bat from the extracted folder", and pause. A console is open at that point, because the user started a `.bat`.
2. Run `start "" "%~dp0python\pythonw.exe" -X utf8 -B "%~dp0app\launcher.py"` and exit. `pythonw.exe` opens no console window. The flags go on the command line because the `._pth` file makes Python ignore `PYTHONUTF8` and `PYTHONDONTWRITEBYTECODE`.

There is no console window while ACP runs. The log is in the log file, and ACP stops from the tray menu or the Quit ACP button on the settings page. The shortcuts in section 3.5 start `pythonw.exe` directly, so they never flash a console. The first step of phase 2 prints `sys.flags` from the zip's Python and checks the `._pth` behaviour. I have not confirmed it on Windows.

### 3.3 The launcher

`launcher.py` serves all three platforms. It does these steps in order.

1. Resolve the config folder: `ACP_HOME` if set, else the per-OS folder. Set `ACP_DATA_DIR` to `<config folder>/data` unless the user already set it. Set `ACP_PACKAGED=1` and `ACP_BIND_HOST=127.0.0.1`. All of this happens before `app.py` is imported, because `app.py` reads them at import.
2. Send stdout and stderr to `logs/acp.log`, so `print` output from `app.py` lands there. `pythonw.exe` has no stdout at all, so this happens before anything prints. A Linux launch from a terminal also echoes the log to it.
3. Take an exclusive lock on `acp.lock` in the config folder, held for the life of the process. The file records the port. If the lock is held, ACP is already running, possibly still starting. Open the browser at the recorded port and exit. A second double-click during a slow start therefore never starts a second copy.
4. Read the effective port. If another program is using it, stop with a message naming the port. The launcher does not move to another port, because the NINA plugin is set to the port it knows.
5. Create the server with `waitress.create_server`, bound to `127.0.0.1`, so a port error surfaces now. Run it with `server.run()` in a worker thread. The launcher ignores `HOST`.
6. A background thread polls `/api/version` for up to 90 seconds, then opens the browser. After 90 seconds it logs the URL and opens the browser anyway. `ACP_NO_BROWSER=1` skips this, for tests.
7. Run the tray (section 3.4) in the main thread. If there is no tray, or it fails to start, join the server thread instead. That is how ACP ran before the tray existed.
8. If a shortcut was made in another folder, rewrite it (section 3.5).

If anything fails before the browser opens, the launcher shows the error and the log path in a native dialog. Windows uses `MessageBoxW` through `ctypes`, because there is no console. Mac uses `osascript -e 'display alert'`. Linux uses the terminal when there is one, else `zenity` or `kdialog` if installed, else the log only.

One function, `shutdown()`, stops ACP. It returns False while a scan runs. Otherwise it stops the tray, calls `server.close()` and releases the lock. The tray's Quit item and `POST /api/quit` both call it, and the route answers 409 when it returns False. The Quit ACP button on the settings page stays, because Windows hides new tray icons in an overflow menu and a Linux desktop may have no tray.

### 3.4 Tray icon

The library is `pystray`. It is the one library with a backend for Windows, Mac and Linux. The alternatives cover one platform each (`infi.systray` for Windows, `rumps` for Mac) or are heavy (Qt, wxPython). It needs Pillow for the image. Both go in a new `requirements-packaged.txt`, with `pyobjc-framework-Quartz` on macOS and `python-xlib` on Linux, so source and Docker installs get nothing new. The `pip-audit` step in `tests.yml` audits both files.

Threads:

- The main thread runs the tray. Mac's menu bar API must run on the main thread, and Windows and Linux use the same arrangement so there is one code path.
- A worker thread runs waitress.
- A poller thread reads `app.scan_status()` and the progress file every 2 seconds. It calls `icon.update_menu()` only when the text changes. The launcher imports `app`, so it needs no HTTP call.

The menu:

| Item | Behaviour |
|---|---|
| Open ACP | The default item. Opens the browser at the bound port. |
| Status line | Disabled text: "ACP is running", or "Scanning: reading, 12,340 files" from the stage and count. |
| Quit ACP | Calls `shutdown()`. Disabled while a scan runs, so the status line explains why. |

Per platform:

- **Windows**: `pythonw.exe`, so no console window. Windows 11 puts a new icon in the overflow menu, and the docs tell the user to drag it out.
- **Mac**: `LSUIElement` stays true, so the menu bar item is the app's only presence and there is no Dock icon. pystray's Mac backend starts the AppKit loop from `icon.run()` on the main thread. I did not confirm that it works under the stub's `execv`.
- **Linux**: pystray's `xorg` backend only, because the GTK and AppIndicator backends need the system `gi` module, which the bundled Python cannot import. It shows in X11 panels that have a system tray, such as the Raspberry Pi desktop, MATE, XFCE and KDE. It does not show in GNOME or on native Wayland. Before starting, the launcher checks that `DISPLAY` is set and that something owns the `_NET_SYSTEM_TRAY_S0` selection. If not, it skips the tray and runs as before: server in the main thread, Ctrl+C in a terminal, and the Quit button on the page.

`ACP_NO_TRAY=1` skips the tray, for tests. Any exception while starting the tray logs "tray unavailable" and falls back, and never stops the server.

Icon files: Windows and Linux use `acp-tray.png` (32 px). Mac uses `acp-menubar.png`, black on transparent at 22 pt with a 44 px copy for retina. I will confirm at build whether pystray tints it as a template image. If it does not, the file uses a mid-grey that reads on light and dark menu bars.

### 3.5 Shortcuts and the icon

The `shortcuts` key is `null` until the user answers the tick box in section 2.3. ACP also records `shortcuts_app_dir`, the folder the shortcuts point into. `shortcuts.py` at the repo root does the work. Mac has no shortcuts (`available` is false), and the docs tell the user to drag `ACP.app` to Applications.

Windows creates two links with PowerShell and the `WScript.Shell` COM object, so there is no new dependency. The script text is fixed. Every path reaches it through environment variables and is never interpolated, which avoids the quoting trap with spaces and non-ASCII names. It runs with `-NoProfile -NonInteractive -Command` and `CREATE_NO_WINDOW`. The execution policy does not apply to `-Command` text.

| Property | Value |
|---|---|
| Files | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Astro Coverage Planner.lnk`, and the same name in the folder `GetFolderPath('Desktop')` returns, which follows a OneDrive-redirected desktop. |
| `TargetPath` | `<root>\python\pythonw.exe` |
| `Arguments` | `-X utf8 -B "<root>\app\launcher.py"` |
| `WorkingDirectory` | `<root>` |
| `IconLocation` | `<root>\app\static\acp.ico` |

Linux writes `~/.local/share/applications/acp.desktop`, mode 0644, with `Type=Application`, `Name=Astro Coverage Planner`, `Exec="<root>/start-acp"` (absolute and quoted), `Icon=<root>/app/static/acp.png` (an absolute path), `Terminal=false` and `Categories=Science;Astronomy;`.

Remove deletes the files ACP made. At each packaged start, if `shortcuts` is true and `shortcuts_app_dir` differs from the current root, the launcher rewrites the shortcut files that still exist and records the new folder. This covers a user who moves the folder. A shortcut the user deleted is not recreated. An upgrade into the same path needs nothing.

The icon comes from `static/favicon.svg`, the only icon in the repo: a blue diamond with a white four-point star. The build has no SVG renderer, so `packaging/icons/make_icons.py` redraws the two polygons with Pillow at 8 times size and scales down. It writes these files into `app/static/` of the package. They are build outputs and are not committed.

| File | Use |
|---|---|
| `acp.ico` | 16, 24, 32, 48, 64, 128 and 256 px, for Windows shortcuts |
| `acp.png` | 512 px, for the Linux entry |
| `acp-tray.png` | 32 px, for the Windows and Linux tray |
| `acp-menubar.png` | 22 pt and 44 px, black on transparent, for the Mac menu bar |
| `ACP.icns` | Mac runner only, from an iconset through `iconutil` |

A test compares the polygon points in the script with the paths in `favicon.svg`, so the two cannot drift apart.

### 3.6 Where data lives

The unzipped folder is never written to. Settings, data, logs and the lock go to `%APPDATA%\ACP`. Bytecode is compiled at build time and `-B` stops new files. An upgrade is: quit ACP, delete the old folder, unzip the new one.

### 3.7 Build

`packaging/windows/build.ps1` runs on `windows-latest`.

1. Download the embeddable zip for the pinned Python 3.12 patch release and check its SHA-256 against `packaging/pins.txt`.
2. Write `python312._pth` as in section 3.2.
3. Install with the runner's Python 3.12: `pip install --target site-packages --only-binary=:all: -c packaging/constraints.txt -r requirements.txt -r requirements-packaged.txt`. A package with no wheel fails the build instead of compiling.
4. Delete `tests`, `__pycache__` and the deepest test and data folders until the longest path in the tree is under 150 characters.
5. Run `packaging/icons/make_icons.py` into `app\static`, copy `app\`, and write `Start ACP.bat`, `README.txt` and `VERSION.txt`.
6. Run `compileall --invalidation-mode unchecked-hash` over the tree. A `.pyc` that records the source's timestamp goes stale when zip's two-second time resolution shifts it. The hash mode stays valid. The runner's Python and the embedded Python share a minor version, which the bytecode format requires.
7. Zip, and write `SHA256SUMS-windows.txt`.

`packaging/constraints.txt` pins every dependency with `==` so two builds of one tag match. Dependabot updates it (a new `pip` entry in `.github/dependabot.yml` with `directory: /packaging`), and the smoke test catches a bad update.

### 3.8 Size

These are estimates from typical wheel sizes. I have not built it. Phase 2 measures the real figures and writes them here.

| | Estimate |
|---|---|
| Unpacked | 400 to 600 MB |
| Zip | 150 to 250 MB |
| CI limit | The build fails if the zip passes 400 MB. |

Explorer's extractor is slow on this many files because Defender scans each one. The docs say extraction takes several minutes.

### 3.9 CI

A new workflow, `release-windows.yml`, runs on tags matching `v*`, on manual dispatch, on every push to `main`, and on every pull request except those that change only `docs/` or `*.md`. A change to `app.py` or `requirements.txt` can break the zip, so the filter is on what is excluded. Three jobs.

- **build**: runs `build.ps1`, checks that the tag equals `VERSION` in `app.py` on tag runs, and uploads the zip as a workflow artifact.
- **smoke**: downloads the artifact and unzips it to a different folder from the build. It runs K1 to K5 from section 8 with the zip's own Python.
- **release**: runs only on tags, after smoke. It runs `gh release view` and, if the release does not exist, `gh release create`. Then it runs `gh release upload --clobber` for the zip and `SHA256SUMS-windows.txt`. It is the only job with `contents: write`. The Mac and Linux workflows do the same, so whichever finishes first creates the release and the others only upload.

The release procedure is: bump `VERSION`, commit, tag `v<VERSION>`, push the tag. The first release is `1.0.0`, the value `VERSION` holds today.

`windows-latest` already has the Visual C++ runtime, so smoke cannot prove the zip runs on a clean machine. The embeddable package bundles `vcruntime140.dll`. The numpy and scipy wheels also need `msvcp140.dll`, which a clean machine may lack. The first-release QA entry checks a clean Windows machine and names that file.

## 4. Mac download

### 4.1 Packaging options

| | Relocatable Python in `ACP.app` (python-build-standalone) | PyInstaller `.app` | py2app |
|---|---|---|---|
| The scan | `sys.executable` is a real Python. Works unchanged. | Same break as on Windows. | Python is embedded in a stub, so `sys.executable` is not a plain Python. |
| Build effort | Unpack, `pip install`, compile a stub. Shares steps with Windows. | Hook files for astropy and mocpy. | Setup file plus the same hook problem. |
| Same launcher as Windows | Yes. | No. | No. |

The first option is the recommendation, for the same reason as Windows.

### 4.2 Bundle layout

The release asset is `ACP-macos-arm64.zip`, linked as `releases/latest/download/ACP-macos-arm64.zip`. Only Apple Silicon is built. An Intel build is a second matrix entry that can be added if someone asks.

```
ACP.app/Contents/
  Info.plist            see below
  MacOS/ACP             compiled stub (Mach-O)
  Resources/ACP.icns    built by make_icons.py
  Resources/python/     python-build-standalone, pinned release, SHA-256 in packaging/pins.txt
  Resources/app/        same file set as Windows
```

Dependencies install into the bundled Python's own `lib/python3.12/site-packages`, so Python finds them with no path settings. `launcher.py` sits in `Resources/app/`, which Python puts on `sys.path` as the script folder.

The main executable is a compiled stub, about twenty lines of C built with `clang` on the runner. It finds its own path, builds the absolute path to `Resources/python/bin/python3`, and calls `execv` with `-X utf8 -B` and the launcher path. Notarisation requires the hardened runtime, and that attaches to a Mach-O file, not a script. LaunchServices starts a Mac app with `/` as its working folder, so no path in the stub is relative.

`Info.plist` carries:

- `CFBundleIdentifier`, `CFBundleExecutable` (`ACP`), `CFBundleShortVersionString` (from `VERSION`) and `LSMinimumSystemVersion`.
- `CFBundleIconFile` (`ACP`), and `LSUIElement` set to true, so there is no Dock icon.
- `NSDocumentsFolderUsageDescription`, `NSDesktopFolderUsageDescription`, `NSRemovableVolumesUsageDescription` and `NSNetworkVolumesUsageDescription`. Scanning an archive in Documents, on an external drive or on a NAS triggers privacy prompts, and a denied prompt would otherwise show only as `not_readable`.

The app has no window and no Dock icon. Its presence is a menu bar item (section 3.4) with Open ACP, a status line and Quit ACP. Double-clicking the app opens the browser, and starting it again while it runs only opens the browser again. Quit ACP on the settings page also works. pystray's Mac backend adds `pyobjc-framework-Quartz`, which brings more Mach-O files to sign, and the signing step already finds them by file magic. If the server fails to start before the browser opens, the launcher shows a native alert with the error and the log path, using `osascript -e 'display alert'`. No Terminal window opens. The launcher also reports an error after the browser has opened through the log only.

A signed bundle must not change after signing. Bytecode is compiled at build, `-B` is set, and the smoke test checks the bundle is unchanged after a run. macOS can run a downloaded app from a randomised read-only copy, which is another reason data lives in `~/Library/Application Support/ACP`.

### 4.3 Signing and notarising

Rohan adds these six secrets to a GitHub Environment named `release`, limited to the `main` branch and `v*` tags. Environment protection enforces that no pull request can read them.

| Secret | Holds |
|---|---|
| `MACOS_CERT_P12_BASE64` | The Developer ID Application certificate with its private key, exported as `.p12`, base64-encoded. |
| `MACOS_CERT_PASSWORD` | The password chosen at export. |
| `MACOS_SIGN_IDENTITY` | The identity name, such as `Developer ID Application: <name> (<team id>)`. |
| `NOTARY_KEY_P8_BASE64` | An App Store Connect API key (`.p8`), base64-encoded. |
| `NOTARY_KEY_ID` | That key's id. |
| `NOTARY_ISSUER_ID` | The issuer id shown beside the key. |

The API key is recommended over an Apple ID with an app-specific password, because it carries no personal login. If Rohan prefers the Apple ID route, three secrets change and the rest stays.

The signed job runs these steps when all six exist.

1. Create a temporary keychain with a random password generated in the job. Import the certificate. Run `security set-key-partition-list -S apple-tool:,apple: -k "$KEYCHAIN_PASSWORD"` and add the keychain to the search list. Without the first, `codesign` can wait on an access prompt that nobody answers.
2. Find every Mach-O file by file magic, not by extension, because wheels carry Mach-O files with other names. Sign each from the deepest path up with `--force --options runtime --timestamp`.
3. Sign `ACP.app` and run `codesign --verify --deep --strict`.
4. Zip with `ditto -c -k --keepParent`, then `xcrun notarytool submit --wait --timeout 30m`.
5. On rejection, print `notarytool log` (it holds no secrets) and fail.
6. Staple the ticket to the app, zip again, and write `SHA256SUMS-macos.txt`.
7. Delete the keychain and the decoded key file in a step that always runs.

Hardened runtime starts with no entitlements. If the smoke test shows the bundled Python cannot load an extension, the fix is the `com.apple.security.cs.disable-library-validation` entitlement in a committed file. Nothing else is added without a failing test.

Secret rules:

- A secret is referenced as `${{ secrets.X }}` only inside an `env:` block. The `run:` text reads it through the shell variable, as `"$X"`.
- Some tools take a value only as an argument, such as `security import -P`. That is allowed through the shell variable.
- No step uses `set -x` or `-v`. GitHub masks the values in logs.
- Decoded files are written under `$RUNNER_TEMP` with mode 600.
- The availability check records only `signed=true` or `signed=false`.

Without the secrets, the build signs ad hoc (`codesign --sign -`, which arm64 needs to run at all), skips notarising, and uploads `ACP-macos-arm64-UNSIGNED.zip` as a workflow artifact only. An unsigned build is never attached to a release, and the workflow summary says it will not open on another Mac without right-click Open. On a tag with missing secrets the job passes with a warning annotation.

The certificate name appears in the macOS "downloaded from the internet" dialog, so users see the name on Rohan's certificate.

### 4.4 Mac CI

`release-macos.yml` mirrors the Windows workflow on `macos-latest` (Apple Silicon): build, smoke, release, with the same triggers and the same release procedure. Smoke repeats K1 to K4 and checks the bundle is unchanged after a run. It also imports every package in `requirements.txt` with the bundle's Python, because a scan never loads `mocpy`, `paramiko` or its cryptography dependencies and a missing entitlement shows only when a module loads. On signed builds it copies the app, sets the quarantine attribute on the copy, and runs `codesign --verify`, `spctl --assess` and `stapler validate` on it, so `spctl` assesses it the way a downloaded app is assessed.

## 5. Linux download

### 5.1 What it is

The release assets are `ACP-linux-x86_64.tar.gz` and `ACP-linux-aarch64.tar.gz`, with `SHA256SUMS-linux-<arch>.txt`. The arm64 file targets 64-bit Raspberry Pi OS images such as Astroberry and StellarMate OS. A 32-bit Pi is not supported, and `start-acp` stops with a clear message when `uname -m` is not one of the two.

```
ACP/
  start-acp             POSIX shell script
  README.txt
  VERSION.txt
  python/               python-build-standalone (gnu), dependencies in its own site-packages
  app/                  same file set as Windows
```

`start-acp` resolves its own folder with `cd "$(dirname "$0")" && pwd`, then runs `exec "$dir/python/bin/python3" -X utf8 -B "$dir/app/launcher.py"`. There is no signing, so a script is fine. The launcher is the shared one, with these Linux differences.

- Data lives in `${XDG_CONFIG_HOME:-~/.config}/acp/data`, beside the settings file.
- With no `DISPLAY` and no `WAYLAND_DISPLAY`, it prints the URL instead of opening a browser. It also prints the SSH port-forward command that reaches ACP from another computer: `ssh -L 5555:127.0.0.1:5555 <user>@<host>`.
- The lock uses `fcntl.flock`.
- The tray is best effort, as section 3.4 describes. Without one, `start-acp` runs in the foreground as before.
- The applications-menu entry from section 3.5 is offered by the same tick box. Starting ACP from it opens no terminal, so the log file and the native dialog from section 3.3 are what the user sees.

### 5.2 Which distros

The floor is glibc 2.28. python-build-standalone and the numpy and scipy wheels set it, not the runner. That covers Debian 10 and newer, Ubuntu 20.04 and newer, RHEL 8 and newer, and Raspberry Pi OS from Bullseye. Builds run on `ubuntu-22.04` (x86_64) and `ubuntu-22.04-arm` (arm64), an older image than `ubuntu-latest`. The arm64 runner is free for public repositories, which I believe this one is. The smoke job also runs the tarball inside an `ubuntu:20.04` container on each architecture, which proves the glibc floor.

### 5.3 Build and CI

`packaging/linux/build.sh` follows the Windows steps: pinned Python archive with a SHA-256, `pip install --only-binary=:all: -c packaging/constraints.txt` into the bundled Python's site-packages, the trimmed file set, `compileall --invalidation-mode unchecked-hash`, then `tar --owner=0 --group=0 -czf`. The script checks that `start-acp` and `python/bin/python3` keep their executable bits.

`release-linux.yml` has a matrix of the two runners and the same triggers, jobs and release procedure as Windows. Size is expected in the same range as Windows. Phase 4 measures it.

Wheels for `mocpy` and `paramiko`'s cryptography dependency on aarch64 are a build assumption. If one is missing, the arm64 build fails at install and the spec's answer is to drop that dependency from the packaged set, not to compile.

### 5.4 Not in this phase

Binding off loopback. A headless Pi user will want to open ACP from a laptop, and the SSH forward covers that for now. A `--lan` option that refuses to start without `ACP_API_TOKEN` is a candidate follow-up, and it is out of scope here.

## 6. Updating

- A packaged user quits ACP from the settings page, replaces the folder (Windows and Linux) or the app (Mac), and starts it. Data and settings are elsewhere and carry over. ACP already migrates its own files at startup, as it does for gear.
- Downgrading is unsupported, and the docs say to back up the data folder first.
- Source and Docker users update as they do today.
- The app does not check for new versions. The settings page shows the installed version and a link to the releases page. An automatic check would be an outbound call that Rohan has not asked for.

## 7. Security

- The launcher binds `127.0.0.1` only and ignores `HOST`. A user with NINA on a separate observatory PC cannot reach a packaged ACP, and that is a deliberate limit. They use a source install or Docker with `ACP_API_TOKEN`. I expect no Windows Firewall prompt for a loopback-only listener, and the first release QA checks it.
- **Authorisation.** Restricted routes are the five marked "Authorised client only" in section 2.1, plus the path values from `GET /api/settings`. With `ACP_API_TOKEN` set, the existing gate already requires a bearer header or session cookie on every route, and the cookie counts. With no token, a restricted route answers 403 to anything but a loopback client with no `X-Forwarded-For`. Rohan's Brutix instance has a token set, so its browser reaches the new routes through its cookie, the same as today.
- **Cross-site requests.** Restricted routes that take a body require `Content-Type: application/json` (415 otherwise). A browser form cannot send that type without a preflight, which ACP never answers. `check-path` is one of them, because on Windows a UNC path makes the machine open an SMB connection and send its login hash. It must refuse before it touches the path. If an `Origin` header is present, the routes reuse `_origin_refused`.
- **DNS rebinding.** In packaged mode, `before_request` checks the `Host` header on every route for requests that carry no valid token or cookie. The host part must be `127.0.0.1`, `localhost`, `[::1]` or `::ffff:127.0.0.1`. Packaged mode has no reverse proxy, so this is safe there. In a source install the same check applies to the restricted routes only, because a same-machine proxy with a hostname would otherwise lock out its own users. `X-Forwarded-Host` is compared as `_origin_refused` already does.
- **Tray and shortcuts.** The tray's Quit calls the same `shutdown()` as the route. Setting `shortcuts` goes through `PUT /api/settings`, so it is a restricted route. Shortcut creation writes only the fixed files in section 3.5, builds them from the app's own folder, and takes no text from the request. The PowerShell script is fixed and gets paths through environment variables.
- Paths are validated as in section 2.4. They are read as scan roots only, and test C11 pins that a scan never writes inside a root.
- Responses carry error codes and fixed messages, not operating-system error text or paths from errors.
- Outbound calls from a packaged app are the MOC fetches ACP already makes, the astropy earth rotation tables it already downloads, and the VizieR queries when the user presses Download catalogues. The launcher itself never calls out.

## 8. Docs

- `docs/install.md` and the README open with three short sections: **Windows: download and run**, **Mac: download and run** and **Linux: download and run**. Each links to the version-free release asset. Each warns that the release page also lists "Source code (zip)", which is not the app.
- The Windows section says first to extract the zip to a short path such as `C:\ACP`, before the download link, because Explorer fails on long paths. It says extraction takes several minutes. It tells the user to right-click the zip, choose Properties and tick Unblock, then double-click `Start ACP.bat`. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. It says the first run offers a Start menu and desktop shortcut, and to use that afterwards. It says ACP then lives in the notification area, that Windows 11 may hide the icon behind the ^ arrow, and that Quit ACP in the icon's menu stops it.
- The Mac section says to open the zip, drag `ACP.app` to Applications and open it. macOS asks once to confirm a downloaded app. ACP then appears in the menu bar, with Open ACP and Quit ACP.
- The Linux section gives the `curl` and `tar` commands and `./ACP/start-acp`. It mentions the applications-menu entry, the tray on desktops that have one, and the SSH forward for a headless Pi.
- Each section says the NINA plugin's address is `http://127.0.0.1:5555`, and that a packaged ACP cannot be reached from another computer.
- The existing Windows, macOS and Linux sections stay as "install from source". Their environment variable and manifest script steps become "open `/setup`". That also changes the Linux terminal steps, which Rohan had left unchanged. `docs/setup-archive.md` keeps the variables as a reference for Docker and scripting.
- `docs/sharing.md` and the onboarding banner text point at `/setup`.

## 9. Tests

The build includes these tests, written against the spec. A reviewer checks that each fails when the behaviour is wrong.

### Settings (`tests/test_settings.py`)

| ID | Asserts |
|---|---|
| S1 | For `fits_roots`, `port` and `manifest_path`: environment beats file, file beats default. |
| S2 | An empty environment variable is treated as unset. |
| S3 | `FITS_ROOTS` splits on `;`, trims, and drops empty entries. The file accepts a list. |
| S4 | A write keeps unknown keys and is atomic. A corrupt file gives defaults, reports an error, and is saved as `.bak` once. A second corrupt file does not replace the first copy. |
| S5 | With platform and environment passed in, the config folder is correct for Windows, macOS and Linux (with and without `XDG_CONFIG_HOME`), and `ACP_HOME` overrides each. |
| S6 | With no file and no new variables, every path constant in `app.py` and the builder, and the port, equal today's values written as literal strings. |
| S7 | `PUT /api/settings` on a key set by the environment returns 409 `locked_by_env`. |
| S8 | `PUT` of `fits_roots` during a running scan returns 409. |
| S9 | `host` in the file has no effect. |
| S10 | With `ACP_DATA_DIR` set, every user-written file, including `catalogs.json`, resolves inside it and the three tracked files stay in `data/`. |
| S11 | `GET /api/settings` reports `source` as `env`, `file` and `default` for the same key under three setups. |
| S12 | Resolving the config folder with `Path.home()` raising returns "no settings file" and does not raise. |
| S13 | The builder, run by hand from a source checkout with roots only in the settings file, finds them. |
| S14 | `adopt-env` copies environment roots into the file and the file value is then shadowed until the variable is removed. |
| S15 | `shortcuts` round-trips as `null`, `true` and `false`. `PUT` of `shortcuts` where `available` is false returns 400 `unavailable`. `GET /api/settings` reports the `shortcuts` object to every client. |

### First run and paths (`tests/test_setup.py`)

| ID | Asserts |
|---|---|
| F1 | No roots and no manifest, with `Accept: text/html`: `first_run` is true and `GET /` redirects to `/setup`. |
| F2 | `?setup=skip` does not redirect. A request without `text/html` never redirects. |
| F3 | Roots from the environment, or from the file, make `first_run` false. |
| F4 | A manifest with no roots makes `first_run` false. |
| P1 | `not_absolute`, `not_found`, `not_a_directory`, `invalid` each return their code. |
| P2 | `not_readable` on a folder with mode 000 (skipped on Windows). |
| P3 | Surrounding quotes and whitespace are stripped, and `~` is expanded. |
| P4 | `duplicate` after `normcase(normpath())`, with the path stored as typed, and `too_many` at 33. |
| P5 | `overlaps` and `no_images_seen` return `ok` with a warning. `no_images_seen` finds an image in a subfolder. A UNC path passes the syntax check. |
| P6 | No response contains operating-system error text or the raw input path from an exception. |
| P7 | A folder read that blocks past 5 seconds returns `timed_out` and does not hold a waitress thread. |
| P8 | `PUT` with a `not_absolute` path saves nothing and returns 400 with the code. `PUT` with `port` returns 400. |
| P9 | A container started as a uid with no passwd entry, with Brutix-like environment variables, shows the read-only page, and its `GET /api/settings` has `writable: false`. |

### Scan and catalogues (`tests/test_scan_endpoint.py`)

| ID | Asserts |
|---|---|
| C1 | `POST /api/scan/start` with roots returns 202 and the status shows running. |
| C2 | Two requests fired together, with the subprocess stubbed to block, return one 202 and one 409. Only one subprocess starts. |
| C3 | No roots returns 400 `no_archive_roots` and spawns nothing. In packaged mode, `run_scan_now` itself refuses with no roots. |
| C4 | The subprocess environment holds `FITS_ROOTS` and `MANIFEST_PATH` from the file, and the environment variable wins when both exist. |
| C5 | Success sets exit code 0 and a final `progress`. Failure returns an error kind and a fixed message with no path in the JSON. |
| C6 | A missing or corrupt `scan_progress.json` leaves `/api/scan/status` valid. The writer never writes a path, and a failed write does not fail the scan. |
| C7 | On `PUT`, `check-path`, `adopt-env`, `scan/start`, `catalogues/fetch` and `quit`: a non-JSON content type returns 415, a mismatched `Origin` returns 403, and a non-loopback `Host` on a packaged bind returns 400. `check-path` refuses before it touches the path. |
| C8 | With no token, a non-loopback client, or one with `X-Forwarded-For`, gets 403 on every restricted route. `GET /api/settings` for that client has no path values. `GET /setup` still serves. |
| C9 | With `ACP_API_TOKEN` set, a request with the bearer header, or with the session cookie, is authorised. A request with neither gets 401. |
| C10 | The scheduled scan passes its existing tests. A case where `scan_cron` comes from the file starts the scheduler. |
| C11 | A scan of a fixture folder leaves every file in it unchanged. The fixture comes from `tests/fixtures/make_hostile_archive.py`, which writes real FITS files. |
| C12 | `POST /api/catalogues/fetch` runs once at a time, the status holds no paths, and the file is written to the data directory. The script is stubbed. |

### Packaging

| ID | Asserts |
|---|---|
| L1 | `tests/test_launcher.py`, run in a subprocess because `app.py` reads its environment at import: an explicit busy port stops with a message naming it, a held lock opens the browser at the recorded port without starting a server, a second launch during a slow first start starts nothing, the browser opener is called only after `/api/version` answers, and `ACP_NO_BROWSER=1` suppresses it. |
| L2 | `tests/test_launcher.py`: the server binds `127.0.0.1` even when `HOST=0.0.0.0`. `POST /api/quit` exists only when `ACP_PACKAGED=1`, and returns 409 during a scan. |
| K1 | Smoke, each platform: unzip to a fresh folder, start with `ACP_HOME` in a temp folder, `ACP_NO_BROWSER=1`, and `Accept: text/html` on page requests. `/api/version` answers within 30 seconds with the tag's version. |
| K2 | Smoke: `GET /` redirects to `/setup`. `PUT /api/settings` with a fixture folder and `POST /api/scan/start` finish with exit code 0. `ACP_HOME` and the fixture path contain non-ASCII characters. `/api/extensions/manifest` lists the Target Scheduler sync extension (confirm the exact entry when building). |
| K3 | Smoke: after a run, the unzipped folder has no new files, and `settings.json` and `data/` exist under `ACP_HOME`. |
| K4 | Smoke: `POST /api/quit` stops the process and the port is free. |
| K5 | Build: the tag equals `VERSION`, the archive is under 400 MB, the longest path inside is under 150 characters, and it contains the launcher script, the bundled Python and `bundled_extensions/nina_ts_sync`. |
| K6 | Windows smoke prints `sys.flags` from the zip's Python and asserts `utf8_mode` is 1 and `dont_write_bytecode` is 1. A scan subprocess also runs with those flags. |
| K7 | Linux smoke runs K1 to K4 on x86_64 and arm64, and again inside `ubuntu:20.04` on each, with `ACP_NO_TRAY=1`. |
| K8 | Windows and Mac smoke start with the tray enabled. `/api/version` must answer. If the runner cannot show a tray, the log says "tray unavailable" and the server still answers. |
| T1 | `tests/test_launcher.py`, with pystray stubbed: Open ACP opens the browser at the bound port. The status line reads "ACP is running" or "Scanning: reading, N files" from a stubbed scan state. Quit is disabled during a scan and enabled after. |
| T2 | With pystray stubbed: Quit runs the same `shutdown()` as `POST /api/quit`, releases the lock and stops the server thread. The tray runs on the main thread and waitress on another. |
| T3 | A tray that raises on start, `ACP_NO_TRAY=1`, no `DISPLAY` on Linux, and no owner for `_NET_SYSTEM_TRAY_S0` each leave the server running in the main thread and log the reason. |
| T4 | The startup error reporter calls `MessageBoxW` on Windows, `osascript` on Mac, and the terminal, `zenity` or `kdialog` on Linux, with those calls stubbed. |
| W1 | Windows CI: `shortcuts.create()` with the Start menu and desktop folders redirected to a temp folder. It reads each `.lnk` back through PowerShell and checks target, arguments, working folder and icon. The app folder has spaces and non-ASCII characters. Remove deletes both. A moved folder rewrites only the links that still exist. |
| W2 | Windows smoke starts through `Start ACP.bat` and through a shortcut. In both, the ACP process is `pythonw.exe` and has no `conhost.exe` child. |
| X1 | Linux: the `.desktop` file has an absolute quoted `Exec`, an absolute `Icon`, `Terminal=false`, and passes `desktop-file-validate`. It is written only when `shortcuts` is true, and rewritten when the folder moves. |
| I1 | `make_icons.py` writes an `.ico` holding all seven sizes, the PNGs at their stated sizes, and an `.icns` on the Mac runner. Its polygon points equal the paths in `static/favicon.svg`. |
| M1 | Mac smoke repeats K1 to K4, checks the bundle is unchanged after a run, and imports every package in `requirements.txt`. |
| M2 | Mac signed builds pass `codesign --verify --deep --strict` on the app, and `spctl --assess` and `stapler validate` on a quarantined copy. |
| M3 | A workflow lint test (`tests/test_workflows.py`) fails if a release workflow contains `set -x`, a `secrets.` reference outside an `env:` block, or a signing step that can run on `pull_request`. |
| M4 | A run with no secrets produces an `-UNSIGNED` artifact and attaches nothing to a release. |

## 10. Phases

Each phase merges on its own and leaves the app working.

| Phase | Contents | Merge value |
|---|---|---|
| 1 | Settings, setup page and catalogue button. Two pull requests in order: 1a is `acp_settings.py`, `data_path`, `ACP_DATA_DIR`, the claim/run split of `run_scan_now` and the settings routes (S tests). 1b is the setup page, scan start, progress, first-run redirect and the catalogue fetch (F, P, C tests), plus the docs for source installs. | Mac and Linux source users and Docker users stop setting environment variables. |
| 2 | `launcher.py`, `/api/quit`, the tray, `shortcuts.py` and its tick box and settings row, `make_icons.py`, `requirements-packaged.txt`, `packaging/` constraints and pins, `packaging/windows`, the Windows workflow and docs (L, K, T, W, I tests). Measures the real size. | Windows users get a download. |
| 3 | `packaging/macos`, the stub, the menu bar item and its `pyobjc` dependency, `ACP.icns`, signing and notarising, the Mac workflow and docs (M tests). Needs Rohan's secrets for a signed result, and works without them. | Mac users get a signed download. |
| 4 | `packaging/linux`, `start-acp`, the best-effort tray, the `.desktop` entry, the Linux workflow and docs (K7, X1). | Linux and Raspberry Pi users get a download. |

Phases 3 and 4 reuse `launcher.py`, the tray code, `shortcuts.py` and `make_icons.py` from phase 2, so they follow it. None of the tray or shortcut code goes in phase 1, because it needs the packaged layout to run. Phase 1 adds nothing for it. Phases 3 and 4 do not depend on each other. All three packaged phases need phase 1, because a packaged app has no other way to set image folders.

## 11. Out of scope

- Code signing for Windows (the SmartScreen step stays in the docs).
- An Intel Mac build.
- An installer (MSI, or a Mac DMG).
- Auto-update, and an automatic check for new versions.
- A server-side folder browser.
- A page control for `scan_cron`.
- A packaged build that listens off loopback, including a Linux `--lan` option.
- A 32-bit Raspberry Pi build.
- A menu-bar icon or Dock icon for the Mac app.
- Shipping `catalogs.json` in a release.
- Starting ACP at login, and desktop notifications from the tray.
- A tray on GNOME or native Wayland, and tray items beyond Open ACP, the status line and Quit ACP (no Open log folder, no pause).
- Shortcuts on Mac, and a Linux desktop-folder shortcut.

## 12. Limits of this spec

I have not built or run any of it. These points rest on reading the code or on general knowledge and need checking in the build.

- The embeddable package's `._pth` behaviour with environment variables and `-X utf8`.
- The builder can report file counts for progress, its step numbering, and the claim that it never writes inside an image folder.
- Wheels exist for Python 3.12 on Windows x64, macOS arm64, Linux x86_64 and Linux aarch64 for every package in `requirements.txt`, including `mocpy`.
- The bundled Mac Python loads all its extensions under hardened runtime without extra entitlements, and notarisation accepts the stub.
- `ubuntu-22.04-arm` is available to this repository.
- The licence terms of the four VizieR tables.
- pystray's Mac backend runs under the stub's `execv` as an accessory app, and tints a menu bar template image.
- pystray's `xorg` backend shows an icon on the Raspberry Pi desktop, and `_NET_SYSTEM_TRAY_S0` is a reliable test for a tray.
- `server.close()` from another thread ends waitress's loop cleanly. If it does not, `shutdown()` ends the process after flushing the log.
- GitHub's Windows and Mac runners can show a tray icon. K8 tolerates a runner that cannot.

## Review changes

Two independent reviews of the earlier version produced these changes. A means the first review, B the second.

| Finding | Change |
|---|---|
| A1, B2 | `check-path` and every path-revealing route need an authorised client. `check-path` requires JSON and refuses before touching the path. `GET /api/settings` hides paths from others. |
| A2 | The `._pth` file ignores environment variables, so `-X utf8 -B` go on the command line. K6 checks `sys.flags`. K2 uses non-ASCII paths. |
| A3 | A lock file replaces the port-moving rule. The port move is dropped. B asked to log the chosen port, which no longer applies. |
| A4, B1 | The app folder is `git ls-files` minus five folders. K5 and K2 check the bundled extension. |
| A5 | Every launcher path is absolute. The `.bat` checks for the bundled Python and explains the Explorer case. |
| A6 | `acp_settings.py` stays at the root and the builder inserts the repo root. S13 tests a hand-run builder. |
| A7 | Release assets have no version in their names, and the docs link to `latest/download`. |
| A8, B12 | `compileall --invalidation-mode unchecked-hash`, and K1 has a 30-second start budget. |
| A9 | `run_scan_now` splits into claim and run. C2 fires two requests. |
| A10, B7 | `PUT` re-runs every check. Add saves at once. Duplicate uses `normcase(normpath())`. `no_images_seen` walks subfolders. |
| A11 | The catalogue question is decided in section 2.6: a button. I found no licence record and did not verify CDS terms, so shipping the file is not safe to assume. |
| A12 | Folder reads time out at 5 seconds with `timed_out`. |
| A13 | Mac signing steps added: partition list, keychain search list, Mach-O by magic, `--force`, plist keys, GitHub Environment, import smoke, quarantined `spctl`. |
| A14, B8 | Config folder resolution never raises. Section 2.3 defines the read-only page. The cookie counts. The NAT remark is dropped. |
| A15 | The `Host` check covers every route in packaged mode, and restricted routes in source installs, reusing `_origin_refused`. B4 asked to skip it for token holders, which is adopted. |
| A16 | The Windows workflow runs on every push and non-docs pull request. Checksum files are per platform. One shared release procedure creates the release if missing. |
| A17 | `packaging/constraints.txt` pinned with `==`, updated by Dependabot. |
| A18 | Quit returns 409 during a scan and uses `server.close()`. The browser falls back after 90 seconds. `run_scan_now` refuses without roots in packaged mode. Logs capture `print`. The outbound sentence is corrected. F1 uses the `Accept` header. S6 uses literals. |
| B3 | The Mac main executable is a compiled stub. |
| B4 | The token rule says the cookie counts. `ACP_BIND_HOST` carries the real bind. The `Host` check skips token holders. |
| B5 | The launcher sets the config folder first, then `ACP_DATA_DIR` from it, before `app.py` imports. |
| B6 | A release procedure is in section 3.9. |
| B9 | Locked roots get unlock instructions and a "Copy to settings file" button. |
| B10 | Help text for copying a path sits beside the box. |
| B11 | The longest path is under 150 characters, the top folder is `ACP`, and the extract-first line leads the docs. |
| B13 | A stage table maps stages to the builder's steps. The build confirms the numbering. |
| B14 | Covered under A18. |
| B15 | The secret rule is reworded to match M3. |
| B16 | Added S5 parameters, C10 file case, the fixture source, S11, S4 once, and L1 and L2 in subprocesses. |
| B17 | Linux docs change is stated in section 8. The two-folder note on Mac is in section 1.1. |
| Smaller | The NINA address, the HOST limit, the pinned `.bat` and `msvcp140.dll` are in the docs and QA. |
| Follow-up 3 | Rohan asked for shortcuts and a tray icon. Sections 3.4 and 3.5 add them, and sections 2.3, 4.2, 5.1, 7, 8, 9 and 10 follow. |
| Open questions | Settled with the defaults from the brief: token cookie counts, first release 1.0.0, Apple Silicon only, native alert instead of Terminal, no shipped catalogues. |

## Phase 2 build notes

What the Windows build changed from this spec, and what it could not check.

| Spec text | What phase 2 does | Why |
|---|---|---|
| Section 3.7 step 6: `compileall --invalidation-mode unchecked-hash` | `checked-hash` | A hash survives zip's two-second time rounding either way. `unchecked-hash` also ignores an edited `.py` file, which breaks the "fix in place" row in section 3.1. Checking costs one hash per import. |
| Section 3.3 step 4: port in use is found when waitress binds | The launcher also tries to connect to the port first | On Windows, `SO_REUSEADDR` lets a second program bind a port that is already listening, so a bind does not always fail. |
| Section 3.7 step 4: delete `tests` folders | Keeps `astropy/tests` | astropy imports it when it starts. |
| Section 3.1 and 9, K6: scan runs with `-X utf8 -B` | `app.py` repeats its own `-X utf8` and `-B` flags on the scan and catalogue commands | The `._pth` file makes the embedded Python ignore the environment variables a child would otherwise inherit. |
| Section 7: Host check on every route in packaged mode | Added as a `before_request` in `app.py` | It was not part of phase 1, which has no packaged mode to apply it to. |
| `POST /api/quit` "exists only when `ACP_PACKAGED=1`" | The route is always registered and answers 404 when not packaged | The result is the same to a client, and it can be tested without importing `app.py` twice. |
| Section 3.2: `python312._pth` | Written for the Python minor in `packaging/pins.txt` (3.12.10, the last 3.12 with a Windows embeddable package) | |

Not checked, because the work was done on a Mac with no network route to python.org or PyPI: the real zip size (section 3.8 still shows estimates), the `._pth` behaviour, `msvcrt` locking, PowerShell shortcut creation, wheel availability for every pin on Python 3.12 Windows, and whether `pystray` shows an icon on a runner. The Windows workflow checks each of these.
