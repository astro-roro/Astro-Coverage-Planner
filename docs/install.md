# Installation guide

Pick your operating system below and follow along. Each section starts from "I have nothing installed" and ends with "ACP is running in my browser."

If you already have **Python 3.10 or newer** and **git** installed, you can skip this guide and head straight to the [Quickstart in the README](../README.md#get-it-running).

- [Windows](#windows)
- [macOS](#macos)
- [Linux](#linux)
- [What happens after the demo loads](#what-happens-after-the-demo-loads)

---

## Windows

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
    python scripts\make_demo_manifest.py
    python app.py

Open <http://127.0.0.1:5555/> in your browser. You should see a sky map with five demo targets plotted on it. Skip to [What happens after the demo loads](#what-happens-after-the-demo-loads).

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
    python scripts/make_demo_manifest.py
    python app.py

Open <http://127.0.0.1:5555/> in your browser. You should see a sky map with five demo targets plotted on it.

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
    python scripts/make_demo_manifest.py
    python app.py

Open <http://127.0.0.1:5555/> in your browser.

---

## What happens after the demo loads

You'll see five well-known southern-sky targets plotted on a sky map. This is **demo data** — it's there so you can poke around the viewer before pointing it at your own files. Try:

- Click any of the coloured FOV polygons to see filter coverage and integration hours in the right rail.
- Toggle the catalogue checkboxes in the right rail (Green SNR, WISE HII, etc.) to overlay public datasets on the map.
- Use the survey-background dropdown at the top to flip between optical, Hα, infrared, etc.
- Switch to **Planning mode** in the topbar and click the sky to drop a target — try a 2×2 mosaic with the rotation handle.

When you're ready to scan your own FITS archive, see [Setting up your own archive](setup-archive.md). It covers the manifest builder, environment variables, and the optional pipeline-DB integration for sub-exposure hours.

## Troubleshooting

**`pip install` fails with `error: externally-managed-environment`** — you skipped the `python -m venv .venv` step or didn't activate the venv. Re-run those two commands, then retry the `pip install`.

**`python` not found on Windows after installing** — the "Add to PATH" tick wasn't checked. Run the installer again, choose Modify, and enable it. Or use `py` instead of `python` everywhere; `py` is installed unconditionally by the Windows Python installer.

**Port 5555 already in use** — set a different port: `PORT=5556 python app.py` (macOS/Linux) or `$env:PORT=5556; python app.py` (Windows PowerShell).

**Can't reach `http://127.0.0.1:5555/` from another machine** — by default ACP only listens on the local loopback interface for security. To expose it on your LAN, set `HOST=0.0.0.0` before launching, but be aware that ACP has no authentication and exposes your archive paths over HTTP — only do this on a trusted network.

**Anything else** — open an issue at <https://github.com/astro-roro/Astro-Coverage-Planner/issues> with the command you ran and the full error.
