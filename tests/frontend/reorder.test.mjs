// Reordering filter rows by their grab handle (docs/specs/goal-panel-and-ts-order.md
// section 1, tests U6 to U8). The tests run in node with no DOM, so they cover
// the pure helpers and the markup. app.js wires them to the drag events and
// the keydown handler; the focus step (re-find the moved row's handle after
// the redraw) is not driven here.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  HANDLE_LABEL,
  dropLineHtml,
  dropTarget,
  filterListHtml,
  keyMoveTarget,
  moveRow,
} from "../../static/filter-rows.mjs";

const mk = id => ({ id, filter: "L", template: "L", template_sub_s: 180, unit: "h", target_hours: 1, actual_hours: 0 });
const plan = (smart = false) => ({ filter_rows: [mk("a"), mk("b"), mk("c")], smart_exposure_order: smart });
const ids = rows => rows.map(r => r.id);
const count = (s, re) => (s.match(re) || []).length;

describe("the handle", () => {
  it("is a labelled, draggable button holding the number and an aria-hidden grip", () => {
    const html = filterListHtml(plan());
    assert.equal(count(html, /<button type="button" class="fr-handle" draggable="true" aria-label="Drag to reorder\. Arrow keys also move it\."/g), 3);
    assert.equal(HANDLE_LABEL, "Drag to reorder. Arrow keys also move it.");
    assert.equal(count(html, /<svg [^>]*aria-hidden="true"/g), 3);
    assert.deepEqual([...html.matchAll(/class="fr-pos">(\d+)</g)].map(m => m[1]), ["1", "2", "3"]);
  });
  it("is the only draggable thing and no up or down buttons exist", () => {
    const html = filterListHtml(plan());
    assert.equal(count(html, /draggable=/g), 3);
    assert.doesNotMatch(html, /fr-up|fr-down|Move up|Move down/);
  });
});

describe("U6: drag row 3 over row 1", () => {
  it("puts the line above row 1", () => {
    assert.deepEqual(dropTarget(2, 0), { placement: "before", to: 0 });
  });
  it("draws one line with a marker at each end", () => {
    const html = dropLineHtml();
    assert.equal(count(html, /fr-drop-line/g), 1);
    assert.equal(count(html, /class="fr-drop-mark"/g), 2);
    assert.equal(count(html, /class="fr-drop-rule"/g), 1);
  });
  it("orders 3, 1, 2 on drop", () => {
    const rows = plan().filter_rows;
    const t = dropTarget(2, 0);
    assert.deepEqual(ids(moveRow(rows, 2, t.to)), ["c", "a", "b"]);
  });
  it("puts the line below the hovered row when dragging downwards", () => {
    assert.deepEqual(dropTarget(0, 2), { placement: "after", to: 2 });
    assert.deepEqual(ids(moveRow(plan().filter_rows, 0, 2)), ["b", "c", "a"]);
    assert.deepEqual(dropTarget(0, 1), { placement: "after", to: 1 });
    assert.deepEqual(ids(moveRow(plan().filter_rows, 0, 1)), ["b", "a", "c"]);
  });
  it("shows no line over the dragged row itself", () => {
    assert.equal(dropTarget(1, 1), null);
  });
  it("returns a new array and leaves the old one alone", () => {
    const rows = plan().filter_rows;
    const out = moveRow(rows, 2, 0);
    assert.notEqual(out, rows);
    assert.deepEqual(ids(rows), ["a", "b", "c"]);
  });
  it("renumbers at once", () => {
    const moved = moveRow(plan().filter_rows, 2, 0);
    const html = filterListHtml({ filter_rows: moved });
    assert.deepEqual([...html.matchAll(/data-row-id="(\w)"/g)].map(m => m[1]), ["c", "a", "b"]);
    assert.deepEqual([...html.matchAll(/class="fr-pos">(\d+)</g)].map(m => m[1]), ["1", "2", "3"]);
  });
  it("ignores out-of-range moves", () => {
    assert.deepEqual(ids(moveRow(plan().filter_rows, 0, 5)), ["a", "b", "c"]);
    assert.deepEqual(ids(moveRow(plan().filter_rows, -1, 1)), ["a", "b", "c"]);
  });
});

describe("U7: arrow keys on the handle", () => {
  it("ArrowUp on row 2 moves it to row 1", () => {
    const to = keyMoveTarget("ArrowUp", 1, 3);
    assert.equal(to, 0);
    assert.deepEqual(ids(moveRow(plan().filter_rows, 1, to)), ["b", "a", "c"]);
  });
  it("ArrowDown moves one place down", () => {
    assert.equal(keyMoveTarget("ArrowDown", 0, 3), 1);
  });
  it("ArrowUp on row 1 and ArrowDown on the last row do nothing", () => {
    assert.equal(keyMoveTarget("ArrowUp", 0, 3), null);
    assert.equal(keyMoveTarget("ArrowDown", 2, 3), null);
  });
  it("other keys do nothing", () => {
    assert.equal(keyMoveTarget("Enter", 1, 3), null);
    assert.equal(keyMoveTarget("ArrowLeft", 1, 3), null);
  });
});

describe("U8: smart order on", () => {
  it("disables every handle and drops draggable", () => {
    const html = filterListHtml(plan(true));
    assert.equal(count(html, /class="fr-handle" disabled /g), 3);
    assert.doesNotMatch(html, /draggable=/);
  });
  it("re-enables them when smart order goes off", () => {
    const html = filterListHtml(plan(false));
    assert.doesNotMatch(html, /fr-handle" disabled/);
    assert.equal(count(html, /draggable="true"/g), 3);
  });
  it("arrow keys change nothing", () => {
    assert.equal(keyMoveTarget("ArrowUp", 1, 3, true), null);
    assert.equal(keyMoveTarget("ArrowDown", 1, 3, true), null);
  });
  it("keeps the list order and the smart text", () => {
    const html = filterListHtml(plan(true));
    assert.deepEqual([...html.matchAll(/data-row-id="(\w)"/g)].map(m => m[1]), ["a", "b", "c"]);
    assert.match(html, /Target Scheduler picks the order itself/);
  });
});
