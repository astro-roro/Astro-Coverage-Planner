import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  splitAtSeam, crossesSeam, projectionHasSeam, seamRaFor, SEAM_EPS_DEG, SEAM_TOL_DEG,
  toViewFrame, fromViewFrame, splitAtViewSeam, crossesViewSeam,
} from "../../static/seam.mjs";

const wrap180 = d => { const m = ((d % 360) + 360) % 360; return m > 180 ? m - 360 : m; };
const SEAM = 180;           // view centred on RA 0
const C = SEAM - 180;

// Position in the flat map frame: x is longitude from the view centre.
const flat = ([ra, dec]) => [wrap180(ra - C), dec];

function area(points) {
  const p = points.map(flat);
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2;
}

function inPoly(pt, poly) {
  const p = poly.map(flat);
  let inside = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    if ((p[i][1] > pt[1]) !== (p[j][1] > pt[1]) &&
        pt[0] < (p[j][0] - p[i][0]) * (pt[1] - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0]) inside = !inside;
  }
  return inside;
}

const sideOf = pts => {
  const xs = pts.map(p => flat(p)[0]);
  return { min: Math.min(...xs), max: Math.max(...xs) };
};

describe("projectionHasSeam and seamRaFor", () => {
  it("knows which projections have a seam", () => {
    for (const p of ["AIT", "MOL", "ait"]) assert.equal(projectionHasSeam(p), true, p);
    for (const p of ["SIN", "TAN", "", undefined]) assert.equal(projectionHasSeam(p), false, String(p));
  });
  it("puts the seam opposite the view centre, not at RA 0", () => {
    assert.equal(seamRaFor(0), 180);
    assert.equal(seamRaFor(100), 280);
    assert.equal(seamRaFor(270), 90);
  });
});

describe("splitAtSeam", () => {
  it("leaves a polygon that doesn't cross the seam as it is", () => {
    const sq = [[10, 0], [20, 0], [20, 10], [10, 10]];
    const parts = splitAtSeam(sq, SEAM);
    assert.equal(parts.length, 1);
    assert.equal(parts[0].points, sq);
    assert.equal(crossesSeam(sq, SEAM), false);
  });

  it("pulls a polygon that only touches the seam back inside it", () => {
    const sq = [[170, 0], [180, 0], [180, 10], [170, 10]];
    const parts = splitAtSeam(sq, SEAM);
    assert.equal(parts.length, 1);
    for (const [ra] of parts[0].points) assert.ok(ra <= 180 - SEAM_EPS_DEG + 1e-9, String(ra));
    assert.ok(Math.abs(area(parts[0].points) - 100) < 0.2);
  });

  it("treats vertices a hair either side of the seam as on it", () => {
    // Aladin rounds in single precision, so these can land on opposite edges.
    for (const [a, b] of [[180 - 1e-5, 180 + 1e-5], [180 + 1e-5, 180 + 1e-5], [180 - 1e-5, 180 - 1e-5]]) {
      const cell = [[170, 0], [a, 0], [b, 10], [170, 10]];
      const parts = splitAtSeam(cell, SEAM);
      assert.ok(parts.length >= 1);
      for (const p of parts) {
        const s = sideOf(p.points);
        assert.ok(s.max - s.min < 15, `part spans ${s.max - s.min}`);
        for (const q of p.points) {
          assert.ok(Math.abs(wrap180(q[0] - SEAM)) >= SEAM_TOL_DEG, `point at ${q[0]}`);
        }
      }
    }
  });

  it("splits a box over the seam into two closed parts on either side", () => {
    const box = [[170, 10], [190, 10], [190, 20], [170, 20]];
    assert.equal(crossesSeam(box, SEAM), true);
    const parts = splitAtSeam(box, SEAM);
    assert.equal(parts.length, 2);
    for (const p of parts) assert.equal(p.closed, true);
    const total = parts.reduce((s, p) => s + area(p.points), 0);
    assert.ok(Math.abs(total - 200) < 0.5, `area ${total}`);
    const sides = parts.map(p => sideOf(p.points)).sort((a, b) => a.min - b.min);
    assert.ok(sides[0].min < -170 && sides[0].max < 0, "east part sits at the left edge");
    assert.ok(sides[1].max > 170 && sides[1].min > 0, "west part sits at the right edge");
    for (const p of parts) for (const [ra] of p.points) {
      assert.ok(Math.abs(wrap180(ra - SEAM)) >= SEAM_EPS_DEG - 1e-9);
    }
  });

  it("handles a shape that crosses the seam and comes back, twice", () => {
    const c = [[170, 0], [190, 0], [190, 10], [170, 10], [170, 20], [190, 20],
               [190, 30], [170, 30]];
    // Same shape either way round the ring.
    for (const poly of [c, [...c].reverse()]) {
      const parts = splitAtSeam(poly, SEAM);
      const total = parts.reduce((s, p) => s + area(p.points), 0);
      assert.ok(Math.abs(total - 400) < 1, `area ${total}`);
      for (const p of parts) {
        const s = sideOf(p.points);
        assert.ok(s.max - s.min <= 180, "no part spans the map");
      }
    }
  });

  it("splits a 120 degree ring around a southern moon into parts that cover the same sky", () => {
    const ra0 = 100, dec0 = -60, r = 120 * Math.PI / 180;
    const d0 = dec0 * Math.PI / 180, a0 = ra0 * Math.PI / 180;
    const ring = [];
    for (let i = 0; i < 720; i++) {
      const bearing = (i / 720) * 2 * Math.PI;
      const dec = Math.asin(Math.sin(d0) * Math.cos(r) + Math.cos(d0) * Math.sin(r) * Math.cos(bearing));
      const ra = a0 + Math.atan2(Math.sin(bearing) * Math.sin(r) * Math.cos(d0),
        Math.cos(r) - Math.sin(d0) * Math.sin(dec));
      ring.push([((ra * 180 / Math.PI) % 360 + 360) % 360, dec * 180 / Math.PI]);
    }
    const seam = seamRaFor(ra0 + 30);   // view centred 30 degrees away from the moon
    const c = seam - 180;
    const parts = splitAtSeam(ring, seam, { pole: -1 });
    assert.ok(parts.length >= 1 && parts.every(p => p.closed));
    const fl = ([ra, dec]) => [wrap180(ra - c), dec];
    for (const p of parts) {
      const xs = p.points.map(q => fl(q)[0]);
      assert.ok(Math.max(...xs) - Math.min(...xs) <= 360, "within the map");
    }
    const inAny = pt => parts.some(p => {
      const q = p.points.map(fl);
      let inside = false;
      for (let i = 0, j = q.length - 1; i < q.length; j = i++) {
        if ((q[i][1] > pt[1]) !== (q[j][1] > pt[1]) &&
            pt[0] < (q[j][0] - q[i][0]) * (pt[1] - q[i][1]) / (q[j][1] - q[i][1]) + q[i][0]) inside = !inside;
      }
      return inside;
    });
    // The ring is a circle on the sphere; its edges are straight in this flat
    // frame, so only test points well clear of it. Sample on a fixed grid.
    let tested = 0;
    for (let x = -178; x < 178; x += 7) {
      for (let dec = -88; dec < 88; dec += 5) {
        const ra = wrap180(x + c);
        const rr = ra * Math.PI / 180, dd = dec * Math.PI / 180;
        const dist = Math.acos(Math.max(-1, Math.min(1,
          Math.sin(dd) * Math.sin(d0) + Math.cos(dd) * Math.cos(d0) * Math.cos(rr - a0)))) * 180 / Math.PI;
        if (Math.abs(dist - 120) < 4) continue;
        tested++;
        assert.equal(inAny([x, dec]), dist < 120, `x=${x} dec=${dec} dist=${dist.toFixed(1)}`);
      }
    }
    assert.ok(tested > 500);
  });

  it("follows the seam with extra vertices instead of a long chord", () => {
    // A big ring whose two crossings are far apart in Dec: the edge joining them
    // along the seam must not be one straight chord across the map.
    const ring = [];
    for (let i = 0; i < 180; i++) {
      const t = (i / 180) * 2 * Math.PI;
      ring.push([(170 + 50 * Math.cos(t) + 360) % 360, 60 * Math.sin(t)]);
    }
    const parts = splitAtSeam(ring, SEAM);
    assert.equal(parts.length, 2);
    let seamEdges = 0;
    for (const p of parts) {
      for (let i = 0; i < p.points.length; i++) {
        const a = p.points[i], b = p.points[(i + 1) % p.points.length];
        const onSeam = q => Math.abs(wrap180(q[0] - SEAM)) < 0.01;
        if (onSeam(a) && onSeam(b)) {
          seamEdges++;
          assert.ok(Math.abs(a[1] - b[1]) <= 3, `chord of ${Math.abs(a[1] - b[1])} degrees`);
        }
      }
    }
    assert.ok(seamEdges > 20);
  });

  it("cuts an outline into open lines with nothing added along the seam or through a pole", () => {
    const ring = [];
    for (let i = 0; i < 180; i++) {
      const t = (i / 180) * 2 * Math.PI;
      ring.push([(170 + 50 * Math.cos(t) + 360) % 360, 60 * Math.sin(t)]);
    }
    const parts = splitAtSeam(ring, SEAM, { fill: false });
    assert.ok(parts.length >= 2);
    for (const p of parts) {
      assert.equal(p.closed, false);
      for (const q of p.points) {
        assert.ok(Math.abs(q[1]) <= 60 + 1e-9, "no vertex off the ring");
      }
    }
    const lengths = parts.reduce((n, p) => n + p.points.length, 0);
    assert.ok(lengths >= 180);
    // A ring that winds round the pole: the pole is not visited.
    const around = [[0, 80], [90, 80], [180, 80], [270, 80]];
    for (const p of splitAtSeam(around, SEAM, { fill: false })) {
      assert.equal(p.closed, false);
      assert.ok(p.points.every(q => q[1] === 80));
    }
    // A box over the seam becomes two open lines.
    const box = splitAtSeam([[170, 10], [190, 10], [190, 20], [170, 20]], SEAM, { fill: false });
    assert.ok(box.length >= 2 && box.every(p => !p.closed));
  });

  it("closes a polygon round the pole along the pole's edge", () => {
    const ring = [[0, 80], [90, 80], [180, 80], [270, 80]];
    const parts = splitAtSeam(ring, SEAM);
    assert.ok(parts.length >= 2);
    const total = parts.reduce((s, p) => s + area(p.points), 0);
    // 360 degrees of longitude by the 10 degrees between dec 80 and the pole.
    assert.ok(Math.abs(total - 3600) < 20, `area ${total}`);
    assert.ok(parts.some(p => p.points.some(q => q[1] === 90)));
    const south = splitAtSeam([[0, -80], [90, -80], [180, -80], [270, -80]], SEAM);
    assert.ok(south.some(p => p.points.some(q => q[1] === -90)));
    const forced = splitAtSeam(ring, SEAM, { pole: -1 });
    assert.ok(forced.some(p => p.points.some(q => q[1] === -90)));
  });

  it("splits an out-and-back line into separate lines, not closed across the seam", () => {
    const line = [[170, 10], [190, 10], [170, 10]];
    const parts = splitAtSeam(line, SEAM);
    assert.equal(parts.length, 2);
    for (const p of parts) {
      assert.ok(area(p.points) < 1e-9, "still a line");
      const s = sideOf(p.points);
      assert.ok(s.max - s.min <= 10 + 1e-6, "stays on one side");
    }
  });

  it("splits a longer out-and-back line that crosses the seam twice", () => {
    const path = [[170, 0], [190, 0], [170, 0]];
    const there = [[160, 0], [175, 5], [185, 5], [200, 0]];
    const poly = [...there, ...[...there].reverse().slice(1, -1)];
    const parts = splitAtSeam(poly, SEAM);
    assert.ok(parts.length >= 2);
    for (const p of parts) {
      assert.ok(area(p.points) < 1e-6);
      const s = sideOf(p.points);
      assert.ok(s.max - s.min < 180);
    }
    assert.equal(splitAtSeam(path, SEAM).length, 2);
  });

  it("cuts an open polyline into open pieces", () => {
    const parts = splitAtSeam([[170, 0], [190, 10]], SEAM, { closed: false });
    assert.equal(parts.length, 2);
    for (const p of parts) assert.equal(p.closed, false);
    const [west, east] = parts.sort((a, b) => flat(b.points[0])[0] - flat(a.points[0])[0]);
    assert.ok(Math.abs(west.points[west.points.length - 1][1] - 5) < 0.05, "cut at the midpoint");
    assert.ok(Math.abs(east.points[0][1] - 5) < 0.05);
  });

  it("handles points exactly on the seam", () => {
    const eastOfSeam = [[180, 0], [190, 0], [190, 10], [180, 10]];
    const parts = splitAtSeam(eastOfSeam, SEAM);
    assert.equal(parts.length, 1);
    for (const [ra] of parts[0].points) assert.ok(ra > 180 && ra <= 190, String(ra));
    const through = splitAtSeam([[180, 0], [190, 5], [170, 10]], SEAM);
    assert.equal(through.length, 2);
    for (const p of through) assert.ok(p.points.every(q => q.every(Number.isFinite)));
  });

  it("is fast enough for ~1700 cells", () => {
    const cells = [];
    for (let i = 0; i < 1700; i++) {
      const ra = (i * 37) % 360, dec = ((i * 11) % 160) - 80;
      cells.push([[ra, dec], [ra + 8, dec], [ra + 8, dec + 6], [ra, dec + 6]].map(([r, d]) => [r % 360, d]));
    }
    const t0 = performance.now();
    let crossing = 0;
    for (let rep = 0; rep < 10; rep++) {
      for (const cell of cells) {
        if (crossesSeam(cell, 180)) { crossing++; splitAtSeam(cell, 180); }
      }
    }
    const ms = (performance.now() - t0) / 10;
    console.log(`seam: ${ms.toFixed(2)} ms per pass over 1700 cells (${crossing / 10} crossing)`);
    assert.ok(ms < 100, `${ms} ms`);
  });
});

describe("rotated view frame", () => {
  const V = (ra, dec, roll = 0) => ({ ra, dec, roll });

  it("round trips and puts the view centre at the origin, at any roll", () => {
    for (const v of [V(100, -30), V(250, 45, 30), V(0, 70, -45), V(10, 0, 180)]) {
      const [lon, lat] = toViewFrame(v.ra, v.dec, v);
      assert.ok(Math.abs(lon) < 1e-9 && Math.abs(lat) < 1e-9);
      for (const [ra, dec] of [[0, 0], [123, 45], [300, -60], [180, 89]]) {
        const [l, b] = toViewFrame(ra, dec, v);
        const [r2, d2] = fromViewFrame(l, b, v);
        assert.ok(Math.abs(wrap180(r2 - ra)) < 1e-7 && Math.abs(d2 - dec) < 1e-7, `${ra},${dec}`);
      }
    }
  });

  it("agrees with Aladin Lite 3.8.2's Aitoff map (probed with world2pix)", () => {
    // Centre (100, -30), no roll: view lon +/-179.9 at lat 0 is RA 279.9/280.1 at
    // Dec +30, which Aladin drew at the left and right edges of the map, and
    // 'straight up' from the centre is lat +60.
    const v = V(100, -30);
    assert.ok(Math.abs(toViewFrame(279.9, 30, v)[0] - 179.9) < 0.05);
    const [lon, lat] = toViewFrame(100, 30, v);
    assert.ok(Math.abs(lon) < 1e-6 && Math.abs(lat - 60) < 1e-6);
    // With a roll of 30 degrees, the point Aladin drew at lon 179.9 / lat 40
    // (ra 11.4?, probed centre 273.1 -28.7) is where fromViewFrame puts it.
    const rolled = V(273.1, -28.7, 30);
    const [ra, dec] = fromViewFrame(179.9, 40, rolled);
    const [l2, b2] = toViewFrame(ra, dec, rolled);
    assert.ok(Math.abs(l2 - 179.9) < 1e-6 && Math.abs(b2 - 40) < 1e-6);
  });

  it("puts the seam over the pole's far side when the centre is off the equator", () => {
    // Centre (273, -29), no roll: the seam is the RA 93 meridian from Dec -61 over
    // the pole and down RA 273 to Dec +61.
    const v = V(273, -29);
    assert.equal(crossesViewSeam([[92, -45], [94, -45], [94, -40], [92, -40]], v), true);
    assert.equal(crossesViewSeam([[272, 75], [274, 75], [274, 80], [272, 80]], v), true);
    assert.equal(crossesViewSeam([[272, 40], [274, 40], [274, 45], [272, 45]], v), false);
    assert.equal(crossesViewSeam([[272, 75], [274, 75], [274, 80], [272, 80]], V(273, 0)), false);
  });

  it("moves the seam when the view is rolled", () => {
    const cell = [[92, -45], [94, -45], [94, -40], [92, -40]];
    assert.equal(crossesViewSeam(cell, V(273, -29, 0)), true);
    assert.equal(crossesViewSeam(cell, V(273, -29, 60)), false);
  });

  it("splits across the seam and keeps each part on one side", () => {
    const v = V(273, -29, 25);
    // A cell on the seam for this view: walk along lon 180 in the view frame.
    const [ra0, dec0] = fromViewFrame(180, 10, v);
    const [ra1, dec1] = fromViewFrame(180, 14, v);
    const cell = [fromViewFrame(176, 10, v), fromViewFrame(-176, 10, v), fromViewFrame(-176, 14, v), fromViewFrame(176, 14, v)];
    assert.ok(Number.isFinite(ra0 + dec0 + ra1 + dec1));
    assert.equal(crossesViewSeam(cell, v), true);
    const parts = splitAtViewSeam(cell, v);
    assert.equal(parts.length, 2);
    for (const p of parts) {
      const lons = p.points.map(q => toViewFrame(q[0], q[1], v)[0]);
      assert.ok(Math.max(...lons) - Math.min(...lons) < 6, "each part stays on one side");
      for (const l of lons) assert.ok(180 - Math.abs(l) >= SEAM_TOL_DEG - 1e-9, String(l));
    }
  });

  it("leaves a polygon away from the seam untouched", () => {
    const cell = [[10, 0], [15, 0], [15, 5], [10, 5]];
    const parts = splitAtViewSeam(cell, V(273, -29));
    assert.equal(parts.length, 1);
    assert.equal(parts[0].points, cell);
  });

  it("handles a cell whose edge lies along the seam, where vertices flip sides under float noise", () => {
    // The Nights case: centre RA 273.12, cell edge at RA 93.12.
    const v = V(273.11996161693475, -28.758810522062728);
    const cell = [[88.16, -49.8], [91.84, -49.8], [93.1199616, -49.76], [93.1199616, -44.87], [91.84, -44.9], [88.16, -44.9]];
    const parts = splitAtViewSeam(cell, v, { hint: [91, -47] });
    for (const p of parts) {
      const lons = p.points.map(q => toViewFrame(q[0], q[1], v)[0]);
      assert.ok(Math.max(...lons) - Math.min(...lons) < 30, "no part spans the map");
      for (const l of lons) assert.ok(180 - Math.abs(l) >= SEAM_TOL_DEG - 1e-9, String(l));
    }
  });
});
