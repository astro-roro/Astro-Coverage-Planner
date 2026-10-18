// Splitting sky polygons along the seam of an all-sky projection.
//
// Aitoff and Mollweide put the view centre in the middle of the map, so the
// map's left and right edges meet at one meridian: the seam, 180 degrees of
// RA from the view centre. Aladin joins consecutive vertices with straight
// lines on screen, so a polygon with vertices on both sides of the seam is
// drawn the long way across the whole map. splitAtSeam() cuts such a polygon
// along the seam so each part sits on one side.
//
// Everything here works in sky degrees: [ra, dec] pairs. Parts keep real RA
// values (0 to 360) so they can go straight to Aladin; the cut edge sits
// SEAM_EPS_DEG inside the seam on each side, so Aladin can't place it on the
// wrong edge of the map.

export const SEAM_EPS_DEG = 0.005;

// A vertex this close to the seam is treated as being on it. Aladin works in
// single precision, so a vertex a few millionths of a degree either side of
// the seam can land on either edge of the map; drawn as it is, the polygon
// would run across the whole map. Such vertices are snapped to the seam and
// then pulled SEAM_EPS_DEG inside, like the cut edges. Larger than Aladin's
// rounding error, smaller than SEAM_EPS_DEG.
export const SEAM_TOL_DEG = 0.003;

// Projections whose map has a seam meridian. The others (orthographic,
// gnomonic) show one hemisphere or less and have none.
const SEAM_PROJECTIONS = new Set(["AIT", "MOL", "MER", "HPX"]);

export function projectionHasSeam(name) {
  return SEAM_PROJECTIONS.has(String(name ?? "").toUpperCase());
}

// RA of the seam for a view centred on `centreRa`.
export function seamRaFor(centreRa) {
  return (((centreRa + 180) % 360) + 360) % 360;
}

// Degrees into (-180, 180].
function wrap180(d) {
  const m = ((d % 360) + 360) % 360;
  return m > 180 ? m - 360 : m;
}

function wrap360(d) {
  return ((d % 360) + 360) % 360;
}

// Follows the shortest way round between consecutive vertices and returns
// each vertex as a continuous longitude x, measured from the view centre
// (seam at x = +/-180), plus how many times the ring winds round the sphere.
function unwrap(points, seamRa, closed) {
  const c = seamRa - 180;
  const xs = [wrap180(points[0][0] - c)];
  for (let i = 1; i < points.length; i++) {
    xs.push(xs[i - 1] + wrap180(points[i][0] - points[i - 1][0]));
  }
  let winding = 0;
  if (closed) {
    const end = xs[xs.length - 1] + wrap180(points[0][0] - points[points.length - 1][0]);
    winding = Math.round((end - xs[0]) / 360);
  }
  return { xs, winding };
}

// True when drawing the polygon as given would run across the seam.
export function crossesSeam(points, seamRa, closed = true) {
  if (!Array.isArray(points) || points.length < 2) return false;
  const { xs, winding } = unwrap(points, seamRa, closed);
  if (winding !== 0) return true;
  let lo = Infinity, hi = -Infinity;
  for (const x of xs) { if (x < lo) lo = x; if (x > hi) hi = x; }
  return hi >= 180 - SEAM_TOL_DEG || lo <= -180 + SEAM_TOL_DEG;
}

// Sutherland-Hodgman against one vertical line. keepAbove: keep x >= bound.
function clipRing(ring, bound, keepAbove) {
  const out = [];
  const inside = p => (keepAbove ? p[0] >= bound : p[0] <= bound);
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i], b = ring[(i + 1) % ring.length];
    const ina = inside(a), inb = inside(b);
    if (ina) out.push(a);
    if (ina !== inb) {
      const t = (bound - a[0]) / (b[0] - a[0]);
      out.push([bound, a[1] + t * (b[1] - a[1])]);
    }
  }
  return out;
}

// Pieces of an open path inside lo <= x <= hi.
function clipPath(path, lo, hi) {
  const runs = [];
  let run = null;
  for (let i = 0; i + 1 < path.length; i++) {
    const [x1, d1] = path[i], [x2, d2] = path[i + 1];
    let t0 = 0, t1 = 1;
    const dx = x2 - x1;
    if (dx === 0) {
      if (x1 < lo || x1 > hi) { run = null; continue; }
    } else {
      const ta = (lo - x1) / dx, tb = (hi - x1) / dx;
      t0 = Math.max(0, Math.min(ta, tb));
      t1 = Math.min(1, Math.max(ta, tb));
      if (t0 > t1) { run = null; continue; }
    }
    const p0 = [x1 + t0 * dx, d1 + t0 * (d2 - d1)];
    const p1 = [x1 + t1 * dx, d1 + t1 * (d2 - d1)];
    if (!run || t0 > 0) { run = [p0]; runs.push(run); }
    run.push(p1);
    if (t1 < 1) run = null;
  }
  return runs;
}

function dedupe(pts, closed) {
  const out = [];
  for (const p of pts) {
    const q = out[out.length - 1];
    if (!q || q[0] !== p[0] || q[1] !== p[1]) out.push(p);
  }
  if (closed && out.length > 1) {
    const f = out[0], l = out[out.length - 1];
    if (f[0] === l[0] && f[1] === l[1]) out.pop();
  }
  return out;
}

// An edge that runs along the seam is a straight chord to Aladin, but the
// seam is the curved edge of the map, so a long one would cut across the sky.
// Adds a vertex every SEAM_STEP_DEG of Dec along such edges.
export const SEAM_STEP_DEG = 2;

function along(pts, closed) {
  const out = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    out.push(a);
    if (!closed && i === n - 1) break;
    const b = pts[(i + 1) % n];
    if (a[0] === b[0] && Math.abs(b[1] - a[1]) > SEAM_STEP_DEG * 1.5 &&
        isSeamX(a[0])) {
      const steps = Math.ceil(Math.abs(b[1] - a[1]) / SEAM_STEP_DEG);
      for (let j = 1; j < steps; j++) out.push([a[0], a[1] + (b[1] - a[1]) * j / steps]);
    }
  }
  return out;
}

// True for an x on a seam crossing after nudging (180 or -180 plus a multiple
// of 360, pulled SEAM_EPS_DEG inside).
function isSeamX(x) {
  const r = ((x % 360) + 360) % 360;   // 0..360
  return Math.abs(r - (180 + SEAM_EPS_DEG)) < 1e-9 || Math.abs(r - (180 - SEAM_EPS_DEG)) < 1e-9;
}

// Cuts a polygon (or, with closed = false, a polyline) along the seam.
// Returns [{points, closed}]: one entry holding the original points when
// nothing crosses, otherwise one per side of the seam and per crossing.
//
// A closed polygon that winds right round the sphere encloses a pole. Its
// parts are closed along that pole's edge: `opts.pole` (+1 or -1) says which,
// and by default it is the pole on the side of the polygon's mean Dec.
// A polygon with no area, such as a line drawn out along a path and back
// again, comes out as separate lines on each side, not joined across the seam.
// With opts.fill === false a polygon is treated as an outline: its parts are
// open lines (closed: false) that follow the polygon's own edge only.
export function splitAtSeam(points, seamRa, opts = {}) {
  const closed = opts.closed !== false;
  if (!Array.isArray(points) || points.length < 2) return [{ points: points || [], closed }];
  const { xs, winding } = unwrap(points, seamRa, closed);
  // Snap vertices that are within SEAM_TOL_DEG of a seam crossing (x = 180 plus
  // a multiple of 360) onto it.
  for (let i = 0; i < xs.length; i++) {
    const b = 360 * Math.round((xs[i] - 180) / 360) + 180;
    if (Math.abs(xs[i] - b) < SEAM_TOL_DEG) xs[i] = b;
  }
  let lo = Infinity, hi = -Infinity;
  for (const x of xs) { if (x < lo) lo = x; if (x > hi) hi = x; }
  if (winding === 0 && hi < 180 && lo > -180) return [{ points, closed }];

  const c = seamRa - 180;
  const path = points.map((p, i) => [xs[i], p[1]]);
  // An outline (opts.fill === false) is cut into open lines along the curve
  // itself, with no edges added along the seam or through a pole.
  const outline = closed && opts.fill === false;
  if (outline) {
    const xe = xs[0] + 360 * winding;
    path.push([xe, points[0][1]]);
    hi = Math.max(hi, xe); lo = Math.min(lo, xe);
  } else if (closed && winding !== 0) {
    // Close the ring through the pole instead of straight back to the start.
    const meanDec = points.reduce((s, p) => s + p[1], 0) / points.length;
    const pole = (opts.pole === -1 || opts.pole === 1 ? opts.pole : (meanDec >= 0 ? 1 : -1)) * 90;
    const xe = xs[0] + 360 * winding;
    path.push([xe, points[0][1]], [xe, pole], [xs[0], pole]);
    hi = Math.max(hi, xe); lo = Math.min(lo, xe);
  }

  const parts = [];
  const kMin = Math.floor((lo + 180) / 360), kMax = Math.floor((hi + 180) / 360);
  for (let k = kMin; k <= kMax; k++) {
    const sLo = (2 * k - 1) * 180, sHi = (2 * k + 1) * 180;
    const asRing = closed && !outline;
    const pieces = asRing
      ? [clipRing(clipRing(path, sLo, true), sHi, false)]
      : clipPath(path, sLo, sHi);
    for (const piece of pieces) {
      const pts = dedupe(piece, asRing);
      if (pts.length < 2) continue;   // two points is a line, which is a valid footprint
      let pl = Infinity, ph = -Infinity;
      for (const p of pts) { if (p[0] < pl) pl = p[0]; if (p[0] > ph) ph = p[0]; }
      if (ph - pl <= SEAM_TOL_DEG) continue;   // only touches the seam
      const shift = 360 * k;
      const nudged = pts.map(([x, dec]) => {
        let xx = x;
        if (xx <= sLo) xx = sLo + SEAM_EPS_DEG;
        else if (xx >= sHi) xx = sHi - SEAM_EPS_DEG;
        return [xx, dec];
      });
      parts.push({
        closed: asRing,
        points: (asRing ? along(nudged, true) : nudged).map(([xx, dec]) => [wrap360(c + xx - shift), dec]),
      });
    }
  }
  return parts.length ? parts : [{ points, closed }];
}

// --- Rotated views ----------------------------------------------------------
//
// Aladin draws Aitoff and Mollweide with the view centre (RA, Dec) at the
// middle of the map. Unless the centre is on the equator, and the view hasn't
// been rolled by dragging, the seam is not a meridian of constant RA: it is
// the half great circle 180 degrees of longitude from the centre in a rotated
// frame whose equator runs through the centre, turned by the view's roll
// angle about the line of sight. The helpers below rotate the sky into that
// frame, cut along lon = +/-180 there, and rotate the parts back. Checked
// against Aladin Lite 3.8.2 (world2pix) at several centres and roll angles.
// A view is {ra, dec, roll}, all degrees; roll is Aladin's getRotation().

const D2R = Math.PI / 180;

// Position of a sky point in the view frame: [lon, lat] with the view centre
// at [0, 0], lon in (-180, 180].
export function toViewFrame(ra, dec, view) {
  const a = (ra - view.ra) * D2R, d = dec * D2R, s = view.dec * D2R, r = (view.roll || 0) * D2R;
  const x = Math.cos(d) * Math.cos(a), y = Math.cos(d) * Math.sin(a), z = Math.sin(d);
  const x2 = x * Math.cos(s) + z * Math.sin(s), z2 = -x * Math.sin(s) + z * Math.cos(s);
  const y1 = y * Math.cos(r) + z2 * Math.sin(r), z1 = -y * Math.sin(r) + z2 * Math.cos(r);
  return [Math.atan2(y1, x2) / D2R, Math.asin(Math.max(-1, Math.min(1, z1))) / D2R];
}

// Inverse of toViewFrame: [ra, dec] with ra in [0, 360).
export function fromViewFrame(lon, lat, view) {
  const l = lon * D2R, b = lat * D2R, s = view.dec * D2R, r = (view.roll || 0) * D2R;
  const x2 = Math.cos(b) * Math.cos(l), y1 = Math.cos(b) * Math.sin(l), z1 = Math.sin(b);
  const y = y1 * Math.cos(r) - z1 * Math.sin(r), z2 = y1 * Math.sin(r) + z1 * Math.cos(r);
  const x = x2 * Math.cos(s) - z2 * Math.sin(s), z = x2 * Math.sin(s) + z2 * Math.cos(s);
  return [wrap360(Math.atan2(y, x) / D2R + view.ra), Math.asin(Math.max(-1, Math.min(1, z))) / D2R];
}

// splitAtSeam's input and output, but for a view {ra, dec, roll}. Pass
// opts.hint, a [ra, dec] point inside the polygon (a tile's centre), to
// choose which pole closes a polygon that winds round one.
export function splitAtViewSeam(points, view, opts = {}) {
  const closed = opts.closed !== false;
  const vp = points.map(([ra, dec]) => {
    const [lon, lat] = toViewFrame(ra, dec, view);
    return [wrap360(lon), lat];
  });
  let pole;
  if (Array.isArray(opts.hint) && opts.hint.every(Number.isFinite)) {
    pole = toViewFrame(opts.hint[0], opts.hint[1], view)[1] < 0 ? -1 : 1;
  }
  const parts = splitAtSeam(vp, 180, { closed, pole, fill: opts.fill });
  if (parts.length === 1 && parts[0].points === vp) return [{ points, closed }];
  return parts.map(p => ({
    closed: p.closed,
    points: p.points.map(([lon, lat]) => fromViewFrame(lon, lat, view)),
  }));
}

export function crossesViewSeam(points, view, closed = true) {
  if (!Array.isArray(points) || points.length < 2) return false;
  const lons = points.map(([ra, dec]) => [wrap360(toViewFrame(ra, dec, view)[0]), 0]);
  return crossesSeam(lons, 180, closed);
}
