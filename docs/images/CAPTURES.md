# Capture checklist for README screenshots and GIFs

The README references images in this directory. Capture each one with the suggested filename and content. Aim for ~1600px wide for screenshots, ~1000px wide / 10–25s for GIFs.

**Tool**: [ScreenToGif](https://www.screentogif.com/) on Windows. Capture at native res, then in the editor: trim dead air at start/end, resize to ~1000px wide, set frame rate to 15fps, export with the Octree or Neural quantizer. Aim for <5MB per GIF.

All images: dark UI, no developer-tools panes visible, no personal file paths in shot if possible (the manifest leaks paths in `/api/manifest`, so close devtools and zoom into the map area).

## The "See it in action" reel — 4 captures ✓ done

All four are GIFs, captured at native 2501×1500 then optimised down to ~1MB each via ffmpeg palette+rescale (1000px wide, 256-colour palette, Bayer dither). Total weight ~4.4 MB across all four.

### 1. `survey-swap.gif` ✓

Background survey switching — your FOV polygons over a region of sky, background cycling through optical → Hα → infrared → radio. Polygons stay locked in place while the sky transforms. Captured 2026-05-05.

### 2. `smart-search.gif` ✓

Right-rail search bar with a query being typed (e.g. `filter:Ha hours>2 tel:redcat`) and the target list filtering live as the keys land. Captured 2026-05-05.

### 3. `feature-catalogues.gif` ✓

Catalogues rail with overlays being toggled — markers light up across the galactic plane as each catalogue is enabled. Captured 2026-05-05.

### 4. `planning-mode.gif` ✓

End-to-end planning demo: Planning tab → + New plan → click sky → pick telescope+camera → 2×2 mosaic → drag rotation handle → set Ha hours → save (mosaic stays on map with dashed borders). Captured 2026-05-05.

## Already captured

### `hero-movie.mp4` ✓

Hero video, top of README. ~15s screen capture of dragging and zooming around the all-sky map with FOV polygons covering the southern Milky Way. Mellinger optical background. Captured at 3831×1690 as a 398MB GIF, then re-encoded to H.264 MP4 (1600px wide, CRF 23, yuv420p, faststart) at **7.4MB** — sub-percent of the original. Embedded in the README via `<video autoplay loop muted playsinline>` with the static hero PNG as `poster` for autoplay-blocked viewers. Captured 2026-05-05.

Re-encode recipe (if ever needed):

```bash
ffmpeg -i source.gif -vf "scale=1600:-2:flags=lanczos" \
       -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p \
       -movflags +faststart hero-movie.mp4
```

### `hero-coverage-map.png` ✓

Static hero shot — now serves as the **poster fallback** for the hero video (shown to readers whose browser blocks autoplay, and during the brief moment before the MP4 starts streaming). Mellinger optical background, Aitoff projection, equatorial, no selected target. Resized to 1600px wide. Captured 2026-05-05.

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
