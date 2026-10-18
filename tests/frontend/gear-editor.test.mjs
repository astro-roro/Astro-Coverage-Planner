// Phase 2 of docs/specs/gear-templates.md: the gear editor (section 6) and the
// plan editor's template drop-down and flags (section 7). Test ids (U1, P1 and
// so on) are the spec's. The fixture is what GET /api/gear serves for the
// migrated gear with the IMAGING-PC wheel (L R G B H O S) and a plan set that
// uses the templates as often as the live data does; regenerate it with
// tests/fixtures/make_gear_editor_fixture.py.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  CONFLICT_MESSAGE,
  PLUGIN_NOTE,
  addFilter,
  applySaveFailure,
  gearFromResponse,
  refreshGearBadge,
  reloadDraft,
  addTemplate,
  buildSavePayload,
  canonFilterName,
  conflictTooltip,
  duplicateTemplate,
  fieldOfView,
  filterOrder,
  fmtGO,
  gearBadgeHtml,
  gearEditorHtml,
  parseGO,
  removeFilter,
  removeTemplate,
  resolveToWheel,
  setTemplateField,
  starTemplate,
  subImpact,
  templateNote,
  templateStatus,
} from "../../static/gear-editor.mjs";
import {
  SIDE_PANEL_DEFAULT_W,
  cardHtml,
  newFilterRow,
  setRowTemplate,
  templateOptions,
} from "../../static/filter-rows.mjs";

const read = rel => readFileSync(new URL(rel, import.meta.url), "utf8");
const css = read("../../static/style.css");
const appSrc = read("../../static/app.js");
const fixture = JSON.parse(read("../fixtures/gear-editor-api.json"));
const P1 = "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7";
const CAM = "zwo-asi6200mm-pro";

const clone = o => JSON.parse(JSON.stringify(o));

// Just enough of a button for refreshGearBadge: it holds the badges inserted.
function fakeButton() {
  const btn = {
    badges: [], title: "",
    querySelector: () => (btn.badges.length ? { remove: () => { btn.badges.length = 0; } } : null),
    insertAdjacentHTML: (_where, html) => { if (html) btn.badges.push(html); },
  };
  return btn;
}

function editorState(over = {}) {
  const gear = clone(fixture.gear);
  const doc = { telescopes: gear.telescopes, cameras: gear.cameras };
  return {
    tab: "cameras", doc, saved: clone(doc), plans: clone(fixture.plans), wheelUnlinked: [],
    openCamera: CAM, openTemplate: null, openTelescope: null, menu: null, addFilterFor: null, message: null,
    colourOf: () => "#6b7a90", ...over,
  };
}
const camOf = st => st.doc.cameras.find(c => c.id === CAM);
const tplNamed = (cam, filter, name) => cam.filters[filter].templates.find(t => t.name === name);

// The filter boxes in the html, in order, each with its template lines.
function boxes(html) {
  return html.split('<div class="ge-box"').slice(1).map(chunk => ({
    name: chunk.match(/data-filter-box="([^"]*)"/)[1],
    lines: (chunk.match(/class="ge-line"/g) || []).length,
    note: (chunk.match(/class="ge-wnote">([^<]*)</) || [])[1],
    html: chunk,
  }));
}

describe("U1: opening the editor on the fixture", () => {
  const st = editorState();
  const html = gearEditorHtml(st);
  it("starts the panel at 420 px", () => {
    assert.equal(SIDE_PANEL_DEFAULT_W, 420);
    assert.match(css, /--side-panel-w:\s*420px/);
  });
  it("reads Telescopes 8 and Cameras 8 on the tabs", () => {
    assert.match(html, /Telescopes \u00b7 8</);
    assert.match(html, /Cameras \u00b7 8</);
  });
  it("shows the 6200's boxes in slot order with their template lines", () => {
    const b = boxes(html);
    assert.deepEqual(b.map(x => x.name), ["L", "R", "G", "B", "Ha", "OIII", "SII"]);
    assert.deepEqual(b.map(x => x.lines), [1, 2, 2, 2, 1, 1, 1]);
  });
  it("names the wheel slot in each box", () => {
    assert.deepEqual(boxes(html).map(x => x.note), [
      "wheel slot L", "wheel slot R", "wheel slot G", "wheel slot B", "wheel slot H", "wheel slot O", "wheel slot S",
    ]);
  });
  it("shows the other cameras as one-line summaries", () => {
    assert.match(html, /class="ge-summary"[^>]*data-cam="qhy268m"/);
    assert.match(html, /7 filters \u00b7 7 templates/);
    assert.match(html, /1 filter \u00b7 1 template</);
  });
});

describe("U2: opening R Stars", () => {
  const st = editorState();
  const rs = tplNamed(camOf(st), "R", "R Stars");
  st.openTemplate = rs.id;
  const html = gearEditorHtml(st);
  it("shows its fields", () => {
    assert.match(html, /data-field="name" value="R Stars"/);
    assert.match(html, /data-field="sub_s" value="15"/);
    assert.match(html, /data-field="offset" value="25"/);
    assert.match(html, /data-field="gain" value="Default"/);
  });
  it("disables Remove and says why", () => {
    assert.match(html, /data-act="del-tpl"[^>]*disabled/);
    assert.ok(html.includes("Used by 23 plans, so it cannot be removed."));
  });
  it("leaves the unused one removable", () => {
    const st2 = editorState();
    const t = addTemplate(camOf(st2), "R", { name: "R Long", sub_s: 600 });
    st2.openTemplate = t.id;
    assert.doesNotMatch(gearEditorHtml(st2), /data-act="del-tpl"[^>]*disabled/);
    assert.equal(removeTemplate(camOf(st2), "R", t.id), true);
  });
});

describe("U3: the star", () => {
  it("moves to the ticked template, and its checkbox is disabled", () => {
    const st = editorState();
    const cam = camOf(st);
    const ha600 = addTemplate(cam, "Ha", { name: "Ha 600", sub_s: 600 });
    assert.equal(ha600.starred, false);
    assert.equal(starTemplate(cam, "Ha", ha600.id), true);
    assert.equal(tplNamed(cam, "Ha", "Ha").starred, false);
    assert.equal(ha600.starred, true);
    st.openTemplate = ha600.id;
    const html = gearEditorHtml(st);
    assert.match(html, /data-act="star" data-tpl="[^"]*" checked disabled/);
  });
  it("keeps exactly one star when the starred template is removed", () => {
    const st = editorState();
    const cam = camOf(st);
    const stars = tplNamed(cam, "R", "R Stars");
    stars.used_by = 0;
    const r = tplNamed(cam, "R", "R (ZWO ASI6200MM Pro)");
    r.used_by = 0;
    assert.equal(removeTemplate(cam, "R", r.id), true);
    assert.deepEqual(cam.filters.R.templates.map(t => t.starred), [true]);
  });
  it("never removes a filter's last template", () => {
    const st = editorState();
    const cam = camOf(st);
    const only = cam.filters.SII.templates[0];
    only.used_by = 0;
    assert.equal(removeTemplate(cam, "SII", only.id), false);
  });
});

describe("U4: + Template", () => {
  it("adds a line with status New and no star", () => {
    const st = editorState();
    const cam = camOf(st);
    const t = addTemplate(cam, "Ha", { name: "Ha 600", sub_s: 600 });
    const html = gearEditorHtml(st);
    const ha = boxes(html).find(b => b.name === "Ha").html;
    const line = ha.split('class="ge-line"').find(c => c.includes(`data-line-name="${t.id}"`));
    assert.match(line, /ge-pill-new[^>]*>New</);
    assert.match(line, /class="ge-star"><\/span>/);
    assert.match(line, /Ha 600/);
    assert.match(line, /600 s/);
  });
  it("makes a unique name when the typed one is taken", () => {
    const cam = camOf(editorState());
    const t = addTemplate(cam, "Ha", { name: "ha" });
    assert.equal(t.name.toLowerCase(), "ha 2");
  });
  it("duplicates a template as a new, unstarred one", () => {
    const cam = camOf(editorState());
    const src = tplNamed(cam, "R", "R Stars");
    const copy = duplicateTemplate(cam, "R", src.id);
    assert.equal(copy.name, "R Stars copy");
    assert.equal(copy.sub_s, 15);
    assert.equal(copy.offset, 25);
    assert.equal(copy.starred, false);
    assert.deepEqual(copy.ts, {});
  });
});

describe("U5: a filter that is not on the wheel", () => {
  const st = editorState();
  const cam = camOf(st);
  cam.filters.V = { templates: [{ id: "v-1", name: "V", sub_s: 120, gain: -1, offset: -1, bin: 1, starred: true,
    status: "waiting", conflicts: [], used_by: 0, note: "V is not on the wheel of IMAGING-PC yet." }] };
  cam.wheel.filters.V = { state: "not_on_wheel", slots: {} };
  const html = gearEditorHtml(st);
  const v = boxes(html).find(b => b.name === "V");
  it("carries the pill in the box", () => {
    assert.match(v.html, /Not on the wheel yet/);
  });
  it("shows its templates as Waiting", () => {
    assert.match(v.html, /ge-pill-waiting[^>]*>Waiting</);
  });
  it("says the sync skips it", () => {
    st.openTemplate = "v-1";
    assert.match(gearEditorHtml(st), /V is not on the wheel of IMAGING-PC yet\. The sync skips it and says so in its notes\./);
  });
  it("works the state out for a filter added in the draft", () => {
    const st2 = editorState();
    addFilter(camOf(st2), "V");
    assert.match(boxes(gearEditorHtml(st2)).find(b => b.name === "V").html, /Not on the wheel yet/);
  });
});

describe("U6: the 409 answer", () => {
  it("shows the message and offers Reload, keeping the draft", () => {
    const st = editorState();
    camOf(st).name = "Edited name";
    st.message = { text: CONFLICT_MESSAGE, reload: true };
    const html = gearEditorHtml(st);
    assert.ok(html.includes("Gear changed elsewhere. Reload to see it."));
    assert.match(html, /data-act="reload"/);
    assert.match(html, /value="Edited name"/);
  });
  it("keeps the draft when the save comes back 409, and says so", () => {
    const st = editorState();
    camOf(st).name = "Edited name";
    applySaveFailure(st, { ok: false, status: 409, error: "stale" });
    assert.deepEqual(st.message, { text: CONFLICT_MESSAGE, reload: true });
    assert.equal(camOf(st).name, "Edited name");
    assert.notDeepEqual(st.doc, st.saved);
    assert.match(gearEditorHtml(st), /value="Edited name"/);
  });
  it("words any other failure plainly, with no Reload", () => {
    const st = editorState();
    applySaveFailure(st, { ok: false, status: 500, error: "boom" });
    assert.deepEqual(st.message, { text: "Save failed: boom" });
    applySaveFailure(st, { ok: false, status: 500, error: "boom" }, true);
    assert.equal(st.message.text, "Couldn't save current edits before scanning: boom");
  });
  it("Reload asks first, keeps the draft on a no, and replaces it on a yes", async () => {
    const st = editorState();
    camOf(st).name = "Edited name";
    st.message = { text: CONFLICT_MESSAGE, reload: true };
    let fetched = 0, asked = null;
    const server = { telescopes: clone(fixture.gear.telescopes), cameras: clone(fixture.gear.cameras), wheel_unlinked: [] };
    const fetchGear = async () => { fetched++; return server; };
    assert.equal(await reloadDraft(st, q => { asked = q; return false; }, fetchGear), false);
    assert.match(asked, /unsaved edits will be lost/);
    assert.equal(fetched, 0);
    assert.equal(camOf(st).name, "Edited name");
    assert.ok(st.message);
    assert.equal(await reloadDraft(st, () => true, fetchGear), true);
    assert.equal(fetched, 1);
    assert.equal(camOf(st).name, fixture.gear.cameras.find(c => c.id === CAM).name);
    assert.deepEqual(st.doc, st.saved);
    assert.equal(st.message, null);
  });
});

describe("U7: adding a filter by name", () => {
  it("stores H as Ha, with one starred 300 s template", () => {
    const cam = { id: "c", name: "C", filters: {} };
    const r = addFilter(cam, "H");
    assert.deepEqual(r, { ok: true, name: "Ha" });
    assert.deepEqual(Object.keys(cam.filters), ["Ha"]);
    const [t] = cam.filters.Ha.templates;
    assert.equal(t.name, "Ha");
    assert.equal(t.sub_s, 300);
    assert.equal(t.starred, true);
  });
  it("gives broad filters 120 s and keeps names the table does not know", () => {
    const cam = { id: "c", name: "C", filters: {} };
    addFilter(cam, "Red");
    addFilter(cam, "Antlia Ha");
    assert.equal(cam.filters.R.templates[0].sub_s, 120);
    assert.equal(cam.filters["Antlia Ha"].templates[0].sub_s, 120);
    assert.equal(canonFilterName("Antlia Ha"), "Antlia Ha");
  });
  it("refuses a repeat and a bad name", () => {
    const cam = { id: "c", name: "C", filters: {} };
    addFilter(cam, "Ha");
    assert.equal(addFilter(cam, "ha").ok, false);
    assert.equal(addFilter(cam, "").ok, false);
    assert.equal(addFilter(cam, "x/y").ok, false);
  });
});

describe("U8: gain and offset", () => {
  it("takes Default as -1 and shows -1 as Default", () => {
    const t = { gain: 100, offset: 25 };
    assert.equal(setTemplateField(t, "gain", "Default"), true);
    assert.equal(t.gain, -1);
    assert.equal(fmtGO(t.gain), "Default");
    assert.equal(setTemplateField(t, "offset", "default"), true);
    assert.equal(t.offset, -1);
  });
  it("takes a whole number and refuses anything else", () => {
    const t = { gain: -1, offset: -1 };
    assert.equal(setTemplateField(t, "gain", "120"), true);
    assert.equal(t.gain, 120);
    assert.equal(setTemplateField(t, "gain", "12.5"), false);
    assert.equal(setTemplateField(t, "gain", "abc"), false);
    assert.equal(setTemplateField(t, "offset", "-5"), false);
    assert.equal(t.gain, 120);
    assert.equal(parseGO(" Default "), -1);
  });
  it("checks sub and bin ranges", () => {
    const t = { sub_s: 300, bin: 1 };
    assert.equal(setTemplateField(t, "sub_s", "0"), false);
    assert.equal(setTemplateField(t, "sub_s", "3601"), false);
    assert.equal(setTemplateField(t, "sub_s", "90"), true);
    assert.equal(setTemplateField(t, "bin", "5"), false);
    assert.equal(setTemplateField(t, "bin", "2"), true);
  });
});

describe("U9: the template line at 420 px and 300 px", () => {
  const long = "x".repeat(60);
  const st = editorState();
  addTemplate(camOf(st), "Ha", { name: long, sub_s: 300 });
  const html = gearEditorHtml(st);
  it("keeps four aligned columns, the second able to shrink", () => {
    assert.match(css, /\.ge-line \{[^}]*grid-template-columns: 12px minmax\(0, 1fr\) auto auto/);
  });
  it("truncates the name with an ellipsis", () => {
    const rule = css.match(/\.ge-tname \{[^}]*\}/)[0];
    assert.match(rule, /overflow: hidden/);
    assert.match(rule, /text-overflow: ellipsis/);
    assert.match(rule, /white-space: nowrap/);
    assert.ok(html.includes(long));
  });
  it("never scrolls the panel sideways", () => {
    assert.match(css.match(/\.ge-body \{[^}]*\}/)[0], /overflow-x: hidden/);
    assert.match(css, /\.ge-grid4 \{[^}]*repeat\(4, minmax\(0, 1fr\)\)/);
  });
});

describe("U10: a held conflict", () => {
  const st = editorState();
  const rs = tplNamed(camOf(st), "R", "R Stars");
  rs.status = "conflict";
  rs.conflicts = [{ profile_id: P1, field: "offset", acp: 25, ts: 10 }];
  st.openTemplate = rs.id;
  const html = gearEditorHtml(st);
  it("reads Conflict on the pill", () => {
    assert.match(html, /ge-pill-conflict[^>]*>Conflict</);
  });
  it("shows both values in the note", () => {
    assert.ok(html.includes("Offset is 25 here and 10 in TS, and TS changed it after the last sync. The sync leaves it alone until you choose."));
  });
  it("offers Keep ACP's and Take TS's for the resolve route", () => {
    assert.match(html, new RegExp(`data-act="resolve" data-tpl="${rs.id}" data-profile="${P1}" data-field="offset" data-take="acp">Keep ACP&#39;s \\(25\\)<|data-take="acp">Keep ACP's \\(25\\)<`));
    assert.match(html, /data-take="ts">Take TS's \(10\)</);
    assert.match(appSrc, /\/api\/gear\/templates\/\$\{encodeURIComponent\(el\.dataset\.tpl\)\}\/resolve/);
    assert.match(appSrc, /take: el\.dataset\.take/);
  });
});

describe("U11: editing a sub", () => {
  const st = editorState();
  const cam = camOf(st);
  const ha = tplNamed(cam, "Ha", "Ha");
  const haRows = [];
  for (const p of st.plans) for (const r of p.filter_rows) if (r.template_id === ha.id) haRows.push([p, r]);
  [24, 8, 30].forEach((frames, i) => { haRows[i][1].actual_hours = frames * 300 / 3600; });
  haRows[3][1].template_sub_s = 180;
  const impact = subImpact(st.plans, cam, ha, 300, 600);
  it("lists the three rows with their frames at the old sub", () => {
    assert.match(impact.lines[0], /^3 rows have frames at 300 s: /);
    assert.ok(impact.lines[0].includes("plan-0 Ha 24 fr"));
    assert.ok(impact.lines[0].includes("plan-1 Ha 8 fr"));
    assert.ok(impact.lines[0].includes("plan-2 Ha 30 fr"));
    assert.ok(impact.lines[0].endsWith("Their goals stay in hours, so their frame goals change."));
  });
  it("names the deviating row as keeping its own sub", () => {
    assert.ok(impact.lines.includes("plan-3 Ha keeps its own 180 s."));
    assert.equal(impact.deviating, 1);
  });
  it("counts the rows that follow without frames", () => {
    assert.ok(impact.lines.includes("19 more rows will move to 600 s."));
  });
  it("shows in the open editor once the draft sub differs from the saved one", () => {
    ha.sub_s = 600;
    st.openTemplate = ha.id;
    const html = gearEditorHtml(st);
    assert.ok(html.includes("3 rows have frames at 300 s: "));
    assert.ok(html.includes("plan-3 Ha keeps its own 180 s."));
  });
  it("says nothing when the sub is unchanged", () => {
    assert.deepEqual(subImpact(st.plans, cam, ha, 300, 300).lines, []);
  });
});

describe("U12 and U14: a camera whose plugin cannot update templates", () => {
  it("shows the note and disables name and sub on saved templates", () => {
    const st = editorState();
    camOf(st).template_edit_blocked = true;
    st.openTemplate = tplNamed(camOf(st), "R", "R Stars").id;
    const html = gearEditorHtml(st);
    assert.ok(html.includes(PLUGIN_NOTE));
    assert.match(html, /data-field="name" value="R Stars" maxlength="64" disabled/);
    assert.match(html, /data-field="sub_s" value="15"[^>]*disabled/);
    assert.doesNotMatch(html, /data-field="offset"[^>]*disabled/);
  });
  it("leaves a template that is not saved yet editable", () => {
    const st = editorState();
    camOf(st).template_edit_blocked = true;
    const t = addTemplate(camOf(st), "R", { name: "R Long", sub_s: 600 });
    st.openTemplate = t.id;
    assert.doesNotMatch(gearEditorHtml(st), new RegExp(`data-tpl="${t.id}" data-field="name"[^>]*disabled`));
  });
  it("drops the note and enables the fields once the server says the camera is free", () => {
    const st = editorState();
    camOf(st).template_edit_blocked = false;
    st.openTemplate = tplNamed(camOf(st), "R", "R Stars").id;
    const html = gearEditorHtml(st);
    assert.ok(!html.includes(PLUGIN_NOTE));
    assert.doesNotMatch(html, /data-field="sub_s"[^>]*disabled/);
  });
});

describe("U13: where added filters sit", () => {
  it("puts slot filters first, then the rest alphabetically", () => {
    const st = editorState();
    addFilter(camOf(st), "V");
    addFilter(camOf(st), "Antlia Ha");
    assert.deepEqual(boxes(gearEditorHtml(st)).map(b => b.name),
      ["L", "R", "G", "B", "Ha", "OIII", "SII", "Antlia Ha", "V"]);
  });
  it("uses L R G B Ha OIII SII, then the rest, when no slots are known", () => {
    const cam = { filters: { Zeta: {}, SII: {}, Ha: {}, L: {}, Alpha: {}, OIII: {} } };
    assert.deepEqual(filterOrder(cam), ["L", "Ha", "OIII", "SII", "Alpha", "Zeta"]);
  });
});

describe("U15: the badge on the Gear button", () => {
  it("shows the count and the tooltip, then nothing at 0", () => {
    const badge = gearBadgeHtml(2);
    assert.match(badge, /class="gear-badge"[^>]*>2</);
    assert.equal(conflictTooltip(2), "2 template settings differ between ACP and TS. Open Gear to choose.");
    assert.ok(badge.includes(conflictTooltip(2)));
    assert.equal(gearBadgeHtml(0), "");
  });
  it("is redrawn from each answer: a sync that adds conflicts, then one that clears them", () => {
    const btn = fakeButton();
    refreshGearBadge(btn, gearFromResponse({ conflict_count: 3 }).conflict_count);
    assert.deepEqual(btn.badges, [gearBadgeHtml(3)]);
    assert.equal(btn.title, conflictTooltip(3));
    refreshGearBadge(btn, gearFromResponse({ conflict_count: 1 }).conflict_count);
    assert.deepEqual(btn.badges, [gearBadgeHtml(1)]);
    refreshGearBadge(btn, gearFromResponse({}).conflict_count);
    assert.deepEqual(btn.badges, []);
    assert.equal(btn.title, "");
    refreshGearBadge(null, 2);
  });
  it("reads the count from the gear answer", () => {
    assert.equal(gearFromResponse({ conflict_count: 4, rev: 7 }).conflict_count, 4);
    assert.equal(gearFromResponse({ conflict_count: 4, rev: 7 }).rev, 7);
    assert.equal(gearFromResponse({}).conflict_count, 0);
  });
  it("is refreshed after a sync", () => {
    // The sync calls loadGear, which ends in updateGearBadge; that call is in app.js.
    assert.match(appSrc, /if \(typeof loadGear === "function"\) await loadGear\(\);/);
  });
  it("is red", () => {
    assert.match(css.match(/\.gear-badge \{[^}]*\}/)[0], /background: #d64545/);
  });
});

describe("P1: the plan editor's template drop-down", () => {
  it("lists the camera's gear templates for the filter, the starred one marked", () => {
    const cam = camOf(editorState());
    const row = { id: "r", filter: "R", template: "R (ZWO ASI6200MM Pro)", template_id: tplNamed(cam, "R", "R (ZWO ASI6200MM Pro)").id,
      template_sub_s: 120, unit: "h", target_hours: 2, actual_hours: 0 };
    const labels = templateOptions(row, cam).map(o => o.label);
    assert.deepEqual(labels, ["R (ZWO ASI6200MM Pro) \u00b7 120 s \u2605", "R Stars \u00b7 15 s", "New template\u2026"]);
    assert.equal(templateOptions(row, cam).find(o => o.selected).value, row.template_id);
  });
  it("no longer reads the plugin's TS list", () => {
    assert.doesNotMatch(appSrc, /tsTemplates/);
  });
});

describe("P2: + Add filter on a plan with no rows", () => {
  it("starts row 1 on L's starred template, goal 0 in hours", () => {
    const cam = camOf(editorState());
    const row = newFilterRow({ rows: [], camera: cam, id: "x" });
    assert.equal(row.filter, "L");
    assert.equal(row.template, "L");
    assert.equal(row.template_id, cam.filters.L.templates[0].id);
    assert.equal(row.template_sub_s, 120);
    assert.equal(row.unit, "h");
    assert.equal(row.target_hours, 0);
  });
});

describe("P3: picking R Stars on a row with 2 h", () => {
  it("switches to frames and records the template id", () => {
    const cam = camOf(editorState());
    const rs = tplNamed(cam, "R", "R Stars");
    const row = { id: "r", filter: "R", template: "R (ZWO ASI6200MM Pro)", template_sub_s: 120, unit: "h", target_hours: 2, actual_hours: 0 };
    setRowTemplate(row, rs.name, rs.sub_s, rs.id);
    assert.equal(row.unit, "fr");
    assert.equal(row.target_frames, 480);
    assert.equal(row.template_id, rs.id);
  });
});

describe("P6: a row that differs from its template", () => {
  it("shows the row's sub and the template's", () => {
    const cam = camOf(editorState());
    const t = tplNamed(cam, "R", "R (ZWO ASI6200MM Pro)");
    const row = { id: "r", filter: "R", template: t.name, template_id: t.id, template_sub_s: 180, unit: "h", target_hours: 2, actual_hours: 0 };
    assert.ok(cardHtml(row, 0, { camera: cam }).includes("180 s, template says 120 s"));
  });
  it("shows nothing when the row follows", () => {
    const cam = camOf(editorState());
    const t = tplNamed(cam, "R", "R (ZWO ASI6200MM Pro)");
    const row = { id: "r", filter: "R", template: t.name, template_id: t.id, template_sub_s: 120, unit: "h", target_hours: 2, actual_hours: 0 };
    assert.ok(!cardHtml(row, 0, { camera: cam }).includes("template says"));
  });
});

describe("P7: a row whose template gear does not have", () => {
  const cam = camOf(editorState());
  const row = { id: "r", filter: "R", template: "Q Stars", template_unresolved: true, template_sub_s: 15, unit: "fr", target_frames: 20, actual_hours: 0 };
  it("flags it in amber", () => {
    const html = cardHtml(row, 0, { camera: cam });
    assert.match(html, /fr-flag-warn">template not in gear</);
  });
  it("offers the camera's templates for the filter", () => {
    const labels = templateOptions(row, cam).map(o => o.label);
    assert.ok(labels.includes("R (ZWO ASI6200MM Pro) \u00b7 120 s \u2605"));
    assert.ok(labels.includes("R Stars \u00b7 15 s"));
  });
  it("clears the flag when a template is chosen", () => {
    const rs = tplNamed(cam, "R", "R Stars");
    setRowTemplate(row, rs.name, rs.sub_s, rs.id);
    assert.ok(!("template_unresolved" in row));
  });
});

describe("filters off the wheel in the plan editor (section 7)", () => {
  it("marks them in the filter drop-down and under the card", () => {
    const cam = camOf(editorState());
    cam.filters.V = { templates: [{ id: "v1", name: "V", sub_s: 120, starred: true }] };
    cam.wheel.filters.V = { state: "not_on_wheel", slots: {} };
    const row = { id: "r", filter: "V", template: "V", template_id: "v1", template_sub_s: 120, unit: "h", target_hours: 1, actual_hours: 0 };
    const html = cardHtml(row, 0, { camera: cam });
    assert.ok(html.includes("V \u00b7 not on the wheel yet"));
    assert.ok(html.includes("Not synced until V is on the wheel"));
  });
});

describe("status and notes", () => {
  it("judges an in-TS template again against its base", () => {
    const cam = camOf(editorState());
    const t = tplNamed(cam, "R", "R Stars");
    assert.equal(templateStatus(t, cam), "in_ts");
    t.offset = -1;
    assert.equal(templateStatus(t, cam), "changed");
    assert.ok(templateNote(t, cam).startsWith("Offset changed from 25 to Default here. The next sync updates this template in TS, for every plan that uses it."));
    t.offset = 25;
    assert.equal(templateStatus(t, cam), "in_ts");
  });
  it("reads the notes of the approved wording", () => {
    const cam = camOf(editorState());
    const t = tplNamed(cam, "R", "R Stars");
    assert.equal(templateNote(t, cam), "Matches TS. Edits here update the TS template on the next sync.");
    const fresh = addTemplate(cam, "R", { name: "R Long", sub_s: 600 });
    assert.equal(templateNote(fresh, cam), "Not in TS yet. The next sync creates it, and plans can pick it now.");
  });
});

describe("saving", () => {
  it("sends the schema, the rev and only what the server stores", () => {
    const st = editorState();
    const cam = camOf(st);
    const t = addTemplate(cam, "Ha", { name: "Ha 600", sub_s: 600 });
    const body = buildSavePayload(st.doc, 17);
    assert.equal(body.gear_schema, 3);
    assert.equal(body.rev, 17);
    const sent = body.cameras.find(c => c.id === CAM);
    assert.ok(!("wheel" in sent));
    assert.ok(!("template_edit_blocked" in sent));
    assert.deepEqual(Object.keys(sent.filters.Ha), ["templates"]);
    const newOne = sent.filters.Ha.templates.find(x => x.name === "Ha 600");
    assert.ok(!("id" in newOne), "a new template goes without an id, so the server makes one");
    assert.ok(!("status" in newOne) && !("used_by" in newOne) && !("_new" in newOne));
    const old = sent.filters.Ha.templates.find(x => x.name === "Ha");
    assert.equal(old.id, tplNamed(cam, "Ha", "Ha").id);
    assert.ok(old.ts[P1], "stored links ride along; the server ignores the client's copy");
    assert.notEqual(t.id, undefined);
  });
  it("does not mutate the draft", () => {
    const st = editorState();
    const before = JSON.stringify(st.doc);
    buildSavePayload(st.doc, 1);
    assert.equal(JSON.stringify(st.doc), before);
  });
  it("removes a filter nothing uses", () => {
    const st = editorState();
    const cam = camOf(st);
    addFilter(cam, "V");
    assert.equal(removeFilter(cam, "V"), true);
    assert.equal(removeFilter(cam, "R"), false, "R has templates plans use");
  });
});

describe("the field of view line", () => {
  it("matches the approved mockup for the 110GTX and the 6200", () => {
    const st = editorState();
    const scope = st.doc.telescopes.find(t => t.name === "110GTX");
    const f = fieldOfView(camOf(st), scope);
    assert.equal(f.scale.toFixed(2), "1.41");
    assert.equal(f.width_deg.toFixed(2), "3.75");
    assert.equal(f.height_deg.toFixed(2), "2.50");
  });
  it("is on the open card for the telescope whose usual camera it is", () => {
    const st = editorState();
    const scope = st.doc.telescopes.find(t => t.name === "110GTX");
    scope.default_camera_id = CAM;
    assert.ok(gearEditorHtml(st).includes("On the 110GTX: 1.41\u2033 per pixel, field 3.75\u00b0 \u00d7 2.50\u00b0"));
  });
});

describe("the Telescopes tab", () => {
  it("lists telescopes with a summary, amber when something is missing", () => {
    const st = editorState({ tab: "telescopes" });
    st.doc.telescopes.push({ id: "x", name: "Mystery", focal_length_mm: 517, aperture_mm: 0 });
    const html = gearEditorHtml(st);
    assert.match(html, /aria-selected="true">Telescopes/);
    assert.match(html, /517 mm \u00b7 aperture missing/);
    assert.match(html, /ge-muted ge-warn">517 mm/);
  });
  it("opens one telescope with its f ratio", () => {
    const st = editorState({ tab: "telescopes" });
    const t = st.doc.telescopes[0];
    t.focal_length_mm = 550;
    t.aperture_mm = 110;
    st.openTelescope = t.id;
    assert.match(gearEditorHtml(st), /f\/5\.0/);
  });
});

describe("W3 (JavaScript half): the shared wheel cases", () => {
  const cases = JSON.parse(read("../fixtures/wheel_resolve_cases.json")).cases;
  for (const c of cases) {
    it(`${JSON.stringify(c.filter)} on ${JSON.stringify(c.slots)} gives ${JSON.stringify(c.expect)}`, () => {
      assert.equal(resolveToWheel(c.filter, c.slots), c.expect);
    });
  }
});
