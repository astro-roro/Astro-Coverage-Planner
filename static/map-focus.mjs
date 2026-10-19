// Deciding how far to zoom when a list row sends the map to a target.
// Kept out of app.js so tests/frontend/ can import it.

const D2R = Math.PI / 180;

// Footprint size to assume when the target gives no corners or field of view.
export const DEFAULT_FOOTPRINT_DEG = 3;
// Zoom only when the view is wider than this many footprints.
export const ZOOM_TRIGGER_RATIO = 10;
// After zooming, the footprint fills this fraction of the view.
export const FOOTPRINT_VIEW_FRACTION = 1 / 3;

function sepDeg(ra1, dec1, ra2, dec2) {
  const a = dec1 * D2R, b = dec2 * D2R, dra = (ra2 - ra1) * D2R;
  const c = Math.sin(a) * Math.sin(b) + Math.cos(a) * Math.cos(b) * Math.cos(dra);
  return Math.acos(Math.max(-1, Math.min(1, c))) / D2R;
}

// Longest side of a target's footprint in degrees: the widest separation
// between any two corners, else the larger of fov_arcmin, else the default.
export function footprintSizeDeg(target) {
  const corners = target?.corners_icrs;
  if (Array.isArray(corners) && corners.length >= 3) {
    let widest = 0;
    for (let i = 0; i < corners.length; i++) {
      for (let j = i + 1; j < corners.length; j++) {
        const [r1, d1] = corners[i];
        const [r2, d2] = corners[j];
        if (![r1, d1, r2, d2].every(Number.isFinite)) continue;
        widest = Math.max(widest, sepDeg(r1, d1, r2, d2));
      }
    }
    if (widest > 0) return widest;
  }
  const fov = target?.fov_arcmin;
  if (Array.isArray(fov) && fov.length === 2) {
    const side = Math.max(Number(fov[0]), Number(fov[1])) / 60;
    if (Number.isFinite(side) && side > 0) return side;
  }
  return DEFAULT_FOOTPRINT_DEG;
}

// The field of view in degrees to zoom to, or null to leave the zoom alone.
// Zooms only when the current view is more than ZOOM_TRIGGER_RATIO times the
// footprint, so a user who is already zoomed in keeps their zoom.
export function zoomFovFor(currentFovDeg, footprintDeg) {
  if (!Number.isFinite(currentFovDeg) || !(currentFovDeg > 0)) return null;
  if (!Number.isFinite(footprintDeg) || !(footprintDeg > 0)) return null;
  if (currentFovDeg <= footprintDeg * ZOOM_TRIGGER_RATIO) return null;
  return footprintDeg / FOOTPRINT_VIEW_FRACTION;
}
