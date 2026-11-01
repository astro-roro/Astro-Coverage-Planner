Astro Coverage Planner {{VERSION}} for Mac (Apple Silicon)

This download is for Macs with an Apple chip (M1 or later). On an older Intel Mac, install ACP from source or Docker instead.

HOW TO START
1. Open the downloaded zip, then drag ACP.app to your Applications folder. Do not run it from inside the zip or from Downloads.
2. Double-click ACP.app. Your browser opens ACP. There is no Dock icon and no window.
3. The first time, ACP asks where your images are. Paste the folder path and press Scan. ACP only reads your files. macOS may ask whether ACP can read your Documents, Desktop, an external drive or a network drive. Say yes, or ACP cannot see the images.

If macOS says it cannot verify ACP, this copy is not signed and approved by Apple. Right-click ACP.app, choose Open, then press Open in the box that appears. If you downloaded a signed release, this does not happen.

HOW TO FIND ACP AGAIN
ACP runs from an icon in the menu bar, at the top right of the screen. Click it and choose Open ACP to open the page in your browser. Double-clicking ACP.app again also opens the page.

HOW TO STOP
Click the menu bar icon and choose Quit ACP, or open ACP in your browser, go to Settings and press Quit ACP. ACP will not quit while a scan is running.

WHERE YOUR DATA LIVES
~/Library/Application Support/ACP holds your settings, your plans and the scan results, and a log in the logs folder (acp.log). ACP.app itself is never written to, so you can delete it and drop in a newer one without losing anything.

UPDATING
Quit ACP, move the old ACP.app to the Bin, and drag the new one into Applications. Your data stays where it is. Going back to an older version is not supported: copy ~/Library/Application Support/ACP somewhere safe first if you want to try.

USING IT WITH NINA
Set the ACP address in the NINA plugin to http://127.0.0.1:5555 . This copy of ACP can only be reached from this computer. If NINA runs on a different computer, install ACP from source or Docker instead and set an access token.

LICENCES
ACP is free software under the GNU General Public License version 3 or later (LICENSE). THIRD-PARTY-NOTICES.txt has the licences of the software that comes with it, and CREDITS.md lists the sky surveys and catalogues ACP uses.

MORE
https://github.com/astro-roro/Astro-Coverage-Planner
