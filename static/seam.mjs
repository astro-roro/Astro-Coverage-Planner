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
  return hi > 180 || lo <= -180;
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

// Cuts a polygon (or, with closed = false, a polyline) along the seam.
// Returns [{points, closed}]: one entry holding the original points when
// nothing crosses, otherwise one per side of the seam and per crossing.
//
// A closed polygon that winds right round the sphere encloses a pole. Its
// parts are closed along that pole's edge: `opts.pole` (+1 or -1) says which,
// and by default it is the pole on the side of the polygon's mean Dec.
// A polygon with no area, such as a line drawn out along a path and back
// again, comes out as separate lines on each side, not joined across the seam.
export function splitAtSeam(points, seamRa, opts = {}) {
  const closed = opts.closed !== false;
  if (!Array.isArray(points) || points.length < 2) return [{ points: points || [], closed }];
  const { xs, winding } = unwrap(points, seamRa, closed);
  let lo = Infinity, hi = -Infinity;
  for (const x of xs) { if (x < lo) lo = x; if (x > hi) hi = x; }
  if (winding === 0 && hi <= 180 && lo > -180) return [{ points, closed }];

  const c = seamRa - 180;
  const path = points.map((p, i) => [xs[i], p[1]]);
  if (closed && winding !== 0) {
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
    const pieces = closed
      ? [clipRing(clipRing(path, sLo, true), sHi, false)]
      : clipPath(path, sLo, sHi);
    for (const piece of pieces) {
      const pts = dedupe(piece, closed);
      if (pts.length < 2) continue;   // two points is a line, which is a valid footprint
      let pl = Infinity, ph = -Infinity;
      for (const p of pts) { if (p[0] < pl) pl = p[0]; if (p[0] > ph) ph = p[0]; }
      if (ph - pl <= 1e-9) continue;   // only touches the seam
      const shift = 360 * k;
      parts.push({
        closed,
        points: pts.map(([x, dec]) => {
          let xx = x;
          if (xx <= sLo) xx = sLo + SEAM_EPS_DEG;
          else if (xx >= sHi) xx = sHi - SEAM_EPS_DEG;
          return [wrap360(c + xx - shift), dec];
        }),
      });
    }
  }
  return parts.length ? parts : [{ points, closed }];
}
