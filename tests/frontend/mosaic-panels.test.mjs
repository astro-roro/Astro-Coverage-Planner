import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  creditedHours, hoursByFilter, mosaicDims, rowPanelProgress, rowPanels, rowProgress, cardHtml,
} from "../../static/filter-rows.mjs";
import { planHoursLeft, planPanelLeft, planProgress } from "../../static/plan-list.mjs";

const plan = () => JSON.parse(readFileSync(new URL("../fixtures/mosaic_2x1_plan.json", import.meta.url), "utf8"));
const lOnly = panels => {
  const p = plan();
  p.filter_rows = p.filter_rows.filter(r => r.id === "r-l");
  if (panels) p.filter_rows[0].panels = panels;
  return p;
};

describe("per-panel hours on a mosaic row", () => {
  it("reads the grid", () => {
    assert.deepEqual(mosaicDims(plan()), { rows: 2, cols: 1 });
    assert.deepEqual(mosaicDims({}), { rows: 1, cols: 1 });
  });

  it("rowPanels is null without a map, and drops bad and out-of-grid keys", () => {
    assert.equal(rowPanels({}), null);
    assert.equal(rowPanels({ panels: [1] }), null);
    const row = { panels: { "1,1": 1, "2,1": 2, "3,1": 9, "x": 5, "1,1,1": 4, "2,2": -1 } };
    assert.deepEqual(rowPanels(row), { "1,1": 1, "2,1": 2, "3,1": 9 });
    assert.deepEqual(rowPanels(row, { rows: 2, cols: 1 }), { "1,1": 1, "2,1": 2 });
  });

  it("a panel counts up to its own goal", () => {
    const row = lOnly({ "1,1": 12, "2,1": 1 }).filter_rows[0];
    assert.equal(creditedHours(row, { rows: 2, cols: 1 }), 9);
  });

  it("hoursByFilter sums the map for actual and credits per panel", () => {
    const t = hoursByFilter(lOnly({ "1,1": 12, "2,1": 1 })).L;
    assert.equal(t.actual_hours, 13);
    assert.equal(t.credited_hours, 9);
    assert.equal(t.target_hours, 16);
  });

  it("without a map credited equals actual, as before", () => {
    const t = hoursByFilter(lOnly(null)).L;
    assert.equal(t.credited_hours, 3);
    assert.equal(t.actual_hours, 3);
  });

  it("hours left and progress use the credited hours", () => {
    const p = lOnly({ "1,1": 12, "2,1": 1 });
    assert.equal(planHoursLeft(p), 7);
    assert.deepEqual(planProgress(p), { done: 9, total: 16 });
  });

  it("a plan without a map is unchanged from the totals change", () => {
    assert.equal(planHoursLeft(lOnly(null)), 13);
    assert.deepEqual(planProgress(lOnly(null)), { done: 3, total: 16 });
  });
});

describe("per-panel hours left", () => {
  it("is null for a plan without panel data and for a single target", () => {
    assert.equal(planPanelLeft(plan()), null);
    const single = lOnly({ "1,1": 2 });
    single.target.mosaic = { rows: 1, cols: 1 };
    assert.equal(planPanelLeft(single), null);
  });

  it("lists each panel's hours left per filter", () => {
    const p = plan();
    p.filter_rows.find(r => r.id === "r-l").panels = { "1,1": 8, "2,1": 1.5 };
    p.filter_rows.find(r => r.id === "r-ha").panels = { "1,1": 2 };
    const left = planPanelLeft(p);
    assert.deepEqual(left.map(x => x.key), ["1,1", "2,1"]);
    assert.deepEqual(left[0].byFilter, { L: 0, Ha: 6 });
    assert.deepEqual(left[1].byFilter, { L: 6.5, Ha: 8 });
    assert.equal(left[0].left, 6);
    assert.equal(left[1].left, 14.5);
  });
});

describe("the goal card's panel breakdown", () => {
  const row = () => lOnly({ "1,1": 8, "2,1": 1.5 }).filter_rows[0];
  const dims = { rows: 2, cols: 1 };

  it("rowPanelProgress has one entry per panel", () => {
    const got = rowPanelProgress(row(), dims);
    assert.deepEqual(got.map(g => [g.key, g.left, g.finished]), [["1,1", 0, true], ["2,1", 6.5, false]]);
  });

  it("the bar measures credited hours against goal x panels", () => {
    const p = rowProgress(row(), dims);
    assert.equal(p.left, "9.5 / 16.0 h");
    assert.equal(p.right, "6.5 h left");
    assert.deepEqual(p.panelLines.map(l => l.text), ["done", "6.5 h left"]);
  });

  it("a panel count alone (no grid) shows no breakdown", () => {
    assert.deepEqual(rowProgress(row(), 2).panelLines, []);
  });

  it("the card renders the panel list only for a tracked mosaic row", () => {
    assert.ok(cardHtml(row(), 0, { panels: dims }).includes("fr-panel-list"));
    assert.ok(!cardHtml(lOnly(null).filter_rows[0], 0, { panels: dims }).includes("fr-panel-list"));
    assert.ok(!cardHtml(row(), 0).includes("fr-panel-list"));
  });

  it("frame rows are counted in frames per panel", () => {
    const star = plan().filter_rows[5];
    star.panels = { "1,1": 15 * 15 / 3600, "2,1": 0 };
    const got = rowPanelProgress(star, dims);
    assert.deepEqual(got.map(g => [g.done, g.left]), [[15, 0], [0, 15]]);
  });
});
