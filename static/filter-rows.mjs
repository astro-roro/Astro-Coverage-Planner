// Pure logic and markup for the plan editor's filter rows (the card list that
// replaced the old "Filter goals" table). Factored out of app.js so
// tests/frontend/ can exercise it without a DOM. See
// docs/specs/goal-panel-and-ts-order.md sections 1, 3 and 6.
//
// A plan's `filter_rows` is an ordered list, and the order is the shooting
// order. Each row: { id, filter, template, template_sub_s, unit ("h" | "fr"),
// target_hours | target_frames, actual_hours, aac? }. ACP never edits a sub
// length: the row holds a snapshot of its template's sub, nothing more.

// Default side panel width, in px. app.js starts its panel state from this.
export const SIDE_PANEL_DEFAULT_W = 380;

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
// sub); the same unit keeps the number as typed.
export function setRowTemplate(row, name, subS) {
  const hours = rowGoalHours(row);
  const sub = num(subS) > 0 ? num(subS) : DEFAULT_SUB_S;
  const unit = unitForSub(sub);
  row.template = name;
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

// Templates for one filter, one per name (a plugin list can carry the same
// name for several profiles), sorted by name.
export function templatesForFilter(templates, filter) {
  const want = String(filter || "").trim().toLowerCase();
  const seen = new Set();
  const out = [];
  for (const t of templates || []) {
    if (String(t?.filter || "").trim().toLowerCase() !== want) continue;
    if (!t.name || seen.has(t.name)) continue;
    seen.add(t.name);
    out.push(t);
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

export function templateSub(t) {
  const s = num(t?.default_exposure_s);
  return s > 0 ? s : DEFAULT_SUB_S;
}

function fmtSub(s) {
  return Number.isInteger(s) ? String(s) : String(round4(s));
}

export function templateLabel(name, sub) {
  return `${name} · ${fmtSub(sub)} s`;
}

// The options for a row's template drop-down: the filter's templates, the
// row's current template if the list lacks it (so an empty or missing list
// still shows what the row has), then "New template…".
export function templateOptions(row, templates) {
  const opts = templatesForFilter(templates, row.filter).map(t => ({
    value: t.name,
    label: templateLabel(t.name, templateSub(t)),
    sub: templateSub(t),
    selected: t.name === row.template,
  }));
  if (row.template && !opts.some(o => o.value === row.template)) {
    opts.unshift({
      value: row.template,
      label: templateLabel(row.template, rowSub(row)),
      sub: rowSub(row),
      selected: true,
    });
  }
  opts.push({ value: NEW_TEMPLATE_VALUE, label: "New template…", sub: null, selected: false });
  return opts;
}

// --- adding a row ---

// A filter's usual template: the camera's ts_template_name when the list has
// it, else the first template for the filter by name. With no usable
// template, the camera's name or "{filter} ({camera name})" and its default
// sub (else 300).
export function usualTemplate(filter, camera, templates) {
  const cfg = camera?.filters?.[filter] || {};
  const list = templatesForFilter(templates, filter);
  const usual = list.find(t => t.name === cfg.ts_template_name) || list[0];
  if (usual) return { template: usual.name, sub: templateSub(usual) };
  return {
    template: cfg.ts_template_name || `${filter} (${camera?.name || "camera"})`,
    sub: num(cfg.default_sub_s) > 0 ? num(cfg.default_sub_s) : DEFAULT_SUB_S,
  };
}

// A new row for the "+ Add filter" button. The filter is the first one in
// drop-down order that no row uses yet (else the first again), with its usual
// template. The goal starts at 0 in the unit the sub implies.
export function newFilterRow({ rows, camera, templates, id }) {
  const filters = cameraFilters(camera);
  const used = new Set((rows || []).map(r => r.filter));
  const filter = filters.find(f => !used.has(f)) || filters[0];
  const { template, sub } = usualTemplate(filter, camera, templates);
  const row = { id, filter, template, template_sub_s: sub, unit: unitForSub(sub), actual_hours: 0 };
  if (row.unit === "fr") row.target_frames = 0;
  else row.target_hours = 0;
  return row;
}

// --- totals for readers that want one figure per filter ---

// {filter: {target_hours, actual_hours}} summed over a plan's rows. Frame rows
// count as target_frames x template_sub_s / 3600 hours.
export function hoursByFilter(plan) {
  const out = {};
  for (const r of plan?.filter_rows || []) {
    if (!r?.filter) continue;
    const e = out[r.filter] || (out[r.filter] = { target_hours: 0, actual_hours: 0 });
    e.target_hours += rowGoalHours(r);
    e.actual_hours += num(r.actual_hours);
  }
  return out;
}

// --- progress ---

// What a row has done, in its own unit. AAC's accepted figures win when the
// row has an aac block (section 6); otherwise actual_hours, with frame rows
// converted to whole frames.
export function rowDone(row) {
  const sub = rowSub(row);
  const aac = row?.aac;
  if (row?.unit === "fr") {
    if (aac && Number.isFinite(Number(aac.accepted_frames))) return Math.round(num(aac.accepted_frames));
    if (aac && Number.isFinite(Number(aac.accepted_seconds))) return Math.round(num(aac.accepted_seconds) / sub);
    return Math.round(num(row.actual_hours) * 3600 / sub);
  }
  if (aac && Number.isFinite(Number(aac.accepted_seconds))) return num(aac.accepted_seconds) / 3600;
  return num(row?.actual_hours);
}

export function rowGoal(row) {
  return row?.unit === "fr" ? Math.round(num(row.target_frames)) : num(row?.target_hours);
}

// {left, right, finished, noGoal, pct} for the progress block. `left` is the
// "3.0 / 4.2 h" text, `right` the "1.2 h left", "done" or "set a goal" text.
export function rowProgress(row) {
  const goal = rowGoal(row);
  const done = rowDone(row);
  const frames = row?.unit === "fr";
  const fmt = v => (frames ? String(v) : v.toFixed(1));
  const u = frames ? "fr" : "h";
  const finished = row?.aac ? row.aac.complete === true : (goal > 0 && done >= goal);
  if (!(goal > 0)) {
    return { left: "", right: "set a goal", finished: false, noGoal: true, pct: 0 };
  }
  const left = `${fmt(done)} / ${fmt(goal)} ${u}`;
  const remain = Math.max(0, goal - done);
  const right = finished ? "done" : `${fmt(remain)} ${u} left`;
  return { left, right, finished, noGoal: false, pct: Math.min(100, Math.max(0, done / goal * 100)) };
}

// --- markup ---

function filterOptionsHtml(row, camera) {
  const names = cameraFilters(camera);
  if (row.filter && !names.includes(row.filter)) names.push(row.filter);
  return names.map(f => `<option value="${esc(f)}"${f === row.filter ? " selected" : ""}>${esc(f)}</option>`).join("");
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
export function cardHtml(row, index, { camera, templates, colors = {}, smart = false } = {}) {
  const p = rowProgress(row);
  const dot = colors[row.filter] || "#888";
  const frames = row.unit === "fr";
  const goalVal = frames ? num(row.target_frames) : num(row.target_hours);
  const tmplOpts = templateOptions(row, templates)
    .map(o => `<option value="${esc(o.value)}"${o.selected ? " selected" : ""}>${esc(o.label)}</option>`)
    .join("");
  const cur = templateLabel(row.template, rowSub(row));
  return `<div class="fr-card${p.finished ? " fr-done" : ""}" data-row-id="${esc(row.id)}">
    ${handleHtml(index, smart)}
    <div class="fr-filter"><span class="fr-dot" style="background:${esc(dot)}"></span><select class="fr-filter-sel" aria-label="Filter">${filterOptionsHtml(row, camera)}</select></div>
    <select class="fr-tmpl-sel" aria-label="TS template" title="${esc(cur)}">${tmplOpts}</select>
    <button type="button" class="fr-remove" title="Remove this filter" aria-label="Remove this filter">×</button>
    <div class="fr-goal"><input type="number" class="fr-goal-input" min="0" step="${frames ? 1 : 0.5}" value="${goalVal}" aria-label="Goal"><select class="fr-unit-sel" aria-label="Unit"><option value="h"${frames ? "" : " selected"}>h</option><option value="fr"${frames ? " selected" : ""}>fr</option></select></div>
    <div class="fr-progress"><div class="fr-bar"><i style="width:${p.pct.toFixed(1)}%"></i></div><div class="fr-prog-text"><span class="fr-prog-left">${esc(p.left)}</span><span class="fr-prog-right">${esc(p.right)}</span></div></div>
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
  const cards = rows.map((r, i) => cardHtml(r, i, { ...ctx, smart })).join("");
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
