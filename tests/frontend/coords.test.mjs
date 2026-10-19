import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  deg2dms,
  deg2hms,
  formatDec,
  formatRA,
  parseCoordPair,
  parseDec,
  parseRA,
} from "../../static/coords.mjs";

// Orion Nebula, 05h35m17.3s -05d23m28s.
const RA = (5 + 35 / 60 + 17.3 / 3600) * 15;
const DEC = -(5 + 23 / 60 + 28 / 3600);

function near(actual, expected, tol = 1e-6) {
  assert.ok(actual !== null && Math.abs(actual - expected) < tol, `${actual} vs ${expected}`);
}

describe("parseRA", () => {
  const forms = [
    "05 35 17.3", "05:35:17.3", "05h35m17.3s", "05h 35m 17.3s", " 5h 35m 17.3s ",
    "05h35m17.3", "5 35 17.3",
  ];
  for (const f of forms) {
    it(`reads "${f}" as hours`, () => near(parseRA(f), RA));
  }
  it("reads hours and minutes with no seconds", () => {
    near(parseRA("5h35m"), (5 + 35 / 60) * 15);
    near(parseRA("05 35"), (5 + 35 / 60) * 15);
  });
  it("reads decimal hours", () => {
    near(parseRA("5.588h"), 5.588 * 15);
    near(parseRA("5.588 h"), 5.588 * 15);
  });
  it("treats a bare number as degrees", () => {
    near(parseRA("83.822"), 83.822);
    near(parseRA("10"), 10);
    near(parseRA("0"), 0);
  });
  it("reads degree-suffixed sexagesimal as degrees", () => {
    near(parseRA("83d49m19s"), 83 + 49 / 60 + 19 / 3600);
  });
  it("wraps 24h to 0", () => {
    assert.equal(parseRA("24h"), 0);
    assert.equal(parseRA("24 00 00"), 0);
  });
  it("rejects out-of-range values", () => {
    assert.equal(parseRA("25h"), null);
    assert.equal(parseRA("24 00 01"), null);
    assert.equal(parseRA("05 60 00"), null);
    assert.equal(parseRA("05 35 60"), null);
    assert.equal(parseRA("05 35 61.5"), null);
    assert.equal(parseRA("361"), null);
    assert.equal(parseRA("-10"), null);
  });
  it("rejects junk and never throws", () => {
    for (const bad of ["", "   ", "abc", "1e5", "5h35m17s extra", "1 2 3 4", "5.5 35", "--5", "h", null, undefined, {}]) {
      assert.equal(parseRA(bad), null, String(bad));
    }
  });
});

describe("parseDec", () => {
  const forms = [
    "-05 23 28", "-05:23:28.0", "-05d23m28s", "-05° 23′ 28″", "-05°23'28\"",
    "−05 23 28", "  -05 23 28  ", "-5 23 28",
  ];
  for (const f of forms) {
    it(`reads "${f}"`, () => near(parseDec(f), DEC, 1e-6));
  }
  it("reads positive forms", () => {
    near(parseDec("+41°16′09″"), 41 + 16 / 60 + 9 / 3600);
    near(parseDec("41d16m09s"), 41 + 16 / 60 + 9 / 3600);
    near(parseDec("41 16"), 41 + 16 / 60);
  });
  it("reads decimal degrees unchanged", () => {
    near(parseDec("-5.391"), -5.391);
    near(parseDec("41.5"), 41.5);
  });
  it("keeps the sign on negative zero", () => {
    near(parseDec("-00 30 00"), -0.5);
    near(parseDec("-00:30"), -0.5);
    near(parseDec("-0d0m1s"), -1 / 3600);
  });
  it("returns plain zero for a signed zero", () => {
    assert.ok(Object.is(parseDec("-00 00 00"), 0));
  });
  it("accepts the poles and rejects beyond", () => {
    assert.equal(parseDec("90"), 90);
    assert.equal(parseDec("-90 00 00"), -90);
    assert.equal(parseDec("90.001"), null);
    assert.equal(parseDec("90 00 01"), null);
    assert.equal(parseDec("-91"), null);
    assert.equal(parseDec("-05 60 00"), null);
    assert.equal(parseDec("-05 23 60"), null);
  });
  it("rejects hour units and junk, never throwing", () => {
    for (const bad of ["5h", "", "x", "5 23 28 1", null, undefined, NaN]) {
      assert.equal(parseDec(bad), null, String(bad));
    }
  });
});

describe("parseCoordPair", () => {
  const pairs = [
    "05 35 17.3 -05 23 28",
    "05:35:17.3 -05:23:28.0",
    "05h35m17.3s -05d23m28s",
    "05h 35m 17.3s -05° 23′ 28″",
    "05h 35m 17.3s, -05° 23' 28\"",
    "05 35 17.3,-05 23 28",
    "05 35 17.3   -05 23 28",
    "  05:35:17.3−05:23:28  ",
    "05h35m17.3s;-05d23m28s",
    "RA 05h 35m 17.3s Dec -05° 23′ 28″",
    "RA: 05:35:17.3 Dec: -05:23:28",
    "J053517.3-052328",
    "053517.3-052328",
  ];
  for (const p of pairs) {
    it(`reads "${p}"`, () => {
      const r = parseCoordPair(p);
      assert.ok(r, "null result");
      near(r.ra, RA);
      near(r.dec, DEC);
    });
  }
  it("reads decimal degrees", () => {
    assert.deepEqual(parseCoordPair("83.822 -5.391"), { ra: 83.822, dec: -5.391 });
    assert.deepEqual(parseCoordPair("83.822, -5.391"), { ra: 83.822, dec: -5.391 });
  });
  it("splits an unsigned pair by comma, double space or even groups", () => {
    assert.deepEqual(parseCoordPair("83.822, 5.391"), { ra: 83.822, dec: 5.391 });
    assert.deepEqual(parseCoordPair("83.822  5.391"), { ra: 83.822, dec: 5.391 });
    assert.deepEqual(parseCoordPair("83.822 5.391"), { ra: 83.822, dec: 5.391 });
    const r = parseCoordPair("05 35 17.3 +05 23 28");
    near(r.ra, RA);
    near(r.dec, 5 + 23 / 60 + 28 / 3600);
    const g = parseCoordPair("05 35 17.3 05 23 28");
    near(g.dec, 5 + 23 / 60 + 28 / 3600);
  });
  it("reads decimal hours with a signed Dec", () => {
    const r = parseCoordPair("5.588h -5.391");
    near(r.ra, 5.588 * 15);
    near(r.dec, -5.391);
  });
  it("keeps negative zero in a pair", () => {
    const r = parseCoordPair("05 35 17.3 -00 30 00");
    near(r.dec, -0.5);
  });
  it("reads compact forms without seconds", () => {
    const r = parseCoordPair("J0535-0523");
    near(r.ra, (5 + 35 / 60) * 15);
    near(r.dec, -(5 + 23 / 60));
  });
  it("rejects bad input and never throws", () => {
    for (const bad of ["", "  ", "83.822", "foo bar", "25h -5", "05 35 17 -95 00 00", "05 60 00 -05 00 00",
      "J053517.3-0523", "1 2 3", null, undefined, 5]) {
      assert.equal(parseCoordPair(bad), null, String(bad));
    }
  });
});

describe("formatting", () => {
  it("formats decimals to 4 places", () => {
    assert.equal(formatRA(83.822083), "83.8221");
    assert.equal(formatDec(-5.391111), "-5.3911");
    assert.equal(formatRA(83.822083, { sexagesimal: false }), "83.8221");
  });
  it("formats sexagesimal like the old readouts", () => {
    assert.equal(formatRA(RA, { sexagesimal: true }), "05h 35m 17.30s");
    assert.equal(formatDec(DEC, { sexagesimal: true }), "-05° 23' 28.00\"");
    assert.equal(deg2hms(0), "00h 00m 0.00s");
    assert.equal(deg2dms(41.5), "+41° 30' 0.00\"");
  });
  it("never prints 60 seconds", () => {
    assert.equal(deg2hms(15 - 1e-9), "01h 00m 0.00s");
    assert.equal(deg2hms(360 - 1e-9), "00h 00m 0.00s");
    assert.equal(deg2dms(-(1 - 1e-9)), "-01° 00' 0.00\"");
  });
  it("does not sign a Dec that rounds to zero", () => {
    assert.equal(deg2dms(-1e-9), "+00° 00' 0.00\"");
  });
  it("returns empty text for missing values", () => {
    for (const v of [null, undefined, NaN, Infinity]) {
      assert.equal(formatRA(v), "");
      assert.equal(formatDec(v, { sexagesimal: true }), "");
    }
  });
  it("round trips through format then parse", () => {
    for (const ra of [0, 0.0001, 10.5, 83.822083, 180, 266.4168, 359.9999]) {
      near(parseRA(formatRA(ra, { sexagesimal: true })), ra, 15 * 0.005 / 3600 + 1e-9);
      near(parseRA(formatRA(ra)), ra, 5e-5);
    }
    for (const dec of [-90, -45.123456, -0.5, -0.0001, 0, 0.25, 41.269, 89.9999, 90]) {
      near(parseDec(formatDec(dec, { sexagesimal: true })), Math.abs(dec) < 0.5 / 360000 ? 0 : dec, 0.005 / 3600 + 1e-9);
      near(parseDec(formatDec(dec)), dec, 5e-5);
    }
  });
  it("round trips a pair through both formats", () => {
    const text = `${formatRA(RA, { sexagesimal: true })} ${formatDec(DEC, { sexagesimal: true })}`;
    const r = parseCoordPair(text);
    near(r.ra, RA, 1e-4);
    near(r.dec, DEC, 1e-4);
  });
});
