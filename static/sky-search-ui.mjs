// The "What's here?" panel: runs the search for the part of the sky the map
// shows and lists what it found. What each source returns and how results are
// merged and filtered lives in sky-search.mjs. app.js passes in the few things
// only it can do: read the view, move the map, draw on it, start a plan.

import { esc } from "./activity.mjs";
import {
  CATALOGUE_CHIPS,
  DEFAULT_MIN_PX,
  FILL_PRESETS,
  GROUPS,
  MAX_RADIUS_DEG,
  TYPE_BUCKETS,
  VIZIER_CATALOGUES,
  applyFilters,
  catalogueIds,
  countBy,
  dedupe,
  fillRange,
  formatArcmin,
  listRigs,
  minSizeArcmin,
  searchAll,
  typeBucket,
  viewCone,
  withDistances,
} from "./sky-search.mjs";

const PREFS_KEY = "acp.skySearch.v1";
const ANY_RIG = "any";
const SOURCE_COUNT = 1 + VIZIER_CATALOGUES.length;
const DOT = " · ";
const SORTS = [
  { id: "type", label: "By type" },
  { id: "size", label: "Biggest first" },
  { id: "dist", label: "Nearest the centre" },
  { id: "name", label: "Name" },
];

const DEFAULT_PREFS = {
  rigId: null, minPx: DEFAULT_MIN_PX, sort: "type",
  typesOff: [], catsOff: [], fillPreset: "any", fillMin: "", fillMax: "", showUnknown: true,
};

// Chips are remembered as the ones switched off, so a type or catalogue added
// later starts on.
function loadPrefs() {
  try {
    const s = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
    const ids = (v, all) => (Array.isArray(v) ? v.filter(x => all.includes(x)) : []);
    return {
      rigId: typeof s.rigId === "string" ? s.rigId : null,
      minPx: Number.isFinite(s.minPx) && s.minPx >= 0 ? s.minPx : DEFAULT_MIN_PX,
      sort: SORTS.some(o => o.id === s.sort) ? s.sort : "type",
      typesOff: ids(s.typesOff, TYPE_BUCKETS.map(t => t.id)),
      catsOff: ids(s.catsOff, [...CATALOGUE_CHIPS.map(c => c.id), "other"]),
      fillPreset: s.fillPreset === "custom" || FILL_PRESETS[s.fillPreset] ? s.fillPreset : "any",
      fillMin: typeof s.fillMin === "string" ? s.fillMin : "",
      fillMax: typeof s.fillMax === "string" ? s.fillMax : "",
      showUnknown: s.showUnknown !== false,
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function savePrefs(p) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch { /* storage off */ }
}

const fmtDeg = v => `${Number(v).toFixed(v >= 10 ? 1 : 2)}°`;
const fmtDist = arcmin => (arcmin >= 100 ? `${(arcmin / 60).toFixed(1)}°` : `${Math.round(arcmin)}'`);

// deps:
//   getView()            -> { centre: [ra, dec], fov: [w, h] } in degrees, or null
//   getGear()            -> the gear object (telescopes, cameras)
//   host()               -> the element the panel is drawn into
//   onBack()             -> restore whatever the panel showed before
//   goTo(ra, dec)        -> move the map
//   overlay              -> { showAll(items), showPick(item), clear() }
//   addPlan(item, rig)   -> start a plan at the object; rig is null for "any size"
export function setupSkySearch(deps) {
  let root = null;
  let results = null;      // { cone, centre, items, shown }
  let runId = 0;
  let observer = null;
  let prefs = loadPrefs();

  function close() {
    runId++;
    observer?.disconnect();
    observer = null;
    root = null;
    results = null;
    deps.overlay.clear();
  }

  // The panel closes without a click on Back when something else draws in its
  // place or the side panel is hidden. Clear the map when that happens.
  function watchPanel() {
    observer?.disconnect();
    const panel = document.getElementById("sidePanel");
    if (!panel || typeof MutationObserver === "undefined") return;
    observer = new MutationObserver(() => {
      if (!root || !root.isConnected || root.closest("[hidden]")) close();
    });
    observer.observe(panel, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
  }

  function rigsAndPick() {
    const rigs = listRigs(deps.getGear());
    let pick = prefs.rigId === ANY_RIG ? ANY_RIG : rigs.find(r => r.id === prefs.rigId)?.id;
    if (!pick) pick = rigs[0]?.id ?? ANY_RIG;
    return { rigs, pick, rig: rigs.find(r => r.id === pick) || null };
  }

  function sorted(items) {
    const by = {
      size: (a, b) => (b.major ?? -1) - (a.major ?? -1),
      dist: (a, b) => a.distArcmin - b.distArcmin,
      name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }),
    };
    return [...items].sort(by[prefs.sort === "type" ? "size" : prefs.sort]);
  }

  function rowHtml(o, i, rig) {
    const aliases = o.aliases.slice(0, 4);
    const more = o.aliases.length - aliases.length;
    const wide = rig && o.major !== null && o.major > Math.min(rig.fovW, rig.fovH);
    const meta = [o.typeLabel, o.catalogues.join(", "), `${fmtDist(o.distArcmin)} from centre`];
    if (wide) meta.push("wider than your frame");
    return `<div class="ss-row" data-i="${i}">
      <div class="ss-main">
        <span class="ss-name">${esc(o.name)}</span>
        <span class="ss-size${o.major === null ? " unknown" : ""}">${esc(formatArcmin(o.major))}</span>
      </div>
      <div class="ss-meta">${esc(meta.join(DOT))}</div>
      ${aliases.length ? `<div class="ss-alias">also ${esc(aliases.join(", "))}${more > 0 ? ` +${more}` : ""}</div>` : ""}
      <div class="ss-actions">
        <button type="button" class="link-btn" data-act="centre">Centre</button>
        <button type="button" class="link-btn" data-act="outline">Outline</button>
        <button type="button" class="link-btn" data-act="plan">+ Plan</button>
      </div>
    </div>`;
  }

  function filterState(rig) {
    return {
      minArcmin: rig ? minSizeArcmin(rig.scale, prefs.minPx) : 0,
      fill: rig && prefs.fillPreset !== "any" ? fillRange(prefs.fillPreset, prefs.fillMin, prefs.fillMax) : null,
      shortSideArcmin: rig ? Math.min(rig.fovW, rig.fovH) : 0,
      showUnknown: prefs.showUnknown,
      typesOff: new Set(prefs.typesOff),
      catsOff: new Set(prefs.catsOff),
    };
  }

  // Each chip counts what the other filters leave, so its number is what
  // switching it on would show.
  function chipsHtml(all, f) {
    const typeCounts = countBy(applyFilters(all, f, "types"), o => [typeBucket(o)]);
    const catCounts = countBy(applyFilters(all, f, "cats"), catalogueIds);
    const chip = (kind, id, label, n, off) =>
      `<button type="button" class="ss-chip tog${off ? "" : " on"}" data-kind="${kind}" data-id="${esc(id)}" aria-pressed="${!off}">${esc(label)} ${n || 0}</button>`;
    return `<div class="ss-chiprow"><span class="lab">Type</span>${TYPE_BUCKETS.map(t =>
        chip("type", t.id, t.label, typeCounts.get(t.id), prefs.typesOff.includes(t.id))).join("")}</div>`
      + `<div class="ss-chiprow"><span class="lab">Catalogue</span>${[...CATALOGUE_CHIPS, { id: "other", label: "Other" }].map(c =>
        chip("cat", c.id, c.label, catCounts.get(c.id), prefs.catsOff.includes(c.id))).join("")}</div>`
      + `<button type="button" class="link-btn" id="ssReset">Reset filters</button>`;
  }

  function drawResults() {
    const host = root?.querySelector("#ssResults");
    if (!host || !results) return;
    const { rig } = rigsAndPick();
    const f = filterState(rig);
    const all = withDistances(dedupe(results.items), results.centre);
    const shown = sorted(applyFilters(all, f));
    results.shown = shown;
    deps.overlay.showAll(shown);
    root.querySelector("#ssFilters").innerHTML = chipsHtml(all, f);

    const unknown = shown.filter(o => o.major === null).length;
    const lines = [`Showing ${shown.length} of ${all.length}`];
    if (f.minArcmin > 0) lines.push(`under ${prefs.minPx} px (${formatArcmin(f.minArcmin)}) hidden`);
    if (unknown > 0) lines.push(`${unknown} with no size on file`);
    let html = `<div class="ss-count">${esc(lines.join(DOT))}</div>`;
    if (!shown.length) {
      html += `<div class="tr-empty">Nothing matches${all.length ? ". Loosen a filter above." : " in this view."}</div>`;
    } else if (prefs.sort === "type") {
      for (const g of GROUPS) {
        const rows = shown.map((o, i) => [o, i]).filter(([o]) => o.group === g.id);
        if (!rows.length) continue;
        html += `<h4 class="ss-group">${esc(g.label)} (${rows.length})</h4>`
          + rows.map(([o, i]) => rowHtml(o, i, rig)).join("");
      }
    } else {
      html += shown.map((o, i) => rowHtml(o, i, rig)).join("");
    }
    host.innerHTML = html;
  }

  function drawRigInfo() {
    const el = root?.querySelector("#ssRigInfo");
    if (!el) return;
    const { rig } = rigsAndPick();
    el.textContent = rig
      ? `${rig.scale.toFixed(2)} arcsec/px${DOT}frame ${fmtDeg(rig.fovW / 60)} x ${fmtDeg(rig.fovH / 60)}`
      : "Every object is shown whatever its size.";
    const px = root.querySelector("#ssMinPx");
    if (px) px.disabled = !rig;
    // Fill is a share of the frame, so it means nothing without a rig.
    for (const id of ["#ssFill", "#ssFillMin", "#ssFillMax"]) {
      const el = root.querySelector(id);
      if (el) el.disabled = !rig;
    }
    const custom = root.querySelector("#ssFillCustom");
    if (custom) custom.hidden = prefs.fillPreset !== "custom";
  }

  function drawStatus(reports, waiting) {
    const el = root?.querySelector("#ssStatus");
    if (!el) return;
    const chips = reports.map(r => r.ok
      ? `<span class="ss-chip ok" title="${[r.truncated ? "Hit the row limit: the smallest objects are missing." : "", r.identFailed ? "Messier and NGC names could not be fetched, so the catalogue chips may miss some." : ""].join(" ").trim()}">${esc(r.label)} ${r.items.length}${r.truncated ? "+" : ""}</span>`
      : `<span class="ss-chip bad" title="${esc(r.error)}">${esc(r.label)} failed</span>`);
    if (waiting > 0) chips.push(`<span class="ss-chip">${waiting} still searching</span>`);
    el.innerHTML = chips.join("");
  }

  async function run() {
    if (!root) return;
    const id = ++runId;
    const body = root.querySelector("#ssResults");
    const view = deps.getView();
    const cone = view && viewCone(view.centre, view.fov);
    deps.overlay.clear();
    results = null;
    drawStatus([], 0);
    if (!cone) {
      body.innerHTML = `<div class="tr-empty">The map view is not ready yet.</div>`;
      return;
    }
    root.querySelector("#ssView").textContent =
      `Centre RA ${view.centre[0].toFixed(3)}° Dec ${view.centre[1].toFixed(3)}°${DOT}view ${fmtDeg(view.fov[0])} x ${fmtDeg(view.fov[1])}`;
    if (cone.tooWide) {
      body.innerHTML = `<div class="tr-empty">This view is too wide to search (${fmtDeg(cone.radiusDeg)} from centre to corner, the limit is ${MAX_RADIUS_DEG}°). Zoom in on the map, then search again.</div>`;
      return;
    }
    body.innerHTML = `<div class="tr-empty">Searching...</div>`;
    const reports = [];
    const all = await searchAll(cone, {}, r => {
      if (id !== runId) return;
      reports.push(r);
      drawStatus(reports, SOURCE_COUNT - reports.length);
    });
    if (id !== runId || !root) return;
    drawStatus(all, 0);
    results = { cone, centre: view.centre, items: all.flatMap(r => r.items) };
    if (all.every(r => !r.ok)) {
      body.innerHTML = `<div class="tr-empty">No source answered. Check the connection and search again.</div>`;
      return;
    }
    drawResults();
  }

  function open() {
    const host = deps.host();
    if (!host) return;
    prefs = loadPrefs();
    const { rigs, pick } = rigsAndPick();
    const fillOpts = [...Object.entries(FILL_PRESETS).map(([id, p]) => [id, p.label]), ["custom", "Custom"]]
      .map(([id, label]) => `<option value="${id}"${id === prefs.fillPreset ? " selected" : ""}>${esc(label)}</option>`).join("");
    const rigOpts = rigs.map(r => `<option value="${esc(r.id)}"${r.id === pick ? " selected" : ""}>${esc(r.label)}</option>`).join("")
      + `<option value="${ANY_RIG}"${pick === ANY_RIG ? " selected" : ""}>Any size</option>`;
    host.innerHTML = `<div id="skySearchRoot" class="ss-root">
      <a class="back-link" id="ssBack" href="#">← Back to where you were</a>
      <h3>What's here?</h3>
      <div class="ss-view" id="ssView"></div>
      <div class="ss-controls plan-form">
        <label><span class="lab">Rig</span><select id="ssRig">${rigOpts}</select></label>
        <label><span class="lab">Hide under</span><input type="number" id="ssMinPx" min="0" step="5" value="${esc(prefs.minPx)}"> px</label>
        <div class="ss-rig-info" id="ssRigInfo"></div>
        <label><span class="lab">Fills frame</span><select id="ssFill">${fillOpts}</select></label>
        <div id="ssFillCustom" class="ss-fill-custom" hidden>
          <input type="number" id="ssFillMin" min="0" step="5" placeholder="min" value="${esc(prefs.fillMin)}"> to
          <input type="number" id="ssFillMax" min="0" step="5" placeholder="max" value="${esc(prefs.fillMax)}"> % of the short side
        </div>
        <label><span class="lab">Size unknown</span><input type="checkbox" id="ssUnknown"${prefs.showUnknown ? " checked" : ""}> include</label>
        <label><span class="lab">Sort</span><select id="ssSort">${SORTS.map(s => `<option value="${s.id}"${s.id === prefs.sort ? " selected" : ""}>${esc(s.label)}</option>`).join("")}</select></label>
        <button type="button" class="primary" id="ssAgain">Search this view</button>
      </div>
      <div id="ssFilters" class="ss-filters"></div>
      <div class="ss-status" id="ssStatus"></div>
      <div id="ssResults"></div>
    </div>`;
    root = host.querySelector("#skySearchRoot");
    drawRigInfo();
    watchPanel();

    root.querySelector("#ssBack").addEventListener("click", e => {
      e.preventDefault();
      close();
      deps.onBack();
    });
    root.querySelector("#ssAgain").addEventListener("click", run);
    root.querySelector("#ssRig").addEventListener("change", e => {
      prefs.rigId = e.target.value;
      savePrefs(prefs);
      drawRigInfo();
      drawResults();
    });
    root.querySelector("#ssMinPx").addEventListener("change", e => {
      const v = Number(e.target.value);
      prefs.minPx = Number.isFinite(v) && v >= 0 ? v : DEFAULT_MIN_PX;
      e.target.value = prefs.minPx;
      savePrefs(prefs);
      drawResults();
    });
    root.querySelector("#ssFill").addEventListener("change", e => {
      prefs.fillPreset = e.target.value;
      savePrefs(prefs);
      drawRigInfo();
      drawResults();
    });
    for (const [id, key] of [["#ssFillMin", "fillMin"], ["#ssFillMax", "fillMax"]]) {
      root.querySelector(id).addEventListener("change", e => {
        prefs[key] = e.target.value;
        savePrefs(prefs);
        drawResults();
      });
    }
    root.querySelector("#ssUnknown").addEventListener("change", e => {
      prefs.showUnknown = e.target.checked;
      savePrefs(prefs);
      drawResults();
    });
    root.querySelector("#ssFilters").addEventListener("click", e => {
      if (e.target.closest("#ssReset")) {
        prefs = { ...prefs, typesOff: [], catsOff: [], fillPreset: "any", fillMin: "", fillMax: "", showUnknown: true };
        root.querySelector("#ssFill").value = "any";
        root.querySelector("#ssFillMin").value = "";
        root.querySelector("#ssFillMax").value = "";
        root.querySelector("#ssUnknown").checked = true;
        savePrefs(prefs);
        drawRigInfo();
        drawResults();
        return;
      }
      const chip = e.target.closest("button[data-kind]");
      if (!chip) return;
      const key = chip.dataset.kind === "type" ? "typesOff" : "catsOff";
      const off = new Set(prefs[key]);
      if (off.has(chip.dataset.id)) off.delete(chip.dataset.id); else off.add(chip.dataset.id);
      prefs[key] = [...off];
      savePrefs(prefs);
      drawResults();
    });
    root.querySelector("#ssSort").addEventListener("change", e => {
      prefs.sort = e.target.value;
      savePrefs(prefs);
      drawResults();
    });
    root.querySelector("#ssResults").addEventListener("click", e => {
      const btn = e.target.closest("button[data-act]");
      const row = e.target.closest(".ss-row");
      if (!btn || !row || !results?.shown) return;
      const o = results.shown[Number(row.dataset.i)];
      if (!o) return;
      if (btn.dataset.act === "centre") deps.goTo(o.ra, o.dec);
      else if (btn.dataset.act === "outline") { deps.goTo(o.ra, o.dec); deps.overlay.showPick(o); }
      else if (btn.dataset.act === "plan") deps.addPlan(o, rigsAndPick().rig);
    });
    run();
  }

  return { open, close, isOpen: () => !!root };
}
