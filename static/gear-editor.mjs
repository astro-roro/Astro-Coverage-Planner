// Pure logic and markup for the gear editor (docs/specs/gear-templates.md
// section 6, the approved Main and Telescopes artboards). Factored out of
// app.js so tests/frontend/ can exercise it without a DOM. app.js owns the
// events, the fetches and the panel; this file turns a state object into
// markup and applies edits to the draft.
//
// State: { tab, doc: {telescopes, cameras} (the draft), saved (gear as
// loaded, for the subs on file), plans, openCamera, openTemplate,
// openTelescope, menu, addFilterFor, message }.

import { FILTER_ORDER } from "./filter-rows.mjs";

export const PLUGIN_NOTE = "The NINA plugin on this rig does not update TS templates yet. Renaming and sub changes are off until it does.";
export const CONFLICT_MESSAGE = "Gear changed elsewhere. Reload to see it.";
export const NEW_NARROW_SUB_S = 300;
export const NEW_BROAD_SUB_S = 120;

// The names a filter goes by. Mirrors filter_rows.ROW_FILTER_ALIASES, which
// mirrors the sync and the plugin; tests/fixtures/wheel_resolve_cases.json
// drives all of them. Keys are matched upper-cased.
export const ROW_FILTER_ALIASES = {
  H: "Ha", HA: "Ha", HALPHA: "Ha", "H-ALPHA": "Ha", H_ALPHA: "Ha",
  O: "OIII", O3: "OIII", OIII: "OIII", "O-III": "OIII", O_III: "OIII",
  S: "SII", S2: "SII", SII: "SII", "S-II": "SII", S_II: "SII",
  L: "L", LUM: "L", LUMINANCE: "L", LIGHT: "L", CLEAR: "L",
  R: "R", RED: "R",
  G: "G", GREEN: "G",
  B: "B", BLUE: "B",
  V: "V", IR: "IR", UV: "UV",
  NOFILTER: "NoFilter", "NO FILTER": "NoFilter", NO_FILTER: "NoFilter",
  NONE: "NoFilter", EMPTY: "NoFilter", OPEN: "NoFilter",
};
const NARROW = new Set(["Ha", "OIII", "SII"]);

// A filter name typed into gear: the alias table's spelling when it knows
// the name ("H" is Ha), the name as typed when it does not ("Antlia Ha").
export function canonFilterName(name) {
  const t = String(name ?? "").trim();
  return Object.hasOwn(ROW_FILTER_ALIASES, t.toUpperCase()) ? ROW_FILTER_ALIASES[t.toUpperCase()] : t;
}

// The wheel slot a filter is written under, or null. Exact, then ignoring
// case and surrounding spaces, then the same alias-table spelling.
export function resolveToWheel(name, slots) {
  if (name == null || !String(name).trim() || !Array.isArray(slots) || !slots.length) return null;
  const wanted = String(name);
  const names = slots.filter(s => typeof s === "string" && s.trim());
  for (const s of names) if (s === wanted) return s;
  const low = wanted.trim().toLowerCase();
  for (const s of names) if (s.trim().toLowerCase() === low) return s;
  const spelling = canonFilterName(wanted);
  for (const s of names) if (canonFilterName(s) === spelling) return s;
  return null;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function fmtNum(n) {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10000) / 10000);
}

// --- gain and offset ---

// "Default" is shown for -1.
export function fmtGO(v) {
  return num(v) < 0 || v == null ? "Default" : String(Math.round(num(v)));
}

// "Default" (any case) or a whole number from 0 up. Anything else is null.
export function parseGO(text) {
  const t = String(text ?? "").trim();
  if (/^default$/i.test(t)) return -1;
  if (/^\d+$/.test(t)) return parseInt(t, 10);
  return null;
}

// --- the wheel ---

export function primaryProfile(camera) {
  const profiles = camera?.wheel?.profiles || [];
  return profiles.find(p => p.primary) || profiles[0] || null;
}

// {state, slots} for one filter. The server computes it for the filters it
// knows; a filter added in this draft is worked out here from the same
// slot lists.
export function wheelInfo(camera, filter) {
  const known = camera?.wheel?.filters?.[filter];
  if (known) return known;
  const withSlots = (camera?.wheel?.profiles || []).filter(p => Array.isArray(p.slots) && p.slots.length);
  if (!withSlots.length) return { state: "unknown", slots: {} };
  const slots = {};
  for (const p of withSlots) {
    const s = resolveToWheel(filter, p.slots);
    if (s !== null) slots[p.profile_id] = s;
  }
  const n = Object.keys(slots).length;
  return { state: n === withSlots.length ? "on_wheel" : n ? "partial" : "not_on_wheel", slots };
}

// "wheel slot H", "H in Foo, Ha in Bar", "not on Bar's wheel yet", or "".
export function wheelNote(camera, filter) {
  const info = wheelInfo(camera, filter);
  const profiles = camera?.wheel?.profiles || [];
  const byId = Object.fromEntries(profiles.map(p => [p.profile_id, p]));
  const entries = Object.entries(info.slots || {});
  if (info.state === "on_wheel" || info.state === "partial") {
    if (info.state === "partial") {
      const missing = profiles.filter(p => p.slots?.length && !(p.profile_id in info.slots)).map(p => p.profile_name);
      return `not on ${missing.join(", ")}'s wheel yet`;
    }
    const distinct = [...new Set(entries.map(([, s]) => s))];
    if (distinct.length <= 1) return `wheel slot ${distinct[0]}`;
    return entries.map(([pid, s]) => `${s} in ${byId[pid]?.profile_name || pid}`).join(", ");
  }
  return "";
}

// The boxes' order: the primary profile's slot order, then the filters that
// have no slot there alphabetically. With no slots known: L R G B Ha OIII
// SII, then the rest alphabetically.
export function filterOrder(camera) {
  const names = Object.keys(camera?.filters || {});
  const alpha = list => list.sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }) || (a < b ? -1 : 1));
  const primary = primaryProfile(camera);
  if (primary?.slots?.length) {
    const slotted = [];
    const rest = [];
    for (const f of names) {
      const s = resolveToWheel(f, primary.slots);
      if (s === null) rest.push(f);
      else slotted.push([primary.slots.indexOf(s), f]);
    }
    slotted.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : 1));
    return [...slotted.map(x => x[1]), ...alpha(rest)];
  }
  return [...FILTER_ORDER.filter(f => names.includes(f)), ...alpha(names.filter(f => !FILTER_ORDER.includes(f)))];
}

// Wheel slots (by ACP name) that the camera's profiles hold and gear does not
// have a filter for yet.
export function wheelFiltersMissing(camera) {
  const have = Object.keys(camera?.filters || {});
  const out = [];
  for (const p of camera?.wheel?.profiles || []) {
    for (const slot of p.slots || []) {
      const acp = canonFilterName(slot);
      if (!acp || out.includes(acp) || have.includes(acp)) continue;
      if (have.some(f => resolveToWheel(f, [slot]) === slot)) continue;
      out.push(acp);
    }
  }
  return out;
}

// --- templates ---

let tmpCounter = 0;
export function makeTemplate({ name, sub_s, gain = -1, offset = -1, bin = 1, starred = false }) {
  tmpCounter += 1;
  return {
    id: `new-${tmpCounter}`, _new: true, name, sub_s, gain, offset, bin, starred,
    aliases: [], ts_names: [], ts: {}, status: "new", conflicts: [], used_by: 0,
  };
}

export function allTemplates(doc) {
  const out = [];
  for (const cam of doc?.cameras || []) {
    for (const [filter, cfg] of Object.entries(cam.filters || {})) {
      for (const t of cfg.templates || []) out.push({ camera: cam, filter, template: t });
    }
  }
  return out;
}

export function findTemplate(doc, id) {
  return allTemplates(doc).find(x => x.template.id === id) || null;
}

function uniqueName(camera, base) {
  const taken = new Set(allTemplates({ cameras: [camera] }).map(x => x.template.name.toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let i = 2; ; i++) {
    const cand = `${base} ${i}`;
    if (!taken.has(cand.toLowerCase())) return cand;
  }
}

// Add an unsaved template to a filter. It is starred only when the filter
// has none. Returns the template.
export function addTemplate(camera, filter, fields = {}) {
  const cfg = camera.filters[filter];
  cfg.templates = cfg.templates || [];
  const t = makeTemplate({
    name: uniqueName(camera, fields.name || `${filter} new`),
    sub_s: fields.sub_s ?? (NARROW.has(filter) ? NEW_NARROW_SUB_S : NEW_BROAD_SUB_S),
    gain: fields.gain ?? -1, offset: fields.offset ?? -1, bin: fields.bin ?? 1,
    starred: cfg.templates.length === 0,
  });
  cfg.templates.push(t);
  return t;
}

export function duplicateTemplate(camera, filter, id) {
  const src = (camera.filters[filter].templates || []).find(t => t.id === id);
  if (!src) return null;
  return addTemplate(camera, filter, {
    name: uniqueName(camera, `${src.name} copy`), sub_s: src.sub_s, gain: src.gain, offset: src.offset, bin: src.bin,
  });
}

// One star per filter: starring a template unstars the others.
export function starTemplate(camera, filter, id) {
  const list = camera.filters[filter].templates || [];
  if (!list.some(t => t.id === id)) return false;
  for (const t of list) t.starred = t.id === id;
  return true;
}

function ensureStar(list) {
  if (list.length && !list.some(t => t.starred)) list[0].starred = true;
}

// Remove a template nothing uses, and never a filter's last one. Returns
// whether it went.
export function removeTemplate(camera, filter, id) {
  const list = camera.filters[filter].templates || [];
  const t = list.find(x => x.id === id);
  if (!t || num(t.used_by) > 0 || list.length < 2) return false;
  camera.filters[filter].templates = list.filter(x => x.id !== id);
  ensureStar(camera.filters[filter].templates);
  return true;
}

export function filterUsed(camera, filter) {
  return (camera.filters[filter]?.templates || []).reduce((n, t) => n + num(t.used_by), 0);
}

export function removeFilter(camera, filter) {
  if (!camera.filters[filter] || filterUsed(camera, filter) > 0) return false;
  delete camera.filters[filter];
  return true;
}

// Add a filter by typed name. It goes through the alias lookup ("H" is Ha)
// and gets one starred template named for it: 300 s for the narrow filters,
// 120 s otherwise. Returns {ok, name} or {ok: false, error}.
export function addFilter(camera, typed) {
  const name = canonFilterName(typed);
  if (!name) return { ok: false, error: "Type a filter name." };
  if (!/^[A-Za-z0-9_+.\- ]{1,32}$/.test(name)) {
    return { ok: false, error: "Filter names use letters, numbers, spaces and + - _ . only, up to 32 characters." };
  }
  camera.filters = camera.filters || {};
  if (Object.keys(camera.filters).some(f => f.toLowerCase() === name.toLowerCase())) {
    return { ok: false, error: `${name} is already on this camera.` };
  }
  camera.filters[name] = { templates: [] };
  addTemplate(camera, name, { name: uniqueName(camera, name), sub_s: NARROW.has(name) ? NEW_NARROW_SUB_S : NEW_BROAD_SUB_S });
  return { ok: true, name };
}

// Fields typed in the template editor. Returns false when the text is not
// acceptable (and leaves the draft alone).
export function setTemplateField(t, field, text) {
  if (field === "name") { t.name = String(text); return true; }
  if (field === "sub_s") {
    const v = parseFloat(text);
    if (!(v > 0 && v <= 3600)) return false;
    t.sub_s = v;
    return true;
  }
  if (field === "gain" || field === "offset") {
    const v = parseGO(text);
    if (v === null) return false;
    t[field] = v;
    return true;
  }
  if (field === "bin") {
    const v = parseInt(text, 10);
    if (!(v >= 1 && v <= 4)) return false;
    t.bin = v;
    return true;
  }
  return false;
}

// --- status and notes ---

const BASE_KEY = { name: "name", sub_s: "defaultexposure", gain: "gain", offset: "offset", bin: "bin" };
const FIELD_LABEL = { name: "Name", sub_s: "Sub", gain: "Gain", offset: "Offset", bin: "Bin" };
const FIELDS = ["name", "sub_s", "gain", "offset", "bin"];
export const STATUS_LABEL = { in_ts: "In TS", new: "New", changed: "Changed", waiting: "Waiting", conflict: "Conflict" };

function sameValue(a, b) {
  if (typeof a === "number" || typeof b === "number") return Math.abs(num(a) - num(b)) < 1e-9;
  return a === b;
}

function fmtField(field, v) {
  if (field === "gain" || field === "offset") return fmtGO(v);
  if (field === "sub_s") return `${fmtNum(num(v))} s`;
  return String(v);
}

// The fields on this draft template that differ from what TS held at the
// last push, for the camera's primary profile.
export function linkBase(t, camera) {
  const pid = primaryProfile(camera)?.profile_id || Object.keys(t.ts || {})[0];
  return pid ? t.ts?.[pid]?.base || null : null;
}

export function changedFields(t, camera) {
  const base = linkBase(t, camera);
  if (!base) return [];
  const out = [];
  for (const f of FIELDS) {
    const b = base[BASE_KEY[f]];
    if (b === undefined || b === null) continue;
    if (!sameValue(t[f], b)) out.push({ field: f, base: b, now: t[f] });
  }
  return out;
}

// The pill a template line shows. The server's status holds, except that an
// in-TS or changed template is judged again against its base, so a field the
// draft has moved (or put back) shows at once.
export function templateStatus(t, camera) {
  if (t._new) return "new";
  if ((t.status === "in_ts" || t.status === "changed") && linkBase(t, camera)) {
    return changedFields(t, camera).length ? "changed" : "in_ts";
  }
  return t.status || "new";
}

export function usedByText(n) {
  const k = num(n);
  return `Used by ${k} plan${k === 1 ? "" : "s"}${k ? ", so it cannot be removed" : ""}.`;
}

export function templateNote(t, camera) {
  const status = templateStatus(t, camera);
  if (status === "conflict") {
    const parts = (t.conflicts || []).map(c =>
      `${FIELD_LABEL[c.field] || c.field} is ${fmtField(c.field, c.acp)} here and ${fmtField(c.field, c.ts)} in TS, and TS changed it after the last sync.`);
    return `${parts.join(" ")} The sync leaves it alone until you choose.`;
  }
  if (status === "changed") {
    const ch = changedFields(t, camera);
    const what = ch.length
      ? ch.map(c => `${FIELD_LABEL[c.field]} changed from ${fmtField(c.field, c.base)} to ${fmtField(c.field, c.now)}`).join(", ")
      : "A setting changed";
    return `${what} here. The next sync updates this template in TS, for every plan that uses it.`;
  }
  if (status === "new") return "Not in TS yet. The next sync creates it, and plans can pick it now.";
  if (status === "waiting") {
    return t.note ? `${t.note} The sync skips it and says so in its notes.` : "The filter is not on the wheel yet. The sync skips it and says so in its notes.";
  }
  return "Matches TS. Edits here update the TS template on the next sync.";
}

// --- what a sub change reaches ---

function planLabel(plan) {
  return plan?.target?.name || plan?.name || plan?.id || "plan";
}

// Rows on a template, from the plans the editor holds. A row follows the
// template when its own sub is the template's saved sub.
export function rowsOnTemplate(plans, camera, t) {
  const out = [];
  for (const plan of plans || []) {
    if (plan.camera_id !== camera.id) continue;
    for (const row of plan.filter_rows || []) {
      const on = row.template_id ? row.template_id === t.id : (!row.template_unresolved && row.template === t.name);
      if (on) out.push({ plan, row });
    }
  }
  return out;
}

// {lines} naming the rows that change and the ones that keep their sub, for
// a sub edit from `oldSub` to `newSub` (section 4 and test U11).
export function subImpact(plans, camera, t, oldSub, newSub) {
  if (!(num(oldSub) > 0) || sameValue(oldSub, newSub)) return { lines: [], follow: 0, withFrames: 0, deviating: 0 };
  const follow = [];
  const deviating = [];
  for (const { plan, row } of rowsOnTemplate(plans, camera, t)) {
    const label = `${planLabel(plan)} ${row.filter}`;
    const sub = num(row.template_sub_s);
    if (Math.abs(sub - num(oldSub)) < 1e-9) {
      const frames = Math.round(num(row.actual_hours) * 3600 / num(oldSub));
      follow.push({ label, frames });
    } else {
      deviating.push({ label, sub });
    }
  }
  const lines = [];
  const withFrames = follow.filter(x => x.frames > 0);
  if (withFrames.length) {
    lines.push(`${withFrames.length} row${withFrames.length === 1 ? " has" : "s have"} frames at ${fmtNum(num(oldSub))} s: `
      + `${withFrames.map(x => `${x.label} ${x.frames} fr`).join(", ")}. Their goals stay in hours, so their frame goals change.`);
  }
  const plain = follow.length - withFrames.length;
  if (plain > 0) lines.push(`${plain} more row${plain === 1 ? "" : "s"} will move to ${fmtNum(num(newSub))} s.`);
  for (const d of deviating) lines.push(`${d.label} keeps its own ${fmtNum(d.sub)} s.`);
  return { lines, follow: follow.length, withFrames: withFrames.length, deviating: deviating.length };
}

// --- the camera card ---

export function summaryLine(camera) {
  const filters = Object.values(camera.filters || {});
  const n = filters.reduce((s, f) => s + (f.templates || []).length, 0);
  return `${filters.length} filter${filters.length === 1 ? "" : "s"} · ${n} template${n === 1 ? "" : "s"}`;
}

// 206.265 x pixel (um) / focal length (mm) gives arcseconds per pixel.
export function fieldOfView(camera, telescope) {
  const focal = num(telescope?.focal_length_mm);
  const px = num(camera?.pixel_size_um);
  const [w, h] = camera?.sensor_px || [0, 0];
  if (!(focal > 0 && px > 0 && num(w) > 0 && num(h) > 0)) return null;
  const scale = 206.265 * px / focal;
  return { scale, width_deg: num(w) * scale / 3600, height_deg: num(h) * scale / 3600 };
}

function fovText(camera, telescope, prefix) {
  const f = fieldOfView(camera, telescope);
  if (!f) return "";
  return `${prefix}: ${f.scale.toFixed(2)}″ per pixel, field ${f.width_deg.toFixed(2)}° × ${f.height_deg.toFixed(2)}°`;
}

export function usualScope(doc, camera) {
  return (doc.telescopes || []).find(t => t.default_camera_id === camera.id) || null;
}

export function telescopeSummary(t) {
  const focal = num(t.focal_length_mm);
  const ap = num(t.aperture_mm);
  if (!(focal > 0)) return { text: "focal length missing", warn: true };
  if (!(ap > 0)) return { text: `${fmtNum(focal)} mm · aperture missing`, warn: true };
  return { text: `${fmtNum(focal)} mm · f/${(focal / ap).toFixed(1)}`, warn: false };
}

export function conflictTooltip(n) {
  return `${n} template setting${n === 1 ? "" : "s"} differ${n === 1 ? "s" : ""} between ACP and TS. Open Gear to choose.`;
}

// The red count on the Gear button, as markup. Empty when nothing is held.
export function gearBadgeHtml(n) {
  const k = num(n);
  return k > 0 ? `<span class="gear-badge" title="${esc(conflictTooltip(k))}">${k}</span>` : "";
}

export function gearButtonTitle(n) {
  return num(n) > 0 ? conflictTooltip(num(n)) : "";
}

// Put the count on the Gear button: replace any old badge and set the
// tooltip. *btn* is the button element, or null when the page has none.
export function refreshGearBadge(btn, n) {
  if (!btn) return;
  btn.querySelector(".gear-badge")?.remove();
  btn.insertAdjacentHTML("beforeend", gearBadgeHtml(n));
  btn.title = gearButtonTitle(n);
}

// What the page keeps of a GET /api/gear answer.
export function gearFromResponse(g) {
  return {
    version: g.version || 2, telescopes: g.telescopes || [], cameras: g.cameras || [],
    telescope_colours: g.telescope_colours || {},
    rev: g.rev ?? 0, conflict_count: g.conflict_count || 0,
    wheel_unlinked: g.wheel_unlinked || [], wheel_ambiguous: g.wheel_ambiguous || [],
    tidy: g.tidy || {}, links_capable: g.links_capable || {},
  };
}

// --- saving ---

// A failed save, shown as a message. A 409 (gear changed on the server) says
// so and offers Reload; the draft in *state.doc* is left exactly as it is.
// *before* is the scan's wording for a failure that is not a 409.
export function applySaveFailure(state, res, before = false) {
  if (res.status === 409) state.message = { text: CONFLICT_MESSAGE, reload: true };
  else state.message = { text: before ? `Couldn't save current edits before scanning: ${res.error}` : `Save failed: ${res.error}` };
  return state;
}

// The Reload action: ask first (*ask* is confirm), and only on a yes fetch the
// server's gear (*fetchGear* returns it) and replace the draft with it.
// Returns whether it reloaded.
export async function reloadDraft(state, ask, fetchGear) {
  if (!ask("Reload gear from the server? Your unsaved edits will be lost.")) return false;
  const gearNow = await fetchGear();
  state.doc = JSON.parse(JSON.stringify({ telescopes: gearNow.telescopes || [], cameras: gearNow.cameras || [] }));
  state.saved = JSON.parse(JSON.stringify(state.doc));
  state.wheelUnlinked = gearNow.wheel_unlinked || [];
  state.message = null;
  state.openTemplate = null;
  return true;
}

const SERVER_TEMPLATE_KEYS = ["status", "conflicts", "used_by", "note", "_new"];

// The POST /api/gear body: the draft with the derived keys gone and the
// temporary ids of new templates left out, so the server makes real ones.
export function buildSavePayload(draft, rev) {
  const cameras = (draft.cameras || []).map(cam => {
    const out = {};
    for (const [k, v] of Object.entries(cam)) {
      if (k === "wheel" || k === "template_edit_blocked" || k === "filters") continue;
      out[k] = JSON.parse(JSON.stringify(v));
    }
    out.filters = {};
    for (const [fname, cfg] of Object.entries(cam.filters || {})) {
      out.filters[fname] = {
        templates: (cfg.templates || []).map(t => {
          const c = JSON.parse(JSON.stringify(t));
          if (t._new) delete c.id;
          for (const k of SERVER_TEMPLATE_KEYS) delete c[k];
          return c;
        }),
      };
    }
    return out;
  });
  return {
    gear_schema: 3, rev,
    telescopes: JSON.parse(JSON.stringify(draft.telescopes || [])),
    cameras,
  };
}

// A new camera for "+ Add camera": the seven standard filters, each with one
// starred template.
export function newCamera(id, name = "New camera") {
  const cam = { id, name, pixel_size_um: 3.76, sensor_px: [6000, 4000], filters: {} };
  for (const f of FILTER_ORDER) {
    cam.filters[f] = { templates: [] };
    addTemplate(cam, f, { name: f, sub_s: NARROW.has(f) ? NEW_NARROW_SUB_S : NEW_BROAD_SUB_S });
  }
  return cam;
}

// A copy of a camera for "Duplicate": no TS links, every template new.
export function duplicateCamera(camera, id) {
  const copy = { id, name: `${camera.name} copy`, pixel_size_um: camera.pixel_size_um, sensor_px: [...(camera.sensor_px || [0, 0])], filters: {} };
  for (const [fname, cfg] of Object.entries(camera.filters || {})) {
    copy.filters[fname] = { templates: [] };
    for (const t of cfg.templates || []) {
      const n = addTemplate(copy, fname, { name: t.name, sub_s: t.sub_s, gain: t.gain, offset: t.offset, bin: t.bin });
      n.starred = t.starred === true;
    }
  }
  return copy;
}

// --- markup ---

function fieldInput(label, inner, wide = false) {
  return `<label class="ge-field${wide ? " ge-field-wide" : ""}">${label}${inner}</label>`;
}

function conflictButtons(t, camera) {
  return (t.conflicts || []).map(c => `<div class="ge-btns">
    <button type="button" class="ge-btn" data-act="resolve" data-tpl="${esc(t.id)}" data-profile="${esc(c.profile_id)}" data-field="${esc(c.field)}" data-take="acp">Keep ACP's (${esc(fmtField(c.field, c.acp))})</button>
    <button type="button" class="ge-btn" data-act="resolve" data-tpl="${esc(t.id)}" data-profile="${esc(c.profile_id)}" data-field="${esc(c.field)}" data-take="ts">Take TS's (${esc(fmtField(c.field, c.ts))})</button>
  </div>`).join("");
}

export function templateEditorHtml(state, camera, filter, t) {
  const blocked = camera.template_edit_blocked === true && !t._new;
  const dis = blocked ? " disabled" : "";
  const list = camera.filters[filter].templates || [];
  const used = num(t.used_by) > 0;
  const last = list.length < 2;
  const saved = state.saved ? findTemplate(state.saved, t.id) : null;
  const impact = saved ? subImpact(state.plans, camera, t, saved.template.sub_s, t.sub_s) : { lines: [] };
  const starredAlready = t.starred === true;
  return `<div class="ge-editor" data-tpl-editor="${esc(t.id)}">
    ${fieldInput("Template name", `<input type="text" data-tpl="${esc(t.id)}" data-field="name" value="${esc(t.name)}" maxlength="64"${dis}>`)}
    <div class="ge-grid4">
      ${fieldInput("Sub (s)", `<input type="number" data-tpl="${esc(t.id)}" data-field="sub_s" value="${esc(fmtNum(num(t.sub_s)))}" min="1" max="3600" step="any"${dis}>`)}
      ${fieldInput("Gain", `<input type="text" data-tpl="${esc(t.id)}" data-field="gain" value="${esc(fmtGO(t.gain))}">`)}
      ${fieldInput("Offset", `<input type="text" data-tpl="${esc(t.id)}" data-field="offset" value="${esc(fmtGO(t.offset))}">`)}
      ${fieldInput("Bin", `<input type="number" data-tpl="${esc(t.id)}" data-field="bin" value="${esc(String(t.bin ?? 1))}" min="1" max="4" step="1">`)}
    </div>
    <label class="ge-check"><input type="checkbox" data-act="star" data-tpl="${esc(t.id)}"${starredAlready ? " checked disabled" : ""}> Use for new plans with this filter</label>
    <div class="ge-impact" data-impact="${esc(t.id)}">${impact.lines.map(l => `<div>${esc(l)}</div>`).join("")}</div>
    <div class="ge-note" data-note="${esc(t.id)}">${esc(templateNote(t, camera))}</div>
    ${templateStatus(t, camera) === "conflict" ? conflictButtons(t, camera) : ""}
    <div class="ge-note">${esc(usedByText(t.used_by))}${last && !used ? " A filter keeps at least one template." : ""}</div>
    <div class="ge-btns">
      <button type="button" class="ge-btn" data-act="dup-tpl" data-tpl="${esc(t.id)}" data-filter="${esc(filter)}">Duplicate</button>
      <button type="button" class="ge-btn ge-danger" data-act="del-tpl" data-tpl="${esc(t.id)}" data-filter="${esc(filter)}"${used || last ? " disabled" : ""}>Remove</button>
    </div>
  </div>`;
}

export function templateLineHtml(state, camera, t) {
  const status = templateStatus(t, camera);
  const open = state.openTemplate === t.id;
  return `<button type="button" class="ge-line" data-act="toggle-tpl" data-tpl="${esc(t.id)}" aria-expanded="${open}">
    <span class="ge-star">${t.starred ? "★" : ""}</span>
    <span class="ge-tname" data-line-name="${esc(t.id)}">${esc(t.name)}</span>
    <span class="ge-tsub" data-line-sub="${esc(t.id)}">${esc(fmtNum(num(t.sub_s)))} s</span>
    <span class="ge-pill ge-pill-${status}" data-line-pill="${esc(t.id)}">${STATUS_LABEL[status] || status}</span>
  </button>`;
}

export function filterBoxHtml(state, camera, filter) {
  const cfg = camera.filters[filter];
  const info = wheelInfo(camera, filter);
  const note = wheelNote(camera, filter);
  const pill = info.state === "not_on_wheel" ? `<span class="ge-pill ge-pill-wheel">Not on the wheel yet</span>`
    : info.state === "partial" ? `<span class="ge-pill ge-pill-wheel">Not on every wheel</span>` : "";
  const lines = (cfg.templates || []).map(t => `<div class="ge-tpl${state.openTemplate === t.id ? " ge-open" : ""}">
    ${templateLineHtml(state, camera, t)}
    ${state.openTemplate === t.id ? templateEditorHtml(state, camera, filter, t) : ""}
  </div>`).join("");
  const removable = filterUsed(camera, filter) === 0;
  return `<div class="ge-box" data-filter-box="${esc(filter)}">
    <div class="ge-box-head">
      <span class="ge-fname">${esc(filter)}</span>
      <span class="ge-wnote">${esc(note)}</span>
      ${pill}
      <button type="button" class="ge-dashed" data-act="add-tpl" data-filter="${esc(filter)}">+ Template</button>
      <button type="button" class="ge-x" data-act="del-filter" data-filter="${esc(filter)}" title="${removable ? "Remove this filter" : "A plan uses this filter's templates"}" aria-label="Remove filter ${esc(filter)}"${removable ? "" : " disabled"}>✕</button>
    </div>
    ${lines}
  </div>`;
}

function addFilterHtml(camera, state) {
  if (state.addFilterFor !== camera.id) {
    return `<button type="button" class="ge-dashed-lg" data-act="open-add-filter">+ Add filter</button>`;
  }
  const missing = wheelFiltersMissing(camera);
  const offers = missing.map(f => `<button type="button" class="ge-btn" data-act="add-filter" data-name="${esc(f)}">${esc(f)}</button>`).join("");
  return `<div class="ge-add-filter">
    ${missing.length ? `<div class="ge-muted">On the wheel, not in gear yet:</div><div class="ge-btns">${offers}</div>` : ""}
    <div class="ge-btns">
      <input type="text" data-role="add-filter-name" placeholder="${missing.length ? "or type a name" : "Filter name"}" maxlength="32" style="flex:1;min-width:0">
      <button type="button" class="ge-btn" data-act="add-filter-typed">Add</button>
      <button type="button" class="ge-btn" data-act="cancel-add-filter">Cancel</button>
    </div>
    <div class="ge-muted ge-warn" data-role="add-filter-error"></div>
  </div>`;
}

export function cameraCardHtml(state, camera) {
  const scope = usualScope(state.doc, camera);
  const fov = scope ? fovText(camera, scope, `On the ${scope.name}`) : "";
  const dims = camera.sensor_px || [0, 0];
  const menu = state.menu === camera.id ? `<div class="ge-menu" role="menu">
      <button type="button" data-act="dup-cam" role="menuitem">Duplicate</button>
      <button type="button" data-act="link-cam" role="menuitem">Link to NINA camera</button>
      <button type="button" class="ge-danger" data-act="del-cam" role="menuitem">Remove</button>
    </div>` : "";
  const boxes = filterOrder(camera).map(f => filterBoxHtml(state, camera, f)).join("");
  const note = camera.template_edit_blocked === true ? `<div class="ge-banner ge-info" data-role="plugin-note">${esc(PLUGIN_NOTE)}</div>` : "";
  return `<div class="ge-card" data-cam="${esc(camera.id)}">
    <div class="ge-card-top">
      <div class="ge-row">
        <button type="button" class="ge-caret" data-act="toggle-cam" aria-label="Close this camera" style="border:0;background:transparent;cursor:pointer">▾</button>
        <input type="text" class="ge-name" data-cam-field="name" value="${esc(camera.name || "")}" aria-label="Camera name" placeholder="Camera name">
        <div class="ge-menu-wrap"><button type="button" class="ge-icon-btn" data-act="cam-menu" aria-label="Camera actions" aria-haspopup="true">⋯</button>${menu}</div>
      </div>
      <div class="ge-grid3">
        ${fieldInput("Pixel (µm)", `<input type="number" data-cam-field="pixel_size_um" value="${esc(camera.pixel_size_um ?? "")}" step="0.01">`)}
        ${fieldInput("Width (px)", `<input type="number" data-cam-field="sensor_w" value="${esc(dims[0] ?? "")}" step="1">`)}
        ${fieldInput("Height (px)", `<input type="number" data-cam-field="sensor_h" value="${esc(dims[1] ?? "")}" step="1">`)}
      </div>
      ${fov ? `<div class="ge-muted" data-role="fov">${esc(fov)}</div>` : ""}
    </div>
    <div class="ge-filters">
      <div class="ge-filters-head"><span class="ge-eyebrow">FILTERS AND TEMPLATES</span><span class="ge-legend">★ = used for new plans</span></div>
      ${note}
      ${boxes}
      ${addFilterHtml(camera, state)}
    </div>
  </div>`;
}

function wheelUnlinkedHtml(state) {
  return (state.wheelUnlinked || []).map(u => `<div class="ge-banner">
    <span>NINA profile '${esc(u.profile_name)}' reports '${esc(u.camera_name)}', which is not in gear.</span>
    <span class="ge-linkrow">
      <button type="button" data-act="link-unlinked" data-name="${esc(u.camera_name)}">Link to…</button>
      <button type="button" data-act="add-unlinked" data-name="${esc(u.camera_name)}">Add as camera</button>
    </span>
  </div>`).join("");
}

export function camerasTabHtml(state) {
  const cams = state.doc.cameras || [];
  const open = cams.find(c => c.id === state.openCamera);
  const items = cams.map(c => c === open
    ? cameraCardHtml(state, c)
    : `<button type="button" class="ge-summary" data-act="open-cam" data-cam="${esc(c.id)}"><span class="ge-caret">▸</span><span class="ge-sname">${esc(c.name || "Unnamed camera")}</span><span class="ge-muted">${esc(summaryLine(c))}</span></button>`).join("");
  return `${wheelUnlinkedHtml(state)}${items}<button type="button" class="ge-dashed-lg" data-act="add-cam">+ Add camera</button>`;
}

export function telescopeCardHtml(state, t) {
  const cams = state.doc.cameras || [];
  const cam = cams.find(c => c.id === t.default_camera_id);
  const focal = num(t.focal_length_mm);
  const ap = num(t.aperture_mm);
  const ratio = focal > 0 && ap > 0 ? `f/${(focal / ap).toFixed(1)}` : "";
  const fov = cam ? fovText(cam, t, `with the ${cam.name}`) : "";
  const line = [ratio, fov].filter(Boolean).join(" · ");
  const colour = state.colourOf ? state.colourOf(t.name || "") : "#6b7a90";
  const options = [`<option value="">No usual camera</option>`]
    .concat(cams.map(c => `<option value="${esc(c.id)}"${c.id === t.default_camera_id ? " selected" : ""}>${esc(c.name)}</option>`)).join("");
  return `<div class="ge-card" data-tel="${esc(t.id)}" style="padding:10px 12px;display:flex;flex-direction:column;gap:8px">
    <div class="ge-row">
      <input type="color" data-tel-colour="${esc(t.id)}" value="${esc(colour)}" aria-label="Outline colour" title="Colour for this telescope. Saved as soon as you pick it.">
      <input type="text" class="ge-name" data-tel-field="name" value="${esc(t.name || "")}" aria-label="Telescope name" placeholder="Telescope name">
      <div class="ge-menu-wrap"><button type="button" class="ge-icon-btn" data-act="tel-menu" aria-label="Telescope actions" aria-haspopup="true">⋯</button>${state.menu === `tel:${t.id}` ? `<div class="ge-menu" role="menu"><button type="button" class="ge-danger" data-act="del-tel" role="menuitem">Remove</button></div>` : ""}</div>
    </div>
    <div class="ge-grid3">
      ${fieldInput("Focal (mm)", `<input type="number" data-tel-field="focal_length_mm" value="${esc(t.focal_length_mm ?? "")}" step="1">`)}
      ${fieldInput("Aperture (mm)", `<input type="number" data-tel-field="aperture_mm" value="${esc(t.aperture_mm ?? "")}" step="1">`)}
      ${fieldInput("Usual camera", `<select data-tel-field="default_camera_id">${options}</select>`)}
    </div>
    <div class="ge-muted" data-role="tel-line">${esc(line)}</div>
  </div>`;
}

export function telescopesTabHtml(state) {
  const tels = state.doc.telescopes || [];
  const items = tels.map(t => {
    if (t.id === state.openTelescope) return telescopeCardHtml(state, t);
    const s = telescopeSummary(t);
    const colour = state.colourOf ? state.colourOf(t.name || "") : "#6b7a90";
    return `<button type="button" class="ge-summary" data-act="open-tel" data-tel="${esc(t.id)}"><span class="ge-swatch" style="background:${esc(colour)}"></span><span class="ge-sname">${esc(t.name || "Unnamed telescope")}</span><span class="ge-muted${s.warn ? " ge-warn" : ""}">${esc(s.text)}</span></button>`;
  }).join("");
  return `${items}<button type="button" class="ge-dashed-lg" data-act="add-tel">+ Add telescope</button>`;
}

// The whole editor for #panelBody.
export function gearEditorHtml(state) {
  const nT = (state.doc.telescopes || []).length;
  const nC = (state.doc.cameras || []).length;
  const msg = state.message
    ? `<div class="ge-banner" role="alert" data-role="message"><span>${esc(state.message.text)}</span>${state.message.reload ? `<button type="button" data-act="reload">Reload</button>` : ""}</div>`
    : "";
  return `<div class="ge" data-gear-editor>
    <div class="ge-head">
      <a class="back-link" href="#" data-act="back">← Back to plans</a>
      <h2>Gear</h2>
      <button type="button" class="ge-save" data-act="save">Save</button>
    </div>
    <div class="ge-tabs" role="tablist">
      <button type="button" class="ge-tab" role="tab" data-act="tab" data-tab="telescopes" aria-selected="${state.tab === "telescopes"}">Telescopes · ${nT}</button>
      <button type="button" class="ge-tab" role="tab" data-act="tab" data-tab="cameras" aria-selected="${state.tab === "cameras"}">Cameras · ${nC}</button>
    </div>
    <div class="ge-body">
      ${msg}
      ${state.tab === "telescopes" ? telescopesTabHtml(state) : camerasTabHtml(state)}
      <button type="button" class="ge-btn" data-act="scan" style="align-self:flex-start" title="Scan the coverage manifest for telescopes and cameras not yet in gear">Scan coverage</button>
    </div>
  </div>`;
}

