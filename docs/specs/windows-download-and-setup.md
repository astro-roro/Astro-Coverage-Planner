# Spec: Windows and Mac downloads, and an in-app first-run setup

This spec removes the terminal from installing ACP. A first-run page takes the image folders and runs the scan. A Windows zip and a signed Mac app bundle Python, so nobody installs it. The work ships in three phases that each merge alone.

## Decisions

| Question | Choice |
|---|---|
| Where settings live | A JSON file in a per-OS folder. Environment variables still win. |
| How a user picks a folder | A typed or pasted path, checked by the server. No folder browser in this change. |
| Windows packaging | The python.org embeddable package, not PyInstaller. |
| Mac packaging | A relocatable Python inside `ACP.app`, signed and notarised. Not PyInstaller or py2app. |
| What the user starts | `Start ACP.bat` on Windows, `ACP.app` on Mac. |
| Updating | Replace the app. Data lives elsewhere and stays. No automatic update check. |
| Linux | Unchanged install (Docker or terminal), minus the environment variable steps. |

## Terms

- **Settings file**: `settings.json`, the file the app reads and writes for the keys in section 1.
- **Config folder**: the per-OS folder that holds the settings file.
- **Data directory**: the folder for files the user creates, such as the manifest, plans and gear. Today it is `<repo>/data`.
- **Packaged mode**: ACP started by the Windows or Mac launcher. The launcher sets `ACP_PACKAGED=1`.
- **Effective value**: the value ACP uses after applying precedence in section 1.3.

## 1. Settings file

### 1.1 Where files live

| OS | Settings file | Data directory in packaged mode | Data directory in a source install |
|---|---|---|---|
| Windows | `%APPDATA%\ACP\settings.json` | `%APPDATA%\ACP\data` | `<repo>\data` |
| macOS | `~/Library/Application Support/ACP/settings.json` | `~/Library/Application Support/ACP/data` | `<repo>/data` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/acp/settings.json` | not packaged | `<repo>/data` |

`ACP_HOME` replaces the config folder on every OS. The smoke tests use it to stay out of the real profile. Packaged mode also writes `logs/acp.log` (1 MB, three files) beside the settings file.

### 1.2 Keys

| Key | Environment variable | Default | Notes |
|---|---|---|---|
| `fits_roots` | `FITS_ROOTS` | none | List of folder paths. The environment form is semicolon-separated, as today. |
| `manifest_path` | `MANIFEST_PATH` | `<data directory>/manifest.json` | Shown on the setup page, edited in the file only. |
| `port` | `PORT` | 5555 | Shown on the setup page, edited in the file only. Needs a restart. |
| `scan_cron` | `ACP_SCAN_CRON` | unset | File and environment only. No page control in this change. |

The file also carries `"version": 1`. `HOST` stays environment-only on purpose, so no file or page can open ACP to the network. The data directory is set by `ACP_DATA_DIR` only, because the file cannot say where the file is.

### 1.3 Precedence

The effective value is the environment variable if it is set and not empty, else the settings file, else the default. An empty variable counts as unset, because compose files often declare blank ones.

A new stdlib-only module, `acp_settings.py`, implements this. `app.py` and `scripts/build_archive_manifest.py` both import it, so a scan started by hand sees the same roots as one started from the page. When the app starts a scan it also passes the effective values to the builder as environment variables. The builder keeps its built-in fallback root for the maintainer's setup.

### 1.4 The data directory

`ACP_DATA_DIR` moves every file ACP writes. That covers the manifest, plans, gear and sites, and also the destinations, saved searches and hidden lists. It covers target overrides, Target Scheduler templates and fingerprints, and it covers exports, live output and the MOC cache. The activity database, scan cache and target ids move too. A helper, `data_path(name)`, replaces each `REPO_ROOT / "data" / name` default in `app.py` and the builder. The per-file variables such as `PLANS_PATH` still win.

Three tracked files stay in the app folder and are read-only: `catalog_registry.json`, `filter_catalogue.json` and `surveys.json`. A packaged build cannot keep user files beside them, so the split is required.

### 1.5 Existing installs and Docker

With no settings file and none of the new variables, every path and the port resolve to today's values. Phase 1 proves this with a test (S6). The Dockerfile does not change.

A Docker deployment sets `FITS_ROOTS`, so the page shows folders as "set by FITS_ROOTS" and locks them. Docker clients arrive from the bridge address, so section 6 makes the page read-only there. Docker users keep configuring by environment.

### 1.6 Writing

- The write is atomic, using the existing `target_ids.atomic_write_json`, and keeps keys it does not know.
- A corrupt file is not overwritten. ACP starts on defaults, shows an error on the setup page, and saves the bad file as `settings.json.bak` before the next write.
- A write to a key set by the environment is refused with 409 and `locked_by_env`.
- A write to `fits_roots` while a scan runs is refused with 409. Changing roots under a running builder would make its output mix two archives.

## 2. The setup page

### 2.1 Routes

| Route | Purpose |
|---|---|
| `GET /setup` | The page. |
| `GET /api/settings` | Effective values, each with its source (`env`, `file`, `default`), a `locked` flag, `first_run`, `writable`, and the version. |
| `PUT /api/settings` | Saves `fits_roots`. Other keys are rejected. |
| `POST /api/settings/check-path` | Validates one path and returns codes. Writes nothing. |
| `POST /api/scan/start` | Starts one scan. Returns 202, or 409 if one is running. |
| `GET /api/scan/status` | Existing route, gains a `progress` object. |

### 2.2 First run

First run is true when there are no effective roots and no manifest file at the effective manifest path. A browser `GET /` then redirects to `/setup`, unless the address carries `?setup=skip`. The skip link is on the setup page and shows today's empty map with its banner.

A user with a manifest but no roots is not first run. They see the onboarding banner, which now links to `/setup`. API routes never redirect.

### 2.3 The page

- A list of folders, each with a Remove button.
- One text box and an Add button. The box accepts a pasted path and strips surrounding quotes, because Windows "Copy as path" adds them.
- A Scan button, enabled with at least one folder.
- A progress panel with the stage, files seen and elapsed time.
- On success, a line with the number of targets and a button to the map. The page continues to `/` after three seconds.
- On failure, the message for the error kind and a Change folders button.

The same URL is the settings page afterwards. A "Settings" link in the header opens it. It also shows the data folder, the manifest path, the port, and the version. In packaged mode it shows a Quit ACP button (section 3.3).

### 2.4 Path checks

`check-path` is a pure function plus one bounded directory read. It returns a code, never operating-system error text.

| Code | Meaning |
|---|---|
| `ok` | Absolute, exists, is a folder, can be listed. |
| `not_absolute` | Relative paths are refused. `~` is expanded first. |
| `not_found` | Nothing at that path. |
| `not_a_directory` | A file. |
| `not_readable` | The folder cannot be listed. |
| `invalid` | Contains a NUL or control character, or is longer than 1,000 characters. |
| `duplicate` | Already in the list after normalisation. |
| `too_many` | More than 32 folders. |

Two warnings do not block: `overlaps` (inside or containing another listed folder) and `no_images_seen` (no `.fit`, `.fits`, `.fts` or `.xisf` file in the first 5,000 entries). A drive root gets a `very_large` warning. UNC paths such as `\\nas\astro` are accepted, since many users scan a NAS.

### 2.5 Running the scan

`POST /api/scan/start` calls the existing `run_scan_now("setup")` on a worker thread. That function already refuses a second scan and runs the builder as a subprocess, so a crash cannot take the app down. If no roots are effective, the route answers 400 `no_archive_roots` and starts nothing.

The builder gains a progress writer. It writes `scan_progress.json` beside the manifest, atomically, at each stage change and every two seconds. Stages are a fixed list (`listing`, `reading`, `matching`, `writing`). The file contains the stage, `files_seen`, and a timestamp. `/api/scan/status` returns those and the existing fields. It returns no paths and no builder output, because the status route is unauthenticated in the stock configuration.

The error text for `no_archive_roots` changes from "check FITS_ROOTS" to "check the image folders in Settings".

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

The embeddable package wins on the first row alone. PyInstaller would force changes to the scan design that Docker and source installs rely on. The cost is file count, which section 3.5 trims.

### 3.2 Zip layout

```
ACP-<version>-windows-x64\
  Start ACP.bat
  README.txt            three lines: how to start, how to stop, where data lives
  python\               embeddable runtime, with python312._pth edited
  site-packages\        dependencies from requirements.txt
  app\                  app.py, acp_settings.py, launcher.py, templates, static, scripts, data (tracked files only)
```

`Start ACP.bat` sets `PYTHONUTF8=1` and `PYTHONDONTWRITEBYTECODE=1`, runs `python\python.exe app\launcher.py`, and pauses if it exits with an error. The console window stays open and shows the log. Closing it stops ACP.

### 3.3 The launcher

`launcher.py` is shared with the Mac build. It does these steps in order.

1. Set the config folder, `ACP_DATA_DIR` and `ACP_PACKAGED=1`, and open the log file.
2. Read the effective port.
3. If a server on `127.0.0.1:<port>` answers `/api/version` with a `version` key, open the browser on it and exit. This makes a second double-click harmless.
4. If another program is using the port, the launcher tries the next 20 ports when the port is the default. When the user set the port, it stops with a message.
5. Start waitress bound to `127.0.0.1` in the main thread. The launcher ignores `HOST`.
6. A background thread polls `/api/version` for up to 90 seconds, then opens the browser. `ACP_NO_BROWSER=1` skips this, for tests.

Packaged mode also enables `POST /api/quit`, which stops waitress. It needs a loopback client and the same-origin rules in section 6. The setup page's Quit ACP button calls it. Mac needs it because that app has no window to close.

### 3.4 Where data lives

The unzipped folder is never written to. Settings, data and logs go to `%APPDATA%\ACP`. Bytecode is compiled at build time and `PYTHONDONTWRITEBYTECODE=1` stops new files. An upgrade is: quit ACP, delete the old folder, unzip the new one.

### 3.5 Build

`packaging/windows/build.ps1` runs on `windows-latest`.

1. Download the embeddable zip for the pinned Python 3.12 patch release and check its SHA-256 against a value in `packaging/pins.txt`.
2. Enable `site-packages` and the app folder in `python312._pth`.
3. Install with the runner's Python 3.12: `pip install --target site-packages --only-binary=:all: -r requirements.txt`. A package with no wheel fails the build instead of compiling.
4. Delete `tests`, `__pycache__` and `*.dist-info/RECORD` noise, then run `compileall`.
5. Copy the app files and write `Start ACP.bat` and `README.txt`.
6. Zip, write `SHA256SUMS.txt`.

### 3.6 Size

These are estimates from typical wheel sizes. I have not built it. Phase 2 measures the real figures and writes them here.

| | Estimate |
|---|---|
| Unpacked | 400 to 600 MB |
| Zip | 150 to 250 MB |
| CI limit | The build fails if the zip passes 400 MB. |

### 3.7 CI

A new workflow, `release-windows.yml`, runs on tags matching `v*`, on manual dispatch, and on pull requests that change `packaging/`, `launcher.py` or the workflow. Three jobs.

- **build**: runs `build.ps1`, checks that the tag equals `VERSION` in `app.py`, uploads the zip as a workflow artifact.
- **smoke**: downloads the artifact and unzips it to a different folder from the build. It starts ACP and checks it, as listed in section 8 (K1 to K5). The python it uses is the zip's, never the runner's.
- **release**: runs only on tags, after smoke. It attaches the zip and `SHA256SUMS.txt` to the GitHub release. It is the only job with `contents: write`.

`windows-latest` already has the Visual C++ runtime, so smoke cannot prove the zip runs on a clean machine. The embeddable package bundles `vcruntime140.dll`, which I believe is enough. A clean Windows machine check goes in the QA queue for the first release.

## 4. Mac download

### 4.1 Packaging options

| | Relocatable Python in `ACP.app` (python-build-standalone) | PyInstaller `.app` | py2app |
|---|---|---|---|
| The scan | `sys.executable` is a real Python. Works unchanged. | Same break as on Windows. | Python is embedded in a stub, and `sys.executable` is again not a plain Python. |
| Build effort | Unpack, `pip install --target`, write a launcher. Shares steps with Windows. | Hook files for astropy and mocpy. | Setup file plus the same hook problem, with less community help for numpy 2 and scipy. |
| Signing | Sign every Mach-O in a plain tree. | Same, in a denser layout. | Same. |
| Same code as Windows | Yes, `launcher.py` is shared. | No. | No. |

The first option is the recommendation, for the same reason as Windows: the scan design survives and one launcher serves both platforms.

### 4.2 Bundle layout

```
ACP.app/Contents/
  Info.plist            LSUIElement true (no Dock icon), CFBundleExecutable ACP
  MacOS/ACP             shell script: exec Resources/python/bin/python3 Resources/app/launcher.py
  Resources/python/     python-build-standalone, pinned release, SHA-256 in packaging/pins.txt
  Resources/site-packages/
  Resources/app/
```

The app has no Dock icon and no window. Double-clicking opens the browser. Starting it again while it runs only opens the browser. To stop it, the user presses Quit ACP on the settings page. Activity Monitor lists the process as `python3.12`. If Rohan prefers a visible window, the fallback is to open the server in a Terminal window; that is a launcher change only.

A signed bundle must not change after signing. Bytecode is compiled at build, `PYTHONDONTWRITEBYTECODE=1` is set, and the smoke test checks that the bundle is unchanged after a run. macOS may run a downloaded app from a randomised read-only copy (App Translocation), which is another reason data lives in `~/Library/Application Support/ACP`.

### 4.3 Architectures

| Build | Runner | Status |
|---|---|---|
| Apple Silicon (arm64) | `macos-latest` | Required. |
| Intel (x86_64) | The current Intel runner label (`macos-15-intel` as far as I know; confirm when building) | Included as a second matrix entry. If GitHub retires the label, the entry is dropped and the docs say Apple Silicon only. |

Both entries run the same script with a different Python build and wheel platform. A universal2 bundle is not attempted, because numpy and scipy ship separate wheels per architecture. Zips are named `ACP-<version>-macos-arm64.zip` and `ACP-<version>-macos-x86_64.zip`.

### 4.4 Signing and notarising

Rohan adds these repository secrets himself. The workflow names them and never prints them.

| Secret | Holds |
|---|---|
| `MACOS_CERT_P12_BASE64` | The Developer ID Application certificate with its private key, exported as `.p12`, base64-encoded. |
| `MACOS_CERT_PASSWORD` | The password chosen at export. |
| `MACOS_SIGN_IDENTITY` | The identity name, such as `Developer ID Application: <name> (<team id>)`. |
| `NOTARY_KEY_P8_BASE64` | An App Store Connect API key (`.p8`), base64-encoded. |
| `NOTARY_KEY_ID` | That key's id. |
| `NOTARY_ISSUER_ID` | The issuer id shown beside the key. |

The API key is recommended over an Apple ID with an app-specific password, because it carries no personal login. If Rohan prefers the Apple ID route, three secrets change and the rest stays.

When all six exist, the signed job runs these steps.

1. Create a temporary keychain with a random password generated in the job, import the certificate into it.
2. Sign every Mach-O file (`.so`, `.dylib`, and executables under `python/bin`) from the deepest path up, with `--options runtime --timestamp`.
3. Sign `ACP.app` and run `codesign --verify --deep --strict`.
4. Zip with `ditto -c -k --keepParent`, then `xcrun notarytool submit --wait --timeout 30m`.
5. On rejection, print `notarytool log` (it holds no secrets) and fail.
6. Staple the ticket to the app, zip again, and run `spctl --assess --type execute` and `stapler validate`.
7. Delete the keychain and the decoded key file in a step that always runs.

Hardened runtime starts with no entitlements. If the smoke test shows the bundled Python cannot load its extensions, the fix is the `com.apple.security.cs.disable-library-validation` entitlement in a committed file. Nothing else is added without a failing test.

Secret handling rules:

- A secret appears only in the `env:` block of the step that needs it, never inside `run:` text and never as a command argument.
- No step uses `set -x` or `-v`.
- Decoded files are written under `$RUNNER_TEMP` with mode 600.
- The availability check records only `signed=true` or `signed=false`.
- Signing runs on tags and on manual dispatch from the main branch. Pull requests, including those from forks, never receive the secrets.

Without the secrets, the build signs ad hoc (`codesign --sign -`, which arm64 requires to run at all), skips notarising, and uploads `ACP-<version>-macos-<arch>-UNSIGNED.zip` as a workflow artifact only. An unsigned build is never attached to a release. On a tag with missing secrets the job passes with a warning annotation.

The certificate name appears in the macOS "downloaded from the internet" dialog, so users see the name on Rohan's certificate.

### 4.5 Mac CI

`release-macos.yml` mirrors the Windows workflow: build, smoke, release, with the same triggers. Smoke runs the app from an unzipped copy with `ACP_HOME` in a temp folder and `ACP_NO_BROWSER=1`, and repeats the Windows checks. On signed builds it also runs `codesign --verify`, `spctl --assess` and `stapler validate` on the unzipped app.

## 5. Updating

- A packaged user quits ACP, replaces the folder (Windows) or the app (Mac), and starts it. Data and settings are elsewhere and carry over. ACP already migrates its own files at startup, as it does for gear.
- Downgrading is unsupported, and the docs say to back up the data folder first.
- Source and Docker users update as they do today (`git pull` and `pip install -r requirements.txt`, or pull the image).
- The app does not check for new versions. The setup page shows the installed version and a link to the GitHub releases page that opens in a new tab. An automatic check would be the first outbound call from the packaged app other than MOC fetches, and Rohan has not asked for one.
- The release tag is `v<VERSION>`, and the build fails if it differs from `VERSION` in `app.py`.

## 6. Security

- The launcher binds `127.0.0.1` only and ignores `HOST`. Packaged builds add no network exposure. I expect no Windows Firewall prompt for a loopback-only listener, and the first release QA checks it.
- Three routes change state: `PUT /api/settings`, `POST /api/scan/start` and `POST /api/quit`. Each accepts a request only from a loopback address with no `X-Forwarded-For` header. A matching `ACP_API_TOKEN` is the other way in. This closes the case where someone sets `HOST=0.0.0.0` with no token and a LAN peer adds `C:\` as a root. It also makes the page read-only in Docker, where requests come from the bridge address.
- Those routes require `Content-Type: application/json` (415 otherwise), which blocks cross-site form posts. If an `Origin` header is present it must match the `Host` header. When ACP is bound to loopback, the `Host` header must be `127.0.0.1`, `localhost` or `[::1]`, which blocks DNS rebinding.
- The existing access gate already covers `/setup` and every new route when `ACP_API_TOKEN` is set.
- Paths are validated as in section 2.4. They are read as scan roots only. The builder's reads of image folders are read-only; I have not checked that the builder never writes inside a root, and test C11 pins it.
- Responses carry error codes and fixed messages, not operating-system error text or paths from errors.
- The launcher never runs a downloaded file and never calls out to the network.

## 7. Docs

- `docs/install.md` and the README open with two short sections, **Windows: download and run** and **Mac: download and run**.
- The Windows section tells the user to right-click the zip, choose Properties and tick Unblock. It then says to extract to a short path such as `C:\ACP`, because long paths break some unzip tools. It then says to double-click `Start ACP.bat`. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. It says to keep the black window open and press Quit ACP on the page, or close the window, to stop.
- The Mac section says to open the zip, drag `ACP.app` to Applications and open it. macOS asks once to confirm a downloaded app. It says which zip to pick (Apple menu, About This Mac, Chip).
- The existing Windows, macOS and Linux sections stay as "install from source". Their environment variable and manifest script steps become "open `/setup`". `docs/setup-archive.md` keeps the variables as a reference for Docker and scripting.
- `docs/sharing.md` and the onboarding banner text are updated to point at `/setup`.

## 8. Tests

The build includes these tests, written against the spec. A reviewer checks that each fails when the behaviour is wrong.

### Settings (`tests/test_settings.py`)

| ID | Asserts |
|---|---|
| S1 | For `fits_roots`, `port` and `manifest_path`: environment beats file, file beats default. |
| S2 | An empty environment variable is treated as unset. |
| S3 | `FITS_ROOTS` splits on `;`, trims, and drops empty entries. The file accepts a list. |
| S4 | A write keeps unknown keys and is atomic. A corrupt file gives defaults, reports an error, and is saved as `.bak` before any write. |
| S5 | The config folder is correct for Windows, macOS and Linux (with and without `XDG_CONFIG_HOME`), and `ACP_HOME` overrides each. |
| S6 | With no file and no new variables, every path constant in `app.py` and the builder, and the port, equal today's values. |
| S7 | `PUT /api/settings` on a key set by the environment returns 409 `locked_by_env`. |
| S8 | `PUT` of `fits_roots` during a running scan returns 409. |
| S9 | `host` in the file has no effect. |
| S10 | With `ACP_DATA_DIR` set, every user-written file resolves inside it and the three tracked files stay in `data/`. |

### First run and paths (`tests/test_setup.py`)

| ID | Asserts |
|---|---|
| F1 | No roots and no manifest: `first_run` is true and a browser `GET /` redirects to `/setup`. |
| F2 | `?setup=skip` does not redirect. A JSON `GET /api/manifest` never redirects. |
| F3 | Roots from the environment, or from the file, make `first_run` false. |
| F4 | A manifest with no roots makes `first_run` false. |
| P1 | `not_absolute`, `not_found`, `not_a_directory`, `invalid` each return their code. |
| P2 | `not_readable` on a folder with mode 000 (skipped on Windows). |
| P3 | Surrounding quotes and whitespace are stripped, and `~` is expanded. |
| P4 | `duplicate` after normalisation, and `too_many` at 33. |
| P5 | `overlaps` and `no_images_seen` return `ok` with a warning. A UNC path passes the syntax check. |
| P6 | No response contains operating-system error text or the raw input path from an exception. |

### Scan (`tests/test_scan_endpoint.py`)

| ID | Asserts |
|---|---|
| C1 | `POST /api/scan/start` with roots returns 202 and the status shows running. |
| C2 | A second start during a run returns 409 and only one subprocess is spawned. |
| C3 | No roots returns 400 `no_archive_roots` and spawns nothing. |
| C4 | The subprocess environment holds `FITS_ROOTS` and `MANIFEST_PATH` from the file, and the environment variable wins when both exist. |
| C5 | Success sets exit code 0 and a final `progress`. Failure returns an error kind and a fixed message with no path in the JSON. |
| C6 | A missing or corrupt `scan_progress.json` leaves `/api/scan/status` valid. |
| C7 | Non-JSON content type returns 415. A mismatched `Origin` returns 403. A non-loopback `Host` on a loopback bind returns 400. |
| C8 | A non-loopback client, or one with `X-Forwarded-For`, is refused on the three write routes unless the token matches. |
| C9 | With `ACP_API_TOKEN` set, `/setup`, `/api/settings` and `/api/scan/start` return 401 without it. |
| C10 | The scheduled scan and its existing tests pass unchanged. |
| C11 | A scan of a fixture folder leaves every file in it unchanged. |

### Packaging

| ID | Asserts |
|---|---|
| L1 | `tests/test_launcher.py`: a busy default port moves to the next free one, a busy explicit port stops, an already-running ACP is detected by `/api/version` and the browser opens once, the browser opener is called only after `/api/version` answers, and `ACP_NO_BROWSER=1` suppresses it. |
| L2 | `tests/test_launcher.py`: the server binds `127.0.0.1` even when `HOST=0.0.0.0`. `POST /api/quit` exists only when `ACP_PACKAGED=1`. |
| K1 | Windows smoke: unzip to a fresh folder, start with `ACP_HOME` in a temp folder and `ACP_NO_BROWSER=1`, and poll `http://127.0.0.1:5555/api/version` for up to 90 seconds. It must return JSON with the tag's version. |
| K2 | Smoke: `GET /` redirects to `/setup`. `PUT /api/settings` with a fixture folder and `POST /api/scan/start` finish with exit code 0, using the zip's own Python. |
| K3 | Smoke: after a run, the unzipped folder has no new files, and `settings.json` and `data\` exist under `ACP_HOME`. |
| K4 | Smoke: `POST /api/quit` stops the process and the port is free. |
| K5 | Build: the tag equals `VERSION`, the zip is under 400 MB, and `Start ACP.bat` and `python\python.exe` are in it. |
| M1 | Mac smoke repeats K1 to K4 on each architecture, and checks the bundle is unchanged after a run. |
| M2 | Mac signed builds pass `codesign --verify --deep --strict`, `spctl --assess` and `stapler validate`. |
| M3 | A workflow lint test (`tests/test_workflows.py`) fails if a release workflow contains `set -x`, a `secrets.` reference outside an `env:` block, or a signing step that can run on `pull_request`. |
| M4 | A run with no secrets produces an `-UNSIGNED` artifact and attaches nothing to a release. |

## 9. Phases

Each phase merges on its own and leaves the app working.

| Phase | Contents | Merge value |
|---|---|---|
| 1 | Settings and the setup page. Two pull requests in order: 1a is `acp_settings.py`, `data_path`, `ACP_DATA_DIR` and the settings routes (S1 to S10). 1b is the setup page, scan start, progress and first-run redirect (F, P, C tests), plus the Mac and Linux doc changes. | Mac and Linux users stop setting environment variables. |
| 2 | `launcher.py`, `/api/quit`, `packaging/windows`, the release workflow, the Windows docs (L, K tests). Measures the real size. | Windows users get a download. |
| 3 | `packaging/macos`, signing and notarising, the Mac workflow and docs (M tests). Needs Rohan's secrets for a signed result, and works without them. | Mac users get a signed download. |

Phase 3 reuses `launcher.py` from phase 2, so it follows it. Phases 2 and 3 need phase 1 because the packaged app has no other way to set image folders.

## 10. Out of scope

- Code signing for Windows (the SmartScreen step stays in the docs).
- An installer (MSI, or a Mac DMG).
- Auto-update, and an automatic check for new versions.
- A server-side folder browser. A later change can add it for loopback clients.
- A page control for `scan_cron`.
- A Linux download. Linux keeps Docker and the terminal install.
- A menu-bar icon or Dock icon for the Mac app.

## 11. Limits of this spec

I have not built or run any of it. These points rest on reading the code or on general knowledge and need checking in the build.

- The builder can report file counts for the progress file. I read its structure but did not trace its scan loop.
- Wheels exist for Python 3.12 on Windows x64 and both Mac architectures for every package in `requirements.txt`, including `mocpy`.
- The embeddable package needs no extra runtime install on a clean Windows machine.
- The Intel runner label and its remaining lifetime on GitHub.
- The bundled Mac Python loads all its extensions under hardened runtime without extra entitlements.
