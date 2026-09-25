// Sky geometry for footprints: laying a rectangle out on the tangent plane at
// its centre and deprojecting the corners onto the sky (a gnomonic, or TAN,
// projection). This is exact at any declination. The flat shortcut it replaced
// (RA offset divided by cos(dec)) broke near the poles, where corners wrapped in
// RA and the outline crossed itself.
//
// scripts/build_archive_manifest.py has the same maths in
// tangent_offset_to_radec and fov_corners, so a plan and the coverage it is
// compared with line up. tests/fixtures/tangent_corners.json pins both.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

// Offset (east, north) in degrees on the tangent plane at (ra0, dec0), back to
// [ra, dec]. North runs along the centre's meridian. RA comes back unwrapped,
// within 180 degrees of ra0, so a box straddling RA 0 keeps continuous numbers
// the way the old flat maths did.
export function tangentOffsetToRaDec(ra0, dec0, eastDeg, northDeg) {
  const d0 = dec0 * D2R;
  const xi = eastDeg * D2R;
  const eta = northDeg * D2R;
  const denom = Math.cos(d0) - eta * Math.sin(d0);
  const dra = Math.atan2(xi, denom);
  const dec = Math.atan2(Math.sin(d0) + eta * Math.cos(d0), Math.hypot(xi, denom));
  return [ra0 + dra * R2D, dec * R2D];
}

// The reverse: where (ra, dec) sits on the tangent plane at (ra0, dec0), as
// [east, north] in degrees. Returns null for a point 90 degrees or more away,
// which has no place on that plane.
export function raDecToTangentOffset(ra0, dec0, ra, dec) {
  const d0 = dec0 * D2R;
  const d = dec * D2R;
  const dra = (ra - ra0) * D2R;
  const cosC = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(dra);
  if (!(cosC > 0)) return null;
  const xi = Math.cos(d) * Math.sin(dra) / cosC;
  const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(dra)) / cosC;
  return [xi * R2D, eta * R2D];
}

// The 4 corners of a w by h arcmin box centred on (ra, dec), its height turned
// to position angle rotDeg east of north (NINA's convention for the camera's
// +Y axis). Returns [[ra, dec], ...] in order SW, NW, NE, SE of the unturned
// box, so the NE corner (index 2) can host a rotation handle.
export function rectangleCorners(ra, dec, wArcmin, hArcmin, rotDeg) {
  const hw = wArcmin / 2 / 60;
  const hh = hArcmin / 2 / 60;
  const r = (rotDeg || 0) * D2R;
  const cosR = Math.cos(r), sinR = Math.sin(r);
  return [[-hw, -hh], [-hw, hh], [hw, hh], [hw, -hh]].map(([lx, ly]) => {
    const east = lx * cosR + ly * sinR;
    const north = -lx * sinR + ly * cosR;
    return tangentOffsetToRaDec(ra, dec, east, north);
  });
}
