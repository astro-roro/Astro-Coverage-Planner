# Packaging

Everything that builds the downloads. The design is in [the spec](../docs/specs/windows-download-and-setup.md). Phase 2 is the Windows zip, phase 3 the Mac app and phase 4 the Linux tarballs.

| Path | What it is |
|---|---|
| `pins.txt` | The Python versions and the SHA-256 of each download (python.org for Windows, python-build-standalone for Mac) |
| `constraints.txt` | An exact `==` pin for every dependency. Dependabot updates it |
| `windows/build.ps1` | Builds `ACP-windows-x64.zip` and `SHA256SUMS-windows.txt` on `windows-latest` |
| `windows/Start ACP.bat`, `windows/README.txt` | The files at the top of the zip |
| `windows/smoke-start.ps1`, `windows/make_test_links.py` | The CI check that ACP starts through the `.bat` and a shortcut with no console window |
| `macos/build.sh` | Builds `ACP-macos-arm64-UNSIGNED.zip` (ad hoc signed) on `macos-14` |
| `macos/sign.sh` | Signs with the Developer ID certificate, notarises, staples, and writes `ACP-macos-arm64.zip` and `SHA256SUMS-macos.txt`. Skips with one log line when the secrets are missing |
| `macos/stub.c`, `macos/Info.plist.in`, `macos/entitlements.plist` | The launcher stub, the bundle's plist and the hardened-runtime entitlements |
| `macos/macho.py` | Finds every Mach-O file by file magic (for signing) and the highest macOS version the bundle needs |
| `macos/README.txt` | The `README.txt` at the top of the Mac zip |
| `linux/build.sh` | Builds `ACP-linux-<arch>.tar.gz` and `SHA256SUMS-linux-<arch>.txt` on `ubuntu-22.04` (x86_64) or `ubuntu-22.04-arm` (aarch64). Uses the python-build-standalone Python pinned in `pins.txt` for pip, bytecode and checks |
| `linux/start-acp`, `linux/README.txt` | The files at the top of the tarball |
| `smoke.py` | The smoke test, run with the zip's own Python (stdlib only) |
| `check_tree.py` | The build checks: tag equals `VERSION`, size, longest path, required files |
| `zip_tree.py` | Zips a folder quickly with the folder as the top entry. Windows only: the Mac zip uses `ditto`, which keeps symlinks and permissions |
| `icons/make_icons.py` | Draws the icons from the shapes in `static/favicon.svg` into the package. Not committed |

## Releasing

1. Bump `VERSION` in `app.py` and commit.
2. Tag `v<VERSION>` (the first release is `v1.0.0`) and push the tag.
3. `release-windows.yml` builds, smoke-tests, then creates the GitHub release if it does not exist and uploads the zip and `SHA256SUMS-windows.txt`. The tag must equal `VERSION` or the build fails.
4. `release-macos.yml` does the same for `ACP-macos-arm64.zip` and `SHA256SUMS-macos.txt`, signed and notarised in the `release` environment (see below). Whichever workflow finishes first creates the release and the other only uploads. If an Apple secret is missing the tag run fails, and no Mac file is attached.

To try a build without releasing, push a branch named `packaging/<anything>` or run the workflow from the Actions tab. Both build and smoke-test, and neither publishes.

## Before the first build

- Put the SHA-256 of `python-3.12.10-embed-amd64.zip` in `pins.txt`. The build refuses to run while it says `TODO` and prints the hash it downloaded, which you must check against the python.org release page before trusting it.
- `constraints.txt` was drafted from the maintainer's environment, not resolved for Python 3.12 on Windows. If the install step reports a pin with no wheel, move that line to the nearest version that has one.

## Mac signing secrets

The Mac workflow signs and notarises on a `v*` tag, in the `release` environment, and needs five secrets for that. A tag run with a secret missing fails, so an unsigned build is never released. Every other run (branch pushes, pull requests, manual runs on a branch) builds and smoke-tests an unsigned zip, uploads it as a workflow artifact called `acp-macos-unsigned`, and never attaches it to a release. An unsigned app will not open on another Mac without right-click, Open.

You need the paid Apple Developer Program membership. These are the steps, once.

1. Get the certificate. In Keychain Access, open Certificate Assistant and choose Request a Certificate From a Certificate Authority. Use your email, tick Saved to disk, and save the file. At developer.apple.com, open Certificates, press the plus, pick Developer ID Application, upload that file and download the `.cer`. Double-click the `.cer` to add it to your login keychain. Skip this step if "Developer ID Application: your name (TEAMID)" is already in Keychain Access under My Certificates.
2. Export it. In Keychain Access, open the login keychain and My Certificates. Click the small arrow beside "Developer ID Application: ..." and check that a private key sits under it. Right-click the certificate (not the key), choose Export, pick the `.p12` format and set a password you will remember. The password is the second secret.
3. Turn the file into text. In Terminal, run `base64 -i DeveloperID.p12 | pbcopy`. Your clipboard now holds the first secret. Delete the `.p12` file afterwards.
4. Find your team id. At developer.apple.com, open Membership details. The Team ID is ten letters and digits.
5. Make an app-specific password. At appleid.apple.com, open Sign-In and Security, then App-Specific Passwords, and make one called "ACP notarisation". It looks like `abcd-efgh-ijkl-mnop`. Your normal Apple password does not work here.
6. Make the GitHub environment. In the repository, open Settings, Environments, New environment, and name it `release`. Under Deployment branches and tags choose Selected branches and tags, and add only the tag pattern `v*`. That is what stops a pull request or a branch from reading the secrets.
7. Add the five secrets to that environment (Environment secrets, not Repository secrets):

| Secret | Holds |
|---|---|
| `MACOS_CERT_P12_BASE64` | The base64 text from step 3 |
| `MACOS_CERT_PASSWORD` | The `.p12` password from step 2 |
| `APPLE_TEAM_ID` | The ten-character team id from step 4 |
| `APPLE_ID` | The email address of your Apple ID |
| `APPLE_APP_PASSWORD` | The app-specific password from step 5 |

Then push a `v*` tag (or re-run the workflow for one). The sign job takes a few minutes, and notarisation can take longer while it waits for Apple. A good run ends with "Notarisation status: Accepted", and the smoke job then checks the app the way Gatekeeper does. Never paste any of these values into a chat, an issue or a commit.

The certificate name appears in the macOS dialog that says the app was downloaded from the internet, so users see the name on your certificate.

If notarisation rejects the app, the job prints Apple's log (it holds no secrets). The usual cause is a Mach-O file that was not signed, a file signed without a secure timestamp, or an entitlement problem, below.

### Entitlements

`macos/entitlements.plist` is applied to the Python executable and the launcher stub, because the hardened runtime that notarisation requires blocks some things Python does by default. The spec started with none and said to add one only after a failing test. Nothing here has run on a signed build yet, so the two keys are what other signed Python apps need, and the first signed run decides which stay.

- `com.apple.security.cs.disable-library-validation` lets the bundled Python load extension modules that carry a different signature from its own. Every file is signed with the same certificate here, so this may turn out to be unneeded. Remove it first if the signed smoke test passes without it.
- `com.apple.security.cs.allow-unsigned-executable-memory` lets libffi build callback code in memory. `ctypes` and the `pyobjc` menu bar library (pystray) use it. If the smoke test passes with the tray enabled and the module imports succeed without it, remove it.

Remove one key at a time and watch the smoke job's import step, since a missing entitlement shows only when a compiled module loads.

## Building the Mac app by hand

On an Apple Silicon Mac with the Xcode command line tools (`clang`, `ditto`, `codesign`, `iconutil`) and network access:

    ACP_KEEP_STAGE=1 packaging/macos/build.sh --out dist --work /tmp/acp-build

This downloads the pinned Python, installs the dependencies into it, builds the stub and writes `dist/ACP-macos-arm64-UNSIGNED.zip`. It uses no Python from your machine. `ACP_KEEP_STAGE=1` keeps the unpacked `ACP` folder in `--work` so you can try `ACP/ACP.app`. Then smoke-test it:

    P=/tmp/acp-build/ACP/ACP.app/Contents/Resources
    "$P/python/bin/python3" -X utf8 -B packaging/smoke.py --package /tmp/acp-build/ACP/ACP.app \
        --python "$P/python/bin/python3" --launcher "$P/app/launcher.py" --tray \
        --import-requirements requirements.txt requirements-packaged.txt

To try signing, export the five variables from the table above and run `packaging/macos/sign.sh --zip dist/ACP-macos-arm64-UNSIGNED.zip --out dist-signed`. It uses a temporary keychain and removes it on exit. With a variable missing it prints one line and does nothing.

## Running the checks by hand

    python packaging/smoke.py --package <unzipped ACP folder> --version <VERSION> --tray --windows-checks

Run it with the package's own `python\python.exe`, from outside the package. Against a source checkout, add `--python <python> --launcher launcher.py`.
