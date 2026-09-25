import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { compactChip, trendOf } from "../../static/target-chip.mjs";

// Twelve monthly bins from a list of labels, Jan first. `peaks` optionally
// overrides peak altitude per month.
function bins(labels, peaks = {}) {
  return labels.map((label, i) => ({
    month: i + 1,
    label,
    peak_alt_deg: peaks[i + 1] ?? 60,
    hours_above_min: label === "not_visible" ? 0 : 4.5,
  }));
}

const flat = label => bins(Array(12).fill(label));

describe("compactChip label for this month", () => {
  for (const [label, text] of [
    ["great", "Great"],
    ["good", "Good"],
    ["fair", "Fair"],
    ["partial", "Partial"],
  ]) {
    it(`shows ${text} for a ${label} month`, () => {
      const c = compactChip(flat(label), 9);
      assert.equal(c.label, text);
      assert.equal(c.labelClass, `nn-${label}`);
    });
  }

  it("shows the max altitude when the target rises but never clears the minimum", () => {
    const b = bins(Array(12).fill("not_visible"), { 9: 14.6 });
    const c = compactChip(b, 9);
    assert.equal(c.label, "Max 14.6°");
    assert.equal(c.labelClass, "nn-not_visible");
    assert.match(c.title, /Below min \(peaks 14\.6°\)/);
  });

  it("says below horizon when the target never rises", () => {
    const b = bins(Array(12).fill("not_visible"), { 9: -12 });
    assert.equal(compactChip(b, 9).label, "Below horizon");
  });
});

describe("compactChip trend arrow", () => {
  it("combines the label and a down arrow when the next quarter is worse", () => {
    const b = bins(["great","great","great","great","great","great","great","great","great","partial","partial","partial"]);
    const c = compactChip(b, 9);
    assert.equal(c.text, "Great ↓");
    assert.equal(c.trendKind, "down");
  });

  it("combines the label and an up arrow when the next quarter is better", () => {
    const b = bins(["fair","fair","fair","fair","fair","fair","fair","fair","partial","great","great","great"]);
    const c = compactChip(b, 9);
    assert.equal(c.text, "Partial ↑");
    assert.equal(c.trendKind, "up");
  });

  it("shows a flat arrow for a good target that stays good", () => {
    const c = compactChip(flat("good"), 9);
    assert.equal(c.text, "Good →");
    assert.equal(c.trendKind, "flat");
    assert.match(c.title, /Trend: → Steady/);
  });

  it("counts months to the next good month when a poor target holds steady", () => {
    const labels = Array(12).fill("partial");
    labels[0] = "great"; // January, four months after September
    const c = compactChip(bins(labels), 9);
    assert.equal(c.text, "Partial ↗ 4m");
    assert.equal(c.trendKind, "wait");
    assert.match(c.title, /Peaks in 4m/);
  });

  it("shows a flat arrow when a poor target stays poor all year", () => {
    const c = compactChip(flat("fair"), 9);
    assert.equal(c.text, "Fair →");
    assert.match(c.title, /Stays low/);
  });

  it("wraps the lookahead across the year end", () => {
    const b = bins(["partial","partial","partial","great","great","great","great","great","great","great","great","great"]);
    assert.equal(trendOf(b, 12).kind, "down");
  });
});

describe("compactChip tooltip and accessible label", () => {
  it("carries the full Now and Trend wording", () => {
    const b = bins(["great","great","great","great","great","great","great","great","great","partial","partial","partial"]);
    const c = compactChip(b, 9, 30);
    assert.equal(c.title,
      "Now (Sep): Great, peak 60°, 4.5h above 30°\n" +
      "Trend: ↓ Declining. 3-month forward avg rank is lower than current month.");
    assert.equal(c.ariaLabel, c.title.replace("\n", ". "));
  });
});

describe("compactChip with no visibility data", () => {
  it("returns nothing when time-aware is off and no bins are loaded", () => {
    assert.equal(compactChip(null, 9), null);
    assert.equal(trendOf(null, 9), null);
  });

  it("returns nothing when this month has no bin", () => {
    assert.equal(compactChip([{ month: 1, label: "good", peak_alt_deg: 50, hours_above_min: 3 }], 9), null);
  });
});
