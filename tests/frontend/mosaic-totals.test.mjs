import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { hoursByFilter, planPanelCount, rowProgress, cardHtml, filterListHtml } from "../../static/filter-rows.mjs";
import { planHoursLeft, planProgress } from "../../static/plan-list.mjs";

const plan = () => JSON.parse(readFileSync(new URL("../fixtures/mosaic_2x1_plan.json", import.meta.url), "utf8"));

describe("mosaic totals, 2 by 1 fixture", () => {
  it("counts two panels", () => {
    assert.equal(planPanelCount(plan()), 2);
    assert.equal(planPanelCount({ target: { mosaic: { rows: 1, cols: 1 } } }), 1);
    assert.equal(planPanelCount({}), 1);
  });

  it("scales the goal but not the actual", () => {
    const t = hoursByFilter(plan());
    assert.equal(t.L.target_hours, 16);
    assert.equal(t.L.actual_hours, 3);
    assert.equal(t.Ha.target_hours, 16);
  });

  it("leaves a non-mosaic plan alone", () => {
    const p = plan();
    delete p.target.mosaic;
    assert.equal(hoursByFilter(p).L.target_hours, 8);
  });

  it("hours left is goal times panels minus what is shot", () => {
    const p = plan();
    p.filter_rows = p.filter_rows.filter(r => r.id === "r-l");
    assert.equal(planHoursLeft(p), 13);
    assert.deepEqual(planProgress(p), { done: 3, total: 16 });
  });

  it("an empty L row reads 16 h left for two panels, 8 for one", () => {
    const row = plan().filter_rows[0];
    row.actual_hours = 0;
    assert.equal(rowProgress(row, 2).right, "16.0 h left");
    assert.equal(rowProgress(row, 1).right, "8.0 h left");
  });

  it("the card says how the total is made up", () => {
    const row = plan().filter_rows[0];
    assert.match(rowProgress(row, 2).panelText, /^2 panels . 8\.0 h$/);
    assert.equal(rowProgress(row, 1).panelText, "");
    assert.ok(cardHtml(row, 0, { panels: 2 }).includes("fr-panels"));
    assert.ok(!cardHtml(row, 0).includes("fr-panels"));
  });

  it("frame rows scale too", () => {
    const row = plan().filter_rows[5];
    assert.equal(rowProgress(row, 2).left, "0 / 30 fr");
  });

  it("the filter list passes the plan's panel count to its cards", () => {
    assert.ok(filterListHtml(plan(), {}).includes("2 panels"));
  });
});
