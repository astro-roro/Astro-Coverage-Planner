Astro Coverage Planner {{VERSION}} for Linux

HOW TO START
1. Extract the archive anywhere you like. It holds one folder called ACP.
     tar -xzf ACP-linux-x86_64.tar.gz        (use aarch64 on a 64-bit Raspberry Pi)
2. Run the start script.
     ./ACP/start-acp
3. Your browser opens ACP. The first time, ACP asks where your images are. Paste the folder path and press Scan. ACP only reads your files.

If your system has no desktop (a headless Raspberry Pi, say), ACP prints the address instead of opening a browser. To reach it from another computer, forward the port over SSH and then open http://127.0.0.1:5555 on that computer.
     ssh -L 5555:127.0.0.1:5555 <user>@<host>

On first run ACP offers an entry in your applications menu. Use that from then on instead of the script. Starting ACP from the menu opens no terminal, so look in the log file (below) if it does not come up.

HOW TO FIND ACP AGAIN
On a desktop with a system tray, ACP puts an icon there. Click it to open ACP in your browser. Some desktops show no tray icon unless an extension is installed (GNOME needs AppIndicator support). Without a tray, ACP runs in the terminal you started it from, and the same address works any time.

HOW TO STOP
Press Ctrl+C in the terminal, choose Quit ACP from the tray icon, or open ACP in your browser, go to Settings and press Quit ACP. ACP will not quit while a scan is running.

WHERE YOUR DATA LIVES
${XDG_CONFIG_HOME:-~/.config}/acp holds your settings, your plans and the scan results, and a log in the logs folder (acp.log). The ACP folder you extracted is never written to, so you can delete it and extract a newer one without losing anything.

UPDATING
Quit ACP, delete this folder, extract the new archive. Your data stays in the folder above. Going back to an older version is not supported: copy that folder somewhere safe first if you want to try.

WHAT IT NEEDS
A 64-bit Linux with glibc 2.28 or newer: Debian 10, Ubuntu 20.04, RHEL 8, Raspberry Pi OS Bullseye or newer. A 32-bit system is not supported. Check the download's SHA256SUMS-linux-<arch>.txt with: sha256sum -c SHA256SUMS-linux-<arch>.txt

USING IT WITH NINA
Set the ACP address in the NINA plugin to http://127.0.0.1:5555 . This copy of ACP can only be reached from this computer. If NINA runs on a different computer, install ACP from source or Docker instead and set an access token.

LICENCES
ACP is free software under the GNU General Public License version 3 or later (LICENSE). THIRD-PARTY-NOTICES.txt has the licences of the software that comes with it, and CREDITS.md lists the sky surveys and catalogues ACP uses.

MORE
https://github.com/astro-roro/Astro-Coverage-Planner
