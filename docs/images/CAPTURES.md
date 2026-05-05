# Capture checklist for README screenshots and GIFs

The README references images in this directory. Capture each one with the suggested filename and content. Aim for ~1600px wide for screenshots, ~800px wide / 15-30s for GIFs (use [Peek](https://github.com/phw/peek) or [ScreenToGif](https://www.screentogif.com/) on Windows).

All images: dark UI, no developer-tools panes visible, no personal file paths in shot if possible (the manifest leaks paths in `/api/manifest`, so close devtools and zoom into the map area).

## Required

### `hero-coverage-map.png`
Hero shot. Open the app on a real archive with several telescopes and several filters represented. Show:
- Aitoff projection, equatorial frame.
- Many FOV polygons in distinct telescope colors.
- A few filter badges visible on the NW corners of polygons.
- Right rail collapsed or showing a short target list.
- No selected target (clean view).
**Suggested:** zoom on the southern Milky Way region so RedCat wide fields and EdgeHD tight fields contrast nicely.

### `loop-scan-to-app.gif`
Core loop demo. ~20-30 seconds. Sequence:
1. Terminal: run `python scripts/build_archive_manifest.py` (cut/speed up the long part).
2. Terminal: `python app.py`.
3. Browser opens, map loads, polygons draw.
4. Click a single FOV polygon — right rail shows target detail with filter coverage bars and observed hours.
5. Click another nearby FOV.
**Tip:** run against the demo manifest so the scan finishes in seconds.

### `loop-planning-mode.gif`
Planning demo. ~25-40 seconds. Sequence:
1. Click "Planning" in the topbar.
2. Click "+ New plan".
3. Click on the sky to drop a target center.
4. Pick telescope + camera from dropdown — FOV box appears.
5. Set rows × cols × overlap to make a 2×2 mosaic.
6. Drag the rotation handle to spin the mosaic.
7. Set per-filter target hours.
8. Click "Sync" — watch the TS export zip get written.

### `feature-search.png`
Smart search bar in action. Show the right rail with:
- Search box visible at top.
- Active query in the box: `filter:Ha hours>2 tel:redcat`
- Filtered target list below showing only matching targets.
- One target highlighted/selected (optional).

### `feature-catalogs.png`
Catalog overlays. Show:
- The catalogs accordion expanded in the right rail.
- Two or three catalog checkboxes ticked (e.g. Green SNR + WISE HII).
- Map showing the overlay markers spread across the galactic plane.
**Note:** this is currently a known bug (todo.md item 5) — capture this AFTER the catalog overlay rendering bug is fixed.

### `feature-gear-editor.png`
Gear editor open. Show:
- Modal/panel with telescope + camera lists.
- A row in edit state with the FOV box auto-derived from focal length + sensor size.
- The "Scan coverage" button visible.

### `feature-ts-sync.png`
NINA TS sync result. Either:
- (a) The "Sync" button moment in-app, with a success toast/dialog showing the zip path; OR
- (b) A split shot — left half the planner with two plans selected, right half NINA's Target Scheduler showing the imported projects.
**(b) is more impressive but harder to capture cleanly.**

## Optional

### `feature-panel.png`
Right-rail panel after the topbar restructure (see todo.md item 7 for the related viewport-overflow fix). Capture once that lands so it doesn't go stale.

### `feature-mosaic-rotate.gif`
Standalone GIF of just the mosaic rotation handle being dragged. Useful for socials. ~5-10 seconds.

## Hosting

All captures live in this `docs/images/` directory and are committed to the repo so the README renders correctly on GitHub. Keep file sizes under ~2MB each — use `pngquant` for PNGs and `gifsicle -O3` for GIFs if needed:

```bash
pngquant --quality=70-85 hero-coverage-map.png -o hero-coverage-map.png --force
gifsicle -O3 loop-planning-mode.gif -o loop-planning-mode.gif
```
