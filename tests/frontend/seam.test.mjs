import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  splitAtSeam, crossesSeam, projectionHasSeam, seamRaFor, SEAM_EPS_DEG,
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

  it("leaves one that only touches the seam from the west", () => {
    const sq = [[170, 0], [180, 0], [180, 10], [170, 10]];
    assert.equal(splitAtSeam(sq, SEAM)[0].points, sq);
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
