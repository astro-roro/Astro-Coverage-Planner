// Pure logic and markup for the plan editor's filter rows (the card list that
// replaced the old "Filter goals" table). Factored out of app.js so
// tests/frontend/ can exercise it without a DOM. See
// docs/specs/goal-panel-and-ts-order.md sections 1, 3 and 6.
//
// A plan's `filter_rows` is an ordered list, and the order is the shooting
// order. Each row: { id, filter, template, template_id, template_sub_s, unit
// ("h" | "fr"), target_hours | target_frames, actual_hours, aac? }. The row
// holds a snapshot of its template's sub; a gear edit moves it only while the
// row follows the template (docs/specs/gear-templates.md sections 3 and 4).

// Default side panel width, in px. app.js starts its panel state from this.
export const SIDE_PANEL_DEFAULT_W = 420;

// Card grid: position, filter, template, remove button. Column 3 takes the
// rest and may shrink to nothing, so long template names truncate rather than
// push the panel sideways. style.css uses the same string.
export const CARD_GRID_COLUMNS = "22px 104px minmax(0, 1fr) 26px";

// Drop-down order for filters: L R G B Ha OIII SII, then any others by name.
export const FILTER_ORDER = ["L", "R", "G", "B", "Ha", "OIII", "SII"];

// A sub under this many seconds is counted in frames, not hours.
export const FRAME_UNIT_BELOW_S = 60;

// Sub length used when neither a template nor the camera gives one.
export const DEFAULT_SUB_S = 300;

export const NEW_TEMPLATE_VALUE = "__new_template__";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function round4(x) {
  return Math.round(x * 10000) / 10000;
}

export function orderFilters(names) {
  const list = [...new Set(names)];
  const rank = f => {
    const i = FILTER_ORDER.indexOf(f);
    return i < 0 ? FILTER_ORDER.length : i;
  };
  return list.sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0));
}

// The filters a camera offers, in drop-down order. A camera with no filter
// settings falls back to the seven standard ones.
export function cameraFilters(camera) {
  const names = camera?.filters ? Object.keys(camera.filters) : [];
  return orderFilters(names.length ? names : FILTER_ORDER);
}

export function unitForSub(sub) {
  return num(sub) < FRAME_UNIT_BELOW_S ? "fr" : "h";
}

function rowSub(row) {
  const s = num(row?.template_sub_s);
  return s > 0 ? s : DEFAULT_SUB_S;
}

// The goal in hours, whatever the unit.
export function rowGoalHours(row) {
  if (row?.unit === "fr") return num(row.target_frames) * rowSub(row) / 3600;
  return num(row?.target_hours);
}

// Hours to frames: round(hours x 3600 / sub). Frames to hours: frames x sub /
// 3600, to 4 places.
export function hoursToFrames(hours, sub) {
  return Math.round(num(hours) * 3600 / sub);
}
export function framesToHours(frames, sub) {
  return round4(num(frames) * sub / 3600);
}

// Switch a row's unit in place, converting the goal with the row's own sub.
// Only the goal field for the new unit is kept.
export function setRowUnit(row, unit) {
  if (unit !== "h" && unit !== "fr") return row;
  if (row.unit === unit) return row;
  const sub = rowSub(row);
  if (unit === "fr") {
    row.target_frames = hoursToFrames(row.target_hours, sub);
    delete row.target_hours;
  } else {
    row.target_hours = framesToHours(row.target_frames, sub);
    delete row.target_frames;
  }
  row.unit = unit;
  return row;
}

// Point a row at a template (or a typed-in new one) in place. The sub comes
// from the template. Under 60 s the row counts frames, otherwise hours. A
// unit change keeps the hours the goal stood for (worked out with the old
// sub); the same unit keeps the number as typed. `templateId` is the gear
// template's id; choosing a template clears `template_unresolved`.
export function setRowTemplate(row, name, subS, templateId) {
  const hours = rowGoalHours(row);
  const sub = num(subS) > 0 ? num(subS) : DEFAULT_SUB_S;
  const unit = unitForSub(sub);
  row.template = name;
  if (templateId) row.template_id = templateId;
  else delete row.template_id;
  delete row.template_unresolved;
  row.template_sub_s = sub;
  if (unit !== row.unit) {
    if (unit === "fr") {
      row.target_frames = hoursToFrames(hours, sub);
      delete row.target_hours;
    } else {
      row.target_hours = round4(hours);
      delete row.target_frames;
    }
    row.unit = unit;
  }
  return row;
}

// Set the goal number in the row's own unit. Frames are whole numbers.
export function setRowGoal(row, value) {
  const v = Math.max(0, num(value));
  if (row.unit === "fr") row.target_frames = Math.round(v);
  else row.target_hours = v;
  return row;
}

// --- templates ---

// The gear templates of one camera filter, in gear order. Each has
// { id, name, sub_s, starred, ... } (gear.json version 3).
export function templatesForFilter(camera, filter) {
  const list = camera?.filters?.[filter]?.templates;
  return Array.isArray(list) ? list.filter(t => t && t.name) : [];
}

export function templateSub(t) {
  const s = num(t?.sub_s);
  return s > 0 ? s : DEFAULT_SUB_S;
}

function fmtSub(s) {
  return Number.isInteger(s) ? String(s) : String(round4(s));
}

export function templateLabel(name, sub, starred = false) {
  return `${name} \u00b7 ${fmtSub(sub)} s${starred ? " \u2605" : ""}`;
}

// The gear template a row names: by `template_id`, else by name. An
// unresolved row names none.
export function rowTemplate(row, camera) {
  if (!row || row.template_unresolved) return null;
  const list = templatesForFilter(camera, row.filter);
  if (row.template_id) return list.find(t => t.id === row.template_id) || null;
  return list.find(t => t.name === row.template) || null;
}

// The options for a row's template drop-down: the camera's gear templates
// for the row's filter in gear order (the starred one marked), the row's own
// template when gear has none by that name (an unresolved row, or a camera
// with no templates), then "New template…". An option's value is the gear
// template's id.
export function templateOptions(row, camera) {
  const current = rowTemplate(row, camera);
  const opts = templatesForFilter(camera, row.filter).map(t => ({
    value: t.id,
    label: templateLabel(t.name, templateSub(t), t.starred === true),
    sub: templateSub(t),
    selected: current ? t.id === current.id : false,
  }));
  if (row.template && !current) {
    opts.unshift({
      value: "",
      label: templateLabel(row.template, rowSub(row)),
      sub: rowSub(row),
      selected: true,
    });
  }
  opts.push({ value: NEW_TEMPLATE_VALUE, label: "New template\u2026", sub: null, selected: false });
  return opts;
}

// What a row's card flags beside its sub (section 7). `differs` reads
// "180 s, template says 120 s" when the row's own sub is not its template's.
// `unresolved` is true for a row whose template gear no longer has.
export function rowFlags(row, camera) {
  const t = rowTemplate(row, camera);
  let differs = null;
  if (t && num(row.template_sub_s) > 0 && Math.abs(num(row.template_sub_s) - templateSub(t)) > 1e-9) {
    differs = `${fmtSub(num(row.template_sub_s))} s, template says ${fmtSub(templateSub(t))} s`;
  }
  const unresolved = row?.template_unresolved === true;
  const wheel = camera?.wheel?.filters?.[row?.filter]?.state;
  const offWheel = wheel === "not_on_wheel";
  return { differs, unresolved, offWheel };
}

// --- adding a row ---

// A filter's usual template: its starred gear template, else the first. With
// none, "{filter} ({camera name})" and sub 300 (not in gear).
export function usualTemplate(filter, camera) {
  const list = templatesForFilter(camera, filter);
  const usual = list.find(t => t.starred === true) || list[0];
  if (usual) return { template: usual.name, template_id: usual.id, sub: templateSub(usual) };
  return { template: `${filter} (${camera?.name || "camera"})`, template_id: null, sub: DEFAULT_SUB_S };
}

// A new row for the "+ Add filter" button. The filter is the first one in
// drop-down order that no row uses yet (else the first again), on its starred
// template. The goal starts at 0 in the unit the sub implies.
export function newFilterRow({ rows, camera, id }) {
  const filters = cameraFilters(camera);
  const used = new Set((rows || []).map(r => r.filter));
  const filter = filters.find(f => !used.has(f)) || filters[0];
  const { template, template_id, sub } = usualTemplate(filter, camera);
  const row = { id, filter, template, template_sub_s: sub, unit: unitForSub(sub), actual_hours: 0 };
  if (template_id) row.template_id = template_id;
  if (row.unit === "fr") row.target_frames = 0;
  else row.target_hours = 0;
  return row;
}

// --- totals for readers that want one figure per filter ---

// The plan's mosaic grid, {rows, cols}, 1 x 1 when it is not a mosaic.
export function mosaicDims(plan) {
  const m = plan?.target?.mosaic || {};
  return {
    rows: Math.max(1, parseInt(m.rows) || 1),
    cols: Math.max(1, parseInt(m.cols) || 1),
  };
}

// How many panels the plan's mosaic has: rows x cols, 1 when it is not a
// mosaic. Mirrors panel_count in filter_rows.py.
export function planPanelCount(plan) {
  const { rows, cols } = mosaicDims(plan);
  return rows * cols;
}

// A panel's key, "row,col" from 1, the key ts_links uses.
export function panelKey(rowIdx, colIdx) {
  return `${rowIdx + 1},${colIdx + 1}`;
}

// A row's per-panel acquired hours as {key: hours} for the panels inside
// `dims` ({rows, cols}; omit to keep every key), or null when the row has no
// `panels` map. Mirrors row_panels in filter_rows.py.
export function rowPanels(row, dims = null) {
  const raw = row?.panels;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    const m = /^([1-9][0-9]*),([1-9][0-9]*)$/.exec(key);
    const v = Number(value);
    if (!m || !Number.isFinite(v) || v < 0) continue;
    if (dims && (Number(m[1]) > dims.rows || Number(m[2]) > dims.cols)) continue;
    out[key] = v;
  }
  return out;
}

// Hours of a row that count toward its goal: with a panels map each panel
// counts up to its own goal and no more; without one, actual_hours.
export function creditedHours(row, dims = null) {
  const panels = rowPanels(row, dims);
  if (!panels) return num(row?.actual_hours);
  const goal = rowGoalHours(row);
  return Object.values(panels).reduce((t, v) => t + Math.min(v, goal), 0);
}

// {filter: {target_hours, actual_hours, credited_hours}} summed over a plan's
// rows. Frame rows count as target_frames x template_sub_s / 3600 hours. A
// row's goal is per panel, so a mosaic's target_hours is the goal times the
// panel count. actual_hours is what was shot (the sum of the panels when a row
// has a panels map). credited_hours is what counts toward the goal: the same,
// except that each panel counts up to its own goal and no more.
export function hoursByFilter(plan) {
  const out = {};
  const dims = mosaicDims(plan);
  for (const r of plan?.filter_rows || []) {
    if (!r?.filter) continue;
    const e = out[r.filter] || (out[r.filter] = { target_hours: 0, actual_hours: 0, credited_hours: 0 });
    e.target_hours += rowGoalHours(r);
    const panels = rowPanels(r, dims);
    e.actual_hours += panels ? Object.values(panels).reduce((t, v) => t + v, 0) : num(r.actual_hours);
    e.credited_hours += creditedHours(r, dims);
  }
  const k = planPanelCount(plan);
  if (k > 1) for (const e of Object.values(out)) e.target_hours *= k;
  return out;
}

// --- progress ---

// What a row has done, in its own unit. AAC's accepted figures win when the
// row has an aac block (section 6); otherwise actual_hours, with frame rows
// converted to whole frames.
export function rowDone(row, dims = null) {
  const sub = rowSub(row);
  const aac = row?.aac;
  const shot = rowPanels(row, dims) ? creditedHours(row, dims) : num(row?.actual_hours);
  if (row?.unit === "fr") {
    if (aac && Number.isFinite(Number(aac.accepted_frames))) return Math.round(num(aac.accepted_frames));
    if (aac && Number.isFinite(Number(aac.accepted_seconds))) return Math.round(num(aac.accepted_seconds) / sub);
    return Math.round(shot * 3600 / sub);
  }
  if (aac && Number.isFinite(Number(aac.accepted_seconds))) return num(aac.accepted_seconds) / 3600;
  return shot;
}

// Per-panel progress for a row that has a panels map: one entry per panel of
// the grid, in row-major order, {key, done, goal, left, finished}, in the
// row's own unit. A panel counts up to its own goal. Null without a map.
export function rowPanelProgress(row, dims) {
  const panels = rowPanels(row, dims);
  if (!panels) return null;
  const frames = row?.unit === "fr";
  const sub = rowSub(row);
  const goal = rowGoal(row);
  const out = [];
  for (let r = 0; r < dims.rows; r++) {
    for (let c = 0; c < dims.cols; c++) {
      const key = panelKey(r, c);
      const hours = panels[key] || 0;
      const done = frames ? Math.round(hours * 3600 / sub) : hours;
      const left = Math.max(0, goal - done);
      out.push({ key, done, goal, left, finished: goal > 0 && done >= goal });
    }
  }
  return out;
}

// What Target Scheduler has shot that AAC has not looked at yet, in the row's
// own unit. actual_hours is TS's acquired total from the plugin's live
// reports; aac.seen_* is everything AAC has graded, accepted or not. So the
// difference is frames taken since AAC's last pass, and it falls back to 0 on
// its own once AAC catches up. Rows without AAC figures have nothing waiting:
// their bar already shows actual_hours.
export function rowPending(row) {
  const aac = row?.aac;
  if (!aac) return 0;
  const hasFrames = aac.seen_frames != null && Number.isFinite(Number(aac.seen_frames));
  const hasSeconds = aac.seen_seconds != null && Number.isFinite(Number(aac.seen_seconds));
  // A block stored before AAC sent seen figures: what AAC has looked at is
  // unknown, so claim nothing is waiting rather than the whole total.
  if (!hasFrames && !hasSeconds) return 0;
  const sub = rowSub(row);
  if (row?.unit === "fr") {
    const shot = Math.round(num(row.actual_hours) * 3600 / sub);
    const seen = hasFrames ? Math.round(num(aac.seen_frames)) : Math.round(num(aac.seen_seconds) / sub);
    return Math.max(0, shot - seen);
  }
  if (!hasSeconds) return 0;
  const pending = num(row.actual_hours) - num(aac.seen_seconds) / 3600;
  // Under half a sub is rounding between the two counts, not a frame.
  return pending * 3600 >= sub / 2 ? pending : 0;
}

export function rowGoal(row) {
  return row?.unit === "fr" ? Math.round(num(row.target_frames)) : num(row?.target_hours);
}

// {left, right, finished, noGoal, pct, pendingPct, pendingText, panelText}
// for the progress block. `panels` is the plan's mosaic panel count: the row's
// goal is per panel, so the bar measures against goal x panels and
// `panelText` says how that total is made up. `left` is the "3.0 / 4.2 h" text, `right` the "1.2 h left",
// "done" or "set a goal" text. `pendingPct` is the striped part of the bar
// after `pct` (shot, waiting for AAC), and `pendingText` its line under the
// bar, empty when nothing is waiting.
export function rowProgress(row, panels = 1) {
  // `panels` is the plan's mosaic: a panel count, or its {rows, cols}, which
  // also lets the row's per-panel breakdown be read.
  const dims = panels && typeof panels === "object" ? panels : null;
  const k = dims ? dims.rows * dims.cols : Math.max(1, Math.floor(Number(panels)) || 1);
  const goal = rowGoal(row) * k;
  const done = rowDone(row, dims);
  const pending = rowPending(row);
  const frames = row?.unit === "fr";
  const fmt = v => (frames ? String(v) : v.toFixed(1));
  const u = frames ? "fr" : "h";
  const finished = row?.aac ? row.aac.complete === true : (goal > 0 && done >= goal);
  const pendingText = pending > 0
    ? `+${frames ? pending : pending.toFixed(2)} ${u} shot, waiting for AAC`
    : "";
  if (!(goal > 0)) {
    return { left: "", right: "set a goal", finished: false, noGoal: true, pct: 0, pendingPct: 0, pendingText, panelText: "", panelLines: [] };
  }
  const left = `${fmt(done)} / ${fmt(goal)} ${u}`;
  const remain = Math.max(0, goal - done);
  const right = finished ? "done" : `${fmt(remain)} ${u} left`;
  const pct = Math.min(100, Math.max(0, done / goal * 100));
  const pendingPct = finished ? 0 : Math.min(100 - pct, pending / goal * 100);
  const panelText = k > 1 ? `${k} panels × ${fmt(rowGoal(row))} ${u}` : "";
  const byPanel = k > 1 && dims ? rowPanelProgress(row, dims) : null;
  const panelLines = byPanel ? byPanel.map(p => ({
    key: p.key, finished: p.finished,
    text: p.finished ? "done" : `${fmt(p.left)} ${u} left`,
    title: `${p.key}: ${fmt(p.done)} / ${fmt(p.goal)} ${u}`,
  })) : [];
  return { left, right, finished, noGoal: false, pct, pendingPct, pendingText, panelText, panelLines };
}

// --- markup ---

function filterOptionsHtml(row, camera) {
  const names = cameraFilters(camera);
  if (row.filter && !names.includes(row.filter)) names.push(row.filter);
  return names.map(f => {
    const off = camera?.wheel?.filters?.[f]?.state === "not_on_wheel";
    return `<option value="${esc(f)}"${f === row.filter ? " selected" : ""}>${esc(off ? `${f} \u00b7 not on the wheel yet` : f)}</option>`;
  }).join("");
}

// The grab handle in a card's first column: the position number over a 6-dot
// grip. It is the only thing that starts a drag or takes the arrow keys.
// With smart order on it is disabled and not draggable.
export const HANDLE_LABEL = "Drag to reorder. Arrow keys also move it.";
const GRIP_SVG = `<svg width="10" height="14" viewBox="0 0 10 14" aria-hidden="true" fill="currentColor"><circle cx="3" cy="3" r="1.2"></circle><circle cx="7" cy="3" r="1.2"></circle><circle cx="3" cy="7" r="1.2"></circle><circle cx="7" cy="7" r="1.2"></circle><circle cx="3" cy="11" r="1.2"></circle><circle cx="7" cy="11" r="1.2"></circle></svg>`;

export function handleHtml(index, smart = false) {
  const state = smart ? " disabled" : ` draggable="true"`;
  return `<button type="button" class="fr-handle"${state} aria-label="${HANDLE_LABEL}"><span class="fr-pos">${index + 1}</span>${GRIP_SVG}</button>`;
}

// One card. `colors` maps filter name to dot colour. `smart` disables the
// handle.
export function cardHtml(row, index, { camera, colors = {}, smart = false, panels = 1 } = {}) {
  const p = rowProgress(row, panels);
  const dot = colors[row.filter] || "#888";
  const frames = row.unit === "fr";
  const goalVal = frames ? num(row.target_frames) : num(row.target_hours);
  const tmplOpts = templateOptions(row, camera)
    .map(o => `<option value="${esc(o.value)}"${o.selected ? " selected" : ""}>${esc(o.label)}</option>`)
    .join("");
  const cur = templateLabel(row.template, rowSub(row));
  const flags = rowFlags(row, camera);
  const notes = [
    flags.unresolved ? `<span class="fr-flag fr-flag-warn">template not in gear</span>` : "",
    flags.differs ? `<span class="fr-flag">${esc(flags.differs)}</span>` : "",
    flags.offWheel ? `<span class="fr-flag fr-flag-warn">Not synced until ${esc(row.filter)} is on the wheel</span>` : "",
  ].filter(Boolean).join("");
  return `<div class="fr-card${p.finished ? " fr-done" : ""}" data-row-id="${esc(row.id)}">
    ${handleHtml(index, smart)}
    <div class="fr-filter"><span class="fr-dot" style="background:${esc(dot)}"></span><select class="fr-filter-sel" aria-label="Filter">${filterOptionsHtml(row, camera)}</select></div>
    <select class="fr-tmpl-sel" aria-label="TS template" title="${esc(cur)}">${tmplOpts}</select>
    <button type="button" class="fr-remove" title="Remove this filter" aria-label="Remove this filter">×</button>
    <div class="fr-goal"><input type="number" class="fr-goal-input" min="0" step="${frames ? 1 : 0.5}" value="${goalVal}" aria-label="Goal"><select class="fr-unit-sel" aria-label="Unit"><option value="h"${frames ? "" : " selected"}>h</option><option value="fr"${frames ? " selected" : ""}>fr</option></select></div>
    <div class="fr-progress"><div class="fr-bar${row.aac ? " fr-bar-aac" : ""}"><i style="width:${p.pct.toFixed(1)}%"></i>${p.pendingPct > 0 ? `<b class="fr-pending-bar" style="width:${p.pendingPct.toFixed(1)}%"></b>` : ""}</div><div class="fr-prog-text"><span class="fr-prog-left">${esc(p.left)}</span><span class="fr-prog-right">${esc(p.right)}</span></div>${p.panelText ? `<div class="fr-panels">${esc(p.panelText)}</div>` : ""}${p.panelLines.length ? `<div class="fr-panel-list">${p.panelLines.map(l => `<span class="fr-panel${l.finished ? " fr-panel-done" : ""}" title="${esc(l.title)}">${esc(l.key)} ${esc(l.text)}</span>`).join("")}</div>` : ""}${p.pendingText ? `<div class="fr-pending">${esc(p.pendingText)}</div>` : ""}</div>
    ${notes ? `<div class="fr-notes">${notes}</div>` : ""}
  </div>`;
}

// --- switch frequency and smart order (section 4) ---

// "Rotate every N" keeps N between these.
export const ROTATE_MIN = 1;
export const ROTATE_MAX = 99;
// N offered when the switch first moves to "Rotate every N" on a plan that
// has never rotated.
export const ROTATE_DEFAULT = 3;

// The plan's switch frequency as the editor shows it: absent (unknown) is 0.
export function switchFrequency(plan) {
  const n = Number(plan?.filter_switch_frequency);
  return Number.isInteger(n) && n >= 0 && n <= ROTATE_MAX ? n : 0;
}

export function smartOrder(plan) {
  return plan?.smart_exposure_order === true;
}

// N clamped to 1..99, whole frames. Anything unreadable falls back to the
// default.
export function clampRotate(n) {
  const v = Math.round(Number(n));
  if (!Number.isFinite(v)) return ROTATE_DEFAULT;
  return Math.min(ROTATE_MAX, Math.max(ROTATE_MIN, v));
}

// The one line under the controls saying what Target Scheduler will do.
export function orderExplanation(freq, smart) {
  if (smart) return "Target Scheduler picks the order itself (moon and altitude). Dragging is off.";
  if (freq > 0) {
    return `Shoots ${freq} ${freq === 1 ? "frame" : "frames"} of each filter, top to bottom, then repeats.`;
  }
  return "Shoots each filter until its goal is met, top to bottom.";
}

// The controls above the card list. `rotateN` is the N to show while the
// switch is on "Finish each", so flipping it back keeps the last value.
// `projectNote` is the one-line note for a project shared with other plans.
export function orderControlsHtml(plan, { rotateN, projectNote = "" } = {}) {
  const freq = switchFrequency(plan);
  const smart = smartOrder(plan);
  const n = freq > 0 ? freq : clampRotate(rotateN ?? ROTATE_DEFAULT);
  const rotating = freq > 0;
  return `<div class="fr-order">
    <div class="fr-switch" role="radiogroup" aria-label="Filter switching">
      <label class="fr-switch-opt"><input type="radio" name="fr-switch" value="finish"${rotating ? "" : " checked"}> Finish each</label>
      <label class="fr-switch-opt"><input type="radio" name="fr-switch" value="rotate"${rotating ? " checked" : ""}> Rotate every <input type="number" class="fr-rotate-n" min="${ROTATE_MIN}" max="${ROTATE_MAX}" step="1" value="${n}" aria-label="Frames per filter before switching"${rotating ? "" : " disabled"}></label>
    </div>
    <label class="fr-smart"><input type="checkbox" class="fr-smart-cb"${smart ? " checked" : ""}> Smart order</label>
    <div class="fr-order-text">${esc(orderExplanation(freq, smart))}</div>
    ${projectNote ? `<div class="fr-order-note">${esc(projectNote)}</div>` : ""}
  </div>`;
}

// The whole list: the order controls, then cards in order, then "+ Add
// filter". An empty plan gets a short note first. With smart order on the
// list is marked so reordering (phase 5) stays off; the order is kept.
export function filterListHtml(plan, ctx = {}) {
  const rows = plan?.filter_rows || [];
  const smart = smartOrder(plan);
  const panels = mosaicDims(plan);
  const cards = rows.map((r, i) => cardHtml(r, i, { ...ctx, smart, panels })).join("");
  const note = rows.length
    ? ""
    : `<div class="fr-empty">No filters yet. Add one to set a goal.</div>`;
  const controls = orderControlsHtml(plan, { rotateN: ctx.rotateN, projectNote: ctx.projectNote });
  return `${controls}<div class="fr-list${smart ? " fr-smart-on" : ""}" data-reorder="${smart ? "off" : "on"}">${note}${cards}<button type="button" class="fr-add">+ Add filter</button></div>`;
}

// --- reordering (section 1) ---

// A copy of `rows` with the row at `from` moved to final index `to`. Out of
// range or unchanged gives a plain copy.
export function moveRow(rows, from, to) {
  const out = [...rows];
  const n = out.length;
  if (!Number.isInteger(from) || !Number.isInteger(to)) return out;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return out;
  const [r] = out.splice(from, 1);
  out.splice(to, 0, r);
  return out;
}

// Dragging row `from` over row `over`: where the drop line goes and the row's
// final index. Dragging upwards the line is above the hovered row, downwards
// below it, and either way the row ends up at the hovered row's index. Null
// when the drag is over its own row.
export function dropTarget(from, over) {
  if (!Number.isInteger(from) || !Number.isInteger(over) || from === over) return null;
  return { placement: from > over ? "before" : "after", to: over };
}

// The final index for an arrow key on the row at `index`, or null when the
// key does nothing: not an up or down arrow, already at that end, or smart
// order is on.
export function keyMoveTarget(key, index, length, smart = false) {
  if (smart) return null;
  const to = key === "ArrowUp" ? index - 1 : key === "ArrowDown" ? index + 1 : null;
  if (to === null || to < 0 || to >= length) return null;
  return to;
}

// The drop line: 2 px accent rule between two ring markers. app.js puts at
// most one in the list.
export function dropLineHtml() {
  return `<div class="fr-drop-line" aria-hidden="true"><span class="fr-drop-mark"></span><span class="fr-drop-rule"></span><span class="fr-drop-mark"></span></div>`;
}

// --- sync diff labels ---

// "filter_rows.<row id>.<field>" to "R Stars target frames", looking the row
// up in the plan being shown. Falls back to the bare field name when the row
// is gone. "filter_rows.<row id>" alone is a whole row added or removed.
export function rowDiffLabel(path, plan) {
  const parts = String(path).split(".");
  const row = (plan?.filter_rows || []).find(r => r.id === parts[1]);
  const name = row ? (row.template || row.filter) : "";
  if (parts.length < 3) return name ? `${name} row` : "filter row";
  const field = parts.slice(2).join(".").replace(/_/g, " ");
  return name ? `${name} ${field}` : field;
}
