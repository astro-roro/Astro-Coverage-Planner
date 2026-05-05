# Capture checklist for README screenshots and GIFs

The README references images in this directory. Capture each one with the suggested filename and content. Aim for ~1600px wide for screenshots, ~1000px wide / 10–25s for GIFs.

**Tool**: [ScreenToGif](https://www.screentogif.com/) on Windows. Capture at native res, then in the editor: trim dead air at start/end, resize to ~1000px wide, set frame rate to 15fps, export with the Octree or Neural quantizer. Aim for <5MB per GIF.

All images: dark UI, no developer-tools panes visible, no personal file paths in shot if possible (the manifest leaks paths in `/api/manifest`, so close devtools and zoom into the map area).

## The "See it in action" reel — 4 captures

These four are the headline visuals the README leads with. Two stills, two GIFs.

### 1. `feature-search.png` (still)

Smart search bar in action. Right rail with:
- Search box visible at top.
- Active query: `filter:Ha hours>2 tel:redcat` (or another combo that returns 5–15 targets).
- Filtered target list below showing only matching targets.
- Optional: one target highlighted/selected.

### 2. `loop-planning-mode.gif` (~25s)

Planning demo. Sequence:
1. Click "Planning" in the topbar.
2. Click "+ New plan".
3. Click on the sky to drop a target center.
4. Pick telescope + camera from dropdown — FOV box appears.
5. Set rows × cols × overlap to make a 2×2 mosaic.
6. Drag the rotation handle to spin the mosaic.
7. Set per-filter target hours.
8. Save / close the plan editor — mosaic remains on the map with dashed borders.

### 3. `feature-catalogs.png` (still)

Catalogue overlays. Show:
- The Catalogues accordion expanded in the right rail.
- Two or three catalog checkboxes ticked (e.g. Green SNR + WISE HII + Sharpless).
- Map showing the overlay markers spread across the galactic plane.
- Choose a region with some of your FOVs visible too, so viewers see catalogue markers next to your coverage.

### 4. `feature-survey-swap.gif` (~15s)

Background survey switching — the killer "professional astronomy data on tap" feature. Sequence:
1. Pick a region of sky with several of your FOVs clearly visible (Milky Way southern region works great).
2. Start on **optical** (Mellinger or DSS Color).
3. Switch to **Hα** (Finkbeiner or IPHAS DR2 Hα). Hold ~2-3s.
4. Switch to **infrared** (WISE 12µm or 2MASS-K). Hold ~2-3s.
5. Switch to **radio** if available (SUMSS / NVSS). Hold ~2-3s.
6. Optional: land back on optical.

The point: your FOV polygons stay locked in place across every background while the sky character transforms. Don't move the view between switches — keep the same RA/Dec center and zoom so the contrast is purely the survey, not the framing.

## Already captured

### `hero-coverage-map.png` ✓

Hero shot, top of README. Mellinger optical background, Aitoff projection, equatorial, no selected target. Resized to 1600px wide. Captured 2026-05-05.

## Optional / future

### `feature-gap-finder.gif`

Gap-finder demo (held back from the main reel for now, but worth capturing later as a "power features" callout).
1. In Catalogues rail, set Have=Ha, Missing=SII, defaults for thresholds.
2. Click "Find gaps".
3. Yellow MOC paints in over the gap region.
4. Catalogue candidates scatter as dots inside it.
5. Summary line appears (`sky 0.84% • 1808 candidates • from manifest, iphas_ha`).
6. Hover a candidate to show its name.

### `feature-mosaic-rotate.gif`

Standalone short of the rotation handle being dragged around. ~5-10s. Useful for socials.

### `feature-gear-editor.png`

Gear editor open. Modal/panel with telescope + camera lists, FOV box auto-derived from focal length + sensor size, "Scan coverage" button visible.

### `feature-ts-sync.png`

NINA TS sync moment — the "Sync" button click with a success toast/dialog showing the zip path. Hard to capture cleanly; deferred.

### `loop-scan-to-app.gif`

The scan-and-launch core loop. Functional but not headline-worthy — already explained in the setup section text, no need to GIF it.

## Hosting

All captures live in this `docs/images/` directory and are committed to the repo so the README renders correctly on GitHub. Keep file sizes under ~5MB each.

If you need to compress further:
```bash
pngquant --quality=70-85 hero-coverage-map.png -o hero-coverage-map.png --force
gifsicle -O3 loop-planning-mode.gif -o loop-planning-mode.gif
```
