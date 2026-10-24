Astro Coverage Planner {{VERSION}} for Windows

HOW TO START
1. Extract the whole zip to a short folder such as C:\ACP. Do not run ACP from
   inside the zip, and avoid long folder names: Windows stops working with
   paths past about 260 characters. Extraction takes several minutes because
   Windows scans every file.
2. Double-click "Start ACP.bat". Your browser opens ACP.
3. The first time, ACP asks where your images are. Paste the folder path and
   press Scan. ACP only reads your files.

If Windows shows "Windows protected your PC", click More info, then Run anyway.
ACP is not signed with a paid certificate. You can avoid the warning by
right-clicking the zip before you extract it, choosing Properties and ticking
Unblock.

On first run ACP offers a Start menu and desktop shortcut. Use that from then
on instead of the .bat file.

HOW TO FIND ACP AGAIN
ACP has no window of its own. It runs in the notification area (the icons by
the clock). Windows 11 may hide new icons behind the ^ arrow; drag ACP's icon
out to keep it visible. Click the icon to open ACP in your browser.

HOW TO STOP
Right-click the icon and choose Quit ACP, or open ACP in your browser, go to
Settings and press Quit ACP. ACP will not quit while a scan is running.

WHERE YOUR DATA LIVES
%APPDATA%\ACP holds your settings, your plans and the scan results, and a log
in the logs folder (acp.log). This folder is never written to, so you can
delete it and extract a newer one without losing anything.

UPDATING
Quit ACP, delete this folder, extract the new zip. Your data stays in
%APPDATA%\ACP. Going back to an older version is not supported: copy
%APPDATA%\ACP somewhere safe first if you want to try.

USING IT WITH NINA
Set the ACP address in the NINA plugin to http://127.0.0.1:5555
This copy of ACP can only be reached from this computer. If NINA runs on a
different computer, install ACP from source or Docker instead and set an
access token.

MORE
https://github.com/astro-roro/Astro-Coverage-Planner
