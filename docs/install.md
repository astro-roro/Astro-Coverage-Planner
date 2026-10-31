# Installation guide

Pick your operating system below and follow along. Each section starts from "I have nothing installed" and ends with "ACP is running in my browser."

If you already have **Python 3.10 or newer** and **git** installed, you can skip this guide and head straight to the [Quickstart in the README](../README.md#get-it-running).

- [Windows: download and run](#windows-download-and-run)
- [Windows: install from source](#windows-install-from-source)
- [macOS](#macos)
- [Linux](#linux)
- [What happens after ACP starts](#what-happens-after-acp-starts)
- [Optional: load demo data first](#optional-load-demo-data-first)
- [Troubleshooting](#troubleshooting)

---

## Windows: download and run

This is the easiest way on Windows. You do not need Python, git or a terminal.

1. **Pick a short place to extract to.** `C:\` works: the zip holds one folder called `ACP`, so you end up with `C:\ACP\Start ACP.bat`. Windows stops working with very long file paths and ACP's files go deep, so avoid extracting into Downloads or Documents.
2. **Download** [ACP-windows-x64.zip](https://github.com/astro-roro/Astro-Coverage-Planner/releases/latest/download/ACP-windows-x64.zip). The release page also lists "Source code (zip)" and "Source code (tar.gz)". Those are not the app. You want `ACP-windows-x64.zip`.
3. **Unblock the zip.** Right-click the downloaded zip, choose **Properties**, tick **Unblock** at the bottom and press OK. This stops Windows warning about every file inside.
4. **Extract the whole zip** (right-click, Extract All, and type `C:\` as the destination). Extraction takes several minutes, because Windows scans each of the tens of thousands of files. Do not run ACP from inside the zip.
5. **Double-click `Start ACP.bat`.** If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. ACP is not signed with a paid certificate.
6. Your browser opens ACP. The first time, paste the full path of your image folder and press **Scan my images**. On this page ACP also offers to add a Start menu and desktop shortcut. Leave the box ticked and use the shortcut from then on.

ACP has no window of its own. It lives in the notification area by the clock. Windows 11 may hide new icons behind the **^** arrow: drag ACP's icon out to keep it visible. Click the icon to open ACP. Right-click it and choose **Quit ACP** to stop. The **Settings** page has a **Quit ACP** button too, for when the icon is hidden. ACP will not quit while a scan is running.

**Your data** is kept in `%APPDATA%\ACP`, not in the extracted folder. To update, quit ACP, delete the old folder, extract the new zip and start it again. Your settings and scan results carry over. Going back to an older version is not supported, so copy `%APPDATA%\ACP` somewhere safe first if you want to try one.

**The NINA plugin** talks to ACP at `http://127.0.0.1:5555`. A downloaded ACP can only be reached from the computer it runs on. If NINA runs on a different computer, install from source or use Docker, and set an access token.

**If it will not start**, look at `%APPDATA%\ACP\logs\acp.log`. A message box also appears for the common problems, such as another program using port 5555.

The Mac and Linux downloads are not available yet.

---

## Windows: install from source

### 1. Install Python

Go to <https://python.org/downloads/> and download the latest **Python 3.x** installer for Windows (64-bit).

When you run the installer, **tick "Add python.exe to PATH"** at the bottom of the first screen before clicking Install. This is critical — without it, the `python` command won't be found in your terminal.

To verify, open Windows Terminal or PowerShell (right-click the Start button → "Terminal"), and run:

    python --version

You should see something like `Python 3.12.4`. If you instead get "command not found" or the Microsoft Store opens, the PATH wasn't set during install — re-run the installer, choose "Modify", and tick the PATH option.

### 2. Install Git

Go to <https://git-scm.com/download/win> and run the installer. The default settings are fine — you can hit Next on every screen.

To verify, in your terminal:

    git --version

### 3. Clone, install, run

Pick a folder where ACP should live (e.g. `C:\Users\YourName\Documents\Code\`), then in your terminal:

    cd C:\Users\YourName\Documents\Code
    git clone https://github.com/astro-roro/Astro-Coverage-Planner.git
    cd Astro-Coverage-Planner
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python app.py

Open <http://127.0.0.1:5555/> in your browser. You'll see an empty sky map with a banner pointing you at the next step. See [What happens after ACP starts](#what-happens-after-acp-starts).

---

## macOS

### 1. Install Python

The Python that ships with macOS is too old. Two options for installing a current version:

**Option A — install from python.org** (simplest if you've never used Homebrew):
Download the latest installer from <https://python.org/downloads/macos/> and run it. Default settings are fine.

**Option B — install via Homebrew** (preferred if you already have Homebrew or do other dev work):

    brew install python@3.12

To verify, open Terminal (Cmd+Space, type "Terminal", hit Enter) and run:

    python3 --version

You should see `Python 3.12.x` or similar.

### 2. Install Git

On modern macOS, opening Terminal and typing any `git` command will prompt you to install the Xcode Command Line Tools, which include git. Click "Install" and wait a few minutes.

To verify:

    git --version

### 3. Clone, install, run

    cd ~/Documents
    git clone https://github.com/astro-roro/Astro-Coverage-Planner.git
    cd Astro-Coverage-Planner
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python app.py

Open <http://127.0.0.1:5555/> in your browser.

---

## Linux

### 1. Install Python and Git

Most distros ship with Python and Git already, but the Python may be too old or missing the `venv` module. The safe move is to install/upgrade explicitly.

**Ubuntu / Debian / Mint / Pop!_OS:**

    sudo apt update
    sudo apt install -y python3 python3-venv python3-pip git

**Fedora / RHEL / Rocky:**

    sudo dnf install -y python3 python3-pip git

**Arch / Manjaro / EndeavourOS:**

    sudo pacman -S --needed python python-pip git

To verify (you want Python 3.10 or newer):

    python3 --version
    git --version

### 2. Clone, install, run

    cd ~
    git clone https://github.com/astro-roro/Astro-Coverage-Planner.git
    cd Astro-Coverage-Planner
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python app.py

Open <http://127.0.0.1:5555/> in your browser.

---

## What happens after ACP starts

A fresh install opens the **setup page** (`/setup`). Paste the full path of the folder that holds your FITS/XISF images, press **Add**, then **Scan my images**. The page shows progress, and when the scan finishes it opens the map. ACP doesn't ship with anyone else's imaging data, so what you see is what you've shot. The **Skip for now** link shows the empty map instead, and the **Settings** link in the map's header brings the page back.

For several image folders, the command-line builder, environment variables, Docker and the optional pipeline-DB integration for sub-exposure hours, see **[Setting up your own archive](setup-archive.md)**.

## Optional: load demo data first

If you'd like to poke around the viewer before doing the work to scan your archive, you can load five well-known targets (M 42, M 31, Eta Carinae, M 45, M 8 — spanning both hemispheres) as sample data:

    python scripts/make_demo_manifest.py

(Use `python3` instead of `python` on macOS/Linux.) Refresh the browser tab and the sample data will appear. When you're ready to use your own files, just re-run the real manifest builder — it'll overwrite the demo.

## Troubleshooting

**`pip install` fails with `error: externally-managed-environment`** — you skipped the `python -m venv .venv` step or didn't activate the venv. Re-run those two commands, then retry the `pip install`.

**`python` not found on Windows after installing** — the "Add to PATH" tick wasn't checked. Run the installer again, choose Modify, and enable it. Or use `py` instead of `python` everywhere; `py` is installed unconditionally by the Windows Python installer.

**Port 5555 already in use** — set a different port: `PORT=5556 python app.py` (macOS/Linux) or `$env:PORT=5556; python app.py` (Windows PowerShell).

**Can't reach `http://127.0.0.1:5555/` from another machine** — by default ACP only listens on the local loopback interface for security. To expose it on your LAN, set `HOST=0.0.0.0` before launching, but be aware that ACP has no authentication and exposes your archive paths over HTTP — only do this on a trusted network.

**Anything else** — open an issue at <https://github.com/astro-roro/Astro-Coverage-Planner/issues> with the command you ran and the full error.

## Backing up the Activity history

The Activity tab keeps its history in `activity.sqlite`, beside `plans.json` (or wherever `ACP_ACTIVITY_DB` points). Back it up with the rest of the data directory. Deleting it clears the history and nothing else. Settings: `ACP_ACTIVITY=off` switches the tab off, `ACP_ACTIVITY_TZ` sets the zone whose local noon ends a night (default `Australia/Sydney`), `ACP_ACTIVITY_KEEP_NIGHTS` prunes older nights (unset keeps everything), and `ACP_ACTIVITY_PATH_MAP` is a JSON list of `{"from", "mac", "windows"}` entries that turn AAC's container paths into copyable Mac and Windows folders. See `docs/specs/activity-tab.md`.
