// The switch frequency and smart order controls above the filter list
// (docs/specs/goal-panel-and-ts-order.md sections 1 and 4).
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  ROTATE_DEFAULT,
  clampRotate,
  filterListHtml,
  orderControlsHtml,
  orderExplanation,
  smartOrder,
  switchFrequency,
} from "../../static/filter-rows.mjs";

const plan = (over = {}) => ({ filter_rows: [], ...over });

describe("the line under the controls", () => {
  it("reads the spec's three texts", () => {
    assert.equal(orderExplanation(0, false), "Shoots each filter until its goal is met, top to bottom.");
    assert.equal(orderExplanation(3, false), "Shoots 3 frames of each filter, top to bottom, then repeats.");
    assert.equal(orderExplanation(1, false), "Shoots 1 frame of each filter, top to bottom, then repeats.");
    assert.equal(orderExplanation(3, true),
      "Target Scheduler picks the order itself (moon and altitude). Dragging is off.");
  });
});

describe("unknown values", () => {
  it("show as Finish each and smart order off", () => {
    assert.equal(switchFrequency(plan()), 0);
    assert.equal(smartOrder(plan()), false);
    const html = orderControlsHtml(plan());
    assert.match(html, /value="finish" checked/);
    assert.doesNotMatch(html, /value="rotate" checked/);
    assert.doesNotMatch(html, /fr-smart-cb" checked/);
    assert.match(html, /until its goal is met/);
  });
});

describe("Rotate every N", () => {
  it("checks rotate, enables N and shows the frames line", () => {
    const html = orderControlsHtml(plan({ filter_switch_frequency: 5 }));
    assert.match(html, /value="rotate" checked/);
    assert.match(html, /class="fr-rotate-n"[^>]*value="5"(?![^>]*disabled)/);
    assert.match(html, /Shoots 5 frames/);
  });
  it("disables N under Finish each and keeps the last value", () => {
    const html = orderControlsHtml(plan({ filter_switch_frequency: 0 }), { rotateN: 7 });
    assert.match(html, /class="fr-rotate-n"[^>]*value="7"[^>]*disabled/);
  });
  it("keeps N between 1 and 99", () => {
    assert.equal(clampRotate(0), 1);
    assert.equal(clampRotate(150), 99);
    assert.equal(clampRotate(4.6), 5);
    assert.equal(clampRotate("x"), ROTATE_DEFAULT);
    assert.match(orderControlsHtml(plan()), /min="1" max="99"/);
  });
});

describe("Smart order", () => {
  it("is checked, says TS picks the order, and turns reordering off", () => {
    const p = plan({ smart_exposure_order: true, filter_switch_frequency: 3,
      filter_rows: [{ id: "a", filter: "Ha", template: "Ha", template_sub_s: 300, unit: "fr", target_frames: 4 }] });
    const html = filterListHtml(p);
    assert.match(html, /fr-smart-cb" checked/);
    assert.match(html, /Target Scheduler picks the order itself/);
    assert.match(html, /class="fr-list fr-smart-on" data-reorder="off"/);
    // The list order is kept: the card is still there, first.
    assert.match(html, /data-row-id="a"/);
  });
  it("leaves reordering on when off", () => {
    assert.match(filterListHtml(plan()), /class="fr-list" data-reorder="on"/);
  });
});

describe("the controls sit above the list", () => {
  it("comes before the cards and carries the project note", () => {
    const html = filterListHtml(plan(), { projectNote: "Filter switching and smart order apply to all 2 plans in project M42." });
    assert.ok(html.indexOf("fr-order") < html.indexOf("fr-list"));
    assert.match(html, /apply to all 2 plans in project M42/);
  });
});
