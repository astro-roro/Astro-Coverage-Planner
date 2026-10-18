// The "What's here?" panel: runs the search for the part of the sky the map
// shows and lists what it found. What each source returns and how results are
// merged and filtered lives in sky-search.mjs. app.js passes in the few things
// only it can do: read the view, move the map, draw on it, start a plan.

import { esc } from "./activity.mjs";
import {
  DEFAULT_MIN_PX,
  GROUPS,
  MAX_RADIUS_DEG,
  VIZIER_CATALOGUES,
  dedupe,
  filterBySize,
  formatArcmin,
  listRigs,
  minSizeArcmin,
  searchAll,
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

function loadPrefs() {
  try {
    const s = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
    return {
      rigId: typeof s.rigId === "string" ? s.rigId : null,
      minPx: Number.isFinite(s.minPx) && s.minPx >= 0 ? s.minPx : DEFAULT_MIN_PX,
      sort: SORTS.some(o => o.id === s.sort) ? s.sort : "type",
    };
  } catch {
    return { rigId: null, minPx: DEFAULT_MIN_PX, sort: "type" };
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

  function drawResults() {
    const host = root?.querySelector("#ssResults");
    if (!host || !results) return;
    const { rig } = rigsAndPick();
    const minArcmin = rig ? minSizeArcmin(rig.scale, prefs.minPx) : 0;
    const all = withDistances(dedupe(results.items), results.centre);
    const shown = sorted(filterBySize(all, minArcmin));
    results.shown = shown;
    deps.overlay.showAll(shown);

    const hidden = all.length - shown.length;
    const unknown = shown.filter(o => o.major === null).length;
    const lines = [`${shown.length} shown`];
    if (hidden > 0) lines.push(`${hidden} hidden as smaller than ${prefs.minPx} px (${formatArcmin(minArcmin)})`);
    if (unknown > 0) lines.push(`${unknown} with no size on file`);
    let html = `<div class="ss-count">${esc(lines.join(DOT))}</div>`;
    if (!shown.length) {
      html += `<div class="tr-empty">Nothing found${all.length ? " at this size. Lower the minimum or pick Any size." : " in this view."}</div>`;
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
  }

  function drawStatus(reports, waiting) {
    const el = root?.querySelector("#ssStatus");
    if (!el) return;
    const chips = reports.map(r => r.ok
      ? `<span class="ss-chip ok" title="${r.truncated ? "Hit the row limit: the smallest objects are missing" : ""}">${esc(r.label)} ${r.items.length}${r.truncated ? "+" : ""}</span>`
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
        <label><span class="lab">Sort</span><select id="ssSort">${SORTS.map(s => `<option value="${s.id}"${s.id === prefs.sort ? " selected" : ""}>${esc(s.label)}</option>`).join("")}</select></label>
        <button type="button" class="primary" id="ssAgain">Search this view</button>
      </div>
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
