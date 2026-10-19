import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_FOOTPRINT_DEG,
  footprintSizeDeg,
  zoomFovFor,
} from "../../static/map-focus.mjs";

describe("footprintSizeDeg", () => {
  it("measures the widest corner separation", () => {
    // A 2 x 1 degree box at the equator: the diagonal is about 2.236 degrees.
    const t = { corners_icrs: [[10, -0.5], [12, -0.5], [12, 0.5], [10, 0.5]] };
    assert.ok(Math.abs(footprintSizeDeg(t) - Math.hypot(2, 1)) < 0.01);
  });

  it("copes with corners across RA 0", () => {
    const t = { corners_icrs: [[359, 0], [1, 0], [1, 1], [359, 1]] };
    assert.ok(Math.abs(footprintSizeDeg(t) - Math.hypot(2, 1)) < 0.01);
  });

  it("falls back to fov_arcmin, larger side, in degrees", () => {
    assert.equal(footprintSizeDeg({ fov_arcmin: [90, 120] }), 2);
  });

  it("prefers corners over fov_arcmin", () => {
    const t = {
      corners_icrs: [[0, 0], [1, 0], [1, 1], [0, 1]],
      fov_arcmin: [600, 600],
    };
    assert.ok(footprintSizeDeg(t) < 2);
  });

  it("uses 3 degrees when nothing is known", () => {
    assert.equal(footprintSizeDeg({}), DEFAULT_FOOTPRINT_DEG);
    assert.equal(footprintSizeDeg(null), 3);
    assert.equal(footprintSizeDeg({ corners_icrs: [] , fov_arcmin: [0, 0] }), 3);
  });
});

describe("zoomFovFor", () => {
  it("zooms so the footprint fills a third of the view from all-sky", () => {
    assert.equal(zoomFovFor(180, 2), 6);
  });

  it("leaves the zoom alone when already close in", () => {
    assert.equal(zoomFovFor(10, 2), null);
    assert.equal(zoomFovFor(5, 2), null);
  });

  it("does not zoom at exactly ten footprints", () => {
    assert.equal(zoomFovFor(20, 2), null);
  });

  it("zooms just past ten footprints", () => {
    assert.equal(zoomFovFor(20.5, 2), 6);
  });

  it("ignores unusable inputs", () => {
    assert.equal(zoomFovFor(NaN, 2), null);
    assert.equal(zoomFovFor(180, 0), null);
    assert.equal(zoomFovFor(undefined, 2), null);
  });
});
