import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  CARD_GRID_COLUMNS,
  SIDE_PANEL_DEFAULT_W,
  cardHtml,
  filterListHtml,
  hoursByFilter,
  newFilterRow,
  orderFilters,
  rowDiffLabel,
  rowProgress,
  setRowGoal,
  setRowTemplate,
  setRowUnit,
  templateOptions,
} from "../../static/filter-rows.mjs";

const css = readFileSync(new URL("../../static/style.css", import.meta.url), "utf8");
const appSrc = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");

// Gear version 3: each camera filter holds its templates, one starred.
const T = (id, name, sub_s, starred = false) => ({ id, name, sub_s, starred });
const CAMERA = {
  id: "cam",
  name: "ZWO ASI6200MM Pro",
  filters: {
    L: { templates: [T("t-l", "L (ZWO ASI6200MM Pro)", 120, true)] },
    R: { templates: [T("t-r", "R", 180, true), T("t-rs", "R Stars", 15)] },
    G: { templates: [T("t-g", "G", 180, true)] },
    B: { templates: [T("t-b", "B", 180, true)] },
    Ha: { templates: [T("t-ha", "Ha", 300, true)] },
  },
};
const TEMPLATES = [];

function row(over = {}) {
  return {
    id: "r1", filter: "R", template: "R", template_sub_s: 180,
    unit: "h", target_hours: 2, actual_hours: 0, ...over,
  };
}

describe("U1: a new plan", () => {
  it("shows the note and the add button with no cards", () => {
    const html = filterListHtml({ filter_rows: [] }, { camera: CAMERA, templates: TEMPLATES });
    assert.match(html, /class="fr-empty"/);
    assert.match(html, /\+ Add filter/);
    assert.ok(!html.includes("fr-card"));
  });
  it("keeps the add button under the last card when there are rows", () => {
    const html = filterListHtml({ filter_rows: [row()] }, { camera: CAMERA, templates: TEMPLATES });
    assert.ok(!html.includes("fr-empty"));
    assert.ok(html.indexOf("fr-card") < html.indexOf("fr-add"));
  });
  it("starts the side panel at 420 px", () => {
    assert.equal(SIDE_PANEL_DEFAULT_W, 420);
    assert.match(css, /--side-panel-w:\s*420px/);
    assert.match(appSrc, /width: SIDE_PANEL_DEFAULT_W/);
  });
  it("starts a new plan with no rows", () => {
    assert.match(appSrc, /filter_rows: \[\],/);
  });
});

describe("U2: + Add filter", () => {
  it("picks the first unused filter and its usual template, goal 0", () => {
    const rows = [];
    rows.push(newFilterRow({ rows, camera: CAMERA, id: "a" }));
    rows.push(newFilterRow({ rows, camera: CAMERA, id: "b" }));
    assert.equal(rows[0].filter, "L");
    assert.equal(rows[0].template, "L (ZWO ASI6200MM Pro)");
    assert.equal(rows[0].template_id, "t-l");
    assert.equal(rows[0].template_sub_s, 120);
    assert.equal(rows[1].filter, "R");
    assert.equal(rows[1].template, "R");
    assert.equal(rows[1].template_sub_s, 180);
    for (const r of rows) {
      assert.equal(r.unit, "h");
      assert.equal(r.target_hours, 0);
      assert.equal(rowProgress(r).right, "set a goal");
    }
  });
  it("starts on the filter's starred template, not the first", () => {
    const cam = { name: "Cam", filters: { R: { templates: [T("a", "R Stars", 15), T("b", "R", 180, true)] } } };
    const r = newFilterRow({ rows: [], camera: cam, id: "x" });
    assert.equal(r.template, "R");
    assert.equal(r.template_id, "b");
    assert.equal(r.template_sub_s, 180);
  });
  it("falls back to the first filter once all are used", () => {
    const used = Object.keys(CAMERA.filters).map(filter => ({ filter }));
    const r = newFilterRow({ rows: used, camera: CAMERA, id: "x" });
    assert.equal(r.filter, "L");
  });
  it("with no gear template names one after the camera, sub 300", () => {
    const cam = { name: "Cam", filters: { Ha: { templates: [] } } };
    const r = newFilterRow({ rows: [], camera: cam, id: "x" });
    assert.equal(r.template, "Ha (Cam)");
    assert.equal(r.template_sub_s, 300);
    assert.ok(!("template_id" in r));
  });
  it("starts a frame row when the starred template's sub is under 60 s", () => {
    const cam = { name: "Cam", filters: { R: { templates: [T("a", "R Stars", 15, true)] } } };
    const r = newFilterRow({ rows: [], camera: cam, id: "x" });
    assert.equal(r.unit, "fr");
    assert.equal(r.target_frames, 0);
    assert.ok(!("target_hours" in r));
  });
});

describe("U3: picking a template", () => {
  it("sets a sub under 60 s to frames and labels the option", () => {
    const r = row({ target_hours: 0 });
    setRowTemplate(r, "R Stars", 15);
    assert.equal(r.unit, "fr");
    assert.equal(r.template_sub_s, 15);
    setRowTemplate(r, "R Stars", 15, "t-rs");
    assert.equal(r.template_id, "t-rs");
    const opts = templateOptions(r, CAMERA);
    assert.equal(opts.find(o => o.selected).label, "R Stars \u00b7 15 s");
    assert.equal(opts[0].label, "R \u00b7 180 s \u2605");
  });
  it("sets 60 s or more to hours", () => {
    const r = row({ unit: "fr", template: "R Stars", template_sub_s: 15, target_frames: 0 });
    delete r.target_hours;
    setRowTemplate(r, "R", 180);
    assert.equal(r.unit, "h");
    assert.equal(r.target_hours, 0);
    assert.ok(!("target_frames" in r));
  });
  it("keeps the hours a goal stood for when the unit changes", () => {
    const r = row({ target_hours: 1 });
    setRowTemplate(r, "R Stars", 15);
    assert.equal(r.target_frames, 240);
  });
  it("keeps the number as typed when the unit stays", () => {
    const r = row({ target_hours: 3 });
    setRowTemplate(r, "R Long", 300);
    assert.equal(r.target_hours, 3);
  });
});

describe("U4: unit conversion", () => {
  it("goes 2.0 h to 40 fr and back", () => {
    const r = row();
    setRowUnit(r, "fr");
    assert.equal(r.target_frames, 40);
    assert.ok(!("target_hours" in r));
    setRowUnit(r, "h");
    assert.equal(r.target_hours, 2);
    assert.ok(!("target_frames" in r));
    assert.equal(rowProgress(r).left, "0.0 / 2.0 h");
  });
  it("rounds hours to 4 places", () => {
    const r = row({ unit: "fr", template_sub_s: 15, target_frames: 20 });
    delete r.target_hours;
    setRowUnit(r, "h");
    assert.equal(r.target_hours, 0.0833);
  });
  it("sets whole frames from the goal field", () => {
    const r = row({ unit: "fr", target_frames: 1 });
    setRowGoal(r, 2.6);
    assert.equal(r.target_frames, 3);
  });
});

describe("U5: removing a row", () => {
  it("renumbers the cards", () => {
    const rows = ["a", "b", "c"].map((id, i) => row({ id, filter: ["L", "R", "G"][i] }));
    const left = rows.slice(1);
    const html = filterListHtml({ filter_rows: left }, { camera: CAMERA, templates: TEMPLATES });
    const nums = [...html.matchAll(/class="fr-pos">(\d+)</g)].map(m => m[1]);
    assert.deepEqual(nums, ["1", "2"]);
    assert.equal(left.length, 2);
  });
});

describe("U9: the grid at 420 px and 300 px", () => {
  const long = row({ template: "B Stars (ZWO ASI6200MM Pro)", filter: "B" });
  it("uses the four aligned columns", () => {
    assert.equal(CARD_GRID_COLUMNS, "22px 104px minmax(0, 1fr) 26px");
    assert.ok(css.includes(`grid-template-columns: ${CARD_GRID_COLUMNS}`));
  });
  it("lets the template column shrink and truncate with an ellipsis", () => {
    assert.match(css, /\.fr-card > \* \{ min-width: 0; \}/);
    const tmpl = css.match(/\.fr-tmpl-sel \{[^}]*\}/)[0];
    assert.match(tmpl, /text-overflow: ellipsis/);
    assert.match(tmpl, /overflow: hidden/);
    assert.match(tmpl, /width: 100%/);
  });
  it("never scrolls the list sideways", () => {
    assert.match(css.match(/\.fr-list \{[^}]*\}/)[0], /overflow-x: hidden/);
  });
  it("keeps the progress block out of the remove button's column", () => {
    assert.match(css, /\.fr-progress \{ grid-column: 3; grid-row: 2; \}/);
    assert.match(css, /\.fr-remove \{ grid-column: 4; grid-row: 1;/);
    assert.match(css, /\.fr-handle \{ grid-column: 1; grid-row: 1 \/ span 2;/);
  });
  it("carries the full name on the select and the long template in its option", () => {
    const html = cardHtml(long, 0, { camera: CAMERA, templates: [] });
    assert.match(html, /title="B Stars \(ZWO ASI6200MM Pro\) \u00b7 180 s"/);
  });
});

describe("U10: progress text", () => {
  it("shows hours done, goal and left", () => {
    const p = rowProgress(row({ target_hours: 4.2, actual_hours: 3.0 }));
    assert.equal(p.left, "3.0 / 4.2 h");
    assert.equal(p.right, "1.2 h left");
    assert.equal(p.finished, false);
  });
  it("shows frames done, goal and left", () => {
    const p = rowProgress(row({ unit: "fr", template: "R Stars", template_sub_s: 15, target_frames: 20, actual_hours: 18 * 15 / 3600 }));
    assert.equal(p.left, "18 / 20 fr");
    assert.equal(p.right, "2 fr left");
  });
  it("says done when the goal is met", () => {
    const p = rowProgress(row({ target_hours: 2, actual_hours: 2.5 }));
    assert.equal(p.right, "done");
    assert.equal(p.finished, true);
  });
});

describe("U11: AAC figures", () => {
  const base = { target_hours: 4.2, actual_hours: 4.2, aac: { accepted_seconds: 10800, complete: false } };
  it("uses accepted seconds and ignores actual_hours until complete", () => {
    const r = row(base);
    const p = rowProgress(r);
    assert.equal(p.left, "3.0 / 4.2 h");
    assert.equal(p.right, "1.2 h left");
    assert.ok(!cardHtml(r, 0, { camera: CAMERA, templates: TEMPLATES }).includes("fr-done"));
  });
  it("shows done and dims the card when complete", () => {
    const r = row({ ...base, aac: { ...base.aac, complete: true } });
    assert.equal(rowProgress(r).right, "done");
    assert.match(cardHtml(r, 0, { camera: CAMERA, templates: TEMPLATES }), /class="fr-card fr-done"/);
  });
  it("counts accepted frames in a frame row", () => {
    const r = row({ unit: "fr", template_sub_s: 15, target_frames: 20, aac: { accepted_frames: 12, complete: false } });
    assert.equal(rowProgress(r).left, "12 / 20 fr");
  });
});

describe("U12: no template list, no stray comma", () => {
  it("offers the current template and New template…", () => {
    const opts = templateOptions(row({ template: "Ha (Cam)", template_sub_s: 300 }), { filters: {} });
    assert.deepEqual(opts.map(o => o.label), ["Ha (Cam) \u00b7 300 s", "New template\u2026"]);
    assert.equal(opts[0].selected, true);
  });
  it("renders no cell that is exactly a comma", () => {
    const html = filterListHtml({ filter_rows: [row()] }, { camera: CAMERA, templates: [] });
    assert.ok(!/>\s*,\s*</.test(html));
  });
});

describe("totals and labels", () => {
  it("sums rows per filter, converting frame rows to hours", () => {
    const plan = { filter_rows: [
      row({ target_hours: 2, actual_hours: 1 }),
      row({ id: "r2", template: "R Stars", template_sub_s: 15, unit: "fr", target_frames: 240, actual_hours: 0.5 }),
      row({ id: "r3", filter: "Ha", target_hours: 1, actual_hours: 0 }),
    ] };
    delete plan.filter_rows[1].target_hours;
    const t = hoursByFilter(plan);
    assert.equal(t.R.target_hours, 3);
    assert.equal(t.R.actual_hours, 1.5);
    assert.equal(t.Ha.target_hours, 1);
    assert.deepEqual(hoursByFilter({}), {});
  });
  it("orders filters L R G B Ha OIII SII then the rest by name", () => {
    assert.deepEqual(orderFilters(["SII", "Z", "Ha", "A", "L", "B"]), ["L", "B", "Ha", "SII", "A", "Z"]);
  });
  it("labels sync diff paths from the row", () => {
    const plan = { filter_rows: [row({ id: "abc", template: "R Stars" })] };
    assert.equal(rowDiffLabel("filter_rows.abc.target_frames", plan), "R Stars target frames");
    assert.equal(rowDiffLabel("filter_rows.zzz.target_hours", plan), "target hours");
    assert.equal(rowDiffLabel("filter_rows.abc", plan), "R Stars row");
  });
  it("leaves filter_goals in app.js only to strip it and as a response fallback", () => {
    const uses = appSrc.split("\n").filter(l => l.includes("filter_goals") && !l.trim().startsWith("//"));
    assert.equal(uses.length, 3, uses.join("\n"));
  });
});
