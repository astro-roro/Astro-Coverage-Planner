import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  raDecToTangentOffset,
  rectangleCorners,
  tangentOffsetToRaDec,
} from "../../static/sky-geometry.mjs";

const fixture = JSON.parse(readFileSync(
  new URL("../fixtures/tangent_corners.json", import.meta.url), "utf-8"));

const D2R = Math.PI / 180;
function sepArcsec([a1, d1], [a2, d2]) {
  const u = [Math.cos(d1 * D2R) * Math.cos(a1 * D2R), Math.cos(d1 * D2R) * Math.sin(a1 * D2R), Math.sin(d1 * D2R)];
  const v = [Math.cos(d2 * D2R) * Math.cos(a2 * D2R), Math.cos(d2 * D2R) * Math.sin(a2 * D2R), Math.sin(d2 * D2R)];
  const c = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  const dot = u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  return Math.atan2(Math.hypot(...c), dot) / D2R * 3600;
}

// The flat maths computePlanCorners used before 2026-09-25.
function flatCorners(ra, dec, w, h, rot) {
  const hw = w / 120, hh = h / 120, r = rot * D2R;
  const cosD = Math.max(1e-6, Math.cos(dec * D2R));
  return [[-hw, -hh], [-hw, hh], [hw, hh], [hw, -hh]].map(([lx, ly]) => {
    const e = lx * Math.cos(r) + ly * Math.sin(r);
    const n = -lx * Math.sin(r) + ly * Math.cos(r);
    return [ra + e / cosD, dec + n];
  });
}

describe("rectangleCorners", () => {
  for (const c of fixture.cases) {
    it(`matches the manifest builder: ${c.name}`, () => {
      const got = rectangleCorners(c.ra, c.dec, c.w, c.h, c.rot);
      assert.equal(got.length, 4);
      got.forEach((p, i) => {
        assert.ok(sepArcsec(p, c.corners[i]) < 1e-4,
          `corner ${i}: ${p} vs ${c.corners[i]}`);
        assert.ok(p[1] >= -90 && p[1] <= 90);
      });
    });
  }

  it("draws a field on the pole as a box that does not cross itself", () => {
    const corners = rectangleCorners(120, -90, 323, 216, 37);
    const xy = corners.map(([ra, dec]) => raDecToTangentOffset(120, -90, ra, dec));
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const signs = [0, 1, 2, 3].map(i => Math.sign(cross(xy[i], xy[(i + 1) % 4], xy[(i + 2) % 4])));
    assert.equal(new Set(signs).size, 1);
  });

  it("keeps RA continuous across 0 like the old code did", () => {
    const corners = rectangleCorners(0.2, 40, 120, 80, 0);
    assert.ok(corners.some(([ra]) => ra < 0), "west corners run below 0, not up near 360");
  });

  it("stays within 30 arcsec of the old flat corners at moderate declinations", () => {
    for (const dec of [-60, -30, 0, 30, 60]) {
      for (const rot of [0, 33, 137]) {
        const got = rectangleCorners(251.94, dec, 60, 40, rot);
        const old = flatCorners(251.94, dec, 60, 40, rot);
        got.forEach((p, i) => assert.ok(sepArcsec(p, old[i]) < 30, `dec ${dec} rot ${rot}`));
      }
    }
  });
});

describe("tangent offsets", () => {
  it("round-trip anywhere, including on the pole", () => {
    for (const [ra0, dec0] of [[10, 0], [200, 55], [300, -89.9], [45, -90], [0, 90]]) {
      for (const [e, n] of [[0.3, -0.2], [-2, 1.5], [0, 2.7]]) {
        const [ra, dec] = tangentOffsetToRaDec(ra0, dec0, e, n);
        const [e2, n2] = raDecToTangentOffset(ra0, dec0, ra, dec);
        assert.ok(Math.abs(e2 - e) < 1e-9 && Math.abs(n2 - n) < 1e-9, `${ra0},${dec0}`);
      }
    }
  });

  it("has no place for a point on the far side of the sky", () => {
    assert.equal(raDecToTangentOffset(0, 0, 180, 0), null);
  });
});
