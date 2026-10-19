import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  filterHiddenCount,
  filterHiddenNoteText,
  initialFilterSelection,
} from "../../static/filter-defaults.mjs";

describe("initialFilterSelection", () => {
  const available = ["L", "R", "G", "B", "Ha", "OIII", "SII"];

  it("ticks every available filter when nothing is saved", () => {
    assert.deepEqual([...initialFilterSelection(undefined, available)], available);
    assert.deepEqual([...initialFilterSelection(null, available)], available);
  });

  it("keeps a saved selection as is", () => {
    assert.deepEqual([...initialFilterSelection(["Ha"], available)], ["Ha"]);
  });

  it("keeps a saved empty selection empty", () => {
    assert.equal(initialFilterSelection([], available).size, 0);
  });
});

describe("filterHiddenCount", () => {
  // Five targets: two narrowband, two broadband, one with neither.
  const targets = [
    { id: 1, f: ["Ha"] },
    { id: 2, f: ["Ha", "OIII"] },
    { id: 3, f: ["L", "R"] },
    { id: 4, f: ["R"] },
    { id: 5, f: [] },
  ];
  const narrow = new Set(["Ha", "SII", "OIII"]);
  const all = new Set(["Ha", "SII", "OIII", "L", "R", "G", "B"]);
  const pass = set => t => t.f.some(x => set.has(x));

  it("counts only targets the reset would bring back", () => {
    assert.equal(filterHiddenCount(targets, pass(narrow), pass(all)), 2);
  });

  it("is zero when the filters already include everything", () => {
    assert.equal(filterHiddenCount(targets, pass(all), pass(all)), 0);
  });

  it("does not count targets hidden for other reasons", () => {
    const notThree = t => t.id !== 3;
    const now = t => notThree(t) && pass(narrow)(t);
    const reset = t => notThree(t) && pass(all)(t);
    assert.equal(filterHiddenCount(targets, now, reset), 1);
  });
});

describe("filterHiddenNoteText", () => {
  it("is empty for zero", () => assert.equal(filterHiddenNoteText(0), ""));
  it("is singular for one", () => assert.equal(filterHiddenNoteText(1), "Filters hide 1 target"));
  it("is plural for many", () => assert.equal(filterHiddenNoteText(3), "Filters hide 3 targets"));
});
