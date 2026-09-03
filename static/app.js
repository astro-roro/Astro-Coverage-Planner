// Astro Coverage Webapp — frontend
// Uses Aladin Lite v3 for sky rendering.

// HTML-escape any string interpolated into innerHTML. Manifest values originate
// from FITS headers / filesystem paths, neither of which is trusted input.
function esc(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const FILTER_COLORS = {
  Ha:   "#ff4d4d",
  SII:  "#ffd84d",
  OIII: "#4da6ff",
  L:    "#d0d0d0",
  R:    "#ff8080",
  G:    "#80ff80",
  B:    "#8080ff",
  V:    "#b0b0b0",
  IDAS: "#a0ffd0",
};

// Priority for "deepest filter" coloring (used for legacy fill color)
const FILTER_PRIORITY = ["Ha", "SII", "OIII", "L", "R", "G", "B", "V", "IDAS"];

// Filter dot render order (user-requested: L R G B Ha OIII SII)
const FILTER_DOT_ORDER = ["L", "R", "G", "B", "Ha", "OIII", "SII"];

// Stable palette for telescope colors (ColorBrewer Set1 + extras)
const TELESCOPE_PALETTE = [
  "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
  "#a65628", "#f781bf", "#17becf", "#bcbd22", "#ff1493",
  "#00ced1", "#ffd700", "#8b4513", "#6a5acd", "#2e8b57",
];
const TELESCOPE_FALLBACK = "#888";

let manifest = null;
let aladin = null;
let overlay = null;     // main target footprints (polygons) — polygons themselves are the click/hover target now
let hoverOverlay = null; // transient highlight overlay for the polygon currently under the cursor
let filterBadgeCat = null; // single catalog of "filter badges" (one source per target, custom draw)
let coverageHitList = []; // [{poly, target, corners}] — mirror of overlay for hit-testing
let planHitList = [];     // [{poly, plan, corners}] — mirror of plan overlays for hit-testing
let tileHitList = [];     // [{poly, tile, source_id, corners}] — Inventory tile polygons (Plan 2a)
let selectedTileKey = null; // "<source_id>/<tile_id>" while in tile-detail mode
let hoveredHit = null;    // currently-hovered entry from the active hit list (null otherwise)
let lastClickStack = null; // {ra, dec, ids: [...], cycleIdx} — for repeat-click cycling through overlapping polys
let catOverlays = {};   // catalog overlays (Phase 3)
let selectedFilters = new Set(["Ha", "SII", "OIII"]);
let selectedTelescopes = new Set(); // populated after manifest loads
let telescopeColor = {};  // telescope name → color
let filterLogic = "any";
let minHours = 0;
// Gap-finder: server-side multi-source gap analysis (Phase 4b). Replaces the
// old client-side `Ha ∧ ¬SII` toggle that just filtered the panel list.
let gapEnabled = false;
let gapHave = "Ha";
let gapMissing = "SII";
let gapMinHave = 1.0;
let gapMaxMissing = 0.5;
let gapSourceIds = [];     // explicit selection; empty = "all enabled"
let gapMocLayer = null;    // live A.MOCFromURL overlay for the gap region
// catalog name -> Set of object names that fall inside the active gap MOC.
// When non-empty AND gapEnabled is true, drawCatalogOverlay filters each
// catalog to just these entries — so users see "Find gaps × catalog X" by
// simply ticking catalog X in the Catalogues rail.
let gapNamesByCatalog = {};
let currentSite = { lat: 19.82, lon: -155.47, height: 4205, min_alt_deg: 30 };
let sites = [];                // [{id, name, lat, lon, elev_m?, min_alt_deg?}] from /api/sites
let activeSiteId = null;       // localStorage acp.active_site_id, applied once `sites` is loaded
let timeAware = false;         // localStorage acp.time_aware, default off
let _obsIntervalId = null;     // setInterval handle for the rolling obsNow refresh
let visibilityData = null;     // {site_id, year, targets: {<id>: [12 bins]}} | null
let currentAlts = {};          // {<target_id>: alt_deg} from latest /api/observability — for sort=tonight
let sortBy = "hours";          // "hours" | "best_month" | "up_tonight"
let planSortBy = "priority";   // "priority" | "name" | "panels_up_now" | "peak_panels_month"
let catalogRegistry = [];      // [{id, data_key, label, color, marker, size, ...}] from /api/catalog-registry
let panelMode = "list"; // "list" | "detail" | "plan-list" | "plan-edit"
let searchTokens = [];  // parsed tokens from the search box
let selectedTargetId = null; // target_id while in detail view, null otherwise
let completionFilter = "all"; // "all" | "finished" | "unfinished"
let targetOverrides = {};     // target_id (string) → { finished: bool, updated_at: ... }
let _pressInfo = null;        // {x, y, t, dragged} — last mousedown over the map. The Aladin
                              // "click" event fires on every mouseup whether the user dragged
                              // or not, so we use this to suppress pan-clicks from selecting/deselecting.

// --- Planner mode globals ---
let planningMode = false;      // false = Coverage mode, true = Planning mode
let plans = [];                // [{id, guid, project_name, target: {...}, telescope_id, camera_id, filter_goals, priority, ...}]
let gear = { telescopes: [], cameras: [] };  // /api/gear response (v2)
let livePageEnabled = false;  // /api/publish/config: ACP_PUBLISH_DEST is set on the server
let tsTemplates = { available: false, templates: [] }; // /api/ts-templates response
let selectedPlanId = null;     // currently-edited plan id
let editingPlan = null;        // in-memory copy of the plan under edit (unsaved edits live here)
let planOverlay = null;        // Aladin overlay for plan footprints (solid — plans with data)
let planOverlayDashed = null;  // Aladin overlay for plan footprints (dashed — not-started plans)
let planCenterCat = null;      // Aladin catalog for plan center + rotation handle markers
let dragState = null;          // { mode: "center"|"rotate", planId, start: {x,y}, origin: {...} }

// --- Onboarding banner (shown when the manifest is empty) ---
// The user has just installed but hasn't pointed ACP at their FITS archive
// yet — show a clear "next step" overlay until they build a manifest. The
// dismiss button hides it for the current session only; we don't persist
// the dismissal because seeing the prompt on every load until a manifest
// exists is a useful nag rather than annoying noise.
function setupOnboardingBanner(manifest) {
  const banner = document.getElementById("onboardingBanner");
  if (!banner) return;
  const targetCount = (manifest && manifest.targets || []).length;
  if (targetCount > 0) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  const dismiss = document.getElementById("onboardingDismiss");
  if (dismiss) {
    dismiss.addEventListener("click", () => { banner.hidden = true; }, { once: true });
  }
}

// --- localStorage persistence ---
// Bump the version suffix if the shape of the saved state ever changes
// incompatibly, so stale saves from older clients get ignored.
const UI_STATE_KEY = "acp.uiState.v1";

function loadUiState() {
  try { return JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}") || {}; }
  catch { return {}; }
}

function applyUiStatePreManifest() {
  const s = loadUiState();

  const search = document.getElementById("searchInput");
  if (search && typeof s.search === "string" && s.search) {
    search.value = s.search;
    searchTokens = tokenizeSearch(s.search);
  }

  if (Array.isArray(s.filters)) {
    selectedFilters = new Set(s.filters);
    document.querySelectorAll(".filters input[type=checkbox][data-f]").forEach(cb => {
      cb.checked = selectedFilters.has(cb.dataset.f);
    });
  }

  if (typeof s.filterLogic === "string") {
    filterLogic = s.filterLogic;
    const sel = document.getElementById("filterLogic");
    if (sel) sel.value = s.filterLogic;
  }

  if (typeof s.minHours === "number") {
    minHours = s.minHours;
    const slider = document.getElementById("depthSlider");
    if (slider) slider.value = String(s.minHours);
    const depthVal = document.getElementById("depthValue");
    if (depthVal) depthVal.textContent = `${minHours}h`;
  }

  // Gap-finder restore. The gap-finder DOM (selects, source list) hasn't been
  // populated yet at this point — populateGapDropdowns / populateGapSources will
  // see these state vars and pick the right values when they run.
  if (s.gap && typeof s.gap === "object") {
    if (typeof s.gap.have === "string") gapHave = s.gap.have;
    if (typeof s.gap.missing === "string") gapMissing = s.gap.missing;
    if (typeof s.gap.minHave === "number") gapMinHave = s.gap.minHave;
    if (typeof s.gap.maxMissing === "number") gapMaxMissing = s.gap.maxMissing;
    if (Array.isArray(s.gap.sourceIds)) gapSourceIds = s.gap.sourceIds.slice();
    // `enabled` is handled after the manifest loads (see init()).
  }

  if (typeof s.projection === "string" && s.projection) {
    const proj = document.getElementById("projSel");
    if (proj) proj.value = s.projection;
    if (aladin) aladin.setProjection(s.projection);
  }

  if (typeof s.imageSurvey === "string" && s.imageSurvey && aladin?.setImageSurvey) {
    try { aladin.setImageSurvey(surveyLayerFor(s.imageSurvey)); } catch { /* unknown id, keep default */ }
    syncSkyControl(s.imageSurvey);
  }

  if (typeof s.frame === "string" && s.frame) {
    const fr = document.getElementById("frameSel");
    if (fr) fr.value = s.frame;
    if (aladin) aladin.setFrame(s.frame);
  }

  // Active site is loaded from localStorage in initSites() once /api/sites
  // resolves; legacy `s.site` payloads from older builds are ignored.

  if (Array.isArray(s.catalogs)) {
    for (const id of catalogDomIds()) {
      const cb = document.getElementById(id);
      if (cb) cb.checked = s.catalogs.includes(id);
    }
    // The Objects panel's type-chip set depends on which catalogues
    // are enabled — refresh after restoring their checked state so
    // the chips show the right categories on first paint.
    if (typeof refreshObjectFilterPanel === "function") {
      refreshObjectFilterPanel();
    }
  }

  if (typeof s.completionFilter === "string"
      && ["all", "finished", "unfinished"].includes(s.completionFilter)) {
    completionFilter = s.completionFilter;
    const radio = document.querySelector(`input[name=completionFilter][value=${s.completionFilter}]`);
    if (radio) radio.checked = true;
  }

  if (s.accordions && typeof s.accordions === "object") {
    for (const [id, open] of Object.entries(s.accordions)) {
      const el = document.getElementById(id);
      if (el && "open" in el) el.open = !!open;
    }
  }

  if (s.planningMode === true) {
    planningMode = true;
  }
}

function applyUiStatePostManifest() {
  const s = loadUiState();

  if (Array.isArray(s.telescopes)) {
    const available = new Set(selectedTelescopes);
    selectedTelescopes = new Set(s.telescopes.filter(n => available.has(n)));
    document.querySelectorAll("input[data-telescope]").forEach(cb => {
      cb.checked = selectedTelescopes.has(cb.dataset.telescope);
    });
  }

  if (s.selectedTargetId != null && manifest) {
    const t = manifest.targets.find(x => x.target_id === s.selectedTargetId);
    if (t) renderTargetPanel(t);
  }
}

// --- Sky survey dropdown (#skySel) + caption (#skyCaption) ---
// A curated, described list of HiPS surveys (static/surveys.mjs) offered in
// place of Aladin's own layers control, which lists hundreds by id and only
// explains them on hover. Aladin's control still works; a survey picked
// there that is not in our table shows as "Custom" with an unknown-coverage
// chip rather than a made-up description.

function buildSkyControl() {
  const sel = document.getElementById("skySel");
  if (!sel) return;
  sel.innerHTML = "";
  for (const group of SURVEY_GROUPS) {
    const og = document.createElement("optgroup");
    og.label = group.label;
    for (const s of group.surveys) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      og.appendChild(opt);
    }
    sel.appendChild(og);
  }
  const custom = document.createElement("option");
  custom.value = CUSTOM_SURVEY_VALUE;
  custom.textContent = "Custom (chosen in Aladin)";
  custom.hidden = true;
  sel.appendChild(custom);
  sel.value = DEFAULT_SURVEY_ID;
  syncSkyControl(DEFAULT_SURVEY_ID);
}

// What to hand aladin.setImageSurvey for a survey id: the id itself, or a
// HiPS object carrying a colour map for single-band surveys that would
// otherwise render in greyscale.
function surveyLayerFor(id) {
  const opts = surveyRenderOptions(id);
  if (opts && typeof A !== "undefined" && A?.imageHiPS) {
    try { return A.imageHiPS(id, opts); } catch (err) { console.warn("imageHiPS failed", id, err); }
  }
  return id;
}

// Open Aladin's HiPS browser (search plus a folder tree of every survey)
// with the base layer as its target, so a pick there replaces the sky.
// Aladin has no public call for this, so we drive its own controls: open
// the stack panel, choose "More..." in the base-layer select, then close
// the stack panel so only the browser is left. If Aladin's markup has
// changed and any step fails, the stack panel stays open as the fallback.
async function openAladinSurveyBrowser() {
  const root = document.getElementById("aladin-lite-div");
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const stackBtn = root?.querySelector(".aladin-stack-control button");
  if (!stackBtn) return;
  let stack = root.querySelector(".aladin-stack-box");
  if (!stack || !stack.offsetParent) stackBtn.click();
  await wait(50);
  stack = root.querySelector(".aladin-stack-box");
  const sel = stack?.querySelector("select");
  const more = sel && [...sel.options].find(o => o.value === "More...");
  if (!more) return;
  sel.value = more.value;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
  await wait(50);
  const browser = root.querySelector(".aladin-HiPS-browser-box");
  if (browser && stack.offsetParent) stackBtn.click();
}

function currentSurveyId() {
  try {
    const layer = aladin?.getBaseImageLayer?.();
    return layer?.id || layer?.url || "";
  } catch { return ""; }
}

// Point the dropdown and caption at `id` (or at whatever Aladin currently
// shows when `id` is omitted). Safe to call before Aladin is up.
function syncSkyControl(id) {
  const sel = document.getElementById("skySel");
  const chip = document.getElementById("skyCoverage");
  const text = document.getElementById("skyCaptionText");
  const surveyId = id || currentSurveyId();
  if (sel) {
    const value = surveySelectValue(surveyId);
    const customOpt = sel.querySelector(`option[value="${CUSTOM_SURVEY_VALUE}"]`);
    if (customOpt) customOpt.hidden = value !== CUSTOM_SURVEY_VALUE;
    sel.value = value;
  }
  const cap = surveyCaption(surveyId);
  if (chip) {
    chip.textContent = cap.chip;
    chip.classList.toggle("full", cap.chipFull);
    chip.classList.toggle("partial", !cap.chipFull);
  }
  if (text) text.textContent = cap.text;
}

function saveUiState() {
  try {
    // Prefer the Sky dropdown: it is set synchronously when the user picks a
    // survey, whereas Aladin reports the new base layer only once the tiles
    // resolve. A save in that window (or before Aladin is up at all) would
    // otherwise blank the stored survey. Custom means the user chose in
    // Aladin's own control, so fall through to what Aladin reports.
    let imageSurvey = "";
    const skySel = document.getElementById("skySel");
    if (skySel && skySel.value && skySel.value !== CUSTOM_SURVEY_VALUE) {
      imageSurvey = skySel.value;
    } else {
      imageSurvey = currentSurveyId();
    }
    const state = {
      search: document.getElementById("searchInput")?.value || "",
      filters: [...selectedFilters],
      filterLogic,
      minHours,
      telescopes: [...selectedTelescopes],
      catalogs: catalogDomIds().filter(id => document.getElementById(id)?.checked),
      projection: document.getElementById("projSel")?.value || "",
      frame: document.getElementById("frameSel")?.value || "",
      imageSurvey,
      gap: {
        have: gapHave,
        missing: gapMissing,
        minHave: gapMinHave,
        maxMissing: gapMaxMissing,
        sourceIds: gapSourceIds.slice(),
        enabled: gapEnabled,
      },
      selectedTargetId,
      planningMode,
      selectedPlanId,
      completionFilter,
      accordions: {
        railFilters: !!document.getElementById("railFilters")?.open,
        railSources: !!document.getElementById("railSources")?.open,
        railCatalogs: !!document.getElementById("railCatalogs")?.open,
      },
      sources: sourcesEnabled,
      extToggles: extToggleState,
    };
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(state));
  } catch { /* localStorage full / disabled — ignore */ }
}

// Show the search box only while browsing a top-level list (targets or plans).
// Inside a single target/plan/gear editor there's nothing to search for, so
// we hide it to reclaim vertical space for the detail content.
function updateSearchVisibility() {
  const wrap = document.getElementById("panelSearchWrap");
  if (!wrap) return;
  const topLevel = panelMode === "list" || panelMode === "plan-list";
  wrap.style.display = topLevel ? "" : "none";
}

// --- Search tokenizer + matchers ---
// Lifted into ./search.mjs so tests/frontend/ can import them via
// `node --test`. Re-bound here so the rest of app.js can keep using the
// bare names without churning every call site.
import {
  SEARCH_KV_KEYS,
  SEARCH_CMP_KEYS,
  tokenizeSearch,
  catalogObjectMatchesTokens,
  targetMatchesSearch,
} from "./search.mjs";
import {
  CUSTOM_SURVEY_VALUE,
  DEFAULT_SURVEY_ID,
  SURVEY_GROUPS,
  surveyCaption,
  surveyRenderOptions,
  surveySelectValue,
} from "./surveys.mjs";
import {
  ALADIN_INIT_TIMEOUT_MS,
  describeInitError,
  startupError,
  webgl2Available,
  withTimeout,
} from "./init-error.mjs";

function deepestFilter(filters, minH = 0) {
  for (const f of FILTER_PRIORITY) {
    if (filters[f] && filters[f].total_hours >= Math.max(minH, 0.1)) return f;
  }
  return null;
}

// Custom shape drawer for the filter-coverage badge.
// Builds a local 2-D basis from the anchor corner + top-edge corner + inside
// corner, so the badge rotates with the FOV on screen and lives inside the
// polygon regardless of projection or orientation. Also caps badge width so
// it never exceeds the FOV's on-screen top-edge length.
function filterBadgeShape(src, ctx /*, viewParams */) {
  const DOT_R_BASE = 3.5;
  const DOT_GAP_BASE = 2;
  const PAD_X_BASE = 4;
  const PAD_Y_BASE = 3;
  const n = FILTER_DOT_ORDER.length;
  const baseW = PAD_X_BASE * 2 + n * (DOT_R_BASE * 2) + (n - 1) * DOT_GAP_BASE;
  const baseH = PAD_Y_BASE * 2 + DOT_R_BASE * 2;

  const sx = src.x ?? 0;
  const sy = src.y ?? 0;

  // Default basis: axis-aligned, top-edge along +X, inside along +Y.
  let ex = 1, ey = 0;   // edge unit vector (along top)
  let ix = 0, iy = 1;   // inside unit vector (into FOV from top edge)
  let topLenPx = baseW; // length of the top edge on screen

  try {
    const edge = src.data?.edge;
    const inside = src.data?.inside;
    if (edge && inside && typeof aladin !== "undefined" && aladin?.world2pix) {
      const ep = aladin.world2pix(edge[0], edge[1]);
      const ip = aladin.world2pix(inside[0], inside[1]);
      if (ep && ip && isFinite(ep[0]) && isFinite(ip[0])) {
        const edx = ep[0] - sx, edy = ep[1] - sy;
        const eLen = Math.hypot(edx, edy);
        if (eLen > 4) {
          ex = edx / eLen; ey = edy / eLen;
          topLenPx = eLen;
        }
        const idx = ip[0] - sx, idy = ip[1] - sy;
        const iLen = Math.hypot(idx, idy);
        if (iLen > 4) {
          ix = idx / iLen; iy = idy / iLen;
        }
      }
    }
  } catch (e) { /* fall back to default axis-aligned basis */ }

  // Scale so the badge fits inside the top edge (leave 10% margin).
  const maxW = Math.max(22, topLenPx * 0.90);
  const scale = Math.min(1.0, maxW / baseW);
  if (scale < 0.35) return; // polygon too small to host a legible badge

  const PAD_X = PAD_X_BASE * scale;
  const PAD_Y = PAD_Y_BASE * scale;
  const DOT_R = DOT_R_BASE * scale;
  const DOT_GAP = DOT_GAP_BASE * scale;
  const boxW = baseW * scale;
  const boxH = baseH * scale;
  const inset = 2 * scale;

  ctx.save();
  // transform(a, b, c, d, e, f) MULTIPLIES the current canvas matrix by:
  //   | a c e |
  //   | b d f |
  //   | 0 0 1 |
  // with (a,b) = edge unit vector (local +X → screen), (c,d) = inside unit vector.
  // We use transform() rather than setTransform() so Aladin's existing canvas
  // matrix (device-pixel-ratio, etc.) is preserved.
  ctx.translate(sx, sy);
  ctx.transform(ex, ey, ix, iy, 0, 0);

  // Rounded-rect background
  const r = 3 * scale;
  ctx.beginPath();
  ctx.moveTo(inset + r,           inset);
  ctx.arcTo(inset + boxW,         inset,
           inset + boxW,          inset + boxH, r);
  ctx.arcTo(inset + boxW,         inset + boxH,
           inset,                 inset + boxH, r);
  ctx.arcTo(inset,                inset + boxH,
           inset,                 inset,        r);
  ctx.arcTo(inset,                inset,
           inset + boxW,          inset,        r);
  ctx.closePath();
  ctx.fillStyle = "rgba(0, 0, 0, 0.72)";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
  ctx.stroke();

  // Filter dots. Local +X runs along the top edge from the NW anchor (screen
  // top-right in N-up E-left) toward NE (screen top-left), so local +X is
  // screen-leftward. Draw the array in reverse local-x order so L lands at
  // the far (screen-left) end and SII near the anchor (screen-right) — gives
  // natural LRGBHOS reading order on the standard view.
  const filters = (src.data && src.data.filters) || {};
  for (let i = 0; i < n; i++) {
    const f = FILTER_DOT_ORDER[i];
    const has = (filters[f]?.total_hours || 0) > 0;
    ctx.globalAlpha = has ? 1.0 : 0.2;
    ctx.fillStyle = FILTER_COLORS[f];
    const cx = inset + PAD_X + DOT_R + (n - 1 - i) * (DOT_R * 2 + DOT_GAP);
    const cy = inset + boxH / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, DOT_R, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

const UNKNOWN_TELESCOPE = "Unknown";

function assignTelescopeColors(targets) {
  const names = new Set();
  let anyUnknown = false;
  for (const t of targets) {
    const tel = t.telescopes || [];
    if (tel.length === 0) anyUnknown = true;
    for (const n of tel) names.add(n);
  }
  const sorted = [...names].sort();
  const map = {};
  for (let i = 0; i < sorted.length; i++) {
    map[sorted[i]] = TELESCOPE_PALETTE[i % TELESCOPE_PALETTE.length];
  }
  // Append "Unknown" at the end with the fallback grey so the user can still
  // filter those FOVs explicitly (e.g. NINA captures with no TELESCOP header).
  if (anyUnknown) {
    map[UNKNOWN_TELESCOPE] = TELESCOPE_FALLBACK;
    sorted.push(UNKNOWN_TELESCOPE);
  }
  return { map, sorted };
}

function telescopeOf(t) {
  return (t.telescopes && t.telescopes.length) ? t.telescopes[0] : UNKNOWN_TELESCOPE;
}

function targetMatches(t) {
  // Search tokens AND with the chip/telescope/depth predicates below.
  if (!targetMatchesSearch(t, searchTokens)) return false;

  const hrs = {};
  for (const [f, d] of Object.entries(t.filters)) hrs[f] = d.total_hours || 0;

  const has = f => (hrs[f] || 0) >= minHours;
  const hasAny = f => (hrs[f] || 0) > 0;

  // Telescope toggle: a tagged target is visible only if its telescope is in
  // the selected set. Empty set = hide every tagged target (so the user can
  // untick all telescopes to clear the rectangles and just see overlays).
  // Untagged targets stay visible regardless — they have nothing to filter on.
  const tel = telescopeOf(t);
  if (tel && !selectedTelescopes.has(tel)) return false;

  if (completionFilter === "finished" && !isTargetFinished(t)) return false;
  if (completionFilter === "unfinished" && isTargetFinished(t)) return false;

  if (filterLogic === "any") {
    return [...selectedFilters].some(f => has(f));
  }
  if (filterLogic === "all") {
    return [...selectedFilters].every(f => has(f));
  }
  if (filterLogic === "ha_not_sii") {
    return hasAny("Ha") && !hasAny("SII");
  }
  return true;
}

// Resolve a target's finished state. Precedence: manual override > plan-derived
// (all goals met across every plan attached to this target_id) > default false.
function isTargetFinished(t) {
  if (!t) return false;
  const key = String(t.target_id);
  const ov = targetOverrides[key];
  if (ov && typeof ov.finished === "boolean") return ov.finished;
  const attached = plans.filter(p => String(p.target?.target_id) === key);
  if (attached.length === 0) return false;
  return attached.every(planGoalsMet);
}

function planGoalsMet(plan) {
  const goals = plan?.filter_goals || {};
  const filterNames = Object.keys(goals);
  if (filterNames.length === 0) return false;
  const target = manifest?.targets?.find(x => String(x.target_id) === String(plan.target?.target_id));
  const actualHrs = f => target?.filters?.[f]?.total_hours || 0;
  return filterNames.every(f => {
    const tgt = parseFloat(goals[f]?.target_hours || 0);
    return tgt > 0 && actualHrs(f) >= tgt;
  });
}

// --- Esc / empty-sky "go up one level" navigation ---
//
// Each entry maps a panelMode to a function that performs the up-one-level
// navigation from that mode. Modes not in the map are top-level (no parent →
// no-op). Add new modes here when nesting deepens.
const PANEL_PARENTS = {
  "detail":    () => renderTargetList(),
  "plan-edit": () => requestNavigateAwayFromPlanEdit(renderPlanList),
  "gear-edit": () => renderPlanList(),
};

function goUpOneLevel() {
  const handler = PANEL_PARENTS[panelMode];
  if (handler) handler();
}

function planIsDirty() {
  if (!editingPlan) return false;
  const orig = plans.find(p => p.id === editingPlan.id);
  if (!orig) return true; // detached — treat as dirty so we don't silently lose work
  return JSON.stringify(editingPlan) !== JSON.stringify(orig);
}

// Pre-flight for any "leave plan-edit" navigation. If the user has unsaved
// edits, prompt; otherwise (or after they choose) call `then()` to actually
// navigate. Discard mirrors the existing Cancel-button logic — scratch plans
// without a guid get pulled back out of `plans[]`.
function requestNavigateAwayFromPlanEdit(then) {
  if (!planIsDirty()) {
    const orig = plans.find(p => p.id === editingPlan?.id);
    if (orig && !orig.guid) plans = plans.filter(p => p !== orig);
    then();
    return;
  }
  showUnsavedPlanModal({
    onSave: async () => {
      const ok = await savePlan();
      if (ok) then();
      // On save failure, savePlan() already alerted; stay on the editor so the user can retry.
    },
    onDiscard: () => {
      const orig = plans.find(p => p.id === editingPlan?.id);
      if (orig && !orig.guid) plans = plans.filter(p => p !== orig);
      then();
    },
    onCancel: () => { /* stay put */ },
  });
}

function isModalOpen() {
  return !!document.querySelector(".dirty-modal-backdrop");
}

function showUnsavedPlanModal({ onSave, onDiscard, onCancel }) {
  document.querySelector(".dirty-modal-backdrop")?.remove();

  const backdrop = document.createElement("div");
  backdrop.className = "dirty-modal-backdrop";
  backdrop.innerHTML = `
    <div class="dirty-modal" role="dialog" aria-modal="true" aria-labelledby="dirtyModalTitle">
      <h4 id="dirtyModalTitle">Unsaved plan changes</h4>
      <p>You have unsaved edits to this plan. What would you like to do?</p>
      <div class="dirty-modal-buttons">
        <button data-action="cancel">Cancel</button>
        <button data-action="discard">Discard</button>
        <button class="btn-primary" data-action="save">Save</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);

  const close = () => backdrop.remove();
  const handle = async (action) => {
    close();
    if (action === "save")    await onSave();
    else if (action === "discard") onDiscard();
    else onCancel();
  };
  backdrop.addEventListener("click", e => { if (e.target === backdrop) handle("cancel"); });
  backdrop.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => handle(btn.dataset.action));
  });
  // Focus the safe default (Cancel) so a stray Enter doesn't auto-save.
  backdrop.querySelector('button[data-action="cancel"]')?.focus();
}

function summariseFilters(t) {
  const filters = t.filters || {};
  const pairs = FILTER_DOT_ORDER
    .filter(f => (filters[f]?.total_hours || 0) > 0)
    .map(f => [f, filters[f]]);
  return pairs.map(([f, d]) => `${f}=${(d.total_hours || 0).toFixed(1)}h`).join(" · ");
}

function totalHoursOf(t) {
  let s = 0;
  for (const d of Object.values(t.filters || {})) s += (d.total_hours || 0);
  return s;
}

function filterDotsHtml(filters) {
  return FILTER_DOT_ORDER.map(f => {
    const hrs = filters[f]?.total_hours || 0;
    const has = hrs > 0;
    const color = FILTER_COLORS[f] || "#888";
    const style = has ? `background:${color}` : `background:${color};opacity:0.22`;
    const title = has ? `${f} ${hrs.toFixed(1)}h` : f;
    return `<span class="fdot" style="${style}" title="${title}"></span>`;
  }).join("");
}

function _bestMonthScore(t) {
  // Combined score for sort=best_month: rank * 100 + hours_at_best (so two
  // "great" targets break tie by who has more dark-time at peak).
  const bins = binsForTarget(t.target_id);
  if (!bins) return -1;
  const best = bestBinFor(bins);
  if (!best) return -1;
  const rank = _LABEL_RANK[best.label] ?? 0;
  return rank * 100 + (best.hours_above_min || 0);
}

function _tonightAlt(t) {
  const v = currentAlts[t.target_id];
  return (typeof v === "number") ? v : -999;
}

function renderTargetList() {
  panelMode = "list";
  updateSearchVisibility();
  selectedTargetId = null;
  saveUiState();
  const panel = document.getElementById("panelBody");
  if (!panel || !manifest) return;

  const matches = manifest.targets.filter(targetMatches);
  // Sort modes: hours = legacy default; best_month + up_tonight require
  // time-aware data and degrade to hours when that data isn't loaded.
  if (sortBy === "best_month" && visibilityData) {
    matches.sort((a, b) => _bestMonthScore(b) - _bestMonthScore(a));
  } else if (sortBy === "up_tonight" && Object.keys(currentAlts).length) {
    matches.sort((a, b) => _tonightAlt(b) - _tonightAlt(a));
  } else {
    matches.sort((a, b) => totalHoursOf(b) - totalHoursOf(a));
  }

  const rows = matches.map(t => {
    const name = esc(t.objects?.[0] || "(no name)");
    const tel = telescopeOf(t);
    const swatch = telescopeColor[tel] || TELESCOPE_FALLBACK;
    const total = totalHoursOf(t).toFixed(1);
    const dots = filterDotsHtml(t.filters || {});
    const finishedMark = isTargetFinished(t) ? `<span class="finished-badge" title="marked finished">✓</span>` : "";
    const yc = yearCurveSparklineHtml(t.target_id);
    const nowChip = nowChipHtml(t.target_id);
    const trChip = trendChipHtml(t.target_id);
    // Two-line layout: row 1 is name/dots/hours, row 2 (only when time-aware
    // is on) carries Now + Trend chips and the 12-month sparkline.
    return `<li class="target-row" data-target-id="${t.target_id}">
        <span class="tr-swatch" style="background:${esc(swatch)}" title="${esc(tel)}"></span>
        <span class="tr-name">#${t.target_id} ${name}${finishedMark}</span>
        <span class="tr-dots">${dots}</span>
        <span class="tr-hours">${total}h</span>
        <span class="tr-meta">${nowChip}${trChip}${yc}</span>
      </li>`;
  }).join("");

  const empty = `<li class="tr-empty">No targets match current filters.</li>`;
  const sortCtl = `<span class="sort-control">sort by
      <select id="sortSel">
        <option value="hours" ${sortBy==="hours"?"selected":""}>hours</option>
        <option value="best_month" ${sortBy==="best_month"?"selected":""} data-time-aware>best month</option>
        <option value="up_tonight" ${sortBy==="up_tonight"?"selected":""} data-time-aware>up tonight</option>
      </select></span>`;

  panel.innerHTML = `
    <div class="panel-list">
      <h3>Targets <span class="tr-count">${matches.length} of ${manifest.targets.length}</span>${sortCtl}</h3>
      <ul class="target-list">${rows || empty}</ul>
    </div>`;

  panel.querySelectorAll(".target-row").forEach(row => {
    row.addEventListener("click", () => {
      const id = parseInt(row.dataset.targetId, 10);
      const t = manifest.targets.find(x => x.target_id === id);
      if (t) {
        // Pan first so even a render throw doesn't swallow the pan.
        panMapTo(t.center_ra_deg, t.center_dec_deg);
        renderTargetPanel(t);
      }
    });
  });
  const sortSel = panel.querySelector("#sortSel");
  if (sortSel) sortSel.addEventListener("change", e => {
    sortBy = e.target.value;
    localStorage.setItem("acp.sort_by", sortBy);
    renderTargetList();
  });
}

function renderTargetPanel(t) {
  panelMode = "detail";
  updateSearchVisibility();
  selectedTargetId = t.target_id;
  saveUiState();
  const panel = document.getElementById("panelBody");
  // LRGBHOS order everywhere — header pills, coverage rows. Extras (non-canonical
  // filters present in the manifest) are dropped for now per the same rule
  // we apply to the sky-map filter chips.
  const filtersSorted = FILTER_DOT_ORDER
    .filter(f => t.filters?.[f])
    .map(f => [f, t.filters[f]]);

  const filterPills = filtersSorted
    .filter(([f, d]) => (d.total_hours || 0) > 0)
    .map(([f]) => `<span class="filter-pill fp-${f}">${f}</span>`)
    .join("");

  const filterRows = filtersSorted.map(([f, d]) => `
    <tr>
      <td><span class="filter-pill fp-${f}">${f}</span></td>
      <td class="num">${(d.total_hours || 0).toFixed(2)}h</td>
      <td class="num">${d.files || 0}</td>
      <td class="num">${d.db_sub_hours ? (d.db_sub_hours).toFixed(1) + "h" : "—"}</td>
    </tr>`).join("");

  const telescopes = esc((t.telescopes || []).join(", ") || "—");
  const cameras = esc((t.cameras || []).join(", ") || "—");
  const objs = esc((t.objects && t.objects.length) ? t.objects.join(" / ") : "(no OBJECT tag)");
  const dateRange = t.date_range ? `${esc(t.date_range[0])} → ${esc(t.date_range[1])}` : "—";
  const fov = t.fov_arcmin ? `${t.fov_arcmin[0].toFixed(1)}' × ${t.fov_arcmin[1].toFixed(1)}'` : "—";

  const finished = isTargetFinished(t);
  const overrideKey = String(t.target_id);
  const hasOverride = !!targetOverrides[overrideKey];
  const hasPlans = plans.some(p => String(p.target?.target_id) === overrideKey);
  const statusText = finished
    ? (hasOverride ? "Marked finished manually." : "All plan goals met.")
    : (hasPlans ? "Plan goals not yet met." : "No plan set — treated as unfinished.");
  const primaryBtn = finished
    ? `<button id="markUnfinishedBtn">Mark in-progress</button>`
    : `<button id="markFinishedBtn">Mark finished</button>`;
  const clearBtn = hasOverride
    ? `<button id="clearOverrideBtn" title="Remove manual override; fall back to plan-derived status">Clear override</button>`
    : "";

  // Visibility section — only renders when time-aware data is available.
  // The CSS rule on .vis-section keeps it hidden if the user toggles off.
  const visBins = binsForTarget(t.target_id);
  let visHtml = "";
  if (visBins) {
    const best = bestBinFor(visBins);
    const bestTxt = best && best.label !== "not_visible"
      ? `Peak: ${_MONTH_LABELS[best.month-1]} (${_LABEL_PRETTY[best.label]}, ${best.hours_above_min}h above min)`
      : (best && best.peak_alt_deg != null && best.peak_alt_deg >= 0
          ? `Never reaches min altitude during dark (year-best peak ${best.peak_alt_deg}°).`
          : "Below horizon during dark all year.");
    const nowAlt = currentAlts[t.target_id];
    const nowLine = (typeof nowAlt === "number")
      ? `Now: ${nowAlt.toFixed(1)}° altitude`
      : "";
    const siteName = sites.find(s => s.id === activeSiteId)?.name || "current site";
    const detailNow = nowChipHtml(t.target_id);
    const detailTrend = trendChipHtml(t.target_id);
    // Below the year-curve bar: two flex rows pairing text on the left with
    // a chip on the right. Empty left span keeps the trend chip aligned
    // even before the first observability ping populates `nowLine`.
    visHtml = `
      <div class="vis-section">
        <h4>Visibility — ${esc(siteName)}</h4>
        ${yearCurveBarHtml(t.target_id)}
        <div class="vis-meta-row">
          <span class="vis-meta">${esc(bestTxt)}</span>
          ${detailNow}
        </div>
        <div class="vis-meta-row">
          <span class="vis-now">${esc(nowLine || "")}</span>
          ${detailTrend}
        </div>
      </div>`;
  }

  panel.innerHTML = `
    <div>
      <a class="back-link" id="backToList" href="#">← Back to list</a>
      <h3>Target #${t.target_id}: ${objs}</h3>
      <div>${filterPills}</div>

      <div class="mark-finished-row">
        <span class="status-text">${finished ? "✓ " : ""}${esc(statusText)}</span>
        ${primaryBtn}
        ${clearBtn}
      </div>

      ${visHtml}

      <h4>Position</h4>
      <table>
        <tr><td>RA / Dec</td><td class="num">${t.center_ra_deg.toFixed(3)}° / ${t.center_dec_deg.toFixed(3)}°</td></tr>
        <tr><td>l / b</td><td class="num">${t.center_l_deg.toFixed(2)}° / ${t.center_b_deg.toFixed(2)}°</td></tr>
        <tr><td>FOV</td><td class="num">${fov} @ ${t.pix_arcsec ? t.pix_arcsec.toFixed(2) + '"/px' : '—'}</td></tr>
        <tr><td>Telescope</td><td class="num">${telescopes}</td></tr>
        <tr><td>Camera</td><td class="num">${cameras}</td></tr>
        <tr><td>Date range</td><td class="num">${dateRange}</td></tr>
      </table>

      <h4>Filters</h4>
      <table>
        <thead><tr><th>Filter</th><th class="num">hours</th><th class="num">masters</th><th class="num">DB subs</th></tr></thead>
        <tbody>${filterRows}</tbody>
      </table>

      <h4>Master files (${t.master_files.length})</h4>
      <div class="pathlist">${t.master_files.slice(0, 12).map(p => esc(p.replace(/\\/g, "/"))).join("<br>")}${t.master_files.length > 12 ? "<br>…" : ""}</div>

      <h4 id="catMatchHdr" style="display:none">Nearby catalog objects</h4>
      <div id="catMatches"></div>
    </div>`;

  // Back-to-list link
  const back = document.getElementById("backToList");
  if (back) back.addEventListener("click", (e) => { e.preventDefault(); renderTargetList(); });

  const reopen = async (flag) => {
    await setTargetFinished(t.target_id, flag);
    redrawFootprints();
    renderTargetPanel(t);
  };
  panel.querySelector("#markFinishedBtn")?.addEventListener("click", () => reopen(true));
  panel.querySelector("#markUnfinishedBtn")?.addEventListener("click", () => reopen(false));
  panel.querySelector("#clearOverrideBtn")?.addEventListener("click", () => reopen(null));

  // If catalog overlays loaded, show nearby entries
  showCatalogMatchesFor(t);
}

function showCatalogMatchesFor(t) {
  const holder = document.getElementById("catMatches");
  if (!holder) return;
  const matches = [];
  for (const [cat, entries] of Object.entries(catalogsData)) {
    for (const e of entries) {
      if (e.ra_deg == null) continue;
      const sep = angularSep(t.center_ra_deg, t.center_dec_deg, e.ra_deg, e.dec_deg);
      if (sep <= 45) { // arcmin
        matches.push({ cat, ...e, sep });
      }
    }
  }
  if (!matches.length) {
    holder.innerHTML = "<span style='color:#78839a;font-size:11px'>No catalog matches within 45′.</span>";
    document.getElementById("catMatchHdr").style.display = "none";
    return;
  }
  document.getElementById("catMatchHdr").style.display = "block";
  matches.sort((a, b) => a.sep - b.sep);
  holder.innerHTML = "<table><thead><tr><th>Catalog</th><th>Name</th><th class='num'>Δ (')</th></tr></thead><tbody>" +
    matches.slice(0, 20).map(m => `<tr><td>${esc(m.cat)}</td><td>${esc(m.name || "")}</td><td class="num">${m.sep.toFixed(1)}</td></tr>`).join("") +
    "</tbody></table>";
}

function angularSep(ra1, dec1, ra2, dec2) {
  const toRad = x => x * Math.PI / 180;
  const d1 = toRad(dec1), d2 = toRad(dec2);
  const dr = toRad(ra1 - ra2);
  const cosA = Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(dr);
  return Math.acos(Math.max(-1, Math.min(1, cosA))) * 180 / Math.PI * 60; // arcmin
}

// Smooth-pan the map to (ra, dec) unless we're already there. Used by the
// rail row clicks so opening a detail/editor view also orients the sky map.
// No-op when current centre is within `threshold_arcmin` of the target so
// re-clicking the same row doesn't trigger a redundant pan.
function panMapTo(ra, dec, { duration_s = 1.0, threshold_arcmin = 1 } = {}) {
  if (!aladin) return;
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
  try {
    const cur = aladin.getRaDec();
    if (cur && cur.length === 2 &&
        angularSep(cur[0], cur[1], ra, dec) < threshold_arcmin) {
      return;
    }
  } catch (e) { /* fall through to pan */ }
  if (typeof aladin.animateToRaDec === "function") {
    aladin.animateToRaDec(ra, dec, duration_s);
  } else if (typeof aladin.gotoRaDec === "function") {
    aladin.gotoRaDec(ra, dec);
  }
}

// Unwraps polygon-corner RAs onto a common 360°-shifted frame when the
// polygon spans the 0/360° seam (threshold: vertex spread > 180°). Vertices
// < 180° get +360 shifted. Returns whether unwrapping happened so callers
// that also need to shift a query RA (see _ptInRaDecPoly) know to do so.
function _unwrapSeamRas(corners) {
  let ras = corners.map(c => c[0]);
  const decs = corners.map(c => c[1]);
  const unwrapped = Math.max(...ras) - Math.min(...ras) > 180;
  if (unwrapped) {
    ras = ras.map(r => r < 180 ? r + 360 : r);
  }
  return { ras, decs, unwrapped };
}

// Ray-cast point-in-polygon in RA/Dec. Polygons here are small (< a few degrees);
// flat math is fine. Handles RA wraparound by unwrapping vertices + query point
// onto a common 360°-shifted frame when the polygon spans the 0/360° seam.
function _ptInRaDecPoly(ra, dec, corners) {
  const { ras, decs, unwrapped } = _unwrapSeamRas(corners);
  if (unwrapped && ra < 180) ra += 360;
  let inside = false;
  for (let i = 0, j = ras.length - 1; i < ras.length; j = i++) {
    const rai = ras[i], deci = decs[i], raj = ras[j], decj = decs[j];
    if (((deci > dec) !== (decj > dec)) &&
        (ra < (raj - rai) * (dec - deci) / (decj - deci) + rai)) {
      inside = !inside;
    }
  }
  return inside;
}

// Bounding-box area in deg² — only used to rank overlapping polygons consistently
// so the smallest (tightest framing) wins on click. Unwraps RA the same way
// _ptInRaDecPoly does: without this, a polygon straddling the 0/360° seam gets
// a ~360°-wide bbox and always loses click-disambiguation priority to smaller,
// non-straddling polygons.
function _polyBBoxArea(corners) {
  const { ras, decs } = _unwrapSeamRas(corners);
  return Math.abs(Math.max(...ras) - Math.min(...ras)) * Math.abs(Math.max(...decs) - Math.min(...decs));
}

// Find all polygons containing the given sky point, sorted smallest-first.
// In planning mode plans are hit-testable; in viewing mode coverage. Tile
// polygons (Inventory rail) overlay both modes — if a tile overlaps a
// coverage/plan polygon, the tile is preferred so the rail shows tile
// detail. Cycling on repeat-click visits each in turn.
function hitPolygonsAt(ra, dec) {
  const tile = tileHitList
    .filter(h => _ptInRaDecPoly(ra, dec, h.corners))
    .map(h => ({ ...h, area: _polyBBoxArea(h.corners) }))
    .sort((a, b) => a.area - b.area);
  const base = (planningMode ? planHitList : coverageHitList)
    .filter(h => _ptInRaDecPoly(ra, dec, h.corners))
    .map(h => ({ ...h, area: _polyBBoxArea(h.corners) }))
    .sort((a, b) => a.area - b.area);
  return [...tile, ...base];
}

function _hitId(h) {
  if (h?.tile) return `tl:${h.source_id}/${h.tile.id}`;
  if (h?.target) return `t:${h.target.target_id}`;
  if (h?.plan) return `p:${h.plan.id}`;
  return null;
}

// Normalize any CSS-ish hex (#rgb, #rgba, #rrggbb, #rrggbbaa) to #rrggbb + the
// requested alpha byte. Aladin expects a uniform 8-char hex for fillColor alpha;
// without this, short forms like "#888" produce "#88855" which Canvas rejects
// silently, so the fill vanishes and only the stroke remains.
function _hexWithAlpha(hex, alphaByte) {
  const h = _hex6(hex);
  const a = Math.max(0, Math.min(255, alphaByte | 0)).toString(16).padStart(2, "0");
  return "#" + h + a;
}

function _hex6(hex) {
  let h = String(hex || "").trim();
  if (h.startsWith("#")) h = h.slice(1);
  if (h.length === 3 || h.length === 4) h = h.slice(0, 3).split("").map(c => c + c).join("");
  if (h.length === 8) h = h.slice(0, 6);
  if (h.length !== 6) h = "888888";
  return h;
}

// Blend toward white to produce a lighter "highlighted" version of a border
// colour — used for the hover outline so it pops against the base polygon.
function _brighten(hex, amount) {
  const h = _hex6(hex);
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const k = Math.max(0, Math.min(1, amount));
  const nr = Math.round(r + (255 - r) * k);
  const ng = Math.round(g + (255 - g) * k);
  const nb = Math.round(b + (255 - b) * k);
  return "#" + [nr, ng, nb].map(v => v.toString(16).padStart(2, "0")).join("");
}

function clearHoverHighlight() {
  hoveredHit = null;
  if (hoverOverlay) hoverOverlay.removeAll();
  const mapEl = document.getElementById("aladin-lite-div");
  if (mapEl) mapEl.style.cursor = "";
}

function setHoverHit(hit) {
  if (_hitId(hit) === _hitId(hoveredHit)) return;
  hoveredHit = hit;
  const mapEl = document.getElementById("aladin-lite-div");
  if (mapEl) mapEl.style.cursor = hit ? "pointer" : "";
  if (hoverOverlay) {
    hoverOverlay.removeAll();
    if (hit) {
      // Use the entity's own border colour and (for mosaics) the full mosaic
      // bounds — not the individual panel — so hover reads as "this whole rig
      // is what you're about to select."
      let color = "#ffffff", outline = hit.corners;
      if (hit.target) {
        const tel = telescopeOf(hit.target);
        color = telescopeColor[tel] || TELESCOPE_FALLBACK;
      } else if (hit.plan) {
        color = planBorderColor(hit.plan);
        outline = planMosaicBoundsCorners(hit.plan) || hit.corners;
      }
      // Two layered polygons: a translucent fill at saturated alpha to tint the
      // interior, then a brighter + thicker outline on top so the "this is what
      // you'll select" target reads clearly even if Aladin drops the fill on
      // some graphics backends.
      hoverOverlay.add(A.polygon(outline, { color, lineWidth: 0.01, fillColor: _hexWithAlpha(color, 0x66) }));
      hoverOverlay.add(A.polygon(outline, { color: _brighten(color, 0.55), lineWidth: 5, fillColor: _hexWithAlpha(color, 0x33) }));
    }
  }
  const tip = document.getElementById("tooltip");
  if (tip) {
    if (hit?.target) {
      const t = hit.target;
      tip.textContent = `#${t.target_id} ${t.objects?.[0] || ""} — ${summariseFilters(t)}`;
    } else if (hit?.plan) {
      const pl = hit.plan;
      const panels = (planPanelCorners(pl) || []).length;
      const goals = Object.keys(pl.filter_goals || {}).join("/");
      const mosaicBit = panels > 1 ? ` · ${panels}-panel mosaic` : "";
      tip.textContent = `${pl.target?.name || pl.id}${pl.project_name ? ` · ${pl.project_name}` : ""}${goals ? ` · ${goals}` : ""}${mosaicBit}`;
    } else {
      tip.textContent = "";
    }
  }
}

function onMapPolyClick(ra, dec) {
  const hits = hitPolygonsAt(ra, dec);
  if (!hits.length) {
    lastClickStack = null;
    goUpOneLevel();
    return;
  }

  const ids = hits.map(_hitId);
  const prev = lastClickStack;
  const sameStack = prev && prev.ids.length === ids.length && prev.ids.every((v, i) => v === ids[i]);
  const fovDeg = (aladin?.getFov?.()?.[0]) || 10;
  const threshold = fovDeg * 0.02; // ~2% of view ≈ 15 px at default canvas
  const cosDec = Math.cos(dec * Math.PI / 180) || 1;
  const sameSpot = prev
    && Math.abs((ra - prev.ra) * cosDec) < threshold
    && Math.abs(dec - prev.dec) < threshold;

  let idx = 0;
  if (sameStack && sameSpot) idx = (prev.cycleIdx + 1) % hits.length;
  lastClickStack = { ra, dec, ids, cycleIdx: idx };

  const chosen = hits[idx];
  if (chosen.tile) {
    renderTilePanel(chosen.tile, chosen.source_id);
  } else if (chosen.target) {
    renderTargetPanel(chosen.target);
  } else if (chosen.plan) {
    if (!planningMode) setPlanningMode(true);
    renderPlanEditor(chosen.plan);
  }

  if (hits.length > 1) {
    const tip = document.getElementById("tooltip");
    if (tip) tip.textContent = `${idx + 1} of ${hits.length} overlapping here — click again to cycle`;
  }
}

let catalogsData = {}; // {green_snrs: [...], smgps_candidates: [...], ...}

// rAF-coalesced trigger for redrawFootprints(), same pattern as the map
// hover handler below: collapse a burst of synchronous "input" events (fast
// typing, slider drag) into at most one rebuild per animation frame instead
// of one full overlay.removeAll()+rebuild per keystroke/tick.
let _redrawRaf = 0;
function scheduleRedrawFootprints() {
  if (_redrawRaf) return;
  _redrawRaf = requestAnimationFrame(() => {
    _redrawRaf = 0;
    redrawFootprints();
  });
}

function redrawFootprints() {
  if (!overlay || !manifest) return;
  overlay.removeAll();
  if (filterBadgeCat) filterBadgeCat.removeAll();
  coverageHitList = [];
  clearHoverHighlight();

  let shown = 0;
  const badgeSources = [];

  for (const t of manifest.targets) {
    if (!targetMatches(t)) continue;
    if (!t.corners_icrs || t.corners_icrs.length < 3) continue;
    const deepest = deepestFilter(t.filters, minHours);
    if (!deepest) continue;

    const tel = telescopeOf(t);
    const borderColor = telescopeColor[tel] || TELESCOPE_FALLBACK;
    const fillColor = (FILTER_COLORS[deepest] || "#888") + "20";

    const poly = A.polygon(t.corners_icrs, {
      color: borderColor,
      lineWidth: 2.5,
      fillColor,
    });
    poly._target = t;
    poly._corners = t.corners_icrs;
    overlay.add(poly);
    coverageHitList.push({ poly, target: t, corners: t.corners_icrs });

    // Filter badge: anchor at corners_icrs[1] (the NW corner — on standard N-up
    // E-left sky renders, NW is the top-right of the FOV on screen). Store the
    // adjacent top-edge corner (corners_icrs[2], NE = screen top-left) AND the
    // opposite-along-the-side corner (corners_icrs[0], SW = screen bottom-right)
    // so the drawer can build a basis (edge, inside) and rotate + scale correctly.
    if (t.corners_icrs.length === 4) {
      const corner_nw = t.corners_icrs[1];  // anchor = screen top-right
      const corner_ne = t.corners_icrs[2];  // along top edge = screen top-left
      const corner_sw = t.corners_icrs[0];  // along side edge = screen bottom-right
      const [ra, dec] = corner_nw;
      const badge = A.source(ra, dec, {
        target_id: t.target_id,
        filters: t.filters,
        objects: t.objects,
        kind: "filter_badge",
        anchor: corner_nw,
        edge: corner_ne,
        inside: corner_sw,
      });
      badgeSources.push(badge);
    }
    shown++;
  }
  if (filterBadgeCat) filterBadgeCat.addSources(badgeSources);
  const cs = document.getElementById("coverageStats");
  if (cs) {
    cs.innerHTML = `<div style="margin-top:10px;font-size:12px;color:#a2aec2">Showing <strong>${shown}</strong> of ${manifest.targets.length} targets.</div>`;
  }
  if (panelMode === "list") renderTargetList();
}

// Build a renderer-shaped cfg for one registry entry. The renderer expects
// `name` (the /api/catalogs key) where the registry stores it as `data_key`.
function _registryToCfg(entry) {
  return {
    id: `cat_${entry.id}`,
    name: entry.data_key || entry.id,
    label: entry.label || entry.id,
    color: entry.color || "#888",
    marker: entry.marker || "circle",
    // Aladin's default-shape prelude errors at sourceSize < 8 — clamp here so
    // a registry typo can't bring the whole catalogue rail down.
    size: Math.max(8, Number(entry.size) || 8),
  };
}

async function setupCatalogOverlays() {
  let entries = [];
  try {
    const r = await fetch("/api/catalog-registry");
    const d = await r.json();
    entries = Array.isArray(d?.catalogues) ? d.catalogues : [];
  } catch (e) {
    console.warn("catalog registry unavailable:", e);
  }
  catalogRegistry = entries;

  // Render chips into the now-empty #catChips container.
  const host = document.getElementById("catChips");
  if (!host) return;
  host.innerHTML = "";
  for (const entry of entries) {
    const cfg = _registryToCfg(entry);
    const label = document.createElement("label");
    label.className = "fchip";
    if (entry.attribution) label.title = entry.attribution;
    label.innerHTML = `<input type="checkbox" id="${esc(cfg.id)}" /> ${esc(cfg.label)}`;
    host.appendChild(label);
    const cb = label.querySelector("input");
    cb.addEventListener("change", () => {
      drawCatalogOverlay(cfg, cb.checked);
      // Enabling/disabling a catalogue shifts which categories +
      // known_tags exist — refresh the cross-catalogue Objects panel
      // so chips appear/disappear in lockstep.
      refreshObjectFilterPanel();
      saveUiState();
    });
  }
  // Initial paint of the Objects panel — it stays hidden until at
  // least one categorised catalogue is enabled, but we still need to
  // pick up any default_filter from sources whose enable_default is
  // already on at boot.
  refreshObjectFilterPanel();
}

// Helper used by saveUiState / applyUiStatePreManifest / redrawEnabledCatalogs
// to iterate the active registry instead of a hardcoded list.
function catalogDomIds() {
  return catalogRegistry.map(e => `cat_${e.id}`);
}

function _cfgForDomId(domId) {
  const entry = catalogRegistry.find(e => `cat_${e.id}` === domId);
  return entry ? _registryToCfg(entry) : null;
}

function drawCatalogOverlay(cfg, enabled) {
  if (!catalogsData[cfg.name]) {
    catalogsData[cfg.name] = [];
  }
  let ovr = catOverlays[cfg.name];
  if (enabled && !ovr) {
    ovr = A.catalog({ name: cfg.name, shape: cfg.marker, color: cfg.color, sourceSize: cfg.size });
    aladin.addCatalog(ovr);
    catOverlays[cfg.name] = ovr;
  }
  if (!ovr) return;
  ovr.removeAll();
  if (!enabled) return;
  // In gap mode, restrict to entries the server told us are inside the gap MOC.
  // Outside gap mode (or when this catalog had no gap matches), show the full set.
  const gapNames = gapEnabled ? gapNamesByCatalog[cfg.name] : null;
  let data = gapNames
    ? catalogsData[cfg.name].filter(e => gapNames.has(e.name))
    : catalogsData[cfg.name];
  // Apply the cross-catalogue Object filter (type chips + search expression).
  // Non-categorised objects pass the type-chip pre-filter; the search
  // expression keys `class:` / `tag:` won't match them so users wanting
  // to keep e.g. Messier visible alongside a categorised catalogue
  // shouldn't add those keys to the box.
  data = applyObjectFilterToData(data);
  for (const e of data) {
    if (e.ra_deg == null) continue;
    const src = A.source(e.ra_deg, e.dec_deg, { name: e.name, catalog: cfg.name, ...e });
    ovr.addSources([src]);
  }
}

// Re-fires the change event on every checked catalog checkbox so its overlay
// re-renders against the current gap-mode filter (or the full data, when off).
function redrawEnabledCatalogs() {
  for (const id of catalogDomIds()) {
    const cb = document.getElementById(id);
    if (cb && cb.checked) cb.dispatchEvent(new Event("change"));
  }
}

// --- Cross-catalogue Object filter --------------------------------------
//
// Sits under the per-catalogue enable chips inside the Catalogues
// accordion. Two filter primitives:
//
//   - Type chips: union of `categories()` declared by every enabled
//     CategorisedCatalogSource. Ticking a chip whitelists that category.
//     Objects without a category aren't subject to this gate (so older
//     non-categorised catalogues like Messier still render even when
//     every chip is unticked).
//
//   - Filter input: free-text search expression sharing the tokenizer
//     with the target/plan search. Supports class:, tag:, name:,
//     bareword name-substring, and leading `-` negation. Used by
//     catalogObjectMatchesTokens.
//
// State persisted in localStorage UI_STATE_KEY.objectsFilter.

let catObjectsTypesVisible = (() => {
  try {
    const s = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}");
    return new Set((s.objectsFilter && s.objectsFilter.types) || []);
  } catch { return new Set(); }
})();
let catObjectsTypesInitialised = (() => {
  try {
    const s = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}");
    return !!(s.objectsFilter && s.objectsFilter.initialised);
  } catch { return false; }
})();
let catObjectsFilterText = (() => {
  try {
    const s = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}");
    return (s.objectsFilter && s.objectsFilter.text) || "";
  } catch { return ""; }
})();
let catObjectsFilterTokens = tokenizeSearch(catObjectsFilterText);

function _saveObjectsFilterState() {
  try {
    const raw = localStorage.getItem(UI_STATE_KEY) || "{}";
    const s = JSON.parse(raw);
    s.objectsFilter = {
      initialised: catObjectsTypesInitialised,
      types: Array.from(catObjectsTypesVisible),
      text: catObjectsFilterText,
    };
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(s));
  } catch (_) { /* quota — non-fatal */ }
}

function _enabledCatalogEntries() {
  const out = [];
  for (const entry of catalogRegistry) {
    const cb = document.getElementById(`cat_${entry.id}`);
    if (cb && cb.checked) out.push(entry);
  }
  return out;
}

function applyObjectFilterToData(data) {
  if (!data || !data.length) return data;
  const usingTypes = catObjectsTypesInitialised;
  const usingTokens = catObjectsFilterTokens.length > 0;
  if (!usingTypes && !usingTokens) return data;
  return data.filter(o => {
    if (usingTypes && o.category) {
      if (!catObjectsTypesVisible.has(String(o.category))) return false;
    }
    if (usingTokens) {
      if (!catalogObjectMatchesTokens(o, catObjectsFilterTokens)) return false;
    }
    return true;
  });
}

// Compute the union of categories + known_tags declared by every
// currently-enabled catalogue. Returns {types: Set, knownTags: Set}.
function _aggregateObjectMeta() {
  const types = new Set();
  const knownTags = new Set();
  for (const entry of _enabledCatalogEntries()) {
    for (const c of (entry.categories || [])) types.add(String(c));
    for (const t of (entry.known_tags || [])) knownTags.add(String(t));
  }
  return { types, knownTags };
}

// First-time setup of the visible-types set: union of every enabled
// catalogue's `default_visible_categories` declaration. Falls back to
// "all visible" if no source declared a default. Called once per
// session so user toggles persist across catalogue enable/disable.
function _seedObjectsTypesIfNeeded(allTypes) {
  if (catObjectsTypesInitialised) return;
  const declared = new Set();
  let anyDeclared = false;
  for (const entry of _enabledCatalogEntries()) {
    const defs = entry.default_visible_categories || [];
    if (defs.length) anyDeclared = true;
    for (const c of defs) declared.add(String(c));
  }
  catObjectsTypesVisible = anyDeclared ? declared : new Set(allTypes);
  catObjectsTypesInitialised = true;
  // Apply the union of default_filter declarations on first load too.
  const defaultFilters = [];
  for (const entry of _enabledCatalogEntries()) {
    if (entry.default_filter) defaultFilters.push(String(entry.default_filter));
  }
  if (defaultFilters.length && !catObjectsFilterText) {
    catObjectsFilterText = defaultFilters.join(" ");
    catObjectsFilterTokens = tokenizeSearch(catObjectsFilterText);
    const input = document.getElementById("catObjectsFilter");
    if (input) input.value = catObjectsFilterText;
  }
  _saveObjectsFilterState();
}

function refreshObjectFilterPanel() {
  const panel = document.getElementById("catObjects");
  const typesRow = document.getElementById("catObjectsTypesRow");
  const typesHost = document.getElementById("catObjectsTypes");
  const filterInput = document.getElementById("catObjectsFilter");
  const hint = document.getElementById("catObjectsHint");
  if (!panel || !typesRow || !typesHost || !filterInput) return;

  const { types, knownTags } = _aggregateObjectMeta();
  const anyCategorised = types.size > 0;
  panel.hidden = !anyCategorised;
  if (!anyCategorised) return;

  _seedObjectsTypesIfNeeded(types);

  // Render type chips, sorted alphabetically for stable ordering.
  const sortedTypes = Array.from(types).sort();
  typesHost.innerHTML = "";
  for (const cat of sortedTypes) {
    const label = document.createElement("label");
    label.className = "fchip";
    const checked = catObjectsTypesVisible.has(cat) ? "checked" : "";
    label.innerHTML = `<input type="checkbox" data-cat-type="${esc(cat)}" ${checked}/> ${esc(cat)}`;
    typesHost.appendChild(label);
    const cb = label.querySelector("input");
    cb.addEventListener("change", () => {
      if (cb.checked) catObjectsTypesVisible.add(cat);
      else catObjectsTypesVisible.delete(cat);
      _saveObjectsFilterState();
      redrawEnabledCatalogs();
    });
  }
  typesRow.hidden = sortedTypes.length === 0;

  // Filter input — wire once, idempotently (avoid stacking handlers
  // on every refresh).
  if (!filterInput.dataset.wired) {
    filterInput.dataset.wired = "1";
    filterInput.value = catObjectsFilterText;
    filterInput.addEventListener("input", () => {
      catObjectsFilterText = filterInput.value;
      catObjectsFilterTokens = tokenizeSearch(catObjectsFilterText);
      _saveObjectsFilterState();
      redrawEnabledCatalogs();
    });
  }

  // Known-tags hint line — surfaces extension-declared tag vocabulary
  // so the user knows what's available in `tag:` / `-tag:`.
  if (hint) {
    const sortedTags = Array.from(knownTags).sort();
    if (sortedTags.length) {
      hint.textContent = "Known tags: " + sortedTags.join(", ");
      hint.hidden = false;
    } else {
      hint.textContent = "";
      hint.hidden = true;
    }
  }
}

// Palette for source swatches when a source's metadata.color is empty.
// Fixed cycle so each source gets a stable color across reloads (the
// list ordering on /api/sources is stable too — manifest first, then
// extensions in registration order).
const SOURCE_PALETTE = ["#7aa2ff", "#ff8a3d", "#65c275", "#c87aff", "#ffc857", "#44d9d3"];
// Hydrated from localStorage at script-load so any saveUiState() that fires
// during boot (accordion toggles, image-survey events, etc.) before the
// async loadSources() has populated this map doesn't write sources: {} and
// wipe the user's previously-saved per-source toggles.
let sourcesEnabled = (() => {
  try { return JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}").sources || {}; }
  catch { return {}; }
})();

// Same script-load hydration for extension toggle state (keyed
// `${extension}.${action_id}`), used by loadExtensions() to restore which
// auto-runners (e.g. nina_ts_sync's "Live progress from NINA") were on at
// last reload — so the user doesn't have to re-tick after every refresh.
let extToggleState = (() => {
  try { return JSON.parse(localStorage.getItem(UI_STATE_KEY) || "{}").extToggles || {}; }
  catch { return {}; }
})();

// Live MOC overlays keyed by source_id. Populated by mocToggleOn / cleared by
// mocToggleOff.
const mocLayers = {};

function mocToggleOn(sourceId, color, checkbox) {
  const url = `/api/moc/${encodeURIComponent(sourceId)}`;
  // First toggle hits the network; subsequent toggles use the server-side disk cache.
  // perimeter+fill explicitly disables `edge` (per-HEALPix-cell borders), which the
  // MOC constructor force-enables when all three render modes are falsy and which
  // murders FPS at order 11+.
  const moc = A.MOCFromURL(url, {
    name: `moc_${sourceId}`,
    perimeter: true,
    fill: true,
    color,
    fillColor: color,
    lineWidth: 1,
    opacity: 0.25,
  }, undefined, () => {
    // errorCallback — fetch or wasm parse failed.
    console.warn(`MOC source ${sourceId} failed to load`);
    if (checkbox) checkbox.checked = false;
    sourcesEnabled[sourceId] = false;
    delete mocLayers[sourceId];
  });
  aladin.addMOC(moc);
  mocLayers[sourceId] = moc;
}

function mocToggleOff(sourceId) {
  const moc = mocLayers[sourceId];
  if (!moc) return;
  try { aladin.removeOverlay(moc); } catch (err) { console.warn("removeOverlay failed:", err); }
  delete mocLayers[sourceId];
}

// --- Gap finder (Phase 4b) ---

// Walk every target's filters dict and produce a sorted, deduped list of
// filter names that actually appear in this manifest. We don't trust a
// hardcoded ["Ha","SII","OIII",...] because friend manifests / future surveys
// may carry filters this client doesn't know about.
function manifestFilterNames() {
  const seen = new Set();
  for (const t of (manifest?.targets || [])) {
    for (const f of Object.keys(t.filters || {})) seen.add(f);
  }
  return [...seen].sort();
}

function populateGapDropdowns() {
  const have = document.getElementById("gapHave");
  const missing = document.getElementById("gapMissing");
  if (!have || !missing) return;
  const names = manifestFilterNames();
  // Empty manifest fallback — keep the selects populated with the persisted
  // values so the user can still type/submit.
  if (names.length === 0) names.push(gapHave, gapMissing);
  const opts = names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  have.innerHTML = opts;
  missing.innerHTML = opts;
  have.value = names.includes(gapHave) ? gapHave : (names[0] || "Ha");
  missing.value = names.includes(gapMissing) ? gapMissing : (names[1] || names[0] || "SII");
  gapHave = have.value;
  gapMissing = missing.value;

  document.getElementById("gapMinHave").value = String(gapMinHave);
  document.getElementById("gapMaxMissing").value = String(gapMaxMissing);

  have.addEventListener("change", e => { gapHave = e.target.value; saveUiState(); });
  missing.addEventListener("change", e => { gapMissing = e.target.value; saveUiState(); });
  document.getElementById("gapMinHave").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v >= 0) { gapMinHave = v; saveUiState(); }
  });
  document.getElementById("gapMaxMissing").addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v >= 0) { gapMaxMissing = v; saveUiState(); }
  });
}

function populateGapSources(sources) {
  const host = document.getElementById("gapSourcesList");
  if (!host) return;
  host.innerHTML = "";
  // First-run default = every source checked. Persisted state wins after that.
  const persisted = gapSourceIds.length > 0
    ? new Set(gapSourceIds)
    : new Set(sources.map(s => s.id));
  gapSourceIds = sources.filter(s => persisted.has(s.id)).map(s => s.id);
  for (const s of sources) {
    const row = document.createElement("label");
    row.className = "fchip";
    if (s.attribution) row.title = s.attribution;
    const checked = persisted.has(s.id) ? "checked" : "";
    row.innerHTML = `
      <input type="checkbox" data-gap-source="${esc(s.id)}" ${checked} />
      ${esc(s.label)}`;
    host.appendChild(row);
  }
  for (const cb of host.querySelectorAll("input[data-gap-source]")) {
    cb.addEventListener("change", () => {
      const id = cb.dataset.gapSource;
      if (cb.checked) {
        if (!gapSourceIds.includes(id)) gapSourceIds.push(id);
      } else {
        gapSourceIds = gapSourceIds.filter(x => x !== id);
      }
      saveUiState();
    });
  }
}

function clearGapOverlays() {
  if (gapMocLayer) {
    try { aladin.removeOverlay(gapMocLayer); } catch (err) { console.warn("gap MOC remove failed:", err); }
    gapMocLayer = null;
  }
  // Drop the gap-name filter and redraw any catalog overlays back to full.
  gapNamesByCatalog = {};
  redrawEnabledCatalogs();
}

async function loadGaps() {
  const stats = document.getElementById("gapStats");
  const params = new URLSearchParams({
    have: gapHave,
    missing: gapMissing,
    min_have_hours: String(gapMinHave),
    max_missing_hours: String(gapMaxMissing),
  });
  if (gapSourceIds.length > 0) params.set("sources", gapSourceIds.join(","));
  let resp;
  try {
    resp = await fetch(`/api/gaps?${params.toString()}`);
  } catch (e) {
    console.warn("gap fetch failed:", e);
    if (stats) stats.textContent = "(network error)";
    return;
  }
  if (resp.status === 503) {
    if (stats) stats.textContent = "(mocpy not installed — gap-finder unavailable)";
    return;
  }
  if (!resp.ok) {
    let msg = `(error ${resp.status})`;
    try { const j = await resp.json(); if (j.error) msg = `(${j.error})`; } catch {}
    if (stats) stats.textContent = msg;
    console.warn("gap fetch returned", resp.status);
    return;
  }
  const data = await resp.json();

  // Drop the previous overlays before mounting the new ones — Aladin doesn't
  // de-dupe by name, layered ghosts tank FPS at high MOC orders.
  clearGapOverlays();

  if (data.moc_url) {
    // Same perimeter+fill recipe as mocToggleOn — explicitly off `edge` mode
    // (per-cell borders), which is the FPS killer at order 11+.
    gapMocLayer = A.MOCFromURL(data.moc_url, {
      name: "gap_moc",
      perimeter: true,
      fill: true,
      color: "#ffd24d",
      fillColor: "#ffd24d",
      lineWidth: 2,
      opacity: 0.35,
    });
    aladin.addMOC(gapMocLayer);
  }

  // Build a per-catalog name filter from the response. Catalog overlays toggled
  // on in the Catalogues rail will draw only their gap-matching entries.
  const cands = data.candidates || [];
  gapNamesByCatalog = {};
  for (const c of cands) {
    if (!c.catalog || !c.name) continue;
    (gapNamesByCatalog[c.catalog] ||= new Set()).add(c.name);
  }
  redrawEnabledCatalogs();

  if (stats) {
    const pct = (100 * (data.gap_sky_fraction || 0)).toFixed(2);
    const from = (data.have_sources || []).join(", ") || "(none)";
    stats.textContent = `sky ${pct}% • ${cands.length} candidates in gap (tick catalogs to view) • from ${from}`;
  }
}

async function loadSources() {
  const host = document.getElementById("sourcesList");
  if (!host) return;
  let sources = [];
  try {
    const r = await fetch("/api/sources");
    sources = await r.json();
  } catch (e) {
    console.warn("sources unavailable:", e);
    return;
  }
  // Restore previously-saved per-source enabled state, falling back to the
  // server-supplied enabled_default for sources we haven't seen before.
  const saved = (loadUiState().sources || {});
  host.innerHTML = "";
  // Track resolved color per source so the checkbox handler reuses the same
  // swatch the user sees in the rail rather than recomputing it.
  const colorById = {};
  sources.forEach((s, i) => {
    const enabled = (s.id in saved) ? !!saved[s.id] : !!s.enabled_default;
    sourcesEnabled[s.id] = enabled;
    const color = s.color || SOURCE_PALETTE[i % SOURCE_PALETTE.length];
    colorById[s.id] = color;
    const row = document.createElement("label");
    row.className = "fchip src-row";
    if (s.attribution) row.title = s.attribution;
    row.innerHTML = `
      <input type="checkbox" data-source="${esc(s.id)}" data-kind="${esc(s.kind || "")}" ${enabled ? "checked" : ""} />
      <span class="tele-swatch" style="background:${esc(color)}"></span>
      ${esc(s.label)}`;
    host.appendChild(row);
  });
  for (const cb of host.querySelectorAll("input[type=checkbox][data-source]")) {
    cb.addEventListener("change", () => {
      const id = cb.dataset.source;
      sourcesEnabled[id] = cb.checked;
      saveUiState();
      if (cb.dataset.kind === "moc") {
        if (cb.checked) mocToggleOn(id, colorById[id], cb);
        else mocToggleOff(id);
      }
      // Non-MOC kinds (manifest, friend) remain a state-bearing no-op here;
      // their map filtering lands in a later phase.
    });
  }
  // Initial render: paint MOC layers for any source already toggled on.
  for (const cb of host.querySelectorAll('input[type=checkbox][data-source][data-kind="moc"]')) {
    if (cb.checked) mocToggleOn(cb.dataset.source, colorById[cb.dataset.source], cb);
  }
  // Hand the same source list to the gap-finder rail.
  populateGapSources(sources);
  // If the user had the gap overlay on at last save, fire it now that both
  // dropdowns and source list are wired up. mocpy-missing sessions silently
  // 503; user re-toggles to retry.
  const savedGap = loadUiState();
  if (savedGap?.gap?.enabled) {
    gapEnabled = true;
    const btn = document.getElementById("gapMode");
    if (btn) {
      btn.textContent = "Hide gaps";
      btn.style.background = "#663";
    }
    loadGaps();
  }
}

// --- Extensions rail ----------------------------------------------------
//
// Generic renderer for extension-registered manifest entries. Three roles:
//
//   1. Buttons with ``replaces: "<core-id>"`` swap the corresponding core
//      button in place (handler + label come from the extension). The
//      REPLACEABLE_BUTTONS map below is core ACP's published contract — it
//      maps stable manifest ids to in-DOM selectors that the renderer mutates.
//
//   2. Buttons without ``replaces`` are rendered into the Extensions rail
//      panel. Same goes for toggles. The rail panel itself stays hidden
//      (per the index.html `<details hidden>`) until at least one manifest
//      entry needs to render there.
//
//   3. For actions with ``needs: ["profile_id"]``, the first click opens
//      a profile-picker step in the modal that calls the extension's
//      ``profiles_endpoint`` and ``config_endpoint``. Subsequent clicks
//      skip straight to the preview step.
//
// All cross-extension state is reset each call to loadExtensions() so
// re-running the function (e.g. after an extension install) refreshes the
// UI cleanly.

const REPLACEABLE_BUTTONS = {
  // Core button id → DOM selector, fallback label, and default handler.
  // The fallback label/handler apply only when no extension supplies a
  // replacement. Default handlers are referenced lazily via arrow wrappers
  // so the underlying function need only exist at click time.
  "sync-to-nina": {
    selector: "#planSync",
    baseLabel: "Manual Sync to NINA",
    defaultHandler: () => syncPlans(),
  },
};

let extensionsManifest = []; // populated by loadExtensions()
const liveProgressTimers = new Map(); // action-id → setInterval handle
const liveProgressState = new Map();  // action-id → {failures, lastIso}

// Wire a single replaceable button to either its extension replacement or the
// core default. Idempotent — clones the existing node first to drop any prior
// listeners so calling this repeatedly leaves the button with exactly one
// click handler. Safe to call before extensionsManifest is populated (empty
// manifest = falls through to the core default).
function wireReplaceableButton(coreId) {
  const meta = REPLACEABLE_BUTTONS[coreId];
  if (!meta) return;
  const btn = document.querySelector(meta.selector);
  if (!btn) return;
  const fresh = btn.cloneNode(true);
  btn.parentNode.replaceChild(fresh, btn);
  for (const ext of extensionsManifest) {
    for (const action of (ext.actions || [])) {
      if (action.replaces === coreId) {
        fresh.textContent = action.label;
        fresh.dataset.extAction = action.id;
        fresh.addEventListener("click", () => runExtensionAction(ext, action));
        return;
      }
    }
  }
  fresh.textContent = meta.baseLabel;
  fresh.addEventListener("click", meta.defaultHandler);
}

async function loadExtensions() {
  const panel = document.getElementById("railExtensions");
  const host = document.getElementById("extensionsList");
  if (!panel || !host) return;

  let entries = [];
  try {
    const r = await fetch("/api/extensions/manifest");
    entries = await r.json();
  } catch (e) {
    console.warn("extensions manifest unavailable:", e);
  }
  if (!Array.isArray(entries)) entries = [];
  extensionsManifest = entries;

  // (1) Re-wire every replaceable button against the freshly-loaded manifest.
  //     Handles both "extension just installed" and "manifest loaded after
  //     planner toolbar already rendered" cases.
  for (const coreId of Object.keys(REPLACEABLE_BUTTONS)) {
    wireReplaceableButton(coreId);
  }

  host.innerHTML = "";
  let railHasContent = false;

  for (const ext of entries) {
    for (const action of (ext.actions || [])) {
      if (action.replaces && REPLACEABLE_BUTTONS[action.replaces]) continue; // handled above
      // (2) Rail-panel path: button or toggle in the Extensions accordion.
      railHasContent = true;
      const row = document.createElement("div");
      row.className = "ext-row";
      if (action.kind === "toggle") {
        const stateKey = `${ext.extension}.${action.id}`;
        const initialOn = !!extToggleState[stateKey];
        row.innerHTML = `
          <label class="fchip">
            <input type="checkbox" data-ext-toggle="${esc(stateKey)}" ${initialOn ? "checked" : ""} />
            ${esc(action.label)}
          </label>
          <div class="ext-status" data-ext-status="${esc(stateKey)}">${initialOn ? "Starting…" : "Off"}</div>
        `;
        host.appendChild(row);
        const cb = row.querySelector(`input[data-ext-toggle]`);
        cb.addEventListener("change", () => {
          extToggleState[stateKey] = cb.checked;
          saveUiState();
          if (cb.checked) startLiveAction(ext, action);
          else stopLiveAction(action.id);
        });
        if (initialOn) startLiveAction(ext, action);
      } else {
        const btn = document.createElement("button");
        btn.textContent = action.label;
        btn.addEventListener("click", () => runExtensionAction(ext, action));
        row.appendChild(btn);
        host.appendChild(row);
      }
    }
  }
  // Show the rail accordion only if there's at least one button/toggle in
  // it. Swapped core buttons live in their original slot (e.g. the planner
  // toolbar) — no need to advertise them here.
  panel.hidden = !railHasContent;
}

// --- Modal flow for extension button actions ----------------------------
//
// All extension flows share a single modal (#extActionModal) with a
// step-history stack so Back can pop the user to the previous step. The
// ModalCtx wrapper owns:
//   - the primary action button at the bottom-right (Next / Apply /
//     extension-customised label) — rebound per step
//   - the Back button at the bottom-left — auto-hidden on first step
//   - the close-X in the header — confirms before close if a dirtyCheck
//     callback signals state would be lost
//   - the step transitions themselves, including a per-step onBack hook
//     so an in-flight fetch / SSE reader can be cancelled when the user
//     leaves the preview step
//
// On every modal open we wipe lingering button onclick handlers (the bug
// that previously routed priority_tiler's Apply through nina_ts_sync's
// onclick still bound to the same DOM node).

function _newModalCtx(dlg) {
  const ctx = {
    dlg,
    history: [],  // [{id, onBack}]
    primaryBtn: dlg.querySelector('[data-act="primary"]'),
    backBtn:    dlg.querySelector('[data-act="back"]'),
    closeBtn:   dlg.querySelector('[data-act="close-x"]'),
    dirtyCheck: () => false,
    _onEsc: null,
  };
  // Wipe stale handlers from prior modal sessions.
  if (ctx.primaryBtn) ctx.primaryBtn.onclick = null;
  if (ctx.backBtn)    ctx.backBtn.hidden = true;
  // Re-bind nav buttons.
  if (ctx.backBtn)    ctx.backBtn.onclick = () => _ctxGoBack(ctx);
  if (ctx.closeBtn)   ctx.closeBtn.onclick = () => _ctxTryClose(ctx);
  // ESC also routes through tryClose.
  ctx._onEsc = (e) => { e.preventDefault(); _ctxTryClose(ctx); };
  dlg.addEventListener("cancel", ctx._onEsc);
  return ctx;
}

function _ctxApplyPrimary(ctx, primary) {
  if (!ctx.primaryBtn) return;
  ctx.primaryBtn.textContent = primary.label;
  ctx.primaryBtn.disabled = !primary.enabled;
  ctx.primaryBtn.onclick = primary.handler;
  ctx.primaryBtn.hidden = primary.hidden;
}

function _ctxShowStep(ctx, stepId, opts = {}) {
  // opts: { primaryLabel, primaryHandler, primaryEnabled, dirtyCheck, onBack, showBack }
  for (const step of ctx.dlg.querySelectorAll(".ext-modal-step")) {
    step.hidden = step.id !== stepId;
  }
  if (!ctx.dlg.open) ctx.dlg.showModal();
  // Each history entry carries the step's current primary-button binding
  // (label / handler / enabled / hidden). _ctxSetPrimary writes through
  // to this entry, and _ctxGoBack restores it when returning. Without
  // this, going Back from preview would leave the primary button still
  // saying "Create plans" instead of reverting to "Generate preview".
  const entry = {
    id: stepId,
    onBack: opts.onBack || null,
    primary: {
      label: opts.primaryLabel || "Next",
      handler: opts.primaryHandler || null,
      enabled: opts.primaryEnabled !== false,
      hidden: opts.primaryHandler === null,
    },
  };
  ctx.history.push(entry);
  if (ctx.backBtn) {
    const canBack = opts.showBack !== false && ctx.history.length > 1;
    ctx.backBtn.hidden = !canBack;
  }
  _ctxApplyPrimary(ctx, entry.primary);
  ctx.dirtyCheck = opts.dirtyCheck || (() => false);
}

function _ctxSetPrimary(ctx, { label, handler, enabled } = {}) {
  if (!ctx.primaryBtn) return;
  // Write through to the current step's saved primary state so going
  // back-and-forward preserves the latest binding.
  const cur = ctx.history[ctx.history.length - 1];
  if (cur && cur.primary) {
    if (label !== undefined)   cur.primary.label = label;
    if (enabled !== undefined) cur.primary.enabled = enabled;
    if (handler !== undefined) {
      cur.primary.handler = handler;
      // Receiving a handler always unhides the button — the only way to
      // hide is via _ctxShowStep with primaryHandler:null.
      cur.primary.hidden = handler === null;
    }
    _ctxApplyPrimary(ctx, cur.primary);
  } else {
    if (label !== undefined)   ctx.primaryBtn.textContent = label;
    if (enabled !== undefined) ctx.primaryBtn.disabled = !enabled;
    if (handler !== undefined) {
      ctx.primaryBtn.onclick = handler;
      ctx.primaryBtn.hidden = handler === null;
    }
  }
}

function _ctxGoBack(ctx) {
  if (ctx.history.length <= 1) return;
  const leaving = ctx.history.pop();
  // The leaving step gets a chance to cancel in-flight work (preview SSE).
  if (leaving && typeof leaving.onBack === "function") {
    try { leaving.onBack(); } catch (_) { /* non-fatal */ }
  }
  const prev = ctx.history[ctx.history.length - 1];
  for (const step of ctx.dlg.querySelectorAll(".ext-modal-step")) {
    step.hidden = step.id !== prev.id;
  }
  if (ctx.backBtn) ctx.backBtn.hidden = ctx.history.length <= 1;
  // Restore the previous step's saved primary-button binding so the user
  // sees "Generate preview" again instead of "Create plans" / etc.
  if (prev && prev.primary) _ctxApplyPrimary(ctx, prev.primary);
}

function _ctxTryClose(ctx) {
  // Per user feedback 2026-05-17: confirm dialogs on ✕ were annoying
  // since the modal state is just a draft that disappears anyway.
  // Close fires onBack so in-flight SSE cancels cleanly.
  _ctxClose(ctx);
}

function _ctxClose(ctx) {
  // Run onBack for the current step too, so in-flight work cancels on close.
  const cur = ctx.history[ctx.history.length - 1];
  if (cur && typeof cur.onBack === "function") {
    try { cur.onBack(); } catch (_) { /* non-fatal */ }
  }
  if (ctx._onEsc) ctx.dlg.removeEventListener("cancel", ctx._onEsc);
  ctx.dlg.close();
}

async function runExtensionAction(ext, action) {
  const dlg = document.getElementById("extActionModal");
  if (!dlg) return;
  const titleEl = document.getElementById("extActionTitle");
  if (titleEl) titleEl.textContent = action.label;

  const ctx = _newModalCtx(dlg);

  // Resolve config needs first. Currently only profile_id.
  let profileId = null;
  if ((action.needs || []).includes("profile_id")) {
    const cfgR = await fetch(ext.config_endpoint);
    const cfg = await cfgR.json().catch(() => ({}));
    const savedId = cfg.profile_id || null;
    // Always re-check the DB so the picker can act on the current set of
    // profiles, not a cached config. 0 → picker (will say "no profiles"),
    // 1 → silent use (auto-update saved config when different), ≥2 →
    // always picker so the user can swap freely at sync time.
    let availableProfiles = null;
    if (ext.profiles_endpoint) {
      try {
        const pr = await fetch(ext.profiles_endpoint);
        const pb = await pr.json();
        availableProfiles = pb.profiles || [];
      } catch (_) { /* transport error — fall back to saved id */ }
    }
    if (availableProfiles === null) {
      profileId = savedId;  // can't validate, trust config; downstream call surfaces real error
    } else if (availableProfiles.length === 1) {
      const only = availableProfiles[0].profile_id;
      profileId = only;
      if (savedId !== only) {
        try {
          await fetch(ext.config_endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile_id: only }),
          });
        } catch (_) { /* non-fatal: sync still proceeds with the right id */ }
      }
    } else {
      // 0 or ≥2 → always prompt. Pre-select the saved id so the common
      // case (re-syncing same profile) is a single Continue click.
      const picked = await promptProfilePicker(ext, ctx, {
        profiles: availableProfiles,
        preselect: savedId,
      });
      if (!picked) { _ctxClose(ctx); return; }
      profileId = picked;
    }
  }

  // Decide which flow to run after inputs (or directly when no input_schema).
  const proceed = (extras) => {
    if (action.pull_diff_endpoint && action.preview_endpoint) {
      runBidirectionalSync(ext, action, profileId, ctx, extras);
    } else if (action.preview_endpoint) {
      runPreviewThenApply(ext, action, profileId, ctx, extras);
    } else {
      runApplyImmediate(ext, action, profileId, ctx, extras);
    }
  };

  if (Array.isArray(action.input_schema) && action.input_schema.length) {
    runInputsThenContinue(ext, action, ctx, proceed);
  } else {
    proceed({});
  }
}

// --- Input-schema form rendering ---------------------------------------

// localStorage key for remembering the last-submitted values for an
// (extension, action) pair, so re-opening the modal pre-fills the form.
function _lastValuesKey(ext, action) {
  return `acp.ext.${ext.extension}.${action.id}.lastValues`;
}

function loadLastValues(ext, action) {
  try {
    const raw = localStorage.getItem(_lastValuesKey(ext, action));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function saveLastValues(ext, action, values) {
  try {
    localStorage.setItem(_lastValuesKey(ext, action), JSON.stringify(values));
  } catch (_) { /* quota; non-fatal */ }
}

// Substitute {today} / {tomorrow} in a string with ISO date strings.
// Non-strings pass through unchanged.
function expandDefaultTokens(value) {
  if (typeof value !== "string") return value;
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86400000);
  const iso = d => d.toISOString().slice(0, 10);
  return value
    .replace(/\{today\}/g, iso(today))
    .replace(/\{tomorrow\}/g, iso(tomorrow));
}

// Build the form DOM from action.input_schema. Returns the form element
// (already appended into formEl). Each field gets a data-field=NAME so
// collectInputs can read it back by name without ID collisions across
// repeated openings.
function renderInputsForm(ext, action, formEl) {
  const schema = action.input_schema || [];
  const last = loadLastValues(ext, action);
  formEl.innerHTML = "";
  for (const field of schema) {
    if (!field || !field.name || !field.type) continue;
    const row = document.createElement("div");
    row.className = "ext-input-row";
    const labelText = field.label || field.name;
    const reqMark = field.required ? ' <span class="ext-input-req" title="required">*</span>' : "";
    const id = `ext-input-${field.name.replace(/[^a-z0-9_-]/gi, "_")}`;

    const labelEl = document.createElement("label");
    labelEl.htmlFor = id;
    labelEl.className = "ext-input-label";
    labelEl.innerHTML = esc(labelText) + reqMark;
    row.appendChild(labelEl);

    const prior = Object.prototype.hasOwnProperty.call(last, field.name)
      ? last[field.name]
      : expandDefaultTokens(field.default);

    let input;
    if (field.type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(prior);
    } else if (field.type === "select") {
      input = document.createElement("select");
      for (const opt of field.options || []) {
        const o = document.createElement("option");
        o.value = String(opt.value);
        o.textContent = String(opt.label != null ? opt.label : opt.value);
        if (prior != null && String(prior) === String(opt.value)) o.selected = true;
        input.appendChild(o);
      }
    } else {
      input = document.createElement("input");
      input.type = (field.type === "int" || field.type === "float") ? "number" : "text";
      if (field.type === "int") input.step = "1";
      if (field.type === "float") input.step = "any";
      if (field.min != null) input.min = String(field.min);
      if (field.max != null) input.max = String(field.max);
      input.value = prior != null ? String(prior) : "";
    }
    input.id = id;
    input.className = "ext-input-control";
    input.dataset.field = field.name;
    input.dataset.fieldType = field.type;
    if (field.required) input.dataset.required = "1";
    if (field.min != null) input.dataset.min = String(field.min);
    if (field.max != null) input.dataset.max = String(field.max);
    row.appendChild(input);

    if (field.help) {
      const help = document.createElement("div");
      help.className = "ext-input-help";
      help.textContent = field.help;
      row.appendChild(help);
    }
    const errEl = document.createElement("div");
    errEl.className = "ext-input-error";
    errEl.dataset.errorFor = field.name;
    errEl.hidden = true;
    row.appendChild(errEl);

    formEl.appendChild(row);
  }
}

// Read every input back, applying client-side validation. Returns
// {values, valid, firstError}. The `values` dict is what gets spread
// into the payload posted to /preview or /apply.
function collectInputs(formEl) {
  const values = {};
  const errors = [];
  for (const el of formEl.querySelectorAll("[data-field]")) {
    const name = el.dataset.field;
    const type = el.dataset.fieldType;
    const required = el.dataset.required === "1";
    const errEl = formEl.querySelector(`[data-error-for="${name}"]`);
    let err = "";
    let value;
    if (type === "bool") {
      value = !!el.checked;
    } else {
      const raw = el.value;
      if (raw == null || String(raw).trim() === "") {
        if (required) err = "Required.";
        else value = type === "int" || type === "float" ? null : "";
      } else if (type === "int") {
        const n = parseInt(raw, 10);
        if (!Number.isFinite(n) || String(n) !== String(raw).trim()) {
          err = "Must be a whole number.";
        } else {
          value = n;
        }
      } else if (type === "float") {
        const n = parseFloat(raw);
        if (!Number.isFinite(n)) err = "Must be a number.";
        else value = n;
      } else if (type === "select") {
        value = String(raw);
      } else {
        value = String(raw);
      }
      if (!err && (type === "int" || type === "float")) {
        const min = el.dataset.min != null ? parseFloat(el.dataset.min) : null;
        const max = el.dataset.max != null ? parseFloat(el.dataset.max) : null;
        if (min != null && value < min) err = `Must be ≥ ${min}.`;
        else if (max != null && value > max) err = `Must be ≤ ${max}.`;
      }
    }
    if (err) {
      errors.push({ name, err });
      if (errEl) { errEl.textContent = err; errEl.hidden = false; }
    } else {
      if (errEl) { errEl.textContent = ""; errEl.hidden = true; }
      if (value !== undefined) values[name] = value;
    }
  }
  return { values, valid: errors.length === 0, firstError: errors[0] || null };
}

function runInputsThenContinue(ext, action, ctx, next) {
  const hint = document.getElementById("extActionInputsHint");
  const formEl = document.getElementById("extActionInputsForm");
  if (!formEl) { next({}); return; }
  if (hint) {
    if (action.preview_hint) {
      hint.textContent = "ℹ️ " + action.preview_hint;
      hint.hidden = false;
    } else {
      hint.textContent = "";
      hint.hidden = true;
    }
  }
  renderInputsForm(ext, action, formEl);

  const submit = () => {
    const { values, valid } = collectInputs(formEl);
    if (!valid) return;
    saveLastValues(ext, action, values);
    next(values);
  };

  _ctxShowStep(ctx, "extActionInputs", {
    primaryLabel: action.next_label || "Next",
    primaryHandler: submit,
    primaryEnabled: false,
    dirtyCheck: () => {
      const { values } = collectInputs(formEl);
      return Object.keys(values).length > 0;
    },
  });

  const revalidate = () => {
    const { valid } = collectInputs(formEl);
    _ctxSetPrimary(ctx, { enabled: valid });
  };
  for (const el of formEl.querySelectorAll("[data-field]")) {
    el.addEventListener("input", revalidate);
    el.addEventListener("change", revalidate);
  }
  revalidate();
}

async function promptProfilePicker(ext, ctx, opts = {}) {
  const list = document.getElementById("extActionPickerList");
  if (!list) return null;
  list.innerHTML = "Loading profiles…";
  _ctxShowStep(ctx, "extActionPicker", {
    primaryLabel: "Continue",
    primaryEnabled: false,
    primaryHandler: null,
  });

  let profiles = opts.profiles || null;
  if (profiles === null) {
    try {
      const r = await fetch(ext.profiles_endpoint);
      const body = await r.json();
      profiles = body.profiles || [];
    } catch (e) {
      list.innerHTML = `<div class="ext-error">Could not load profiles: ${esc(String(e))}</div>`;
      return new Promise((resolve) => {
        ctx.dlg.addEventListener("close", () => resolve(null), { once: true });
      });
    }
  }
  if (!profiles.length) {
    list.innerHTML = `<div class="ext-error">No profiles found in the TS DB.</div>`;
    return new Promise((resolve) => {
      _ctxShowStep(ctx, "extActionPicker", { primaryHandler: null, primaryLabel: "Continue", primaryEnabled: false });
      ctx._closeResolve = resolve;
    });
  }
  // Sort by name so the picker is stable + human-ordered across opens.
  // Unnamed profiles (no NINA Profiles dir, Linux dev) sort last.
  profiles = profiles.slice().sort((a, b) => {
    const an = (a.name || "").toLowerCase(), bn = (b.name || "").toLowerCase();
    if (!!an !== !!bn) return an ? -1 : 1;
    if (an !== bn) return an < bn ? -1 : 1;
    return (a.profile_id || "").localeCompare(b.profile_id || "");
  });
  const preselect = opts.preselect || "";
  const preselectExists = profiles.some(p => p.profile_id === preselect);
  list.innerHTML = profiles.map((p, i) => {
    const checked = (preselectExists && p.profile_id === preselect) || (!preselectExists && i === 0);
    const displayName = p.name || `(unnamed profile · ${(p.profile_id || "").slice(0, 8)}…)`;
    const meta = `${p.project_count} project${p.project_count === 1 ? "" : "s"}`
      + (p.sample_projects.length ? " · " + p.sample_projects.map(esc).join(", ") : "");
    return `
    <label class="ext-profile-row" title="${esc(p.profile_id)}">
      <input type="radio" name="extProfilePick" value="${esc(p.profile_id)}" ${checked ? "checked" : ""} />
      <div>
        <div class="ext-profile-pid">${esc(displayName)}</div>
        <div class="ext-profile-meta">${meta}</div>
      </div>
    </label>`;
  }).join("");

  return new Promise((resolve) => {
    _ctxShowStep(ctx, "extActionPicker", {
      primaryLabel: "Continue",
      primaryEnabled: true,
      primaryHandler: async () => {
        const chosen = list.querySelector('input[name="extProfilePick"]:checked');
        if (!chosen) return;
        try {
          await fetch(ext.config_endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile_id: chosen.value }),
          });
        } catch (e) {
          list.innerHTML += `<div class="ext-error">Save failed: ${esc(String(e))}</div>`;
          return;
        }
        resolve(chosen.value);
      },
    });
    // If the dialog is closed (close-X) while the picker is showing,
    // resolve null so runExtensionAction can clean up gracefully.
    ctx.dlg.addEventListener("close", () => resolve(null), { once: true });
  });
}

// --- Preview-then-apply with optional SSE streaming ---------------------
//
// Tries text/event-stream first so the preview step can show a live
// progress bar for slow extensions (priority_tiler's greedy + rotation
// search on 7k+ catalog objects takes several seconds). If the server
// returns plain JSON (older extension or one that doesn't stream), we
// fall through to the single-shot path.

async function _streamingPreview(action, payload, ctx, onProgress) {
  // Returns { preview, cancelled } on success, throws on transport error.
  const controller = new AbortController();
  ctx.history[ctx.history.length - 1].onBack = () => controller.abort();
  let r;
  try {
    r = await fetch(action.preview_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream, application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } catch (e) {
    if (e.name === "AbortError") return { cancelled: true };
    throw e;
  }
  const ctype = (r.headers.get("Content-Type") || "").toLowerCase();
  if (!ctype.startsWith("text/event-stream")) {
    // Server returned synchronous JSON — fall through to single read.
    const text = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${text.slice(0, 300)}`);
    try { return { preview: JSON.parse(text) }; }
    catch (_) { throw new Error(`bad JSON: ${text.slice(0, 300)}`); }
  }
  if (!r.body || !r.body.getReader) {
    // Browser missing ReadableStream support — degrade.
    return { preview: await r.json() };
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final = null;
  let err = null;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const parsed = _parseSSEBlock(block);
        if (!parsed) continue;
        if (parsed.event === "progress") {
          try { onProgress(parsed.data); } catch (_) { /* non-fatal */ }
        } else if (parsed.event === "result") {
          final = parsed.data;
        } else if (parsed.event === "error") {
          err = parsed.data && parsed.data.error ? parsed.data.error : "unknown error";
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") return { cancelled: true };
    throw e;
  }
  if (err) throw new Error(err);
  if (final === null) throw new Error("stream closed without a result event");
  return { preview: final };
}

function _parseSSEBlock(block) {
  // SSE format: lines of "event: NAME" + "data: JSON" separated by \n.
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  let data = raw;
  try { data = JSON.parse(raw); } catch (_) { /* keep raw string */ }
  return { event, data };
}

function _updateProgressBar(prog) {
  const wrap = document.getElementById("extActionPreviewProgress");
  const bar = document.getElementById("extActionProgressBar");
  const label = wrap ? wrap.querySelector(".ext-progress-label") : null;
  if (!wrap || !bar || !label) return;
  wrap.hidden = false;
  // The server now reports a single 0..1 fraction across the whole
  // pipeline rather than per-pick sub-percentages — the latter were
  // misleading when the algorithm bailed out early on subtraction.
  let pct = 0;
  if (prog.fraction != null) {
    pct = Math.max(0, Math.min(1, Number(prog.fraction)));
  } else if (prog.total) {
    // Backwards-compat for older server: (pick, total, pct) shape.
    const totalPicks = Math.max(1, Number(prog.total || 1));
    const pickIdx = Math.max(0, Number(prog.pick || 0));
    const subPct = Math.max(0, Math.min(1, Number(prog.pct || 0)));
    pct = Math.min(1, (pickIdx + subPct) / totalPicks);
  }
  bar.style.width = `${(pct * 100).toFixed(1)}%`;
  const stage = prog.stage ? `${prog.stage}: ` : "";
  label.textContent = `${stage}${Math.round(pct * 100)}%`;
}

function _resetProgressBar() {
  const wrap = document.getElementById("extActionPreviewProgress");
  const bar = document.getElementById("extActionProgressBar");
  if (wrap) wrap.hidden = true;
  if (bar) bar.style.width = "0%";
}

async function _afterApplyRefreshRail() {
  // After any extension's apply commits, refresh the plans rail so the
  // user sees the new/updated entries without a hard F5. Best-effort.
  try {
    if (typeof loadPlans === "function") await loadPlans();
    if (typeof panelMode !== "undefined" && panelMode === "plan-list" &&
        typeof renderPlanList === "function") {
      renderPlanList();
    }
  } catch (refreshErr) {
    console.warn("plan reload after extension apply failed:", refreshErr);
  }
}

async function runPreviewThenApply(ext, action, profileId, ctx, extras = {}) {
  const body = document.getElementById("extActionPreviewBody");
  if (!body) return;

  setPreviewHint(action);
  body.textContent = "";
  _resetProgressBar();

  _ctxShowStep(ctx, "extActionPreview", {
    primaryLabel: action.apply_label || "Apply",
    primaryEnabled: false,
    primaryHandler: null,
    dirtyCheck: () => true,
    onBack: null,  // overridden by _streamingPreview once it starts
  });

  const payload = { profile_id: profileId, ...extras };
  let result;
  try {
    result = await _streamingPreview(action, payload, ctx, _updateProgressBar);
  } catch (e) {
    _resetProgressBar();
    body.innerHTML = `<div class="ext-error">Preview failed: ${esc(String(e.message || e))}</div>`;
    return;
  }
  if (result.cancelled) {
    // Back/close fired during streaming. Nothing to render — caller
    // already navigated away.
    return;
  }
  const preview = result.preview;
  _resetProgressBar();
  body.innerHTML = renderDiffReport(preview);

  const doApply = async () => {
    const applyingLabel = action.applying_label || `${action.apply_label || "Apply"}ing…`;
    _ctxSetPrimary(ctx, { label: applyingLabel, enabled: false });
    body.innerHTML = renderDiffReport(preview) +
      `<div class="ext-progress">${esc(applyingLabel)}</div>`;
    let applyResult;
    try {
      const r = await fetchWithRetry(action.endpoint, payload, {
        onRetry: (n, max) => {
          const prog = body.querySelector(".ext-progress");
          if (prog) prog.textContent = `${applyingLabel} retrying (${n}/${max})`;
        },
      });
      applyResult = await r.json();
    } catch (e) {
      _showResult(ctx, `<div class="ext-error">${esc(action.apply_label || "Apply")} failed: ${esc(String(e))}</div>`);
      return;
    }
    await _afterApplyRefreshRail();
    const title = action.result_title || "✓ Done";
    _showResult(ctx, `
      <div class="ext-ok">${esc(title)}</div>
      ${renderDiffReport(preview, { applied: true })}
      ${applyResult.backup_path ? `<div class="ext-meta">DB backup: <code>${esc(applyResult.backup_path)}</code></div>` : ""}
    `);
  };

  _ctxSetPrimary(ctx, {
    label: action.apply_label || "Apply",
    handler: doApply,
    enabled: true,
  });
}

// Bidirectional Sync with NINA flow.
//
// Calls /preview (push side) and /import/diff (pull side) in parallel,
// renders both directions in one modal with arrows + per-conflict pickers,
// then on Apply runs /sync followed by /import/resolve. The user owns the
// resolution for every conflict — non-conflicting pull changes apply
// automatically; ts_only_new gets per-project import checkboxes.
async function runBidirectionalSync(ext, action, profileId, ctx, extras = {}) {
  const body = document.getElementById("extActionPreviewBody");
  if (!body) return;
  setPreviewHint(action);
  body.textContent = "Loading preview…";
  _resetProgressBar();

  _ctxShowStep(ctx, "extActionPreview", {
    primaryLabel: action.apply_label || "Apply",
    primaryEnabled: false,
    primaryHandler: null,
    dirtyCheck: () => true,
  });

  const payload = { profile_id: profileId, ...extras };
  // Per-conflict user decisions, keyed by plan_id. Three values:
  // "take_acp", "take_ts", or "skip" (default). Bound via change handlers
  // on the radio inputs in the rendered modal.
  const decisions = new Map();
  // Per-project ts_only_new import opt-ins, keyed by project_name.
  const newImports = new Set();

  let pushPreview, pullDiff;
  try {
    [pushPreview, pullDiff] = await Promise.all([
      fetchWithRetry(action.preview_endpoint, payload).then(r => r.json()),
      fetchWithRetry(action.pull_diff_endpoint, payload).then(r => r.json()),
    ]);
  } catch (e) {
    body.innerHTML = `<div class="ext-error">Preview failed: ${esc(String(e))}</div>`;
    return;
  }

  const rerender = () => {
    body.innerHTML = renderBidirectionalDiff(pushPreview, pullDiff, decisions, newImports);
    wireBidiInteractions(body, decisions, newImports, () => {
      // Cheap re-render keeps the primary button summary in sync with picks.
      _ctxSetPrimary(ctx, { label: applyButtonLabel(pushPreview, pullDiff, decisions, newImports) });
    });
    _ctxSetPrimary(ctx, { label: applyButtonLabel(pushPreview, pullDiff, decisions, newImports) });
  };
  rerender();

  const doApply = async () => {
    const applyingLabel = action.applying_label || `${action.apply_label || "Apply"}ing…`;
    _ctxSetPrimary(ctx, { label: applyingLabel, enabled: false });
    body.insertAdjacentHTML(
      "beforeend",
      `<div class="ext-progress" id="extBidiProgress">${esc(applyingLabel)} (TS may be busy, will retry)</div>`,
    );
    const setProgress = (msg) => {
      const el = body.querySelector("#extBidiProgress");
      if (el) el.textContent = msg;
    };

    // PULL first (TS → ACP). Resolve writes plans.json to reflect the user's
    // decisions: auto_pull defaults absorb TS-side edits, take_acp keeps ACP
    // (which the upcoming push will then overwrite back to TS), take_ts on
    // conflicts replaces ACP with TS, ts_only_new bootstraps brand-new plans.
    // Doing pull BEFORE push is what makes "Take ACP on an incoming change"
    // actually work — push then sends the resolved ACP state to TS.
    let pullResult;
    try {
      setProgress("Pulling NINA edits into ACP…");
      const resolveBody = {
        profile_id: profileId,
        decisions: Object.fromEntries(decisions),
        import_ts_only_new: Array.from(newImports),
      };
      const r = await fetchWithRetry(action.pull_apply_endpoint, resolveBody, {
        onRetry: (n, max) => setProgress(`Pulling… retrying (${n}/${max})`),
      });
      pullResult = await r.json();
    } catch (e) {
      _showResult(ctx, `<div class="ext-error">Pull failed: ${esc(String(e))}</div>`);
      return;
    }

    // PUSH second (ACP → TS) with the now-resolved ACP state.
    let pushResult;
    try {
      setProgress("Pushing resolved ACP plans to TS…");
      const r = await fetchWithRetry(action.endpoint, payload, {
        onRetry: (n, max) => setProgress(`Pushing… retrying (${n}/${max})`),
      });
      pushResult = await r.json();
    } catch (e) {
      _showResult(ctx, `
        <div class="ext-meta">Pull applied; push failed below.</div>
        <div class="ext-error">Push failed: ${esc(String(e))}</div>
      `);
      return;
    }

    await _afterApplyRefreshRail();

    const title = action.result_title || "✓ Sync complete";
    _showResult(ctx, `
      <div class="ext-ok">${esc(title)}</div>
      ${renderBidiResultSummary(pushResult, pullResult)}
      ${pushResult.backup_path ? `<div class="ext-meta">DB backup: <code>${esc(pushResult.backup_path)}</code></div>` : ""}
    `);
  };

  _ctxSetPrimary(ctx, { handler: doApply, enabled: true });
}

// Compute a human-readable summary for the Apply button so the user can see
// at a glance what they're about to do, including how many conflicts are
// still unresolved (skipped). Updated on every decision-state change via
// wireBidiInteractions's onChange callback.
function applyButtonLabel(pushPreview, pullDiff, decisions, newImports) {
  // Mirror the Outgoing-section filter so the button count matches what the
  // user actually sees: changes claimed by Pull are excluded from the Push
  // tally.
  const claimedByPull = new Set();
  for (const d of (pullDiff.auto_pull || [])) {
    for (const path of (d.auto_pull || [])) claimedByPull.add(`${d.plan_id}|${path}`);
  }
  for (const d of (pullDiff.conflicts || [])) {
    for (const path of (d.conflict || [])) claimedByPull.add(`${d.plan_id}|${path}`);
  }
  const pushChangeCount = (pushPreview.plan_diffs || [])
    .filter(p => p.kind === "update" || p.kind === "insert")
    .map(p => filterOutgoingByPull(p, claimedByPull))
    .filter(Boolean)
    .length;
  // Per-item: auto-pull defaults to take_ts (= pull). User can flip to
  // take_acp (= keep LOCAL, push back). Tally by current choice.
  const autoPull = pullDiff.auto_pull || [];
  let pullCount = 0, keepAcpCount = 0;
  for (const ap of autoPull) {
    if (decisions.get(ap.plan_id) === "take_acp") keepAcpCount++;
    else pullCount++;
  }
  const conflicts = pullDiff.conflicts || [];
  const resolved = conflicts.filter(c => {
    const d = decisions.get(c.plan_id);
    return d === "take_acp" || d === "take_ts";
  }).length;
  const skipped = conflicts.length - resolved;
  const imports = newImports.size;
  const bits = [];
  if (pushChangeCount) bits.push(`push ${pushChangeCount}`);
  if (pullCount) bits.push(`pull ${pullCount}`);
  if (keepAcpCount) bits.push(`keep ${keepAcpCount}`);
  if (resolved) bits.push(`resolve ${resolved}`);
  if (imports) bits.push(`import ${imports}`);
  if (skipped) bits.push(`skip ${skipped}`);
  return bits.length ? `Apply (${bits.join(" · ")})` : "Apply";
}

function renderBidiResultSummary(pushResult, pullResult) {
  const pr = pushResult.report || {};
  const ap = pullResult.applied || {};
  const lines = [];
  // Push side.
  const pushed = ["project", "target", "exposureplan", "exposuretemplate"]
    .reduce((acc, t) => acc + ((pr[t] && pr[t].inserted) || 0) + ((pr[t] && pr[t].updated) || 0), 0);
  if (pushed > 0) lines.push(`Pushed ${pushed} row${pushed === 1 ? "" : "s"} to TS`);
  // Pull side.
  const pulled = (ap.auto_pull || []).length;
  const tookAcp = (ap.take_acp || []).length;
  const tookTs = (ap.take_ts || []).length;
  const imported = (ap.imported_new || []).length;
  const skippedPull = (ap.skipped || []).length;
  if (pulled) lines.push(`Auto-pulled ${pulled} plan${pulled === 1 ? "" : "s"} from TS`);
  if (tookAcp) lines.push(`Kept ACP for ${tookAcp} conflicted plan${tookAcp === 1 ? "" : "s"}`);
  if (tookTs) lines.push(`Took TS for ${tookTs} conflicted plan${tookTs === 1 ? "" : "s"}`);
  if (imported) lines.push(`Imported ${imported} new plan${imported === 1 ? "" : "s"} from TS`);
  if (skippedPull) lines.push(`Skipped ${skippedPull} conflict${skippedPull === 1 ? "" : "s"} (no decision)`);
  if (!lines.length) lines.push("Everything was already in sync.");
  return `<div class="ext-meta">${lines.map(l => `• ${esc(l)}`).join("<br>")}</div>`;
}

// Render the combined bidirectional diff. Sections (each only rendered if
// non-empty): Outgoing, Incoming-auto, Conflicts (with picker), TS-only-new
// (with import checkboxes), Notices.
function renderBidirectionalDiff(pushPreview, pullDiff, decisions, newImports) {
  const sections = [];
  // Build a (plan_id, field_path) set claimed by the pull side. Any push
  // entry whose path matches is dropped from Outgoing — pull's section
  // (auto_pull or conflict) is the load-bearing display for that field
  // since the user's decision there decides what actually happens.
  const claimedByPull = new Set();
  const claimAll = (diffList) => {
    for (const d of (diffList || [])) {
      const pid = d.plan_id;
      for (const path of (d.auto_pull || [])) claimedByPull.add(`${pid}|${path}`);
      for (const path of (d.conflict || [])) claimedByPull.add(`${pid}|${path}`);
    }
  };
  claimAll(pullDiff.auto_pull);
  claimAll(pullDiff.conflicts);

  // OUTGOING — filter changes claimed by pull. After filtering, drop plans
  // whose remaining change list is empty.
  const outgoingRaw = (pushPreview.plan_diffs || []).filter(p => p.kind === "update" || p.kind === "insert");
  const outgoing = outgoingRaw.map(p => filterOutgoingByPull(p, claimedByPull)).filter(Boolean);
  if (outgoing.length) {
    sections.push(`
      <div class="ext-bidi-section ext-bidi-out">
        <div class="ext-bidi-head">Outgoing · ACP → NINA <span class="ext-bidi-count">${outgoing.length}</span></div>
        <div class="ext-diff-scroll" style="max-height:200px">${renderOutgoingGroups(outgoing)}</div>
      </div>
    `);
  }
  // INCOMING auto-pull — TS-only changes. Each gets its own Take NINA /
  // Keep ACP radio so the user can override the default per item.
  const autoPull = pullDiff.auto_pull || [];
  if (autoPull.length) {
    sections.push(`
      <div class="ext-bidi-section ext-bidi-in">
        <div class="ext-bidi-head">Incoming · NINA → ACP <span class="ext-bidi-count">${autoPull.length}</span> <span class="ext-bidi-sub">defaults to take NINA</span></div>
        <div class="ext-diff-scroll" style="max-height:240px">${renderIncomingAutoPull(autoPull, decisions)}</div>
      </div>
    `);
  }
  // CONFLICTS — per-plan pickers.
  const conflicts = pullDiff.conflicts || [];
  if (conflicts.length) {
    sections.push(`
      <div class="ext-bidi-section ext-bidi-conflict">
        <div class="ext-bidi-head">Conflicts <span class="ext-bidi-count">${conflicts.length}</span> <span class="ext-bidi-sub">pick a side per plan</span></div>
        <div class="ext-diff-scroll" style="max-height:220px">${renderConflictCards(conflicts, decisions)}</div>
      </div>
    `);
  }
  // TS-only-new — new projects in TS the user can import.
  const tsNew = pullDiff.ts_only_new || [];
  if (tsNew.length) {
    sections.push(`
      <div class="ext-bidi-section ext-bidi-new">
        <div class="ext-bidi-head">New in NINA <span class="ext-bidi-count">${tsNew.length}</span> <span class="ext-bidi-sub">tick to import</span></div>
        <div class="ext-diff-scroll" style="max-height:160px">${renderTsOnlyNew(tsNew, newImports)}</div>
      </div>
    `);
  }
  // NOTICES — non-blocking warnings (e.g. TS row deleted out from under us).
  const notices = pullDiff.notices || [];
  if (notices.length) {
    sections.push(`
      <div class="ext-bidi-section">
        <div class="ext-bidi-head">Notes</div>
        <ul class="ext-bidi-notes">${notices.map(n => `<li>${esc(n.message || n.kind || JSON.stringify(n))}</li>`).join("")}</ul>
      </div>
    `);
  }
  if (!sections.length) {
    return `<div class="ext-meta">Nothing to sync — ACP and TS are already aligned.</div>`;
  }
  return sections.join("");
}

// Drop change entries whose path is also represented on the pull side for
// this plan. Returns a shallow-copied plan_diff with filtered change lists,
// or null when nothing's left to show.
function filterOutgoingByPull(planDiff, claimedByPull) {
  const pid = planDiff.plan_id;
  const keep = (c) => !claimedByPull.has(`${pid}|${c.path}`);
  const projectChanges = (planDiff.project_changes || []).filter(keep);
  const targetChanges = (planDiff.target_changes || []).filter(keep);
  const filterChanges = {};
  for (const [fname, changes] of Object.entries(planDiff.filter_changes || {})) {
    const kept = changes.filter(keep);
    if (kept.length) filterChanges[fname] = kept;
  }
  if (!projectChanges.length && !targetChanges.length && !Object.keys(filterChanges).length) {
    return null;
  }
  return {
    ...planDiff,
    project_changes: projectChanges,
    target_changes: targetChanges,
    filter_changes: filterChanges,
  };
}

function renderOutgoingGroups(outgoing) {
  // Same grouping as renderDiffReport's existing logic — dedup project-level
  // changes across plans sharing a project_name.
  const byProject = new Map();
  for (const d of outgoing) {
    if (!byProject.has(d.project_name)) byProject.set(d.project_name, []);
    byProject.get(d.project_name).push(d);
  }
  return Array.from(byProject.entries()).map(([proj, plans]) => {
    const shared = plans[0].project_changes || [];
    const sharedHtml = shared.length
      ? `<div class="ext-diff-shared">${shared.map(c => renderChangeDirectional(c, "out")).join("")}
          ${plans.length > 1 ? `<div class="ext-diff-meta">applies to ${plans.length} plans</div>` : ""}
        </div>`
      : "";
    const planRows = plans.map(d => {
      const tg = (d.target_changes || []).map(c => renderChangeDirectional(c, "out")).join("");
      const flt = Object.entries(d.filter_changes || {})
        .map(([fname, changes]) => changes.map(c =>
          renderChangeDirectional({ ...c, label: `${fname} ${c.label}` }, "out")
        ).join(""))
        .join("");
      const inner = tg + flt;
      if (!inner) return "";
      return `<div class="ext-diff-plan"><div class="ext-diff-target">${esc(d.target_name)}</div>${inner}</div>`;
    }).join("");
    return `<div class="ext-diff-group"><div class="ext-diff-proj">${esc(proj)}</div>${sharedHtml}${planRows}</div>`;
  }).join("");
}

function renderIncomingAutoPull(autoPullDiffs, decisions) {
  return autoPullDiffs.map(d => {
    const pid = d.plan_id;
    const fields = (d.auto_pull || []).map(path => ({
      label: humanFieldLabel(path),
      from: d.local && d.local[path],
      to: d.remote && d.remote[path],
    }));
    if (!fields.length) return "";
    const rows = fields.map(f => renderChangeDirectional(f, "in")).join("");
    // Default to "take_ts" (the recommended action — TS clearly changed,
    // ACP didn't). User can flip to "take_acp" to skip the pull and let
    // the push step overwrite TS with their LOCAL value instead.
    const decision = decisions.get(pid) || "take_ts";
    return `
      <div class="ext-conflict-card ext-conflict-card-in" data-plan-id="${esc(pid)}">
        <div class="ext-diff-target">${esc(friendlyPlanName(pid))}</div>
        ${rows}
        <div class="ext-conflict-buttons" role="radiogroup">
          <label class="${decision === "take_acp" ? "selected" : ""}">
            <input type="radio" name="conflict-${esc(pid)}" value="take_acp" ${decision === "take_acp" ? "checked" : ""}> Keep ACP
          </label>
          <label class="${decision === "take_ts" ? "selected" : ""}">
            <input type="radio" name="conflict-${esc(pid)}" value="take_ts" ${decision === "take_ts" ? "checked" : ""}> Take NINA (default)
          </label>
        </div>
      </div>
    `;
  }).join("");
}

function renderConflictCards(conflicts, decisions) {
  return conflicts.map(d => {
    const pid = d.plan_id;
    const decision = decisions.get(pid) || "skip";
    const fieldRows = (d.conflict || []).map(path => `
      <div class="ext-conflict-field">
        <span class="ext-diff-label">${esc(humanFieldLabel(path))}</span>
        <span class="ext-conflict-acp">ACP: ${esc(formatValue(d.local && d.local[path]))}</span>
        <span class="ext-conflict-ts">TS: ${esc(formatValue(d.remote && d.remote[path]))}</span>
      </div>
    `).join("");
    return `
      <div class="ext-conflict-card" data-plan-id="${esc(pid)}">
        <div class="ext-diff-target">${esc(friendlyPlanName(pid))}</div>
        ${fieldRows}
        <div class="ext-conflict-buttons" role="radiogroup">
          <label class="${decision === "take_acp" ? "selected" : ""}">
            <input type="radio" name="conflict-${esc(pid)}" value="take_acp" ${decision === "take_acp" ? "checked" : ""}> Take ACP
          </label>
          <label class="${decision === "take_ts" ? "selected" : ""}">
            <input type="radio" name="conflict-${esc(pid)}" value="take_ts" ${decision === "take_ts" ? "checked" : ""}> Take NINA
          </label>
          <label class="${decision === "skip" ? "selected" : ""}">
            <input type="radio" name="conflict-${esc(pid)}" value="skip" ${decision === "skip" ? "checked" : ""}> Skip
          </label>
        </div>
      </div>
    `;
  }).join("");
}

// Look up an ACP plan's friendly "Project / Target" name from its id. Used
// in the bidi modal so the user sees "Fesen SNR / G7.7-3.7" instead of the
// internal "plan-mouufrs7" handle.
function friendlyPlanName(planId) {
  if (typeof plans === "undefined" || !Array.isArray(plans)) return planId;
  const p = plans.find(x => x.id === planId);
  if (!p) return planId;
  const proj = p.project_name || "(no project)";
  const tgt = (p.target && p.target.name) || planId;
  return `${proj} / ${tgt}`;
}

function renderTsOnlyNew(tsNew, newImports) {
  return tsNew.map(p => {
    const key = p.project_name;
    const checked = newImports.has(key);
    return `
      <label class="ext-tsnew-row">
        <input type="checkbox" data-ts-new="${esc(key)}" ${checked ? "checked" : ""}>
        <div>
          <div>${esc(p.project_name)} / ${esc(p.target_name)}</div>
          <div class="ext-diff-meta">${(p.filters || []).join(", ")} · mosaic ${p.mosaic ? `${p.mosaic.rows}×${p.mosaic.cols}` : "1×1"}</div>
        </div>
      </label>
    `;
  }).join("");
}

function wireBidiInteractions(root, decisions, newImports, onChange) {
  // Per-conflict radio buttons.
  for (const radio of root.querySelectorAll('input[type="radio"][name^="conflict-"]')) {
    radio.addEventListener("change", () => {
      const planId = radio.name.slice("conflict-".length);
      decisions.set(planId, radio.value);
      // Re-style the parent labels so the picked one stays highlighted
      // without re-rendering the whole modal.
      const card = radio.closest(".ext-conflict-card");
      if (card) {
        for (const lbl of card.querySelectorAll(".ext-conflict-buttons label")) {
          lbl.classList.toggle("selected", lbl.querySelector("input").checked);
        }
      }
      if (onChange) onChange();
    });
  }
  // ts_only_new checkboxes.
  for (const cb of root.querySelectorAll('input[type="checkbox"][data-ts-new]')) {
    cb.addEventListener("change", () => {
      const key = cb.dataset.tsNew;
      if (cb.checked) newImports.add(key);
      else newImports.delete(key);
      if (onChange) onChange();
    });
  }
}

// Render a single from→to (or from←to) line with an arrow showing direction.
// `dir` is "out" (ACP→TS, push) or "in" (TS→ACP, pull). Visually:
//   out: label: from → to     (yellow → green)
//   in:  label: to   ← from   (green ← yellow), so user reads it as
//                              "what we'll change it TO, sourced from TS"
function renderChangeDirectional(c, dir) {
  const u = c.unit ? esc(c.unit) : "";
  const fromV = c.from === null || c.from === undefined ? "—" : `${esc(formatValue(c.from))}${u}`;
  const toV   = c.to   === null || c.to   === undefined ? "—" : `${esc(formatValue(c.to))}${u}`;
  if (dir === "in") {
    return `<div class="ext-diff-change"><span class="ext-diff-label">${esc(c.label)}:</span> <span class="ext-diff-to">${toV}</span> <span class="ext-diff-arrow ext-diff-arrow-in">←</span> <span class="ext-diff-from">${fromV}</span></div>`;
  }
  return `<div class="ext-diff-change"><span class="ext-diff-label">${esc(c.label)}:</span> <span class="ext-diff-from">${fromV}</span> <span class="ext-diff-arrow ext-diff-arrow-out">→</span> <span class="ext-diff-to">${toV}</span></div>`;
}

// Map a dotted field path (from /import/diff) to a human label that matches
// the wording on the outgoing side of the modal.
function humanFieldLabel(path) {
  const map = {
    "priority": "priority",
    "min_altitude_deg": "min altitude",
    "meridian_window_min": "meridian window",
    "target.center_ra_deg": "RA",
    "target.center_dec_deg": "Dec",
    "target.rotation_deg": "rotation",
    "target.mosaic.rows": "mosaic rows",
    "target.mosaic.cols": "mosaic cols",
    "target.mosaic.overlap_pct": "mosaic overlap",
  };
  if (map[path]) return map[path];
  // filter_goals.Ha.target_hours → "Ha target hours"
  if (path.startsWith("filter_goals.")) {
    const parts = path.split(".");
    const fname = parts[1] || "";
    const sub = parts.slice(2).join(".").replace(/_/g, " ");
    return `${fname} ${sub}`;
  }
  return path;
}

function formatValue(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  return String(v);
}

async function runApplyImmediate(ext, action, profileId, ctx, extras = {}) {
  const body = document.getElementById("extActionResultBody");
  if (!body) return;
  body.innerHTML = `<div class="ext-progress">${esc(action.apply_label || "Running")}…</div>`;
  _ctxShowStep(ctx, "extActionResult", {
    primaryLabel: "Close",
    primaryEnabled: true,
    primaryHandler: () => _ctxClose(ctx),
    showBack: false,
  });
  try {
    const r = await fetchWithRetry(action.endpoint, { profile_id: profileId, ...extras });
    const result = await r.json();
    await _afterApplyRefreshRail();
    const title = action.result_title || "✓ Done";
    body.innerHTML = `<div class="ext-ok">${esc(title)}</div>${renderSyncReport(result.report || result)}`;
  } catch (e) {
    body.innerHTML = `<div class="ext-error">Failed: ${esc(String(e))}</div>`;
  }
}

// Populate the preview-step info banner from action.preview_hint, or hide
// it when no hint declared. Replaces the hardcoded NINA-sync text that
// used to live in templates/index.html.
function setPreviewHint(action) {
  const hint = document.getElementById("extActionPreviewHint");
  if (!hint) return;
  if (action && action.preview_hint) {
    hint.textContent = "ℹ️ " + action.preview_hint;
    hint.hidden = false;
  } else {
    hint.textContent = "";
    hint.hidden = true;
  }
}

function _showResult(ctx, html) {
  const body = document.getElementById("extActionResultBody");
  if (body) body.innerHTML = html;
  _ctxShowStep(ctx, "extActionResult", {
    primaryLabel: "Close",
    primaryEnabled: true,
    primaryHandler: () => _ctxClose(ctx),
    showBack: false,
  });
}

// Render a plan-grouped diff from a /preview response. Order of precedence:
//   1. preview.preview_html — extension supplies its own pre-rendered HTML
//      (extension-first opt-in for non-sync-shaped responses)
//   2. plan_diffs array — nina_ts_sync's structured diff
//   3. report dict (falls through to renderSyncReport)
function renderDiffReport(preview, opts = {}) {
  if (preview && typeof preview.preview_html === "string") return preview.preview_html;
  const diffs = Array.isArray(preview && preview.plan_diffs) ? preview.plan_diffs : null;
  if (!diffs) return renderSyncReport(preview && preview.report);

  // Bucket each plan by its kind so the summary line and the collapsibles
  // know what they're working with.
  const inserts = diffs.filter(d => d.kind === "insert");
  const updates = diffs.filter(d => d.kind === "update");
  const unchanged = diffs.filter(d => d.kind === "unchanged");

  // Group `updates` by project_name so a shared project-level change shows
  // once under the project heading instead of repeating under every plan.
  const updatesByProject = new Map();
  for (const d of updates) {
    if (!updatesByProject.has(d.project_name)) updatesByProject.set(d.project_name, []);
    updatesByProject.get(d.project_name).push(d);
  }

  const total = diffs.length;
  const verb = opts.applied ? "applied to" : "queued for";
  const summary = updates.length + inserts.length === 0
    ? `<div class="ext-meta">All ${total} plan${total === 1 ? "" : "s"} already match TS — nothing ${opts.applied ? "applied" : "to sync"}.</div>`
    : `<div class="ext-meta">${updates.length + inserts.length} of ${total} plan${total === 1 ? "" : "s"} ${verb} TS · ${unchanged.length} already in sync</div>`;

  let changedBlock = "";
  if (updates.length || inserts.length) {
    const groups = [];
    // New projects first (inserts) so the user notices what's being created.
    if (inserts.length) {
      groups.push(`
        <div class="ext-diff-group ext-diff-insert">
          <div class="ext-diff-proj">New: ${inserts.map(d => esc(d.project_name + " / " + d.target_name)).join(", ")}</div>
        </div>
      `);
    }
    for (const [proj, plans] of updatesByProject) {
      // Project-level changes are identical across all sibling plans (the
      // payload built them via strictest-wins). Take the first plan's
      // project_changes as canonical; note the share count.
      const shared = plans[0].project_changes || [];
      const sharedHtml = shared.length
        ? `<div class="ext-diff-shared">${shared.map(c => renderChange(c)).join("")}
            ${plans.length > 1 ? `<div class="ext-diff-meta">applies to ${plans.length} plans in this project</div>` : ""}
          </div>`
        : "";
      const planRows = plans.map(d => {
        const tgtChanges = (d.target_changes || []).map(c => renderChange(c)).join("");
        const filterChangesEntries = Object.entries(d.filter_changes || {});
        const filterChangesHtml = filterChangesEntries.map(([fname, changes]) =>
          changes.map(c => renderChange({...c, label: `${fname} ${c.label}`})).join("")
        ).join("");
        const inner = tgtChanges + filterChangesHtml;
        if (!inner) return "";
        return `
          <div class="ext-diff-plan">
            <div class="ext-diff-target">${esc(d.target_name)}</div>
            ${inner}
          </div>
        `;
      }).join("");
      groups.push(`
        <div class="ext-diff-group">
          <div class="ext-diff-proj">${esc(proj)}</div>
          ${sharedHtml}
          ${planRows}
        </div>
      `);
    }
    changedBlock = `<div class="ext-diff-scroll">${groups.join("")}</div>`;
  }

  let unchangedBlock = "";
  if (unchanged.length) {
    unchangedBlock = `
      <details class="ext-diff-unchanged">
        <summary>${unchanged.length} plan${unchanged.length === 1 ? "" : "s"} unchanged</summary>
        <div class="ext-diff-unchanged-list">
          ${unchanged.map(d => `<div>${esc(d.project_name)} / ${esc(d.target_name)}</div>`).join("")}
        </div>
      </details>
    `;
  }

  return summary + changedBlock + unchangedBlock;
}

function renderChange(c) {
  const u = c.unit ? esc(c.unit) : "";
  const fromV = c.from === null || c.from === undefined ? "—" : `${esc(String(c.from))}${u}`;
  const toV   = c.to   === null || c.to   === undefined ? "—" : `${esc(String(c.to))}${u}`;
  return `<div class="ext-diff-change"><span class="ext-diff-label">${esc(c.label)}:</span> <span class="ext-diff-from">${fromV}</span> <span class="ext-diff-arrow">→</span> <span class="ext-diff-to">${toV}</span></div>`;
}

function renderSyncReport(report) {
  if (!report || typeof report !== "object") return `<div class="ext-meta">No details returned.</div>`;
  // The extension's SyncReport shape has per-entity {inserted, updated, claimed}.
  const tables = ["project", "target", "exposureplan", "exposuretemplate"];
  const rows = tables
    .filter(t => report[t])
    .map(t => `<tr><td>${t}</td><td>${report[t].inserted ?? 0}</td><td>${report[t].updated ?? 0}</td><td>${report[t].claimed ?? 0}</td></tr>`)
    .join("");
  if (!rows) return `<pre class="ext-pre">${esc(JSON.stringify(report, null, 2))}</pre>`;
  return `
    <table class="ext-report">
      <thead><tr><th>Entity</th><th>Inserted</th><th>Updated</th><th>Claimed</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${(report.notes || []).length ? `<div class="ext-meta">Notes:<ul>${report.notes.map(n => `<li>${esc(n.message || JSON.stringify(n))}</li>`).join("")}</ul></div>` : ""}
  `;
}

// fetch with exponential backoff on 5xx or "database is locked" errors.
// 3 attempts: 2s / 4s / 8s. onRetry({n, max}) is called between attempts.
async function fetchWithRetry(url, payload, { onRetry } = {}) {
  const max = 3;
  let lastErr;
  for (let n = 1; n <= max; n++) {
    let r;
    try {
      r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      lastErr = e;
      if (n < max) {
        if (onRetry) onRetry(n + 1, max);
        await new Promise(res => setTimeout(res, 2000 * Math.pow(2, n - 1)));
        continue;
      }
      throw e;
    }
    if (r.ok) return r;
    // Peek for "database is locked" or 5xx to decide retry.
    const cloned = r.clone();
    let bodyText = "";
    try { bodyText = await cloned.text(); } catch {}
    const isLockBusy = r.status === 503 || r.status >= 500 || /database is locked/i.test(bodyText);
    if (!isLockBusy || n === max) {
      lastErr = new Error(`HTTP ${r.status}: ${bodyText.slice(0, 200)}`);
      throw lastErr;
    }
    if (onRetry) onRetry(n + 1, max);
    await new Promise(res => setTimeout(res, 2000 * Math.pow(2, n - 1)));
  }
  throw lastErr || new Error("fetchWithRetry: exhausted");
}

// --- Live progress toggle (auto-runner for sync-acquired) -----------------

async function startLiveAction(ext, action) {
  stopLiveAction(action.id); // belt + braces
  const statusKey = `${ext.extension}.${action.id}`;
  liveProgressState.set(action.id, { failures: 0, lastIso: null });
  setLiveStatus(statusKey, "Refreshing…", "running");
  const max = action.max_consecutive_failures || 3;

  const tick = async () => {
    const cfgR = await fetch(ext.config_endpoint);
    const cfg = await cfgR.json().catch(() => ({}));
    const profileId = cfg.profile_id;
    if (!profileId) {
      setLiveStatus(statusKey, "No profile configured", "error");
      stopLiveAction(action.id);
      return;
    }
    try {
      const r = await fetch(action.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json().catch(() => ({}));
      const st = liveProgressState.get(action.id) || { failures: 0 };
      st.failures = 0;
      st.lastIso = new Date().toISOString();
      liveProgressState.set(action.id, st);
      const updated = body.updated_filter_goals ?? body.updated ?? 0;
      setLiveStatus(statusKey, `Last refresh just now · ${updated} goal${updated === 1 ? "" : "s"} updated`, "running");
      // Server rewrote plans.json with the new actual_hours. Reload the
      // in-memory `plans` array and repaint the rail if we're on it — without
      // this the user has to F5 to see the bumped "h left".
      if (updated > 0) {
        try {
          await loadPlans();
          if (typeof panelMode !== "undefined" && panelMode === "plan-list") {
            renderPlanList();
          } else if (typeof panelMode !== "undefined" && panelMode === "plan-edit" && editingPlan) {
            // Surgical: patch actual_hours displays in-place rather than
            // re-rendering the whole editor (which would clobber any
            // in-progress typing in target_hours / sub_exposure fields).
            updatePlanEditorActuals();
          }
        } catch (reloadErr) {
          console.warn("plan reload after sync-acquired failed:", reloadErr);
        }
      }
    } catch (e) {
      const st = liveProgressState.get(action.id) || { failures: 0 };
      st.failures = (st.failures || 0) + 1;
      liveProgressState.set(action.id, st);
      if (st.failures >= max) {
        setLiveStatus(statusKey, `Failed ${st.failures}× in a row: ${esc(String(e))}`, "error", { showRetry: true, ext, action });
        stopLiveAction(action.id);
      } else {
        setLiveStatus(statusKey, `Retry ${st.failures}/${max}: ${esc(String(e))}`, "warn");
      }
    }
  };
  await tick(); // run once immediately
  const handle = setInterval(tick, (action.interval_s || 60) * 1000);
  liveProgressTimers.set(action.id, handle);
}

function stopLiveAction(actionId) {
  const h = liveProgressTimers.get(actionId);
  if (h) {
    clearInterval(h);
    liveProgressTimers.delete(actionId);
  }
}

// Surgically refresh `actual_hours` displays inside the plan-edit panel,
// for the case where the user has the editor open while live-progress polling
// updates their plan. Pulls fresh values from `plans[]` (already reloaded by
// the caller), merges them into editingPlan in-place to preserve any other
// unsaved edits, then rewrites the per-filter status spans without doing a
// full renderPlanEditor() (which would tear down + recreate every input and
// lose cursor position / typing-in-progress).
function updatePlanEditorActuals() {
  if (panelMode !== "plan-edit" || !editingPlan) return;
  const fresh = plans.find(p => p.id === editingPlan.id);
  if (!fresh || !fresh.filter_goals) return;
  for (const [fname, freshGoal] of Object.entries(fresh.filter_goals)) {
    if (typeof freshGoal.actual_hours !== "number") continue;
    if (editingPlan.filter_goals && editingPlan.filter_goals[fname]) {
      editingPlan.filter_goals[fname].actual_hours = freshGoal.actual_hours;
    }
    const span = document.querySelector(
      `.goal-status[data-actual-filter="${(typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(fname) : fname}"]`
    );
    if (!span) continue;
    const th = (editingPlan.filter_goals && editingPlan.filter_goals[fname]?.target_hours) || 0;
    const ah = freshGoal.actual_hours || 0;
    const cls = (th > 0 && ah >= th) ? "done" : (ah > 0 ? "partial" : "todo");
    span.textContent = `${ah.toFixed(1)}h`;
    span.className = `goal-status ${cls}`;
    span.dataset.actualFilter = fname;
  }
}

function setLiveStatus(stateKey, text, kind, opts = {}) {
  const el = document.querySelector(`[data-ext-status="${stateKey.replaceAll('"', '\\"')}"]`);
  if (!el) return;
  el.className = `ext-status ext-status-${kind}`;
  if (opts.showRetry && opts.ext && opts.action) {
    el.innerHTML = `${text} <button data-ext-retry="${esc(stateKey)}">Retry</button>`;
    const btn = el.querySelector("[data-ext-retry]");
    if (btn) btn.onclick = () => {
      // Also re-check the checkbox since stopLiveAction left it on but the
      // interval cleared. Easier: just re-run startLiveAction.
      startLiveAction(opts.ext, opts.action);
    };
  } else {
    el.textContent = text;
  }
}

// --- Inventory rail (Plan 4b) -------------------------------------------
// Hidden by default. Activates only when /api/tile-sources returns at
// least one entry — i.e. when an extension has registered a
// PrioritisedTilesSource. Per-source state (enabled, filters) lives in
// localStorage under acp.inv_state so it survives reloads.
let tileSources = [];        // [{id, label, color, n_tiles, max_priority_level, categories, bands, ...}]
let tileData = {};           // {<source_id>: [tile, ...]} — fetched lazily on enable
let invState = {};           // {<source_id>: {enabled, openRail, priorities: Set, missing: Set, categories: Set}}

function _loadInvState() {
  try {
    const s = JSON.parse(localStorage.getItem("acp.inv_state") || "{}");
    // Sets don't survive JSON; restore them per-source.
    const out = {};
    for (const [sid, v] of Object.entries(s || {})) {
      const facetSelections = {};
      for (const [fid, arr] of Object.entries(v.facetSelections || {})) {
        facetSelections[fid] = new Set(Array.isArray(arr) ? arr.map(String) : []);
      }
      out[sid] = {
        enabled: !!v.enabled,
        openRail: v.openRail !== false,
        priorities: new Set(Array.isArray(v.priorities) ? v.priorities : []),
        missing:    new Set(Array.isArray(v.missing) ? v.missing : []),
        categories: new Set(Array.isArray(v.categories) ? v.categories : []),
        facetSelections,
        hidePlanned: !!v.hidePlanned,
      };
    }
    return out;
  } catch { return {}; }
}

function _saveInvState() {
  const out = {};
  for (const [sid, v] of Object.entries(invState)) {
    const facetSelections = {};
    for (const [fid, set] of Object.entries(v.facetSelections || {})) {
      facetSelections[fid] = [...set];
    }
    out[sid] = {
      enabled: !!v.enabled,
      openRail: !!v.openRail,
      priorities: [...v.priorities],
      missing:    [...v.missing],
      categories: [...v.categories],
      facetSelections,
      hidePlanned: !!v.hidePlanned,
    };
  }
  localStorage.setItem("acp.inv_state", JSON.stringify(out));
}

// --- Saved Inventory searches (Plan 6) ----------------------------------
let savedSearches = [];   // [{id, name, source_id, filters, created_at}]

async function loadSavedSearches() {
  try {
    const r = await fetch("/api/saved-searches");
    const d = await r.json();
    savedSearches = Array.isArray(d?.searches) ? d.searches : [];
  } catch (e) {
    console.warn("saved searches unavailable:", e);
    savedSearches = [];
  }
}

async function persistSavedSearch(entry) {
  const r = await fetch("/api/saved-searches", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(entry),
  });
  if (!r.ok) {
    alert(`Save failed (${r.status})`);
    return null;
  }
  const saved = await r.json();
  savedSearches = savedSearches.filter(s => s.id !== saved.id);
  savedSearches.push(saved);
  return saved;
}

async function deleteSavedSearch(searchId) {
  const r = await fetch(`/api/saved-searches/${encodeURIComponent(searchId)}`, { method: "DELETE" });
  if (r.status === 204) {
    savedSearches = savedSearches.filter(s => s.id !== searchId);
    return true;
  }
  return false;
}

function applySavedSearch(sourceId, search) {
  const st = invState[sourceId];
  if (!st) return;
  const f = search.filters || {};
  st.priorities = new Set(Array.isArray(f.priorities) ? f.priorities : []);
  st.missing    = new Set(Array.isArray(f.missing) ? f.missing : []);
  st.categories = new Set(Array.isArray(f.categories) ? f.categories : []);
  st.hidePlanned = !!f.hidePlanned;
  _saveInvState();
  // Re-render the rail block for this source so the chips reflect the
  // restored state, then redraw tiles.
  renderInventoryRail();
  renderTileOverlay(sourceId);
}

async function initInventory() {
  const accordion = document.getElementById("railInventory");
  if (!accordion) return;
  let summary = [];
  try {
    const r = await fetch("/api/tile-sources");
    const d = await r.json();
    summary = Array.isArray(d?.sources) ? d.sources : [];
  } catch (e) {
    console.warn("tile sources unavailable:", e);
  }
  if (!summary.length) {
    // No registered extension → keep the accordion hidden so a stock
    // checkout shows no Inventory rail at all.
    accordion.hidden = true;
    return;
  }
  accordion.hidden = false;
  tileSources = summary;
  await loadSavedSearches();
  const saved = _loadInvState();
  for (const s of tileSources) {
    const prior = saved[s.id] || {};
    // Default each facet to all-values-selected so a fresh source filters
    // nothing out until the user starts unticking. Reuse persisted Sets
    // when they exist, otherwise build a full-coverage Set from the facet
    // declaration.
    const facetSelections = {};
    for (const f of (s.facets || [])) {
      const persisted = prior.facetSelections && prior.facetSelections[f.id];
      facetSelections[f.id] = persisted instanceof Set
        ? persisted
        : new Set((f.values || []).map(v => String(v.value)));
    }
    invState[s.id] = {
      enabled: "enabled" in prior ? !!prior.enabled : !!s.enabled_default,
      openRail: prior.openRail !== false,
      priorities: prior.priorities instanceof Set ? prior.priorities
        : new Set(Array.from({length: Math.max(1, s.max_priority_level)}, (_, i) => i + 1)),
      missing:    prior.missing instanceof Set ? prior.missing : new Set(),
      categories: prior.categories instanceof Set ? prior.categories : new Set(s.categories || []),
      facetSelections,
      facetDefs: s.facets || [],
      colorFacet: s.color_facet || "",
      hidePlanned: !!prior.hidePlanned,
    };
  }
  renderInventoryRail();
  // Lazy-load tiles for whichever sources are enabled at startup.
  for (const s of tileSources) {
    if (invState[s.id].enabled) ensureTilesLoaded(s.id);
  }
}

function renderInventoryRail() {
  const host = document.getElementById("inventoryList");
  if (!host) return;
  host.innerHTML = "";
  for (const s of tileSources) {
    const st = invState[s.id];
    const block = document.createElement("div");
    block.className = "inv-source";
    block.dataset.sourceId = s.id;
    block.dataset.open = st.openRail ? "true" : "false";
    const swatch = s.color || "#7aa2ff";

    // Build priority chip set (1..max).
    const priChips = [];
    for (let p = 1; p <= Math.max(1, s.max_priority_level); p++) {
      const cls = p <= 4 ? `pri-${p}` : "pri-other";
      const checked = st.priorities.has(p) ? "checked" : "";
      priChips.push(`<label class="inv-pri ${cls}" title="Priority ${p}"><input type="checkbox" data-priority="${p}" ${checked}/> P${p}</label>`);
    }

    const bandChips = (s.bands || []).map(b => {
      const checked = st.missing.has(b) ? "checked" : "";
      return `<label class="fchip" title="Show only tiles missing ${esc(b)}"><input type="checkbox" data-missing="${esc(b)}" ${checked}/> ${esc(b)}</label>`;
    }).join("");

    const catChips = (s.categories || []).map(c => {
      const checked = st.categories.has(c) ? "checked" : "";
      return `<label class="fchip" title="Show only tiles whose ${esc(c)} count > 0"><input type="checkbox" data-cat="${esc(c)}" ${checked}/> ${esc(c)}</label>`;
    }).join("");

    // Extension-declared facets: one chip row per facet, labels + colors
    // come from the source. Default state is all-values-checked so
    // unchecking is the act of filtering.
    const facetRows = (s.facets || []).map(f => {
      const sel = st.facetSelections[f.id] || new Set();
      const chips = (f.values || []).map(v => {
        const valStr = String(v.value);
        const checked = sel.has(valStr) ? "checked" : "";
        const swatch = v.color
          ? `<span class="facet-swatch" style="background:${esc(v.color)}"></span>`
          : "";
        return `<label class="fchip facet-chip" title="${esc(v.label)}"><input type="checkbox" data-facet="${esc(f.id)}" data-facet-value="${esc(valStr)}" ${checked}/>${swatch}${esc(v.label)}</label>`;
      }).join("");
      return `<div class="inv-row"><span class="inv-row-lbl">${esc(f.label)}</span>${chips}</div>`;
    }).join("");

    const mySaved = savedSearches.filter(ss => ss.source_id === s.id);
    const savedOpts = ['<option value="">Saved searches…</option>']
      .concat(mySaved.map(ss => `<option value="${esc(ss.id)}">${esc(ss.name)}</option>`))
      .join("");
    const savedRow = `
        <div class="inv-row inv-saved-row">
          <span class="inv-row-lbl">Search</span>
          <select class="inv-saved-select" data-saved-select>${savedOpts}</select>
          <button class="link-btn" data-saved-load disabled title="Apply the selected saved search">Load</button>
          <button class="link-btn" data-saved-delete disabled title="Delete the selected saved search">Delete</button>
          <button class="link-btn" data-saved-save title="Save current filters as a new search">Save current…</button>
        </div>`;

    block.innerHTML = `
      <div class="inv-source-head">
        <input type="checkbox" data-enabled ${st.enabled ? "checked" : ""} title="Show this tile source on the map" />
        <span class="swatch" style="background:${esc(swatch)}"></span>
        <span class="label">${esc(s.label)}</span>
        <span class="count" data-count>${st.enabled ? `${s.n_tiles} tiles` : "off"}</span>
      </div>
      <div class="inv-source-body">
        ${savedRow}
        ${priChips.length ? `<div class="inv-row"><span class="inv-row-lbl">Priority</span>${priChips.join("")}</div>` : ""}
        ${bandChips ? `<div class="inv-row"><span class="inv-row-lbl">Missing</span>${bandChips}</div>` : ""}
        ${catChips ? `<div class="inv-row"><span class="inv-row-lbl">Class</span>${catChips}</div>` : ""}
        ${facetRows}
        <div class="inv-row">
          <span class="inv-row-lbl">Status</span>
          <label class="fchip" title="Drop tiles whose centre is already inside one of your plans">
            <input type="checkbox" data-hide-planned ${st.hidePlanned ? "checked" : ""}/> Hide planned
          </label>
        </div>
      </div>`;
    host.appendChild(block);

    // Wire enabled toggle (also collapses the body when off — clicking the
    // header label toggles open/closed, but only when not clicking the
    // checkbox itself).
    const enabledCb = block.querySelector("input[data-enabled]");
    enabledCb.addEventListener("change", () => {
      st.enabled = enabledCb.checked;
      _saveInvState();
      block.querySelector("[data-count]").textContent = st.enabled ? `${s.n_tiles} tiles` : "off";
      if (st.enabled) ensureTilesLoaded(s.id);
      renderTileOverlay(s.id);  // Plan 4c — no-op until that's wired up.
    });
    block.querySelector(".inv-source-head .label").addEventListener("click", () => {
      st.openRail = !st.openRail;
      block.dataset.open = st.openRail ? "true" : "false";
      _saveInvState();
    });

    // Filter-chip handlers.
    for (const cb of block.querySelectorAll("input[data-priority]")) {
      cb.addEventListener("change", () => {
        const p = parseInt(cb.dataset.priority, 10);
        cb.checked ? st.priorities.add(p) : st.priorities.delete(p);
        _saveInvState();
        renderTileOverlay(s.id);
      });
    }
    for (const cb of block.querySelectorAll("input[data-missing]")) {
      cb.addEventListener("change", () => {
        const b = cb.dataset.missing;
        cb.checked ? st.missing.add(b) : st.missing.delete(b);
        _saveInvState();
        renderTileOverlay(s.id);
      });
    }
    for (const cb of block.querySelectorAll("input[data-cat]")) {
      cb.addEventListener("change", () => {
        const c = cb.dataset.cat;
        cb.checked ? st.categories.add(c) : st.categories.delete(c);
        _saveInvState();
        renderTileOverlay(s.id);
      });
    }
    for (const cb of block.querySelectorAll("input[data-facet]")) {
      cb.addEventListener("change", () => {
        const fid = cb.dataset.facet;
        const val = cb.dataset.facetValue;
        if (!st.facetSelections[fid]) st.facetSelections[fid] = new Set();
        cb.checked ? st.facetSelections[fid].add(val) : st.facetSelections[fid].delete(val);
        _saveInvState();
        renderTileOverlay(s.id);
      });
    }
    const hidePlannedCb = block.querySelector("input[data-hide-planned]");
    if (hidePlannedCb) hidePlannedCb.addEventListener("change", () => {
      st.hidePlanned = hidePlannedCb.checked;
      _saveInvState();
      renderTileOverlay(s.id);
    });

    // Saved-searches row wiring.
    const savedSel    = block.querySelector("[data-saved-select]");
    const savedLoad   = block.querySelector("[data-saved-load]");
    const savedDelete = block.querySelector("[data-saved-delete]");
    const savedSave   = block.querySelector("[data-saved-save]");
    if (savedSel) savedSel.addEventListener("change", () => {
      const has = !!savedSel.value;
      savedLoad.disabled = !has;
      savedDelete.disabled = !has;
    });
    if (savedLoad) savedLoad.addEventListener("click", () => {
      const search = savedSearches.find(ss => ss.id === savedSel.value);
      if (search) applySavedSearch(s.id, search);
    });
    if (savedDelete) savedDelete.addEventListener("click", async () => {
      const search = savedSearches.find(ss => ss.id === savedSel.value);
      if (!search) return;
      if (!confirm(`Delete saved search "${search.name}"?`)) return;
      const ok = await deleteSavedSearch(search.id);
      if (ok) renderInventoryRail();
    });
    if (savedSave) savedSave.addEventListener("click", async () => {
      const name = prompt("Save current filters as:", "");
      if (!name || !name.trim()) return;
      const entry = {
        name: name.trim(),
        source_id: s.id,
        filters: {
          priorities: [...st.priorities],
          missing:    [...st.missing],
          categories: [...st.categories],
          hidePlanned: !!st.hidePlanned,
        },
      };
      const saved = await persistSavedSearch(entry);
      if (saved) renderInventoryRail();
    });
  }
}

async function ensureTilesLoaded(sourceId) {
  if (tileData[sourceId]) return tileData[sourceId];
  try {
    const r = await fetch(`/api/tiles/${encodeURIComponent(sourceId)}`);
    const d = await r.json();
    tileData[sourceId] = Array.isArray(d?.tiles) ? d.tiles : [];
  } catch (e) {
    console.warn(`tiles/${sourceId} fetch failed:`, e);
    tileData[sourceId] = [];
  }
  renderTileOverlay(sourceId);
  return tileData[sourceId];
}

// --- Horizon overlay (Plan A.6 / step 7) --------------------------------
// For latitude φ, the maximum altitude declination δ ever reaches at
// transit is `90 - |φ - δ|`. So:
//   never-up:   |φ - δ| > 90       → δ outside [φ-90, φ+90]
//   below-min:  |φ - δ| > 90-minAlt → δ outside [φ-(90-min), φ+(90-min)]
// Shading the corresponding dec bands gives an instant visual of "what's
// reachable from this site at this min-alt setting" — the rest of the
// map stays clear so coverage and tiles still read crisply.
let _horizonOverlay = null;

function _horizonBands(lat, minAlt) {
  const neverHigh = lat + 90;
  const neverLow  = lat - 90;
  const minHigh   = lat + (90 - minAlt);
  const minLow    = lat - (90 - minAlt);
  const out = { never_up: [], below_min: [] };
  if (neverHigh < 90) out.never_up.push([Math.min(89.99, neverHigh), 89.99]);
  if (neverLow > -90) out.never_up.push([-89.99, Math.max(-89.99, neverLow)]);
  // below-min sits between min-alt cutoff and never-up cutoff (or pole if
  // never-up is empty on that side).
  if (minHigh < 90 && minHigh < (neverHigh < 90 ? neverHigh : 90)) {
    out.below_min.push([minHigh, Math.min(89.99, neverHigh < 90 ? neverHigh : 90)]);
  }
  if (minLow > -90 && minLow > (neverLow > -90 ? neverLow : -90)) {
    out.below_min.push([Math.max(-89.99, neverLow > -90 ? neverLow : -90), minLow]);
  }
  return out;
}

// Build a constant-declination "horizontal" small-circle as a polyline of
// (ra, dec) vertices. 5° RA step is dense enough to read smooth on every
// supported projection at typical zoom levels.
function _decContourLine(decDeg) {
  const pts = [];
  for (let ra = 0; ra <= 360; ra += 5) pts.push([ra, decDeg]);
  return pts;
}

function refreshHorizonOverlay() {
  if (!aladin) return;
  if (!_horizonOverlay) {
    _horizonOverlay = A.graphicOverlay({ color: "#a04040", lineWidth: 2, name: "horizon" });
    aladin.addOverlay(_horizonOverlay);
  }
  _horizonOverlay.removeAll();
  if (!timeAware) return;
  const lat = currentSite.lat;
  const minAlt = currentSite.min_alt_deg ?? 30;
  // Compute the four declination thresholds. Skip any that fall outside the
  // celestial sphere (e.g. north-pole observers have no southern threshold).
  const decsNeverUp  = [lat + 90, lat - 90].filter(d => d > -90 && d < 90);
  const decsBelowMin = [lat + (90 - minAlt), lat - (90 - minAlt)].filter(d => d > -90 && d < 90);

  // Below-min line: amber, "rises but never high enough".
  for (const d of decsBelowMin) {
    _horizonOverlay.add(A.polyline(_decContourLine(d), {
      color: "#d6a04a", lineWidth: 1.5,
    }));
  }
  // Never-up line: red, "never above horizon at all". Drawn after so it
  // wins z-order over the below-min line on overlap (e.g. for very low
  // min-alt where the two thresholds coincide).
  for (const d of decsNeverUp) {
    _horizonOverlay.add(A.polyline(_decContourLine(d), {
      color: "#d65a5a", lineWidth: 2,
    }));
  }
}

// --- Inventory tile rendering on Aladin (Plan 4c) -----------------------
const _tileOverlays = {};   // {<source_id>: A.graphicOverlay}
const _PRI_COLOR = {
  1: "#ff5050",   // urgent red
  2: "#ff9933",   // orange
  3: "#ffd24d",   // yellow
  4: "#7aa2ff",   // muted blue
};
const _PRI_COLOR_OTHER = "#5e6679";  // catch-all for priorities ≥ 5

function _tileColor(priorityLevel) {
  return _PRI_COLOR[priorityLevel] || _PRI_COLOR_OTHER;
}

// When the source declares a `color_facet`, paint each tile by its facet
// value's color instead of the priority palette. Falls back to the
// priority palette for tiles that don't declare a value for the facet.
function _tileColorFor(tile, st) {
  const fid = st && st.colorFacet;
  if (fid) {
    const def = (st.facetDefs || []).find(f => f.id === fid);
    if (def) {
      const v = tile.metadata?.[def.field];
      if (v != null) {
        const valDef = (def.values || []).find(x => String(x.value) === String(v));
        if (valDef && valDef.color) return valDef.color;
      }
    }
  }
  return _tileColor(tile.priority_level);
}

// Plan↔tile cross-ref (Plan 5). Every tile checks against every plan's
// mosaic bounds; if the tile centre falls inside, the tile inherits the
// plan's state. ~40 tiles × ~10 plans = 400 polygon point-tests, sub-ms.
function _planAllGoalsMet(plan) {
  const goals = plan?.filter_goals || {};
  const entries = Object.entries(goals);
  if (!entries.length) return false;     // no goals → can't be "done"
  for (const [, g] of entries) {
    const target = Number(g?.target_hours) || 0;
    if (target <= 0) continue;
    if ((Number(g?.actual_hours) || 0) < target) return false;
  }
  return true;
}

// Per-plan mosaic-bounds corners, optionally memoised via `cornersCache` (a
// Map keyed by plan object) so a caller iterating many tiles against the
// same plan list only pays for planMosaicBoundsCorners() once per plan.
// Not cached across renders: plan.target fields mutate in place while a
// plan is being edited, so a cache must not outlive a single render pass.
function _planCornersFor(pl, cornersCache) {
  if (cornersCache && cornersCache.has(pl)) return cornersCache.get(pl);
  let corners;
  try { corners = planMosaicBoundsCorners(pl); } catch { corners = null; }
  if (cornersCache) cornersCache.set(pl, corners);
  return corners;
}

function tilePlanInfo(tile, cornersCache) {
  const ra = Number(tile.ra_deg), dec = Number(tile.dec_deg);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return { plan: null, state: "none" };
  for (const pl of plans) {
    if (pl?.target?.center_ra_deg == null) continue;
    const corners = _planCornersFor(pl, cornersCache);
    if (!corners || corners.length < 3) continue;
    if (!_ptInRaDecPoly(ra, dec, corners)) continue;
    let state;
    if (_planAllGoalsMet(pl)) state = "done";
    else if (planHasData(pl)) state = "in_progress";
    else state = "queued";
    return { plan: pl, state };
  }
  return { plan: null, state: "none" };
}

// Completion ratio drives fill alpha — 0% complete = bright (~55%), 100% =
// nearly invisible (~5%), so the map literally "crosses off" tiles as the
// user fills them in.
function _tileFillAlphaHex(perBand) {
  const keys = perBand ? Object.keys(perBand) : [];
  if (!keys.length) return "55";
  const covered = keys.filter(k => perBand[k]?.covered).length;
  const ratio = covered / keys.length;     // 0..1
  const alpha = Math.round((1 - ratio) * 0.50 * 255 + 0.05 * 255); // 0.05..0.55
  return alpha.toString(16).padStart(2, "0");
}

function _tileFootprint(tile) {
  if (Array.isArray(tile.footprint) && tile.footprint.length >= 3) {
    return tile.footprint.map(p => [Number(p[0]), Number(p[1])]);
  }
  // Derive a square box from fov_arcmin if footprint is missing.
  const fov = tile.fov_arcmin;
  if (!Array.isArray(fov) || fov.length !== 2) return null;
  const ra = Number(tile.ra_deg), dec = Number(tile.dec_deg);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
  const halfDec = (Number(fov[1]) / 60) / 2;
  const halfRa  = (Number(fov[0]) / 60) / 2 / Math.max(0.01, Math.cos(dec * Math.PI / 180));
  return [
    [ra - halfRa, dec - halfDec],
    [ra + halfRa, dec - halfDec],
    [ra + halfRa, dec + halfDec],
    [ra - halfRa, dec + halfDec],
  ];
}

function _tilePassesFilters(tile, st, getPlanInfo) {
  // Priority: tile must be in the active set. Treat anything past pri-4 as
  // "other" to match the chip's pri-other bucket.
  const p = Number(tile.priority_level) || 0;
  if (p && !st.priorities.has(p)) return false;
  // Missing-band: tile must lack EVERY band the user ticked. Empty set = no filter.
  if (st.missing.size) {
    const pb = tile.per_band || {};
    for (const b of st.missing) {
      if (pb[b]?.covered) return false;
    }
  }
  // Category: tile must have at least one of the ticked categories with count>0.
  // Empty set = no filter.
  if (st.categories.size) {
    const cc = tile.category_counts || {};
    let hit = false;
    for (const c of st.categories) {
      if ((cc[c] || 0) > 0) { hit = true; break; }
    }
    if (!hit) return false;
  }
  // Extension-declared facets: for each facet the source declared, the tile's
  // value (read from tile.metadata[field]) must be in the active selection.
  // Tiles with no value for the facet pass (the facet doesn't apply).
  for (const f of (st.facetDefs || [])) {
    const sel = st.facetSelections?.[f.id];
    if (!sel) continue;
    const v = tile.metadata?.[f.field];
    if (v == null) continue;
    if (!sel.has(String(v))) return false;
  }
  // Hide planned: when on, drop tiles whose centre falls inside any plan's
  // mosaic bounds (regardless of plan state). The Inventory then surfaces
  // only the tiles you haven't queued yet.
  if (st.hidePlanned && getPlanInfo().state !== "none") return false;
  return true;
}

function _tileTooltip(tile) {
  const parts = [tile.id ? `Tile ${tile.id}` : null,
                 `Priority ${tile.priority_level ?? "—"}`];
  if (tile.score != null) parts.push(`Score ${tile.score}`);
  const pb = tile.per_band || {};
  const missing = Object.keys(pb).filter(k => !pb[k]?.covered);
  if (missing.length) parts.push(`Missing: ${missing.join(", ")}`);
  const cc = Object.entries(tile.category_counts || {})
    .filter(([_, n]) => n > 0)
    .map(([c, n]) => `${c}=${n}`);
  if (cc.length) parts.push(cc.join(" "));
  return parts.filter(Boolean).join(" · ");
}

function renderTileOverlay(sourceId) {
  if (!aladin) return;
  const st = invState[sourceId];
  if (!st) return;
  let ovr = _tileOverlays[sourceId];
  // Drop any stale hits for this source — rebuilt below.
  tileHitList = tileHitList.filter(h => h.source_id !== sourceId);
  if (!st.enabled) {
    if (ovr) ovr.removeAll();
    return;
  }
  const tiles = tileData[sourceId];
  if (!tiles) return;       // still loading; ensureTilesLoaded will recall this
  if (!ovr) {
    // lineWidth 0.01 keeps the outline invisible on filled tiles — the
    // colour reads from the fill alone, otherwise heavy borders dominate
    // the map at zoomed-out projections.
    ovr = A.graphicOverlay({ color: "#ffffff", lineWidth: 0.5, name: `inv_${sourceId}` });
    aladin.addOverlay(ovr);
    _tileOverlays[sourceId] = ovr;
  }
  ovr.removeAll();
  let drawn = 0;
  // Memoise plan mosaic-bounds corners for this render pass only (plans can
  // be edited in place between renders, so the cache must not persist).
  // tilePlanInfo() is O(plans) per tile; without this, ~40 tiles × ~10 plans
  // recomputed planMosaicBoundsCorners() up to twice per tile per render.
  const planCornersCache = new Map();
  for (const tile of tiles) {
    // planInfo is needed for the Hide-planned filter and for downstream
    // click handling, but only ever computed once per tile per render pass
    // (memoised in the closure below) instead of once in the filter and
    // again unconditionally afterward. It no longer drives tile styling:
    // planned tiles read with their normal status colour so within-plan
    // priority stays legible.
    let planInfo = null;
    const getPlanInfo = () => (planInfo ??= tilePlanInfo(tile, planCornersCache));
    if (!_tilePassesFilters(tile, st, getPlanInfo)) continue;
    const corners = _tileFootprint(tile);
    if (!corners) continue;
    const color = _tileColorFor(tile, st);
    const alphaHex = _tileFillAlphaHex(tile.per_band);
    planInfo = getPlanInfo();
    const poly = A.polygon(corners, {
      color: color,
      lineWidth: 0.5,
      fillColor: color + alphaHex,
    });
    if (poly && tile && (tile.id || tile.score != null)) {
      poly.actionOnHover = _tileTooltip(tile);  // hover string surface for debug
    }
    ovr.add(poly);
    tileHitList.push({ poly, tile, source_id: sourceId, corners, plan_state: planInfo.state, plan: planInfo.plan });
    drawn++;
  }
  // Update the counter pill in the rail to reflect the filtered subset.
  const block = document.querySelector(`.inv-source[data-source-id="${sourceId}"]`);
  const total = (tileSources.find(x => x.id === sourceId) || {}).n_tiles || tiles.length;
  if (block) {
    const cnt = block.querySelector("[data-count]");
    if (cnt) cnt.textContent = `${drawn} of ${total} tiles`;
  }
}

// --- Tile detail panel (Plan 2a) -----------------------------------------
function renderTilePanel(tile, sourceId) {
  panelMode = "tile-detail";
  updateSearchVisibility();
  selectedTileKey = `${sourceId}/${tile.id}`;
  selectedTargetId = null;
  saveUiState();
  const panel = document.getElementById("panelBody");
  if (!panel) return;

  const sourceMeta = tileSources.find(s => s.id === sourceId) || {};
  const sourceLabel = sourceMeta.label || sourceId;

  const fov = Array.isArray(tile.fov_arcmin) && tile.fov_arcmin.length === 2
    ? `${Number(tile.fov_arcmin[0]).toFixed(1)}' × ${Number(tile.fov_arcmin[1]).toFixed(1)}'`
    : "—";

  // Per-band coverage table — band, covered marker, source label, hours if present.
  const pb = tile.per_band || {};
  const bandRows = Object.entries(pb).map(([band, info]) => {
    const cov = info?.covered;
    const mark = cov ? "<span style='color:#6fcc52'>✓</span>" : "<span style='color:#888'>—</span>";
    const src = info?.source ? esc(info.source) : "<span style='color:#666'>none</span>";
    const hrs = (info && typeof info.hours === "number") ? `${info.hours.toFixed(1)}h` : "";
    return `<tr><td>${esc(band)}</td><td>${mark}</td><td class="num">${src}</td><td class="num">${esc(hrs)}</td></tr>`;
  }).join("");

  const cc = tile.category_counts || {};
  const catRows = Object.entries(cc)
    .filter(([_, n]) => n > 0)
    .map(([c, n]) => `<span class="fchip">${esc(c)}: ${n}</span>`)
    .join(" ");

  // Extension-declared facet badges: one chip per facet that has a value
  // for this tile. Coloured swatch + facet label + value label, drawn from
  // the source's facet declaration.
  const st = invState[sourceId];
  const facetBadges = (st?.facetDefs || []).map(f => {
    const v = tile.metadata?.[f.field];
    if (v == null) return "";
    const valDef = (f.values || []).find(x => String(x.value) === String(v));
    if (!valDef) return "";
    const swatch = valDef.color
      ? `<span class="facet-swatch" style="background:${esc(valDef.color)}"></span>`
      : "";
    return `<span class="fchip facet-chip">${swatch}${esc(f.label)}: ${esc(valDef.label)}</span>`;
  }).filter(Boolean).join(" ");

  // Visibility section (Plan 2c) wires up next; placeholder div the loader fills in.
  const visPlaceholder = `<div class="vis-section" id="tileVisSection"></div>`;

  // Suggested-bands hint for the Create-plan button (Plan 2d).
  const missing = Object.entries(pb).filter(([_, v]) => !v?.covered).map(([b]) => b);
  const suggestion = missing.length
    ? `Suggested bands: ${missing.map(b => `<code>${esc(b)}</code>`).join(", ")}`
    : "All declared bands already covered.";

  // Plan↔tile cross-ref status (Plan 5).
  const planInfo = tilePlanInfo(tile);
  const stateLabel = {
    queued: "Queued in your planner",
    in_progress: "In progress in your planner",
    done: "Marked done in your planner",
  }[planInfo.state];
  const planRow = planInfo.plan
    ? `<div class="tile-plan-row" data-plan-id="${esc(planInfo.plan.id)}">
         <span class="tile-plan-state tps-${planInfo.state}">${esc(stateLabel)}</span>
         <button id="tileOpenPlan" class="link-btn">Open plan →</button>
       </div>`
    : "";

  panel.innerHTML = `
    <div>
      <a class="back-link" id="backToList" href="#">← Back to list</a>
      <h3>${esc(sourceLabel)} · <span style="color:#a2aec2">${esc(tile.id || "")}</span></h3>

      ${visPlaceholder}

      <h4>Position</h4>
      <table>
        <tr><td>RA / Dec</td><td class="num">${Number(tile.ra_deg).toFixed(3)}° / ${Number(tile.dec_deg).toFixed(3)}°</td></tr>
        <tr><td>FOV</td><td class="num">${fov}</td></tr>
        <tr><td>Priority</td><td class="num">P${tile.priority_level ?? "—"}</td></tr>
        ${tile.score != null ? `<tr><td>Score</td><td class="num">${tile.score}</td></tr>` : ""}
      </table>

      ${facetBadges ? `<div class="tile-facet-badges">${facetBadges}</div>` : ""}

      ${bandRows ? `
      <h4>Per-band coverage</h4>
      <table>
        <thead><tr><th>Band</th><th>✓</th><th class="num">Source</th><th class="num">Hours</th></tr></thead>
        <tbody>${bandRows}</tbody>
      </table>` : ""}

      ${catRows ? `<h4>Catalog object counts</h4><div>${catRows}</div>` : ""}

      ${planRow}

      <div class="tile-actions">
        <div class="tile-suggestion">${suggestion}</div>
        <button id="tileCreatePlan" class="primary">Create new plan from this tile</button>
      </div>
    </div>`;

  document.getElementById("backToList")?.addEventListener("click", e => {
    e.preventDefault(); renderTargetList();
  });
  document.getElementById("tileCreatePlan")?.addEventListener("click", () => {
    promotePlanFromTile(tile, sourceMeta);
  });
  document.getElementById("tileOpenPlan")?.addEventListener("click", () => {
    if (planInfo.plan) {
      if (!planningMode) setPlanningMode(true);
      renderPlanEditor(planInfo.plan);
    }
  });
  loadTileVisibility(tile);
}

// Build sparkline + Now/Trend chips against an arbitrary {ra, dec} point by
// reusing the same renderers that drive the target detail panel — they read
// `visibilityData` for site metadata, then take the bin list from a
// `_pointBins` cache keyed by the tile.
const _pointVisCache = new Map();   // "<lat>,<lon>,<min>,<ra>,<dec>" → bins[]

async function loadTileVisibility(tile) {
  // Time-aware off → leave the section empty (the CSS rule on .vis-section
  // hides it anyway, but skip the network round-trip too).
  if (!timeAware) return;
  const ra = Number(tile.ra_deg), dec = Number(tile.dec_deg);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
  const lat = currentSite.lat, lon = currentSite.lon, minAlt = currentSite.min_alt_deg ?? 30;
  const key = `${lat.toFixed(4)},${lon.toFixed(4)},${minAlt.toFixed(2)},${ra.toFixed(4)},${dec.toFixed(4)}`;
  let bins = _pointVisCache.get(key);
  if (!bins) {
    try {
      const url = activeSiteId
        ? `/api/visibility/point?site_id=${encodeURIComponent(activeSiteId)}&ra=${ra}&dec=${dec}`
        : `/api/visibility/point?lat=${lat}&lon=${lon}&min_alt_deg=${minAlt}&ra=${ra}&dec=${dec}`;
      const r = await fetch(url);
      if (!r.ok) return;
      const d = await r.json();
      bins = Array.isArray(d?.months) ? d.months : null;
      if (!bins) return;
      _pointVisCache.set(key, bins);
    } catch (e) {
      console.warn("tile visibility fetch failed:", e);
      return;
    }
  }
  // Stitch a synthetic visibilityData entry so the existing helpers
  // (year-curve, Now chip, Trend chip) work without modification.
  const synthId = `__tile__${ra.toFixed(4)}_${dec.toFixed(4)}`;
  if (!visibilityData) visibilityData = { site: { min_alt_deg: minAlt }, targets: {} };
  visibilityData.targets[synthId] = bins;

  const sec = document.getElementById("tileVisSection");
  if (!sec) return;
  // Bail if the user navigated away in the meantime.
  if (panelMode !== "tile-detail") return;
  const siteName = sites.find(s => s.id === activeSiteId)?.name || "current site";
  const nowChip = nowChipHtml(synthId);
  const trChip = trendChipHtml(synthId);
  const bar = yearCurveBarHtml(synthId);
  const best = bestBinFor(bins);
  const bestTxt = best && best.label !== "not_visible"
    ? `Peak: ${_MONTH_LABELS[best.month-1]} (${_LABEL_PRETTY[best.label]}, ${best.hours_above_min}h above min)`
    : (best && best.peak_alt_deg != null && best.peak_alt_deg >= 0
        ? `Never reaches min altitude during dark (year-best peak ${best.peak_alt_deg}°).`
        : "Below horizon during dark all year.");
  sec.innerHTML = `
    <h4>Visibility — ${esc(siteName)}</h4>
    ${bar}
    <div class="vis-meta-row">
      <span class="vis-meta">${esc(bestTxt)}</span>
      ${nowChip}
    </div>
    <div class="vis-meta-row">
      <span class="vis-now"></span>
      ${trChip}
    </div>`;
}

// Plan visibility — aggregated per-month {panels_visible, total_panels} for
// a mosaic, plus per-panel bins for the heatmap. Cached by plan id +
// geometry hash so editing the centre / mosaic shape / gear FOV invalidates
// correctly. Pending fetches are tracked so the UI can show a faded/spinner
// state on rows whose data hasn't arrived yet.
const _planVisCache = new Map();    // "<id>|<geomHash>" → endpoint payload
const _planVisPending = new Set();  // keys currently being fetched

function planGeomHash(plan) {
  const tg = plan.target || {};
  const m = planMosaic(plan);
  const [fw, fh] = planFovArcmin(plan);
  const lat = currentSite?.lat ?? 0;
  const lon = currentSite?.lon ?? 0;
  const minAlt = currentSite?.min_alt_deg ?? 30;
  return [
    (tg.center_ra_deg || 0).toFixed(4),
    (tg.center_dec_deg || 0).toFixed(4),
    (tg.rotation_deg || 0).toFixed(1),
    `${m.rows}x${m.cols}@${m.overlap_pct}`,
    `${fw.toFixed(1)}x${fh.toFixed(1)}`,
    `${lat.toFixed(4)},${lon.toFixed(4)},${minAlt}`,
  ].join("|");
}

function planVisCacheKey(plan) {
  return `${plan.id}|${planGeomHash(plan)}`;
}

async function loadPlanVisibility(plan) {
  if (!timeAware) return null;
  if (plan?.target?.center_ra_deg == null || plan?.target?.center_dec_deg == null) return null;
  const key = planVisCacheKey(plan);
  const cached = _planVisCache.get(key);
  if (cached) return cached;
  if (_planVisPending.has(key)) return null;  // another caller in flight
  _planVisPending.add(key);
  try {
    const panels = mosaicPanelCenters(plan).map(p => ({ ra_deg: p.ra_deg, dec_deg: p.dec_deg }));
    if (!panels.length) return null;
    const qs = activeSiteId
      ? `?site_id=${encodeURIComponent(activeSiteId)}`
      : `?lat=${currentSite.lat}&lon=${currentSite.lon}&min_alt_deg=${currentSite.min_alt_deg}`;
    const r = await fetch(`/api/visibility/panels${qs}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ panels }),
    });
    if (!r.ok) return null;
    const d = await r.json();
    _planVisCache.set(key, d);
    return d;
  } catch (e) {
    console.warn("plan visibility fetch failed:", e);
    return null;
  } finally {
    _planVisPending.delete(key);
  }
}

// Background-fetch any uncached plans, updating the active panel in place
// as each resolves. Safe to call on every panel render — already-cached
// plans short-circuit instantly and pending ones don't double-fetch.
async function loadAllPlanVisibility() {
  if (!timeAware || !plans?.length) return;
  await Promise.all(plans.map(async pl => {
    const before = _planVisCache.has(planVisCacheKey(pl));
    await loadPlanVisibility(pl);
    if (!before) {
      if (panelMode === "plan-list") updatePlanRowVis(pl);
      else if (panelMode === "plan-edit" && editingPlan?.id === pl.id) updatePlanEditorVis();
    }
  }));
}

function planVisCellHtml(plan, { compact = true } = {}) {
  const m = planMosaic(plan);
  const isMosaic = (m.rows * m.cols) > 1;
  const data = _planVisCache.get(planVisCacheKey(plan));
  if (!data) {
    // Render 12 empty cells so the sparkline keeps its grid width while
    // loading — without them the inline-grid collapses to 0 and the row
    // looks like nothing's there.
    const cls = compact ? "yc-sparkline yc-loading" : "yc-bar yc-loading";
    const placeholder = `<span class="yc-cell vc-not_visible"></span>`.repeat(12);
    return `<span class="${cls}" aria-busy="true">${placeholder}</span>`;
  }
  return isMosaic ? planVisFracHtml(data, compact) : planVisLabelHtml(data, compact);
}

function planVisFracHtml(data, compact) {
  const nowMonth = new Date().getUTCMonth() + 1;
  const cells = (data.months || []).map(m => {
    const total = m.total_panels || 0;
    const pct = total > 0 ? Math.round((m.panels_visible / total) * 100) : 0;
    const cur = m.month === nowMonth ? "vc-current" : "";
    const tip = `${_MONTH_LABELS[m.month-1]}: ${m.panels_visible}/${total} panels visible (${pct}%)`;
    return `<span class="yc-cell yc-frac ${cur}" style="--frac:${pct}%" title="${esc(tip)}"></span>`;
  }).join("");
  const cls = compact ? "yc-sparkline" : "yc-bar";
  return `<span class="${cls}" aria-label="visibility by month">${cells}</span>`;
}

function planVisLabelHtml(data, compact) {
  // Single-panel plan: render the same label-bucket sparkline as the target
  // list so it carries "great vs fair vs partial" detail.
  const bins = (data.per_panel?.[0]?.months) || [];
  const nowMonth = new Date().getUTCMonth() + 1;
  const cells = bins.map(b => {
    const cls = b.month === nowMonth ? `yc-cell vc-${b.label} vc-current` : `yc-cell vc-${b.label}`;
    return `<span class="${cls}" title="${esc(_cellTooltip(b))}"></span>`;
  }).join("");
  const wrap = compact ? "yc-sparkline" : "yc-bar";
  return `<span class="${wrap}" aria-label="visibility by month">${cells}</span>`;
}

function updatePlanRowVis(plan) {
  const cell = document.querySelector(`.plan-row[data-plan-id="${plan.id}"] .plan-vis`);
  if (cell) cell.innerHTML = planVisCellHtml(plan, { compact: true });
}

function planVisMetaText(data) {
  if (!data) return "Loading…";
  let best = null, bestFrac = -1;
  for (const m of data.months || []) {
    const frac = m.total_panels > 0 ? m.panels_visible / m.total_panels : 0;
    if (frac > bestFrac) { bestFrac = frac; best = m; }
  }
  if (!best) return "";
  if (data.panel_count === 1) {
    const bin = (data.per_panel?.[0]?.months) || [];
    const ranked = bin.slice().sort((a, b) =>
      (_LABEL_RANK[b.label] || 0) - (_LABEL_RANK[a.label] || 0));
    const top = ranked[0];
    if (!top || top.label === "not_visible") return "Below min altitude all year.";
    return `Peak: ${_MONTH_LABELS[top.month-1]} (${_LABEL_PRETTY[top.label]}, ${top.hours_above_min}h above min)`;
  }
  if (best.panels_visible === 0) return "No panel above min altitude any month.";
  const pct = Math.round((best.panels_visible / best.total_panels) * 100);
  return `Peak: ${_MONTH_LABELS[best.month-1]} — ${best.panels_visible}/${best.total_panels} panels (${pct}%)`;
}

function planVisHeatmapHtml(data) {
  if (!data || !data.per_panel || data.per_panel.length <= 1) return "";
  const nowMonth = new Date().getUTCMonth() + 1;
  // Sort by dec descending then ra so a strip mosaic reads top→bottom by row.
  const indexed = data.per_panel.map((p, i) => ({ p, i }));
  indexed.sort((a, b) => (b.p.dec_deg - a.p.dec_deg) || (a.p.ra_deg - b.p.ra_deg));
  const rows = indexed.map(({ p, i }) => {
    const cells = (p.months || []).map(b => {
      const cls = b.month === nowMonth ? `yc-cell vc-${b.label} vc-current` : `yc-cell vc-${b.label}`;
      return `<span class="${cls}" title="${esc(_cellTooltip(b))}"></span>`;
    }).join("");
    const label = `${p.ra_deg.toFixed(2)}, ${p.dec_deg.toFixed(2)}`;
    return `<div class="vis-heatmap-row" title="Panel ${i+1}: ${esc(label)}"><span class="vis-heatmap-label">${i+1}</span>${cells}</div>`;
  }).join("");
  return rows;
}

function updatePlanEditorVis() {
  if (panelMode !== "plan-edit" || !editingPlan) return;
  const fs = document.getElementById("planVisFieldset");
  if (!fs) return;
  const data = _planVisCache.get(planVisCacheKey(editingPlan));
  const agg = fs.querySelector(".plan-vis-aggregate");
  if (agg) agg.innerHTML = planVisCellHtml(editingPlan, { compact: false });
  const meta = fs.querySelector("#planVisMeta");
  if (meta) meta.textContent = planVisMetaText(data);
  const heat = fs.querySelector("#planVisHeatmap");
  if (heat) heat.innerHTML = planVisHeatmapHtml(data);
}

let _planVisDebounceTimer = null;
function schedulePlanVisRefresh() {
  clearTimeout(_planVisDebounceTimer);
  _planVisDebounceTimer = setTimeout(async () => {
    if (!timeAware || !editingPlan || panelMode !== "plan-edit") return;
    updatePlanEditorVis();          // show loading state immediately
    await loadPlanVisibility(editingPlan);
    updatePlanEditorVis();
  }, 300);
}

// Sort metrics derived from the cached visibility payload. Plans without
// data yet sort to the end (Infinity sentinel) so they don't disrupt the
// stable order — they shuffle into place once their fetch resolves and
// loadAllPlanVisibility re-renders.
function planPanelsUpNow(plan) {
  const data = _planVisCache.get(planVisCacheKey(plan));
  if (!data) return -1;
  const nowMonth = new Date().getUTCMonth() + 1;
  const m = (data.months || []).find(x => x.month === nowMonth);
  if (!m) return -1;
  return (m.total_panels > 0) ? (m.panels_visible / m.total_panels) : 0;
}

function planPeakPanelsMonth(plan) {
  const data = _planVisCache.get(planVisCacheKey(plan));
  if (!data) return -1;
  let best = -1;
  for (const m of data.months || []) {
    const frac = (m.total_panels > 0) ? (m.panels_visible / m.total_panels) : 0;
    if (frac > best) best = frac;
  }
  return best;
}

// Promotes a tile to a new plan (Plan 2d). Switches into planning mode if
// not already there, builds an empty plan with the tile's centre + a name
// derived from the source label + tile id, and opens the editor. FOV is
// owned by the gear preset (per design decision 2d) — left to the user
// to pick when they choose telescope/camera in the editor.
function promotePlanFromTile(tile, sourceMeta) {
  if (!planningMode) setPlanningMode(true);
  const p = newEmptyPlan();
  // newEmptyPlan reads the current Aladin centre — overwrite with the tile's
  // own centre so the editor opens framed on the right spot regardless of
  // where the user happened to be panned.
  p.target.center_ra_deg = Number(tile.ra_deg);
  p.target.center_dec_deg = Number(tile.dec_deg);
  p.target.rotation_deg = 0;
  const sourceLabel = sourceMeta?.label || "";
  const tileId = tile.id || "";
  p.target.name = [sourceLabel, tileId].filter(Boolean).join(" · ") || "From tile";
  // Pre-fill project_name from the source label so the TS sync groups
  // tile-derived plans under a meaningful project node instead of
  // "Unassigned". User can override before saving.
  if (sourceLabel && !p.project_name) p.project_name = sourceLabel;
  plans.push(p);
  renderPlanEditor(p);
}

async function loadCatalogs() {
  try {
    const r = await fetch("/api/catalogs");
    catalogsData = await r.json();
    updateCatalogStatusHint();
    // if any catalog toggle is already checked, redraw
    for (const id of catalogDomIds()) {
      const cb = document.getElementById(id);
      if (cb && cb.checked) cb.dispatchEvent(new Event("change"));
    }
    return catalogsData;
  } catch (e) {
    console.warn("catalogs not available yet:", e);
    updateCatalogStatusHint();
    return {};
  }
}

// Surface "no catalogs loaded" in the rail summary so the user knows toggles
// are no-ops until scripts/fetch_catalogs.py has been run. Empty payload is
// the silent-failure path: file missing on disk → /api/catalogs returns {}.
function updateCatalogStatusHint() {
  const note = document.querySelector("#railCatalogs .broken-note");
  if (!note) return;
  const total = Object.values(catalogsData || {})
    .reduce((n, v) => n + (Array.isArray(v) ? v.length : 0), 0);
  if (total > 0) {
    note.style.display = "none";
  } else {
    note.style.display = "";
    note.textContent = "(no catalogs loaded — run scripts/fetch_catalogs.py)";
    note.title = "Catalog overlays need data/catalogs.json. "
      + "Run `python scripts/fetch_catalogs.py` once (network I/O, ~30s) and reload.";
  }
}

// Filter chips are populated from the manifest so users only see filters
// they actually have data for. Hides hardcoded chips like "V" that no
// FITS file ever uses, and surfaces real-life filters like IDAS/IR.
function _availableFilters() {
  const NOISE = new Set(["Unknown", "NoFilter"]);
  const present = new Set();
  for (const t of (manifest?.targets || [])) {
    for (const [f, d] of Object.entries(t.filters || {})) {
      if ((d?.total_hours || 0) > 0 && !NOISE.has(f)) present.add(f);
    }
  }
  // LRGBHOS order, matching the planning + coverage target lists.
  // Extras (IDAS/IR/etc.) are intentionally hidden for now.
  const CANON = ["L", "R", "G", "B", "Ha", "OIII", "SII"];
  return CANON.filter(f => present.has(f));
}

function renderFilterChips() {
  const host = document.getElementById("filterChips");
  if (!host) return;
  // Wipe everything except the leading "Filters:" label.
  for (const el of [...host.children]) if (!el.classList.contains("label")) el.remove();
  for (const f of _availableFilters()) {
    const lbl = document.createElement("label");
    lbl.className = "fchip";
    const checked = selectedFilters.has(f) ? "checked" : "";
    lbl.innerHTML = `<input type="checkbox" data-f="${esc(f)}" ${checked}/> ${esc(f)}`;
    host.appendChild(lbl);
    const cb = lbl.querySelector("input");
    cb.addEventListener("change", () => {
      if (cb.checked) selectedFilters.add(cb.dataset.f);
      else selectedFilters.delete(cb.dataset.f);
      saveUiState();
      redrawFootprints();
    });
  }
}

function setupFilterUI() {
  renderFilterChips();
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      searchTokens = tokenizeSearch(searchInput.value);
      saveUiState();
      scheduleRedrawFootprints();
    });
  }
  document.getElementById("filterLogic").addEventListener("change", e => {
    filterLogic = e.target.value;
    saveUiState();
    redrawFootprints();
  });
  document.getElementById("depthSlider").addEventListener("input", e => {
    minHours = parseFloat(e.target.value);
    document.getElementById("depthValue").textContent = `${minHours}h`;
    saveUiState();
    scheduleRedrawFootprints();
  });
  populateGapDropdowns();
  document.getElementById("gapMode").addEventListener("click", () => {
    gapEnabled = !gapEnabled;
    const btn = document.getElementById("gapMode");
    if (gapEnabled) {
      btn.textContent = "Hide gaps";
      btn.style.background = "#663";
      loadGaps();
    } else {
      btn.textContent = "Find gaps";
      btn.style.background = "";
      clearGapOverlays();
      const stats = document.getElementById("gapStats");
      if (stats) stats.textContent = "";
    }
    saveUiState();
  });
  document.getElementById("exportCsv").addEventListener("click", () => {
    window.location = "/api/export/priority";
  });

  for (const radio of document.querySelectorAll("input[name=completionFilter]")) {
    radio.addEventListener("change", e => {
      if (!e.target.checked) return;
      completionFilter = e.target.value;
      saveUiState();
      redrawFootprints();
      if (panelMode === "list") renderTargetList();
    });
  }

  for (const id of ["railFilters", "railSources", "railCatalogs"]) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("toggle", () => saveUiState());
  }

  document.getElementById("projSel").addEventListener("change", e => {
    aladin.setProjection(e.target.value);
    saveUiState();
  });
  document.getElementById("frameSel").addEventListener("change", e => {
    aladin.setFrame(e.target.value);
    saveUiState();
  });
  document.getElementById("skyMore")?.addEventListener("click", () => { openAladinSurveyBrowser(); });
  document.getElementById("skySel").addEventListener("change", e => {
    const id = e.target.value;
    if (id === CUSTOM_SURVEY_VALUE) return;
    try { aladin.setImageSurvey(surveyLayerFor(id)); } catch (err) { console.warn("setImageSurvey failed", id, err); }
    // Aladin swaps the base layer asynchronously, so caption from the chosen
    // id rather than from getBaseImageLayer(), which may still be the old one.
    syncSkyControl(id);
    saveUiState();
  });

  const siteSel = document.getElementById("siteSel");
  siteSel.addEventListener("change", () => {
    activeSiteId = siteSel.value;
    localStorage.setItem("acp.active_site_id", activeSiteId);
    applyActiveSite();
    // Clear stale per-target altitudes from the previous site immediately so
    // the detail panel's "Now: X°" line doesn't briefly display old numbers
    // while the new fetch is in flight. updateObsNow + loadVisibility below
    // will repopulate.
    currentAlts = {};
    visibilityData = null;
    rerenderActivePanel();
    updateObsNow();
    if (timeAware) loadVisibility();
    refreshHorizonOverlay();
  });
  document.getElementById("siteAddBtn").addEventListener("click", () => openSiteModal(null));
  document.getElementById("siteEditBtn").addEventListener("click", () => openSiteModal(activeSiteId));

  document.getElementById("siteForm").addEventListener("submit", e => {
    e.preventDefault();
    saveSiteFromForm();
  });
  document.getElementById("siteFCancel").addEventListener("click", closeSiteModal);
  document.getElementById("siteFDelete").addEventListener("click", deleteActiveSite);
}

// --- Sites (Plan A.1) ----------------------------------------------------
// `sites` is loaded from /api/sites; the user's active site is held in
// localStorage so different browsers/machines can pick different defaults
// against the same shared sites.json.

async function initSites() {
  try {
    const r = await fetch("/api/sites");
    const d = await r.json();
    sites = Array.isArray(d.sites) ? d.sites : [];
  } catch {
    sites = [];
  }
  if (!sites.length) {
    // Backend always seeds defaults, so this should never fire — but degrade
    // gracefully with a Mauna Kea fallback if it does.
    sites = [{id: "mauna_kea", name: "Mauna Kea, Hawaii", lat: 19.82, lon: -155.47, elev_m: 4205, min_alt_deg: 30}];
  }
  activeSiteId = localStorage.getItem("acp.active_site_id");
  if (!sites.some(s => s.id === activeSiteId)) activeSiteId = sites[0].id;
  renderSiteOptions();
  applyActiveSite();
}

function renderSiteOptions() {
  const sel = document.getElementById("siteSel");
  if (!sel) return;
  sel.innerHTML = "";
  for (const s of sites) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.name} (${s.lat.toFixed(2)}, ${s.lon.toFixed(2)})`;
    sel.appendChild(opt);
  }
  sel.value = activeSiteId;
}

function applyActiveSite() {
  const s = sites.find(x => x.id === activeSiteId) || sites[0];
  if (!s) return;
  currentSite = {
    lat: s.lat,
    lon: s.lon,
    height: s.elev_m ?? 0,
    min_alt_deg: s.min_alt_deg ?? 30,
  };
}

function openSiteModal(siteId) {
  const dlg = document.getElementById("siteModal");
  const editing = sites.find(s => s.id === siteId) || null;
  document.getElementById("siteModalTitle").textContent = editing ? "Edit site" : "Add site";
  document.getElementById("siteFName").value   = editing?.name ?? "";
  document.getElementById("siteFLat").value    = editing?.lat ?? "";
  document.getElementById("siteFLon").value    = editing?.lon ?? "";
  document.getElementById("siteFElev").value   = editing?.elev_m ?? "";
  document.getElementById("siteFMinAlt").value = editing?.min_alt_deg ?? 30;
  const delBtn = document.getElementById("siteFDelete");
  // Don't allow deleting the last remaining site — the topbar dropdown
  // would be unusable and the visibility code assumes at least one site.
  delBtn.hidden = !editing || sites.length <= 1;
  delBtn.dataset.siteId = editing?.id ?? "";
  document.getElementById("siteFormError").hidden = true;
  dlg.dataset.editingId = editing?.id ?? "";
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  document.getElementById("siteFName").focus();
}

function closeSiteModal() {
  const dlg = document.getElementById("siteModal");
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

function _slugify(s) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "site";
}

async function saveSiteFromForm() {
  const dlg = document.getElementById("siteModal");
  const editingId = dlg.dataset.editingId || "";
  const name = document.getElementById("siteFName").value.trim();
  const lat = parseFloat(document.getElementById("siteFLat").value);
  const lon = parseFloat(document.getElementById("siteFLon").value);
  const elevRaw = document.getElementById("siteFElev").value;
  const minAltRaw = document.getElementById("siteFMinAlt").value;
  const errEl = document.getElementById("siteFormError");
  const showErr = msg => { errEl.textContent = msg; errEl.hidden = false; };

  if (!name) return showErr("Name is required.");
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) return showErr("Latitude must be between -90 and 90.");
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) return showErr("Longitude must be between -180 and 180.");
  const elev = elevRaw === "" ? null : parseFloat(elevRaw);
  if (elev !== null && (!Number.isFinite(elev) || elev < -430 || elev > 9000)) {
    return showErr("Elevation must be between -430 and 9000 m.");
  }
  const minAlt = minAltRaw === "" ? null : parseFloat(minAltRaw);
  if (minAlt !== null && (!Number.isFinite(minAlt) || minAlt < 0 || minAlt > 90)) {
    return showErr("Min altitude must be between 0 and 90°.");
  }

  let id = editingId;
  if (!id) {
    let base = _slugify(name);
    id = base;
    let n = 2;
    while (sites.some(s => s.id === id)) id = `${base}-${n++}`;
  }
  const next = {id, name, lat, lon};
  if (elev !== null) next.elev_m = elev;
  if (minAlt !== null) next.min_alt_deg = minAlt;

  const newSites = editingId
    ? sites.map(s => s.id === editingId ? next : s)
    : [...sites, next];
  await persistSites(newSites, id);
  closeSiteModal();
}

async function deleteActiveSite() {
  const dlg = document.getElementById("siteModal");
  const sid = dlg.dataset.editingId;
  if (!sid || sites.length <= 1) return;
  if (!confirm(`Delete site "${sites.find(s => s.id === sid)?.name}"?`)) return;
  const newSites = sites.filter(s => s.id !== sid);
  const nextActive = (activeSiteId === sid ? newSites[0].id : activeSiteId);
  await persistSites(newSites, nextActive);
  closeSiteModal();
}

async function persistSites(newSites, nextActiveId) {
  try {
    const r = await fetch("/api/sites", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({sites: newSites}),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      const errEl = document.getElementById("siteFormError");
      if (errEl) {
        errEl.textContent = j.error || `Save failed (${r.status}).`;
        errEl.hidden = false;
      }
      return;
    }
    sites = (await r.json()).sites;
    activeSiteId = nextActiveId;
    localStorage.setItem("acp.active_site_id", activeSiteId);
    renderSiteOptions();
    applyActiveSite();
    // Drop stale data from the previous site so the panel doesn't briefly
    // show old numbers between the rerender and the in-flight fetches.
    currentAlts = {};
    visibilityData = null;
    rerenderActivePanel();
    updateObsNow();
    if (timeAware) loadVisibility();
    refreshHorizonOverlay();
  } catch (e) {
    const errEl = document.getElementById("siteFormError");
    if (errEl) {
      errEl.textContent = `Network error: ${e.message || e}`;
      errEl.hidden = false;
    }
  }
}

function rerenderActivePanel() {
  if (panelMode === "list") renderTargetList();
  else if (panelMode === "detail" && selectedTargetId != null && manifest) {
    const t = manifest.targets.find(x => x.target_id === selectedTargetId);
    if (t) renderTargetPanel(t);
  }
}

async function updateObsNow() {
  // Time-aware off = the statusbar string is hidden by CSS anyway, but skip
  // the network round-trip so the page stays quiet for friends/visitors who
  // don't care about visibility.
  if (!timeAware) return;
  try {
    const now = new Date().toISOString();
    const r = await fetch(`/api/observability?lat=${currentSite.lat}&lon=${currentSite.lon}&time=${now}`);
    const d = await r.json();
    if (!d.targets) return;
    // Cache per-target altitudes for sort=up_tonight + the detail panel's
    // "now" line. Refreshed on the same 60s cadence as the statusbar.
    currentAlts = {};
    for (const x of d.targets) currentAlts[x.target_id] = x.alt_deg;
    const n_above30 = d.targets.filter(x => x.alt_deg >= 30).length;
    const n_above60 = d.targets.filter(x => x.alt_deg >= 60).length;
    document.getElementById("obsNow").textContent =
      `@${currentSite.lat},${currentSite.lon}: ${n_above30} targets >30° alt, ${n_above60} >60° alt (UTC ${now.slice(11,16)})`;
    // Refresh whichever panel is showing so the new altitudes (and the
    // "Now: …°" line in the detail panel, and the up-tonight sort) reflect
    // the latest fetch — particularly important right after a site change.
    rerenderActivePanel();
  } catch (e) {
    document.getElementById("obsNow").textContent = "(observability offline)";
  }
}

// --- Visibility data (Plan A.4) ------------------------------------------
async function loadVisibility() {
  if (!timeAware) { visibilityData = null; return; }
  try {
    const url = activeSiteId
      ? `/api/visibility?site_id=${encodeURIComponent(activeSiteId)}`
      : `/api/visibility?lat=${currentSite.lat}&lon=${currentSite.lon}&min_alt_deg=${currentSite.min_alt_deg}`;
    const r = await fetch(url);
    if (!r.ok) { visibilityData = null; return; }
    visibilityData = await r.json();
  } catch (e) {
    visibilityData = null;
  }
  // Re-render so sparklines + best-month chips appear.
  rerenderActivePanel();
}

const _MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const _LABEL_RANK = { not_visible: 0, partial: 1, fair: 2, good: 3, great: 4 };
const _LABEL_PRETTY = {
  not_visible: "Not visible",
  partial: "Partial",
  fair: "Fair",
  good: "Good",
  great: "Great",
};

// "Not visible" splits into two real states the user cares about: target
// genuinely never rises (peak < 0°) vs target rises but doesn't clear the
// site's min-altitude (0° ≤ peak < min). Same bin label internally — only
// the display copy changes.
function prettyLabel(label, peak_alt_deg) {
  if (label !== "not_visible") return _LABEL_PRETTY[label];
  if (peak_alt_deg == null || peak_alt_deg < 0) return "Below horizon";
  return `Below min (peaks ${peak_alt_deg}°)`;
}
function prettyLabelShort(label, peak_alt_deg) {
  if (label !== "not_visible") return _LABEL_PRETTY[label];
  if (peak_alt_deg == null || peak_alt_deg < 0) return "Below horizon";
  return `Max ${peak_alt_deg}°`;
}

function binsForTarget(targetId) {
  return visibilityData?.targets?.[String(targetId)] || null;
}

function bestBinFor(bins) {
  if (!bins || !bins.length) return null;
  let best = bins[0];
  let bestRank = _LABEL_RANK[best.label] ?? 0;
  for (const b of bins) {
    const r = _LABEL_RANK[b.label] ?? 0;
    if (r > bestRank) { best = b; bestRank = r; }
  }
  return best;
}

function _cellTooltip(b) {
  // 3-line tooltip — \n renders as a real linebreak in browser title tooltips.
  const minAlt = visibilityData?.site?.min_alt_deg ?? 30;
  const peak = b.peak_alt_deg == null ? "—" : `${b.peak_alt_deg}°`;
  return `${_MONTH_LABELS[b.month-1]}: ${prettyLabel(b.label, b.peak_alt_deg)}\nPeak: ${peak}\nAbove ${minAlt}°: ${b.hours_above_min}h`;
}

function yearCurveSparklineHtml(targetId) {
  const bins = binsForTarget(targetId);
  if (!bins) return "";
  const nowMonth = new Date().getUTCMonth() + 1;
  const cells = bins.map(b => {
    const cls = b.month === nowMonth ? `yc-cell vc-${b.label} vc-current` : `yc-cell vc-${b.label}`;
    return `<span class="${cls}" title="${_cellTooltip(b)}"></span>`;
  }).join("");
  return `<span class="yc-sparkline" aria-label="visibility by month">${cells}</span>`;
}

function _binFor(bins, month1) {
  return bins.find(x => x.month === month1) || null;
}

function nowChipHtml(targetId) {
  const bins = binsForTarget(targetId);
  if (!bins) return "";
  const nowMonth = new Date().getUTCMonth() + 1;
  const b = _binFor(bins, nowMonth);
  if (!b) return "";
  const minAlt = visibilityData?.site?.min_alt_deg ?? 30;
  const labelTxt = prettyLabelShort(b.label, b.peak_alt_deg);
  const peak = b.peak_alt_deg == null ? "—" : `${b.peak_alt_deg}°`;
  const tip = `Current month — ${_MONTH_LABELS[nowMonth-1]}\n${prettyLabel(b.label, b.peak_alt_deg)}\nPeak: ${peak}, ${b.hours_above_min}h above ${minAlt}°`;
  return `<span class="nn-chip nn-${b.label}" title="${tip}"><span class="nn-prefix">Now</span> ${esc(labelTxt)}</span>`;
}

function trendChipHtml(targetId) {
  const bins = binsForTarget(targetId);
  if (!bins) return "";
  const nowMonth = new Date().getUTCMonth() + 1;
  const nowBin = _binFor(bins, nowMonth);
  if (!nowBin) return "";
  const nowRank = _LABEL_RANK[nowBin.label] ?? 0;

  // 3-month lookahead average vs current rank — simple heuristic for
  // "is the next quarter better/worse/the same".
  const nextRanks = [];
  for (let i = 1; i <= 3; i++) {
    const m = ((nowMonth - 1 + i) % 12) + 1;
    const b = _binFor(bins, m);
    if (b) nextRanks.push(_LABEL_RANK[b.label] ?? 0);
  }
  if (!nextRanks.length) return "";
  const avg = nextRanks.reduce((a, b) => a + b, 0) / nextRanks.length;
  const diff = avg - nowRank;

  if (diff > 0.5) {
    return `<span class="nn-chip nn-trend-up" title="3-month forward avg rank is higher than current month."><span class="nn-prefix">Trend</span> ↑ Improving</span>`;
  }
  if (diff < -0.5) {
    return `<span class="nn-chip nn-trend-down" title="3-month forward avg rank is lower than current month."><span class="nn-prefix">Trend</span> ↓ Declining</span>`;
  }
  // Steady. If we're currently in a poor state (rank < good=3), surface
  // when the next decent month arrives instead of the uninformative
  // "Steady" — that's actually the more actionable signal.
  if (nowRank < 3) {
    for (let i = 1; i <= 12; i++) {
      const m = ((nowMonth - 1 + i) % 12) + 1;
      const b = _binFor(bins, m);
      if (b && (_LABEL_RANK[b.label] ?? 0) >= 3) {
        return `<span class="nn-chip nn-trend-wait" title="First Good-or-better month in the year ahead."><span class="nn-prefix">Trend</span> Peaks in ${i}m</span>`;
      }
    }
    return `<span class="nn-chip nn-trend-flat" title="Stays poor across the year ahead."><span class="nn-prefix">Trend</span> Stays low</span>`;
  }
  return `<span class="nn-chip nn-trend-flat" title="3-month forward rank ≈ current."><span class="nn-prefix">Trend</span> → Steady</span>`;
}

function yearCurveBarHtml(targetId) {
  const bins = binsForTarget(targetId);
  if (!bins) return "";
  const nowMonth = new Date().getUTCMonth() + 1;
  const cells = bins.map(b => {
    const cls = b.month === nowMonth ? `yc-cell vc-${b.label} vc-current` : `yc-cell vc-${b.label}`;
    return `<span class="${cls}" title="${_cellTooltip(b)}"><span class="yc-mlabel">${_MONTH_LABELS[b.month-1][0]}</span></span>`;
  }).join("");
  return `<div class="yc-bar">${cells}</div>`;
}

// --- Time-aware toggle (Plan A.2) ----------------------------------------
function initTimeAware() {
  timeAware = localStorage.getItem("acp.time_aware") === "on";
  const savedSort = localStorage.getItem("acp.sort_by");
  if (savedSort && ["hours", "best_month", "up_tonight"].includes(savedSort)) {
    sortBy = savedSort;
  }
  const savedPlanSort = localStorage.getItem("acp.plan_sort_by");
  if (savedPlanSort && ["priority", "name", "panels_up_now", "peak_panels_month"].includes(savedPlanSort)) {
    planSortBy = savedPlanSort;
  }
  applyTimeAwareState(/*fireImmediate=*/true);
  const btn = document.getElementById("timeAwareToggle");
  if (btn) btn.addEventListener("click", () => setTimeAware(!timeAware));
}

function setTimeAware(on) {
  timeAware = !!on;
  localStorage.setItem("acp.time_aware", timeAware ? "on" : "off");
  applyTimeAwareState(/*fireImmediate=*/true);
}

function applyTimeAwareState(fireImmediate) {
  document.body.dataset.timeAware = timeAware ? "on" : "off";
  const btn = document.getElementById("timeAwareToggle");
  if (btn) btn.setAttribute("aria-pressed", timeAware ? "true" : "false");

  if (_obsIntervalId !== null) {
    clearInterval(_obsIntervalId);
    _obsIntervalId = null;
  }
  if (timeAware) {
    if (fireImmediate) updateObsNow();
    _obsIntervalId = setInterval(updateObsNow, 60_000);
    loadVisibility();  // populates sparklines + best-month chips
    // Plan-side fans out separately — manifest visibility is keyed by
    // target_id, plan visibility by panel centres, so they don't share a
    // cache or fetch.
    if (panelMode === "plan-list") {
      renderPlanList();
    } else if (panelMode === "plan-edit" && editingPlan) {
      renderPlanEditor(editingPlan);
    } else {
      // Pre-warm the plan cache in the background even if the user is on
      // the target list — they may switch panels and we want it ready.
      loadAllPlanVisibility();
    }
  } else {
    const el = document.getElementById("obsNow");
    if (el) el.textContent = "";
    visibilityData = null;
    currentAlts = {};
    if (panelMode === "list") renderTargetList();
    else if (panelMode === "plan-list") renderPlanList();
    else if (panelMode === "plan-edit" && editingPlan) renderPlanEditor(editingPlan);
  }
  refreshHorizonOverlay();
}

// --- Catalog hover tooltip (objectHovered → near-cursor popover) ---
// One reusable DOM node, positioned via fixed coords from the latest mousemove.
// We track cursor in module scope because Aladin's objectHovered event doesn't
// pass screen coordinates.
let _catCursor = { x: 0, y: 0 };
function showCatTooltip(html) {
  const el = document.getElementById("catTooltip");
  if (!el) return;
  el.innerHTML = html;
  el.hidden = false;
  positionCatTooltip();
}
function hideCatTooltip() {
  const el = document.getElementById("catTooltip");
  if (el) el.hidden = true;
}
function positionCatTooltip() {
  const el = document.getElementById("catTooltip");
  if (!el || el.hidden) return;
  // Default placement = lower-right of cursor (12px offset). Flip to upper-left
  // if either edge would clip the viewport.
  const pad = 12;
  const w = el.offsetWidth, h = el.offsetHeight;
  let x = _catCursor.x + pad;
  let y = _catCursor.y + pad;
  if (x + w > window.innerWidth)  x = _catCursor.x - pad - w;
  if (y + h > window.innerHeight) y = _catCursor.y - pad - h;
  el.style.left = `${Math.max(0, x)}px`;
  el.style.top  = `${Math.max(0, y)}px`;
}

function init() {
  const showInitError = err => {
    // Without this, any exception during startup left the "Loading
    // targets…" placeholder on screen forever with nothing but an
    // unhandled rejection in devtools. See GitHub issue #46.
    console.error("ACP failed to start:", err);
    const panel = document.getElementById("panelBody");
    if (panel) panel.innerHTML = `<div class="panel-error">${esc(describeInitError(err))}</div>`;
  };

  // Everything below hangs off Aladin Lite. Name the two ways it fails to
  // even begin, because neither produces an exception on its own: the CDN
  // script never arrived (A is undefined), or the browser has no WebGL2 and
  // A.init simply never settles.
  if (typeof A === "undefined" || !A || !A.init) {
    showInitError(startupError("aladin-missing"));
    return;
  }
  if (!webgl2Available()) {
    showInitError(startupError("webgl2-missing"));
    return;
  }

  const aladinReady = withTimeout(
    A.init,
    ALADIN_INIT_TIMEOUT_MS,
    startupError("aladin-timeout", { ms: ALADIN_INIT_TIMEOUT_MS }),
  );

  aladinReady.then(async () => {
    aladin = A.aladin("#aladin-lite-div", {
      fov: 180,
      projection: "AIT",
      cooFrame: "ICRSd",
      // Mellinger is a natural-colour optical panorama with tiny tiles, so it is
      // both prettier and lighter than DSS2. It goes soft when zoomed into a
      // single target; users who want detail can pick DSS2 or Pan-STARRS from
      // the Sky dropdown and ACP remembers the choice.
      survey: DEFAULT_SURVEY_ID,
      showReticle: false,
      showZoomControl: true,
      showFullscreenControl: true,
      showLayersControl: true,
      showGotoControl: true,
      showFrame: true,
      target: "galactic center",
    });

    overlay = A.graphicOverlay({ color: "#ff4d4d", lineWidth: 2, name: "archive coverage" });
    aladin.addOverlay(overlay);

    // One catalog for filter-coverage badges (custom shape draws a pill of 7 dots).
    // sourceSize must be >= ~8 or Aladin's default shape prelude errors with
    // "negative radius" in arc(). We override with our own shape function.
    filterBadgeCat = A.catalog({
      name: "filter_badges",
      shape: filterBadgeShape,
      sourceSize: 18,
    });
    aladin.addCatalog(filterBadgeCat);

    // Planner overlays — always live, populated only while planningMode is true
    planOverlay = A.graphicOverlay({ color: "#66aaff", lineWidth: 2.5, name: "plans" });
    aladin.addOverlay(planOverlay);
    // Dashed overlay for not-started plans. Aladin's lineDash is set per overlay,
    // not per polygon, so we need a second overlay to mix dashed + solid in one view.
    planOverlayDashed = A.graphicOverlay({ color: "#66aaff", lineWidth: 2.5, name: "plans_dashed", lineDash: [8, 5] });
    if (planOverlayDashed.setLineDash) planOverlayDashed.setLineDash([8, 5]);
    aladin.addOverlay(planOverlayDashed);
    planCenterCat = A.catalog({ name: "plan_markers", sourceSize: 10, shape: "circle", color: "#ffffff" });
    aladin.addCatalog(planCenterCat);

    // Transient hover highlight — registered LAST so it renders on top of every
    // other overlay (coverage + plan polygons). Otherwise plan polygons would
    // occlude the hover fill entirely in planning mode.
    hoverOverlay = A.graphicOverlay({ color: "#ffffff", lineWidth: 4, name: "hover_highlight" });
    aladin.addOverlay(hoverOverlay);

    // Aladin marker click — now only fires for catalog overlays (SNRs, HII) and the
    // plan_rotate drag handle. Target + plan selection is handled by the map-click
    // handler below so users can click anywhere inside a FOV polygon.
    aladin.on("objectClicked", src => {
      if (!src) return;
      if (src.data?.kind === "plan_rotate") return; // drag handler owns this
      const tip = document.getElementById("tooltip");
      if (tip) tip.textContent = src.data?.name ? `catalog: ${src.data.catalog || ""} ${src.data.name}` : "";
    });
    aladin.on("objectHovered", src => {
      // Falsy = hover-out. Hide both the status-bar text and the floating tooltip.
      if (!src || !src.data?.name) {
        hideCatTooltip();
        return;
      }
      const d = src.data;
      // Status-bar mirror (pre-existing behaviour).
      document.getElementById("tooltip").textContent = `${d.catalog || ""} ${d.name}`;
      // Floating near-cursor tooltip. catalog overlays carry {name, catalog, ...row}
      // — pull up to 2 non-null extras (freq, flag_3color, etc.) for context.
      const extras = [];
      const skip = new Set(["name", "catalog", "ra_deg", "dec_deg"]);
      for (const k of Object.keys(d)) {
        if (skip.has(k)) continue;
        const v = d[k];
        if (v == null || v === "") continue;
        extras.push(`${esc(k)}=${esc(String(v))}`);
        if (extras.length >= 2) break;
      }
      const tag = d.catalog ? `<span class="cat-tag">${esc(d.catalog)}</span>` : "";
      const ex  = extras.length ? `<span class="cat-extra">${extras.join(" ")}</span>` : "";
      showCatTooltip(`<strong>${esc(d.name)}</strong>${tag}${ex}`);
    });

    // Map-click selection: click anywhere inside a FOV polygon to select that target / plan.
    // Overlapping polygons resolve smallest-first; repeat-clicks cycle through the stack.
    // Empty-sky clicks navigate up one panel level (see onMapPolyClick → goUpOneLevel).
    aladin.on("click", o => {
      if (_suppressNextMapClick) { _suppressNextMapClick = false; return; }
      if (_pressInfo?.dragged) return; // pan, not a real click
      if (!o || o.ra == null || o.dec == null) return;
      if (dragState) return; // belt-and-suspenders — drag still in progress
      onMapPolyClick(o.ra, o.dec);
    });

    // Reset cycle state on any camera movement — previous stack indices stop making sense.
    aladin.on("zoomChanged", () => { lastClickStack = null; });
    aladin.on("positionChanged", () => { lastClickStack = null; });

    // Persist the chosen HiPS survey across reloads. Aladin v3 may or may not emit
    // "layerChanged" depending on build; a 3s poll is a cheap backstop.
    try { aladin.on("layerChanged", saveUiState); } catch { /* event not supported */ }
    let _lastSurveyId = null;
    setInterval(() => {
      try {
        const layer = aladin?.getBaseImageLayer?.();
        const id = layer?.id || layer?.url;
        if (id && id !== _lastSurveyId) {
          if (_lastSurveyId !== null) saveUiState(); // skip the first observation (init)
          _lastSurveyId = id;
          syncSkyControl(id);
        }
      } catch { /* no-op */ }
    }, 3000);

    // Hover highlight — attach to the Aladin container; convert pixel → world, hit-test.
    const mapEl = document.getElementById("aladin-lite-div");
    if (mapEl) {
      let hoverRaf = 0;
      mapEl.addEventListener("mousemove", ev => {
        // Track latest cursor position so objectHovered's tooltip placement
        // stays glued to the pointer even when Aladin's event lacks coords.
        _catCursor = { x: ev.clientX, y: ev.clientY };
        positionCatTooltip();
        // Promote the press to a "drag" once the pointer has travelled far
        // enough — this is what suppresses pan-then-release from acting as a click.
        if (_pressInfo && !_pressInfo.dragged) {
          const dx = ev.clientX - _pressInfo.x, dy = ev.clientY - _pressInfo.y;
          if (dx * dx + dy * dy >= 25) _pressInfo.dragged = true; // ≥5px
        }
        if (hoverRaf) return;
        hoverRaf = requestAnimationFrame(() => {
          hoverRaf = 0;
          if (!aladin?.pix2world) return;
          const r = mapEl.getBoundingClientRect();
          let w;
          try {
            // Aladin Lite occasionally throws an internal TypeError
            // ("can't access property Symbol.iterator, i is undefined")
            // when mousemove fires before its WebGL view has finished
            // initialising. Treat any failure as "no hit"; the next
            // animation frame will succeed once the view is ready.
            w = aladin.pix2world(ev.clientX - r.left, ev.clientY - r.top);
          } catch {
            return;
          }
          if (!w) { setHoverHit(null); return; }
          const hits = hitPolygonsAt(w[0], w[1]);
          setHoverHit(hits[0] || null);
        });
      });
      mapEl.addEventListener("mousedown", ev => {
        if (ev.button !== 0) return; // primary button only
        _pressInfo = { x: ev.clientX, y: ev.clientY, t: performance.now(), dragged: false };
      });
      mapEl.addEventListener("mouseleave", () => { setHoverHit(null); hideCatTooltip(); });
    }

    // Document-level Esc: navigate up one panel level (mirrors empty-sky click).
    // Skip if focus is in a form control — Esc should keep its native blur/clear
    // behaviour there. The dirty-edit modal handles its own Esc → cancel.
    document.addEventListener("keydown", ev => {
      if (ev.key !== "Escape") return;
      if (isModalOpen()) {
        document.querySelector(".dirty-modal-backdrop")?.remove();
        ev.preventDefault();
        return;
      }
      const ae = document.activeElement;
      const tag = ae?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || ae?.isContentEditable) return;
      goUpOneLevel();
    });

    // Load manifest
    const r = await fetch("/api/manifest");
    if (!r.ok) {
      const err = new Error("manifest request failed");
      err.status = r.status;
      throw err;
    }
    manifest = await r.json();

    // Show the onboarding banner if the manifest is empty (fresh install).
    // The user can dismiss it; we don't pin the dismissal across reloads
    // because seeing it every time is a useful nag until a manifest exists.
    setupOnboardingBanner(manifest);

    // Assign telescope colors and build toggle UI
    const { map, sorted } = assignTelescopeColors(manifest.targets);
    telescopeColor = map;
    selectedTelescopes = new Set(sorted); // all enabled by default
    renderTelescopeLegend(sorted);

    setupFilterUI();
    // Kick off the catalogue chip render in parallel with the rest; we
    // await the resulting promise alongside everything else below so
    // applyUiStatePreManifest can find the chips when restoring saved state.
    const catalogReady = setupCatalogOverlays();
    setupPlannerUI();

    // Auto-import any manifest-discovered telescopes/cameras before first gear load
    // so the planner sees the user's actual rigs from the start.
    await seedGearFromManifest();
    // Load planner data in parallel with catalogs (sites included so the
    // first updateObsNow uses the saved active site rather than the hardcoded
    // Sydney fallback in `currentSite`).
    await Promise.all([
      loadGear(), loadPlans(), loadTsTemplates(), loadTargetOverrides(),
      loadPublishConfig(), initSites(), catalogReady,
    ]);

    // Restore previous session state before the first draw so the map
    // reflects saved filters/telescopes/search immediately.
    buildSkyControl();
    applyUiStatePreManifest();
    applyUiStatePostManifest();

    redrawFootprints();
    loadCatalogs();
    loadSources();
    initInventory();
    loadExtensions();
    initTimeAware();

    // If planning mode was remembered from last session, switch now (after
    // manifest + plans loaded so the right panel renders correctly).
    if (planningMode) setPlanningMode(true);
  }).catch(showInitError);
}

function renderTelescopeLegend(telescopes) {
  const host = document.getElementById("telescopeChips");
  if (!host) return;
  host.innerHTML = "";
  for (const name of telescopes) {
    const color = telescopeColor[name] || TELESCOPE_FALLBACK;
    const label = document.createElement("label");
    label.className = "fchip tele-chip";
    label.innerHTML = `
      <input type="checkbox" data-telescope="${esc(name)}" checked />
      <span class="tele-swatch" style="background:${esc(color)}"></span>
      ${esc(name)}`;
    host.appendChild(label);
  }
  host.querySelectorAll("input[data-telescope]").forEach(cb => {
    cb.addEventListener("change", () => {
      const name = cb.dataset.telescope;
      if (cb.checked) selectedTelescopes.add(name);
      else selectedTelescopes.delete(name);
      saveUiState();
      redrawFootprints();
    });
  });
}

// === Planner mode ===

async function loadPublishConfig() {
  try {
    const r = await fetch("/api/publish/config");
    const c = await r.json();
    livePageEnabled = !!(c && c.live_page_enabled);
  } catch { livePageEnabled = false; }
}

async function loadGear() {
  try {
    const r = await fetch("/api/gear");
    const g = await r.json();
    gear = { version: g.version || 2, telescopes: g.telescopes || [], cameras: g.cameras || [] };
  } catch { gear = { telescopes: [], cameras: [] }; }
}

// Merge telescopes/cameras discovered in the coverage manifest into gear.json.
// Idempotent; safe to call on every app load. Returns {added_telescopes, added_cameras}.
async function seedGearFromManifest() {
  try {
    const r = await fetch("/api/gear/seed", { method: "POST" });
    if (!r.ok) return null;
    const j = await r.json();
    if (j.added_telescopes?.length || j.added_cameras?.length) {
      console.log("[gear] auto-imported from coverage manifest:", j);
    }
    return j;
  } catch (e) {
    console.warn("gear seed failed", e);
    return null;
  }
}

async function saveGear() {
  const r = await fetch("/api/gear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telescopes: gear.telescopes, cameras: gear.cameras }),
  });
  return r.ok;
}

async function loadPlans() {
  try { const r = await fetch("/api/plans"); const j = await r.json(); plans = j.plans || []; }
  catch { plans = []; }
}

async function loadTargetOverrides() {
  try {
    const r = await fetch("/api/target-overrides");
    const j = await r.json();
    targetOverrides = j.overrides || {};
  } catch { targetOverrides = {}; }
}

async function setTargetFinished(targetId, finished) {
  // finished: true | false | null (null clears the override → revert to plan-derived).
  const body = { target_id: targetId, finished };
  try {
    const r = await fetch("/api/target-overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (j && j.overrides) targetOverrides = j.overrides;
  } catch (e) {
    console.warn("target-overrides write failed", e);
  }
}

async function loadTsTemplates() {
  try { const r = await fetch("/api/ts-templates"); tsTemplates = await r.json(); }
  catch { tsTemplates = { available: false, templates: [] }; }
}

function deg2hms(deg) {
  if (deg == null || !isFinite(deg)) return "";
  const d = ((deg % 360) + 360) % 360;
  const hrs = d / 15;
  const h = Math.floor(hrs);
  const mflt = (hrs - h) * 60;
  const m = Math.floor(mflt);
  const s = (mflt - m) * 60;
  return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${s.toFixed(2)}s`;
}

function deg2dms(deg) {
  if (deg == null || !isFinite(deg)) return "";
  const sign = deg < 0 ? "-" : "+";
  const a = Math.abs(deg);
  const d = Math.floor(a);
  const mflt = (a - d) * 60;
  const m = Math.floor(mflt);
  const s = (mflt - m) * 60;
  return `${sign}${String(d).padStart(2, "0")}° ${String(m).padStart(2, "0")}' ${s.toFixed(2)}"`;
}

function planTelescope(plan) {
  return gear.telescopes?.find(t => t.id === plan.telescope_id) || gear.telescopes?.[0] || null;
}

function planCamera(plan) {
  return gear.cameras?.find(c => c.id === plan.camera_id) || gear.cameras?.[0] || null;
}

// True if the plan has any logged actual hours on any filter.
function planHasData(plan) {
  const goals = plan?.filter_goals || {};
  for (const g of Object.values(goals)) {
    if ((Number(g?.actual_hours) || 0) > 0) return true;
  }
  return false;
}

// Normalize a telescope name for fuzzy matching: lowercase, strip punctuation,
// collapse whitespace, drop common boilerplate suffixes (APO, Pro, Mk II…) so
// gear-defined "RedCat 51 APO" can match manifest "RedCat 51" from FITS headers.
function _normTelName(s) {
  if (!s) return "";
  return String(s)
    .toLowerCase()
    .replace(/\b(apo|pro|mk[\s-]*[ivx]+|edge[\s-]*hd|hd|f\/?\d+(\.\d+)?|mm|inch|in|"|zwo|qhy|celestron|svbony|sky[-\s]*watcher|williams?[\s-]*optics?|askar|takahashi)\b/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// Resolve a plan's footprint color from its selected telescope, matching the
// legend swatch used for existing coverage. Match order: exact → case-insensitive
// → normalized substring (either direction). Falls back to a stable palette
// index hashed from the telescope id/name for gear not present in the manifest.
function planBorderColor(plan) {
  const tel = planTelescope(plan);
  if (!tel || !tel.name) return TELESCOPE_FALLBACK;
  if (telescopeColor[tel.name]) return telescopeColor[tel.name];
  const want = tel.name.toLowerCase();
  for (const k of Object.keys(telescopeColor)) {
    if (k.toLowerCase() === want) return telescopeColor[k];
  }
  const wantN = _normTelName(tel.name);
  if (wantN) {
    for (const k of Object.keys(telescopeColor)) {
      const kN = _normTelName(k);
      if (!kN) continue;
      if (kN === wantN || kN.includes(wantN) || wantN.includes(kN)) return telescopeColor[k];
    }
  }
  const key = tel.id || tel.name;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return TELESCOPE_PALETTE[h % TELESCOPE_PALETTE.length];
}

function deriveFovArcmin(telescope, camera) {
  if (!telescope || !camera) return [0, 0];
  const fl = parseFloat(telescope.focal_length_mm);
  const px = parseFloat(camera.pixel_size_um);
  const dims = camera.sensor_px;
  if (!(fl > 0) || !(px > 0) || !dims || dims.length < 2) return [0, 0];
  const arcsecPerPx = 206.265 * px / fl;
  return [+(dims[0] * arcsecPerPx / 60).toFixed(2), +(dims[1] * arcsecPerPx / 60).toFixed(2)];
}

function planFovArcmin(plan) {
  return deriveFovArcmin(planTelescope(plan), planCamera(plan));
}

function planMosaic(plan) {
  const m = plan.target?.mosaic || {};
  return {
    rows: Math.max(1, parseInt(m.rows) || 1),
    cols: Math.max(1, parseInt(m.cols) || 1),
    overlap_pct: Math.max(0, Math.min(99, parseFloat(m.overlap_pct) || 0)),
  };
}

// Compute per-panel centers [{row, col, ra_deg, dec_deg}] for a rows×cols
// mosaic. Panel stride = fov × (1 - overlap). Row 0 = north (when rot=0).
function mosaicPanelCenters(plan) {
  const tg = plan.target || {};
  const [fw, fh] = planFovArcmin(plan);
  const { rows, cols, overlap_pct } = planMosaic(plan);
  const overlap = Math.max(0, Math.min(0.99, overlap_pct / 100));
  const strideW = (fw / 60) * (1 - overlap);
  const strideH = (fh / 60) * (1 - overlap);
  const R = (tg.rotation_deg || 0) * Math.PI / 180;
  const cosR = Math.cos(R), sinR = Math.sin(R);
  const cosD = Math.max(1e-6, Math.cos((tg.center_dec_deg || 0) * Math.PI / 180));
  const panels = [];
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const cx = (j - (cols - 1) / 2) * strideW;
      const cy = ((rows - 1) / 2 - i) * strideH;
      const de =  cx * cosR + cy * sinR;
      const dn = -cx * sinR + cy * cosR;
      panels.push({
        row: i, col: j,
        ra_deg: (tg.center_ra_deg || 0) + de / cosD,
        dec_deg: (tg.center_dec_deg || 0) + dn,
      });
    }
  }
  return panels;
}

function planPanelCorners(plan) {
  // Returns an array of corner-arrays, one per panel, each [[ra,dec], ...] SW/NW/NE/SE.
  const [fw, fh] = planFovArcmin(plan);
  const rot = plan.target?.rotation_deg || 0;
  return mosaicPanelCenters(plan).map(p => computePlanCorners(p.ra_deg, p.dec_deg, fw, fh, rot));
}

// Bounding rectangle of the whole mosaic in sky coords, used to place the
// rotation handle at the overall NE corner. Returns [[ra,dec],...] SW/NW/NE/SE.
function planMosaicBoundsCorners(plan) {
  const tg = plan.target || {};
  const [fw, fh] = planFovArcmin(plan);
  const { rows, cols, overlap_pct } = planMosaic(plan);
  const overlap = Math.max(0, Math.min(0.99, overlap_pct / 100));
  const totalW = fw * ((cols - 1) * (1 - overlap) + 1);
  const totalH = fh * ((rows - 1) * (1 - overlap) + 1);
  return computePlanCorners(tg.center_ra_deg, tg.center_dec_deg, totalW, totalH, tg.rotation_deg || 0);
}

// Compute the 4 corners of a rectangular FOV box centered on (ra, dec) with
// given width/height in arcmin and rotation_deg (PA, degrees east of north for
// the camera's +Y axis — NINA's convention). Returns [[ra, dec], ...] in
// order SW, NW, NE, SE so the NE corner (index 2) can host a rotation handle.
function computePlanCorners(ra_deg, dec_deg, fov_w_arcmin, fov_h_arcmin, rot_deg) {
  const toRad = x => x * Math.PI / 180;
  const half_w = fov_w_arcmin / 2 / 60;   // degrees
  const half_h = fov_h_arcmin / 2 / 60;
  const R = toRad(rot_deg || 0);
  const cosR = Math.cos(R), sinR = Math.sin(R);
  const cosD = Math.max(1e-6, Math.cos(toRad(dec_deg)));
  const local = [
    [-half_w, -half_h],
    [-half_w, +half_h],
    [+half_w, +half_h],
    [+half_w, -half_h],
  ];
  return local.map(([lx, ly]) => {
    // Camera-Y sits at PA = R east of north, so rotate the camera-plane
    // offsets into (east, north) sky offsets:
    const de =  lx * cosR + ly * sinR;
    const dn = -lx * sinR + ly * cosR;
    return [ra_deg + de / cosD, dec_deg + dn];
  });
}

function newEmptyPlan() {
  let ra = 180, dec = 0;
  try { const c = aladin.getRaDec(); if (c && c.length === 2) { ra = c[0]; dec = c[1]; } } catch {}
  const tel = gear.telescopes?.[0];
  const cam = (tel?.default_camera_id && gear.cameras?.find(c => c.id === tel.default_camera_id))
    || gear.cameras?.[0];
  return {
    id: `plan-${Date.now().toString(36)}`,
    project_name: "",
    target: {
      name: "", target_id: null,
      center_ra_deg: ra, center_dec_deg: dec, rotation_deg: 0,
      mosaic: { rows: 1, cols: 1, overlap_pct: 15 },
    },
    telescope_id: tel?.id || "",
    camera_id: cam?.id || "",
    filter_goals: {},
    priority: "normal",
    min_altitude_deg: 30,
    meridian_window_min: 0,
    state: "draft",
  };
}

const _PRIORITY_RANK = { high: 3, normal: 2, low: 1 };

function sortedPlansForList() {
  const arr = plans.slice();
  if (planSortBy === "name") {
    arr.sort((a, b) => (a.target?.name || a.id).localeCompare(b.target?.name || b.id));
  } else if (planSortBy === "panels_up_now" && timeAware) {
    arr.sort((a, b) => planPanelsUpNow(b) - planPanelsUpNow(a));
  } else if (planSortBy === "peak_panels_month" && timeAware) {
    arr.sort((a, b) => planPeakPanelsMonth(b) - planPeakPanelsMonth(a));
  } else {
    // Default "priority": high → normal → low, then name.
    arr.sort((a, b) => {
      const dp = (_PRIORITY_RANK[b.priority] || 2) - (_PRIORITY_RANK[a.priority] || 2);
      if (dp !== 0) return dp;
      return (a.target?.name || a.id).localeCompare(b.target?.name || b.id);
    });
  }
  return arr;
}

function renderPlanList() {
  panelMode = "plan-list";
  updateSearchVisibility();
  editingPlan = null;
  selectedPlanId = null;
  saveUiState();
  const panel = document.getElementById("panelBody");
  if (!panel) return;

  const sorted = sortedPlansForList();
  const rows = sorted.map(pl => {
    const name = esc(pl.target?.name || pl.id);
    const proj = esc(pl.project_name || "(no project)");
    const pri = pl.priority || "normal";
    const goals = pl.filter_goals || {};
    const dots = FILTER_DOT_ORDER.map(f => {
      const g = goals[f];
      const color = FILTER_COLORS[f] || "#888";
      if (!g || !(g.target_hours > 0)) {
        return `<span class="plan-goal-dot todo" style="background:${color}" title="${f}: no goal"></span>`;
      }
      const th = g.target_hours;
      const ah = g.actual_hours || 0;
      const cls = ah >= th ? "done" : (ah > 0 ? "partial" : "todo");
      return `<span class="plan-goal-dot ${cls}" style="background:${color}" title="${f}: ${ah.toFixed(1)}/${th}h"></span>`;
    }).join("");
    const { rows, cols } = planMosaic(pl);
    const panelCount = Math.max(1, rows * cols);
    let remaining = 0;
    for (const g of Object.values(goals)) remaining += Math.max(0, (g.target_hours || 0) - (g.actual_hours || 0));
    remaining *= panelCount;
    const visCell = timeAware ? `<span class="plan-vis">${planVisCellHtml(pl, { compact: true })}</span>` : "";
    const priLabel = pri.charAt(0).toUpperCase() + pri.slice(1);
    return `<li class="plan-row" data-plan-id="${esc(pl.id)}">
        <span class="plan-pri-dot plan-pri-${pri}" title="${priLabel} priority"></span>
        <span class="plan-name">${name}</span>
        ${visCell}
        <div class="plan-row-line2">
          <span class="plan-project">${proj}</span>
          <span class="plan-goals">${dots}</span>
          <span class="plan-remaining">${remaining.toFixed(1)}h left</span>
        </div>
      </li>`;
  }).join("");

  const empty = `<li class="tr-empty">No plans yet. Click "+ New plan" to start.</li>`;
  const sortCtl = `<span class="sort-control">sort by
      <select id="planSortSel">
        <option value="priority" ${planSortBy==="priority"?"selected":""}>priority</option>
        <option value="name" ${planSortBy==="name"?"selected":""}>name</option>
        <option value="panels_up_now" ${planSortBy==="panels_up_now"?"selected":""} data-time-aware>panels up now</option>
        <option value="peak_panels_month" ${planSortBy==="peak_panels_month"?"selected":""} data-time-aware>peak season</option>
      </select></span>`;

  panel.innerHTML = `
    <div class="planner-toolbar">
      <button id="planNew" class="btn-primary">+ New plan</button>
      <button id="planSync">Sync to NINA</button>
      <button id="planGear">Edit gear</button>
    </div>
    <div class="panel-list">
      <h3>Plans <span class="tr-count">${plans.length}</span>${sortCtl}</h3>
      <ul class="target-list">${rows || empty}</ul>
    </div>
    <div id="syncResult"></div>`;

  panel.querySelector("#planNew").addEventListener("click", () => {
    const p = newEmptyPlan();
    plans.push(p);
    renderPlanEditor(p);
  });
  // Wire planSync through the replaceable-button helper so an installed
  // extension can swap it (e.g. nina_ts_sync → "Auto Sync to NINA"). Falls
  // back to the original syncPlans() zip-export handler when no extension
  // claims this slot.
  wireReplaceableButton("sync-to-nina");
  panel.querySelector("#planGear").addEventListener("click", () => renderGearEditor());
  panel.querySelectorAll(".plan-row").forEach(row => {
    row.addEventListener("click", () => {
      const pl = plans.find(p => p.id === row.dataset.planId);
      if (pl) {
        // Pan first so even a render throw doesn't swallow the pan.
        panMapTo(pl.target?.center_ra_deg, pl.target?.center_dec_deg);
        renderPlanEditor(pl);
      }
    });
  });
  const planSortSel = panel.querySelector("#planSortSel");
  if (planSortSel) planSortSel.addEventListener("change", e => {
    planSortBy = e.target.value;
    localStorage.setItem("acp.plan_sort_by", planSortBy);
    renderPlanList();
  });
  redrawPlanFootprints();
  if (timeAware) loadAllPlanVisibility();
}

// Build the list of TS-template <option> tags for a filter row.
//
// Returns the inner HTML for a <select>, OR null when no template UI should
// render at all (e.g. /api/ts-templates wasn't available — caller falls
// back to an em-dash).
//
// Filtering steps:
//   1. By filter name (case-insensitive).
//   2. Then by selected camera, using a normalised-name "contains" check so
//      a template called "Ha (ZWO ASI6200MM Pro)" matches a gear camera
//      named "ZWO ASI6200MM Pro". If the camera-narrow returns empty, we
//      fall back to filter-only — better to show something than nothing.
//
// Option labels: when multiple templates remain, we show only the *varying*
// fields (gain / sub-exposure / offset / bin) instead of the full template
// name, so the user sees the meaningful axis of choice (e.g. "100s" / "300s"
// when only exposure differs, or "gain 0" / "gain 100" when only gain
// differs). When a single template remains we just show its name.
//
// Single-match behaviour: the lone option is rendered as `selected` so the
// auto-persist hook in renderPlanEditor picks it up — user doesn't have to
// open the dropdown to confirm.
function buildTsTemplateOptions(filterName, cameraObj, filtCfg) {
  if (!tsTemplates || !tsTemplates.available) return null;
  let matching = (tsTemplates.templates || []).filter(
    t => (t.filter || "").toLowerCase() === filterName.toLowerCase(),
  );
  if (matching.length === 0) {
    return `<option value="">(no ${esc(filterName)} template)</option>`;
  }
  if (cameraObj && cameraObj.name && matching.length > 1) {
    const camNorm = _normalizeForMatch(cameraObj.name);
    const narrowed = matching.filter(t => _normalizeForMatch(t.name).includes(camNorm));
    if (narrowed.length) matching = narrowed;
  }
  const diffs = _differingTemplateFields(matching);
  // Allow clearing an existing pick when the user wants to fall back to ACP's
  // own derivation. Only show "(none)" when something is currently stored —
  // for fresh plans we don't want the empty choice cluttering the dropdown.
  const opts = [];
  if (filtCfg && filtCfg.ts_template_id) {
    opts.push(`<option value="">(none)</option>`);
  }
  opts.push(...matching.map((t, i) => {
    const label = diffs.length ? _templateOptionLabel(t, diffs) : t.name;
    const stored = filtCfg && String(filtCfg.ts_template_id || "") === String(t.id);
    // Auto-default the lone option when nothing's stored yet so the
    // post-render auto-persist hook fires for it.
    const autoDefault = matching.length === 1 && !(filtCfg && filtCfg.ts_template_id);
    const selected = stored || autoDefault ? "selected" : "";
    return `<option value="${esc(t.id)}" ${selected}>${esc(label)}</option>`;
  }));
  return opts.join("");
}

function _normalizeForMatch(s) {
  return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function _differingTemplateFields(templates) {
  // Order matters — exposure first is what users notice; gain/offset/bin
  // tail off in significance. We list the differing ones in that order so
  // the option label reads naturally.
  const fields = ["default_exposure_s", "gain", "offset", "bin"];
  return fields.filter(f => new Set(templates.map(t => t[f])).size > 1);
}

function _templateOptionLabel(template, diffs) {
  return diffs.map(f => {
    if (f === "default_exposure_s") return `${template[f]}s`;
    return `${f.replace(/_/g, " ")} ${template[f]}`;
  }).join(" · ");
}

function renderPlanEditor(plan) {
  panelMode = "plan-edit";
  updateSearchVisibility();
  selectedPlanId = plan.id;
  editingPlan = JSON.parse(JSON.stringify(plan));
  saveUiState();

  const panel = document.getElementById("panelBody");
  const telescope = planTelescope(editingPlan);
  const camera = planCamera(editingPlan);
  const [fw, fh] = planFovArcmin(editingPlan);
  // LRGBHOS order — keep only the canonical 7 the camera has configured;
  // any extras are hidden for now (same rule as the sky-map chip rail).
  const camFilters = camera?.filters ? Object.keys(camera.filters) : FILTER_DOT_ORDER;
  const filters = FILTER_DOT_ORDER.filter(f => camFilters.includes(f));
  const mos = planMosaic(editingPlan);

  const telOpts = (gear.telescopes || []).map(t =>
    `<option value="${esc(t.id)}" ${t.id === editingPlan.telescope_id ? "selected" : ""}>${esc(t.name)} (${t.focal_length_mm}mm)</option>`
  ).join("") || `<option value="">(no telescopes — open Edit gear)</option>`;
  const camOpts = (gear.cameras || []).map(c =>
    `<option value="${esc(c.id)}" ${c.id === editingPlan.camera_id ? "selected" : ""}>${esc(c.name)}</option>`
  ).join("") || `<option value="">(no cameras — open Edit gear)</option>`;

  const goalRows = filters.map(f => {
    const g = editingPlan.filter_goals[f] || {};
    const color = FILTER_COLORS[f] || "#888";
    const filtCfg = camera?.filters?.[f] || {};
    const th = g.target_hours ?? "";
    const sub = g.sub_exposure_s ?? filtCfg.default_sub_s ?? 300;
    const ah = g.actual_hours || 0;
    const ahClass = (g.target_hours > 0 && ah >= g.target_hours) ? "done" : (ah > 0 ? "partial" : "todo");
    const tsOpts = buildTsTemplateOptions(f, camera, filtCfg);
    return `<tr>
      <td><span class="filter-pill fp-${esc(f)}" style="background:${color};color:#000">${esc(f)}</span></td>
      <td><input type="number" step="0.5" min="0" class="goal-target-hours" data-f="${esc(f)}" value="${th}" placeholder="hrs"></td>
      <td><input type="number" step="10" min="10" class="goal-sub-s" data-f="${esc(f)}" value="${sub}"></td>
      <td><span class="goal-status ${ahClass}" data-actual-filter="${esc(f)}">${ah.toFixed(1)}h</span></td>
      <td>${tsOpts !== null ? `<select class="tmpl-sel" data-f="${esc(f)}">${tsOpts}</select>` : `<span style="color:#78839a;font-size:11px">—</span>`}</td>
    </tr>`;
  }).join("");

  const tg = editingPlan.target;

  panel.innerHTML = `
    <a class="back-link" id="backToPlans" href="#">← Back to plans</a>
    <h3>Plan: ${esc(tg.name || "(unnamed)")}</h3>
    <form class="plan-form" id="planForm" onsubmit="return false">
      <fieldset>
        <legend>Target</legend>
        <label><span class="lab">Name</span>
          <input type="text" class="wide" id="f_name" value="${esc(tg.name)}" placeholder="e.g. Eta Carina">
        </label>
        <label><span class="lab">Project</span>
          <input type="text" class="wide" id="f_project" value="${esc(editingPlan.project_name)}" placeholder="groups plans into a TS project">
        </label>
        <div class="coord-row">
          <label style="flex:1"><span class="lab">RA (deg)</span>
            <input type="number" step="0.1" id="f_ra" value="${Number(tg.center_ra_deg).toFixed(3)}">
          </label>
          <span class="hms" id="f_ra_hms">${deg2hms(tg.center_ra_deg)}</span>
        </div>
        <div class="coord-row">
          <label style="flex:1"><span class="lab">Dec (deg)</span>
            <input type="number" step="0.1" id="f_dec" value="${Number(tg.center_dec_deg).toFixed(3)}">
          </label>
          <span class="hms" id="f_dec_dms">${deg2dms(tg.center_dec_deg)}</span>
        </div>
        <div class="coord-row">
          <label style="flex:1"><span class="lab">Rotation (° PA)</span>
            <input type="number" step="1" id="f_rot" value="${tg.rotation_deg || 0}">
          </label>
          <button type="button" id="aladinLookup">Lookup</button>
        </div>
        <div class="drag-help">Drag the blue center marker to reposition · drag the yellow NE handle to rotate.</div>
      </fieldset>

      <fieldset>
        <legend>Gear <button type="button" id="openGearEditor" style="float:right;font-size:10px;padding:2px 8px">Edit gear</button></legend>
        <label><span class="lab">Telescope</span>
          <select id="f_telescope">${telOpts}</select>
        </label>
        <label><span class="lab">Camera</span>
          <select id="f_camera">${camOpts}</select>
        </label>
        <div style="font-size:11px;color:#78839a">Single-panel FOV: ${fw.toFixed(1)}' × ${fh.toFixed(1)}'</div>
      </fieldset>

      <fieldset>
        <legend>Mosaic</legend>
        <div class="coord-row mosaic-row">
          <label><span class="lab">Rows</span>
            <input type="number" id="f_mrows" min="1" max="20" step="1" value="${mos.rows}">
          </label>
          <label><span class="lab">Cols</span>
            <input type="number" id="f_mcols" min="1" max="20" step="1" value="${mos.cols}">
          </label>
          <label><span class="lab">Overlap %</span>
            <input type="number" id="f_moverlap" min="0" max="90" step="1" value="${mos.overlap_pct}">
          </label>
        </div>
        <div style="font-size:11px;color:#78839a" id="mosaicSummary"></div>
      </fieldset>

      <fieldset id="planVisFieldset" class="vis-section">
        <legend>Visibility</legend>
        <div class="vis-aggregate"><span class="plan-vis-aggregate">${planVisCellHtml(editingPlan, { compact: false })}</span></div>
        <div class="vis-meta-row"><span id="planVisMeta" class="vis-meta"></span></div>
        <div id="planVisHeatmap" class="vis-heatmap"></div>
      </fieldset>

      <fieldset>
        <legend>Filter goals</legend>
        <table class="goals-table">
          <thead><tr><th>Filter</th><th>Target h</th><th>Sub (s)</th><th>Done</th><th>TS template</th></tr></thead>
          <tbody>${goalRows}</tbody>
        </table>
      </fieldset>

      <fieldset>
        <legend>Constraints</legend>
        <label><span class="lab">Priority</span>
          <select id="f_priority">
            <option value="high"   ${editingPlan.priority==="high"?"selected":""}>High</option>
            <option value="normal" ${editingPlan.priority==="normal"?"selected":""}>Normal</option>
            <option value="low"    ${editingPlan.priority==="low"?"selected":""}>Low</option>
          </select>
        </label>
        <label><span class="lab">Min altitude (°)</span>
          <input type="number" step="1" min="0" max="90" id="f_minalt" value="${editingPlan.min_altitude_deg ?? 30}">
        </label>
        <label><span class="lab">Meridian window (min, 0 = none)</span>
          <input type="number" step="1" min="0" id="f_merid" value="${editingPlan.meridian_window_min ?? 0}">
        </label>
        <label><span class="lab">Notes</span>
          <textarea id="f_notes">${esc(editingPlan.notes || "")}</textarea>
        </label>
      </fieldset>

      ${livePageEnabled ? `<fieldset>
        <legend>Public page</legend>
        <label><span class="lab">Visibility</span>
          <select id="f_visibility">
            <option value="private" ${(editingPlan.visibility || "private") === "private" ? "selected" : ""}>Private</option>
            <option value="public"  ${editingPlan.visibility === "public" ? "selected" : ""}>Public</option>
          </select>
        </label>
        <div id="f_public_extra" ${editingPlan.visibility === "public" ? "" : "hidden"}>
          <label><span class="lab">Current project</span>
            <input type="checkbox" id="f_is_current" ${editingPlan.is_current ? "checked" : ""}>
          </label>
          <label><span class="lab">Why I'm shooting this</span>
            <textarea id="f_public_blurb" maxlength="500" rows="3">${esc(editingPlan.public_blurb || "")}</textarea>
          </label>
        </div>
      </fieldset>` : ""}

      <div class="plan-editor-actions">
        <button type="button" id="planSave" class="btn-primary">Save</button>
        <button type="button" id="planCancel">Cancel</button>
        <button type="button" id="planDelete" class="btn-danger">Delete</button>
      </div>
    </form>`;

  // Wire live-edit handlers
  panel.querySelector("#f_name")?.addEventListener("input", e => { editingPlan.target.name = e.target.value; });
  panel.querySelector("#f_project")?.addEventListener("input", e => { editingPlan.project_name = e.target.value; });
  panel.querySelector("#f_ra")?.addEventListener("input", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v)) { editingPlan.target.center_ra_deg = v; document.getElementById("f_ra_hms").textContent = deg2hms(v); redrawPlanFootprints(); schedulePlanVisRefresh(); }
  });
  panel.querySelector("#f_dec")?.addEventListener("input", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v)) { editingPlan.target.center_dec_deg = v; document.getElementById("f_dec_dms").textContent = deg2dms(v); redrawPlanFootprints(); schedulePlanVisRefresh(); }
  });
  panel.querySelector("#f_rot")?.addEventListener("input", e => {
    const v = parseFloat(e.target.value);
    if (isFinite(v)) { editingPlan.target.rotation_deg = v; redrawPlanFootprints(); schedulePlanVisRefresh(); }
  });
  panel.querySelector("#f_telescope")?.addEventListener("change", e => {
    editingPlan.telescope_id = e.target.value;
    renderPlanEditor(editingPlan);
  });
  panel.querySelector("#f_camera")?.addEventListener("change", e => {
    editingPlan.camera_id = e.target.value;
    renderPlanEditor(editingPlan);
  });
  panel.querySelector("#openGearEditor")?.addEventListener("click", () => renderGearEditor());

  const updateMosaicSummary = () => {
    const m = planMosaic(editingPlan);
    const [w, h] = planFovArcmin(editingPlan);
    const overlap = Math.max(0, Math.min(0.99, m.overlap_pct / 100));
    const totalW = w * ((m.cols - 1) * (1 - overlap) + 1);
    const totalH = h * ((m.rows - 1) * (1 - overlap) + 1);
    const el = document.getElementById("mosaicSummary");
    if (el) el.textContent = `${m.rows * m.cols} panel${m.rows * m.cols === 1 ? "" : "s"} · total ${totalW.toFixed(1)}' × ${totalH.toFixed(1)}'`;
  };
  updateMosaicSummary();

  panel.querySelector("#f_mrows")?.addEventListener("input", e => {
    editingPlan.target.mosaic = editingPlan.target.mosaic || { rows: 1, cols: 1, overlap_pct: 15 };
    editingPlan.target.mosaic.rows = Math.max(1, parseInt(e.target.value) || 1);
    updateMosaicSummary(); redrawPlanFootprints(); schedulePlanVisRefresh();
  });
  panel.querySelector("#f_mcols")?.addEventListener("input", e => {
    editingPlan.target.mosaic = editingPlan.target.mosaic || { rows: 1, cols: 1, overlap_pct: 15 };
    editingPlan.target.mosaic.cols = Math.max(1, parseInt(e.target.value) || 1);
    updateMosaicSummary(); redrawPlanFootprints(); schedulePlanVisRefresh();
  });
  panel.querySelector("#f_moverlap")?.addEventListener("input", e => {
    editingPlan.target.mosaic = editingPlan.target.mosaic || { rows: 1, cols: 1, overlap_pct: 15 };
    editingPlan.target.mosaic.overlap_pct = Math.max(0, Math.min(90, parseFloat(e.target.value) || 0));
    updateMosaicSummary(); redrawPlanFootprints(); schedulePlanVisRefresh();
  });
  panel.querySelectorAll(".goal-target-hours").forEach(el => el.addEventListener("input", e => {
    const f = e.target.dataset.f;
    const v = parseFloat(e.target.value);
    editingPlan.filter_goals[f] = editingPlan.filter_goals[f] || {};
    if (isFinite(v) && v > 0) editingPlan.filter_goals[f].target_hours = v;
    else delete editingPlan.filter_goals[f];
  }));
  panel.querySelectorAll(".goal-sub-s").forEach(el => el.addEventListener("input", e => {
    const f = e.target.dataset.f;
    const v = parseFloat(e.target.value);
    if (isFinite(v) && v > 0) {
      editingPlan.filter_goals[f] = editingPlan.filter_goals[f] || {};
      editingPlan.filter_goals[f].sub_exposure_s = v;
    }
  }));
  panel.querySelectorAll(".tmpl-sel").forEach(el => el.addEventListener("change", async e => {
    const f = e.target.dataset.f;
    const cam = planCamera(editingPlan);
    if (!cam) return;
    cam.filters = cam.filters || {};
    cam.filters[f] = cam.filters[f] || {};
    cam.filters[f].ts_template_id = e.target.value || null;
    const opt = e.target.selectedOptions[0];
    cam.filters[f].ts_template_name = opt?.textContent?.split(" (")[0] || null;
    // Persist to data/gear.json so the mapping survives a reload
    await saveGear();
  }));
  // Auto-persist single-match template defaults: when buildTsTemplateOptions
  // pre-selects the only candidate, fire a synthetic change so the handler
  // above writes it to gear.json without the user opening the dropdown.
  // Skips selects where the pre-selected value already matches storage.
  panel.querySelectorAll(".tmpl-sel").forEach(el => {
    const f = el.dataset.f;
    const cam = planCamera(editingPlan);
    const stored = String(cam?.filters?.[f]?.ts_template_id || "");
    if (el.value && el.value !== stored) {
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  panel.querySelector("#f_priority")?.addEventListener("change", e => { editingPlan.priority = e.target.value; });
  panel.querySelector("#f_visibility")?.addEventListener("change", e => {
    editingPlan.visibility = e.target.value;
    const extra = panel.querySelector("#f_public_extra");
    if (extra) extra.hidden = e.target.value !== "public";
  });
  panel.querySelector("#f_is_current")?.addEventListener("change", e => { editingPlan.is_current = !!e.target.checked; });
  panel.querySelector("#f_public_blurb")?.addEventListener("input", e => { editingPlan.public_blurb = e.target.value.slice(0, 500); });
  panel.querySelector("#f_minalt")?.addEventListener("input", e => {
    const v = parseFloat(e.target.value); if (isFinite(v)) editingPlan.min_altitude_deg = v;
  });
  panel.querySelector("#f_merid")?.addEventListener("input", e => {
    const v = parseFloat(e.target.value); if (isFinite(v)) editingPlan.meridian_window_min = v;
  });
  panel.querySelector("#f_notes")?.addEventListener("input", e => { editingPlan.notes = e.target.value; });

  panel.querySelector("#aladinLookup")?.addEventListener("click", () => {
    const q = editingPlan.target.name || prompt("Object name to look up?");
    if (!q) return;
    try {
      aladin.gotoObject(q, {
        success: () => {
          const c = aladin.getRaDec();
          if (c && c.length === 2) {
            editingPlan.target.center_ra_deg = c[0];
            editingPlan.target.center_dec_deg = c[1];
            renderPlanEditor(editingPlan);
          }
        },
        error: () => alert(`Couldn't resolve "${q}".`),
      });
    } catch (e) { alert("Lookup failed: " + e); }
  });

  panel.querySelector("#backToPlans")?.addEventListener("click", e => {
    e.preventDefault();
    // Discard scratch (unsaved) plan
    const orig = plans.find(p => p.id === editingPlan.id);
    if (orig && !orig.guid) plans = plans.filter(p => p !== orig);
    renderPlanList();
  });
  panel.querySelector("#planSave")?.addEventListener("click", savePlan);
  panel.querySelector("#planCancel")?.addEventListener("click", () => {
    const orig = plans.find(p => p.id === editingPlan.id);
    if (orig && !orig.guid) plans = plans.filter(p => p !== orig);
    renderPlanList();
  });
  panel.querySelector("#planDelete")?.addEventListener("click", async () => {
    if (!editingPlan.guid) {
      plans = plans.filter(p => p.id !== editingPlan.id);
      renderPlanList();
      refreshAllInventoryOverlays();
      return;
    }
    if (!confirm("Delete this plan?")) return;
    const r = await fetch(`/api/plans/${encodeURIComponent(editingPlan.id)}`, { method: "DELETE" });
    if (r.status === 204) {
      plans = plans.filter(p => p.id !== editingPlan.id);
      renderPlanList();
      refreshAllInventoryOverlays();
    } else {
      alert("Delete failed: " + r.status);
    }
  });

  redrawPlanFootprints();
  if (timeAware) {
    // Initial fetch + render. The fieldset already shows the loading state
    // from its first render; this resolves it.
    loadPlanVisibility(editingPlan).then(() => updatePlanEditorVis());
  }
}

async function savePlan() {
  if (!editingPlan) return false;
  const r = await fetch("/api/plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(editingPlan),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    alert(j.error || `Save failed (${r.status}).`);
    return false;
  }
  const saved = await r.json();
  const idx = plans.findIndex(p => p.id === saved.id);
  if (idx >= 0) plans[idx] = saved; else plans.push(saved);
  editingPlan = saved;
  const btn = document.getElementById("planSave");
  if (btn) { const orig = btn.textContent; btn.textContent = "Saved ✓"; setTimeout(() => { if (btn.textContent === "Saved ✓") btn.textContent = orig; }, 1500); }
  // Plan list changed — refresh inventory tile rendering so the
  // planned/done fade and "Hide planned" filter pick up the new state.
  refreshAllInventoryOverlays();
  return true;
}

function refreshAllInventoryOverlays() {
  for (const sid of Object.keys(invState || {})) {
    if (invState[sid]?.enabled) renderTileOverlay(sid);
  }
}

async function syncPlans() {
  const holder = document.getElementById("syncResult");
  const previouslySynced = plans.filter(p => p.last_synced_at);
  if (previouslySynced.length) {
    const last = previouslySynced
      .map(p => p.last_synced_at).sort().slice(-1)[0];
    const msg = `${previouslySynced.length} of ${plans.length} plan(s) have been synced before `
      + `(most recent: ${last}).\n\nTarget Scheduler import is append-only — re-syncing will `
      + `create duplicate projects/targets in NINA. Prune the old ones in NINA first, or cancel.\n\nContinue?`;
    if (!confirm(msg)) {
      if (holder) holder.innerHTML = `<div style="font-size:12px;color:#78839a;margin-top:8px">Sync cancelled.</div>`;
      return;
    }
  }
  if (holder) holder.innerHTML = `<div style="font-size:12px;color:#78839a;margin-top:8px">Syncing…</div>`;
  const r = await fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" } });
  const body = await r.json().catch(() => ({}));
  if (!holder) return;
  if (!r.ok) {
    holder.innerHTML = `<div class="sync-warn"><h4>Sync failed</h4><div>${esc(body.error || r.statusText)}</div></div>`;
    return;
  }
  if (body.download_url) {
    const a = document.createElement("a");
    a.href = body.download_url;
    a.download = body.zip_filename || "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  const success = `<div class="sync-warn" style="border-color:#3a7a3a;background:#1e2a1e;margin-top:8px">
    <h4 style="color:#b0ffb0">✓ Exported ${body.plan_count || 0} plan(s), ${body.project_count || 0} project(s), ${body.template_count || 0} template(s)</h4>
    <div style="font-size:11px">Downloaded: ${esc(body.zip_filename || body.zip_path || "")}${body.download_url ? ` · <a href="${esc(body.download_url)}" download="${esc(body.zip_filename || "")}">re-download</a>` : ""}</div>
    <div style="font-size:11px;margin-top:4px">In NINA → Target Scheduler → Manage Profiles → <strong>Import Profile</strong> → pick the zip.</div>
  </div>`;
  const warnings = body.warnings || [];
  let warnBlock = "";
  if (warnings.length) {
    const rows = warnings.map(w => `
      <div class="rename-row">
        <span title="${esc(w.message || "")}">${esc(w.project_name)} · ${esc(w.kind)} → resolved to <strong>${esc(String(w.resolved))}</strong></span>
        <input type="text" data-plan="${esc(w.plan_id)}" value="${esc(w.suggested_name || "")}" placeholder="rename to split into its own project">
      </div>`).join("");
    warnBlock = `<div class="sync-warn">
      <h4>⚠ ${warnings.length} strictest-wins resolution(s)</h4>
      <div style="font-size:12px">The zip above was built with the strictest value. If you want a plan to live in its own project instead, rename below and re-sync.</div>
      ${rows}
      <div style="margin-top:8px"><button id="syncRetry" class="btn-primary">Apply renames &amp; re-sync</button></div>
    </div>`;
  }
  holder.innerHTML = success + warnBlock;
  if (warnings.length) {
    document.getElementById("syncRetry")?.addEventListener("click", async () => {
      for (const inp of holder.querySelectorAll(".rename-row input")) {
        if (!inp.value) continue;
        const pl = plans.find(p => p.id === inp.dataset.plan);
        if (!pl) continue;
        pl.project_name = inp.value;
        await fetch("/api/plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pl) });
      }
      await loadPlans();
      syncPlans();
    });
  }
  await loadPlans();
}

function redrawPlanFootprints() {
  if (!planOverlay) return;
  planOverlay.removeAll();
  if (planOverlayDashed) planOverlayDashed.removeAll();
  if (planCenterCat) planCenterCat.removeAll();
  planHitList = [];
  clearHoverHighlight();
  if (!planningMode) return;

  const srcs = [];
  for (const pl of plans) {
    if (pl.target?.center_ra_deg == null) continue;
    const isEditing = editingPlan && editingPlan.id === pl.id;
    const actual = isEditing ? editingPlan : pl;
    const color = planBorderColor(actual);
    const hasData = planHasData(actual);
    const panelCorners = planPanelCorners(actual);
    const targetOverlay = (hasData || !planOverlayDashed) ? planOverlay : planOverlayDashed;
    for (const corners of panelCorners) {
      const poly = A.polygon(corners, {
        color,
        lineWidth: isEditing ? 3 : 2,
        fillColor: color + (hasData ? "20" : "10"),
      });
      poly._plan = actual;
      poly._corners = corners;
      targetOverlay.add(poly);
      planHitList.push({ poly, plan: actual, corners });
    }

    if (isEditing) {
      // Centre marker — drag this to move the whole plan (mosaic moves as
      // a unit, since planMosaicBoundsCorners derives panels from the
      // centre + dims). Mouse-down handler in onMapMouseDown checks the
      // hit radius against the live centre coords, not this marker, so
      // the marker is purely a visual cue.
      const center = A.marker(actual.target.center_ra_deg, actual.target.center_dec_deg, {
        popupTitle: "Drag to move",
        useMarkerDefaultIcon: false,
        color: "#5bb6ff", shape: "circle", sourceSize: 16,
      });
      center.data = { plan_id: actual.id, kind: "plan_center" };
      srcs.push(center);

      const bounds = planMosaicBoundsCorners(actual);
      const [nera, nedec] = bounds[2]; // overall NE corner of the full mosaic
      const handle = A.marker(nera, nedec, {
        popupTitle: "Drag to rotate",
        useMarkerDefaultIcon: false,
        color: "#ffd84d", shape: "square", sourceSize: 14,
      });
      handle.data = { plan_id: actual.id, kind: "plan_rotate" };
      srcs.push(handle);
    }
  }
  if (planCenterCat && srcs.length) planCenterCat.addSources(srcs);
}

function setupModeToggle() {
  document.getElementById("modeCoverage")?.addEventListener("click", () => setPlanningMode(false));
  document.getElementById("modePlanning")?.addEventListener("click", () => setPlanningMode(true));
}

function setPlanningMode(on) {
  planningMode = !!on;
  const mc = document.getElementById("modeCoverage");
  const mp = document.getElementById("modePlanning");
  if (mc) { mc.classList.toggle("active", !on); mc.setAttribute("aria-selected", String(!on)); }
  if (mp) { mp.classList.toggle("active",  on); mp.setAttribute("aria-selected", String( on)); }
  saveUiState();
  if (on) {
    if (selectedPlanId) {
      const pl = plans.find(p => p.id === selectedPlanId);
      if (pl) { renderPlanEditor(pl); return; }
    }
    renderPlanList();
  } else {
    if (selectedTargetId && manifest) {
      const t = manifest.targets.find(x => x.target_id === selectedTargetId);
      if (t) { renderTargetPanel(t); redrawPlanFootprints(); return; }
    }
    renderTargetList();
    redrawPlanFootprints();
  }
}

function setupMapDrag() {
  const mapDiv = document.getElementById("aladin-lite-div");
  if (!mapDiv) return;
  mapDiv.addEventListener("mousedown", onMapMouseDown, true);
  window.addEventListener("mousemove", onMapMouseMove, true);
  window.addEventListener("mouseup", onMapMouseUp, true);
}

function _pixelIn(mapDiv, evt) {
  const r = mapDiv.getBoundingClientRect();
  return [evt.clientX - r.left, evt.clientY - r.top];
}

function onMapMouseDown(evt) {
  if (!planningMode || !editingPlan || !aladin?.world2pix) return;
  const mapDiv = document.getElementById("aladin-lite-div");
  if (!mapDiv) return;
  const [px, py] = _pixelIn(mapDiv, evt);
  const bounds = planMosaicBoundsCorners(editingPlan);
  const nePix = aladin.world2pix(bounds[2][0], bounds[2][1]);
  if (nePix && isFinite(nePix[0]) && Math.hypot(nePix[0] - px, nePix[1] - py) < 14) {
    // Capture the angular offset between cursor and handle's current PA so
    // the first mouse-move doesn't snap the handle to the cursor (the
    // "1px move = big rotation jump" bug). Subsequent moves rotate
    // relative to where the user actually grabbed the handle.
    const cPix = aladin.world2pix(editingPlan.target.center_ra_deg, editingPlan.target.center_dec_deg);
    let grabOffsetDeg = 0;
    if (cPix && isFinite(cPix[0])) {
      const clickPaDeg = Math.atan2(-(px - cPix[0]), -(py - cPix[1])) * 180 / Math.PI;
      const cornerOffsetDeg = _planCornerOffsetDeg(editingPlan);
      const currentRot = editingPlan.target.rotation_deg || 0;
      // Normalize to [-180, 180] so the rotation math doesn't accumulate a
      // 360° jump when the grab crosses the wrap point.
      grabOffsetDeg = clickPaDeg - (currentRot + cornerOffsetDeg);
      grabOffsetDeg = ((grabOffsetDeg + 180) % 360 + 360) % 360 - 180;
    }
    dragState = { mode: "rotate", planId: editingPlan.id, grabOffsetDeg };
    evt.preventDefault(); evt.stopPropagation();
    return;
  }
  const cPix = aladin.world2pix(editingPlan.target.center_ra_deg, editingPlan.target.center_dec_deg);
  if (cPix && isFinite(cPix[0]) && Math.hypot(cPix[0] - px, cPix[1] - py) < 16) {
    dragState = { mode: "center", planId: editingPlan.id };
    evt.preventDefault(); evt.stopPropagation();
  }
}

// Shared between mousedown's offset capture and mousemove's rotation update —
// the NE-corner angular offset in the unrotated frame, derived from the
// current mosaic geometry.
function _planCornerOffsetDeg(plan) {
  const [fw, fh] = planFovArcmin(plan);
  const mos = planMosaic(plan);
  const overlap = Math.max(0, Math.min(0.99, mos.overlap_pct / 100));
  const totalW = fw * ((mos.cols - 1) * (1 - overlap) + 1);
  const totalH = fh * ((mos.rows - 1) * (1 - overlap) + 1);
  return Math.atan2(totalW / 2, totalH / 2) * 180 / Math.PI;
}

function onMapMouseMove(evt) {
  if (!dragState || !editingPlan || !aladin?.pix2world) return;
  const mapDiv = document.getElementById("aladin-lite-div");
  if (!mapDiv) return;
  const [px, py] = _pixelIn(mapDiv, evt);
  if (dragState.mode === "center") {
    const world = aladin.pix2world(px, py);
    if (!world) return;
    editingPlan.target.center_ra_deg = world[0];
    editingPlan.target.center_dec_deg = world[1];
    const raEl = document.getElementById("f_ra"); if (raEl) raEl.value = world[0].toFixed(3);
    const decEl = document.getElementById("f_dec"); if (decEl) decEl.value = world[1].toFixed(3);
    const rahms = document.getElementById("f_ra_hms"); if (rahms) rahms.textContent = deg2hms(world[0]);
    const ddms = document.getElementById("f_dec_dms"); if (ddms) ddms.textContent = deg2dms(world[1]);
  } else if (dragState.mode === "rotate") {
    const cPix = aladin.world2pix(editingPlan.target.center_ra_deg, editingPlan.target.center_dec_deg);
    if (!cPix) return;
    const dx = px - cPix[0];
    const dy = py - cPix[1];
    // Screen: +x right, +y down. North is up (−y), East is left (−x) on typical sky renders.
    // PA = atan2(east, north) = atan2(−dx, −dy); then subtract the NE corner's intrinsic offset.
    const paDeg = Math.atan2(-dx, -dy) * 180 / Math.PI;
    const cornerOffsetDeg = _planCornerOffsetDeg(editingPlan);
    // Subtract the grab offset captured at mousedown so the handle tracks
    // the cursor including the user's initial click offset — no first-move
    // jump.
    let newRot = paDeg - cornerOffsetDeg - (dragState.grabOffsetDeg || 0);
    newRot = Math.round(((newRot % 360) + 360) % 360);
    editingPlan.target.rotation_deg = newRot;
    const rotEl = document.getElementById("f_rot"); if (rotEl) rotEl.value = newRot;
  }
  redrawPlanFootprints();
  evt.preventDefault(); evt.stopPropagation();
}

function onMapMouseUp() {
  if (dragState) {
    dragState = null;
    // Aladin fires a synthetic "click" right after mouseup. By the time it
    // reaches our click handler, dragState is already null — without this
    // latch, releasing the rotate (or centre) handle on empty sky would
    // route through onMapPolyClick → goUpOneLevel → "unsaved plan changes"
    // modal, which is wrong: the user just rotated, didn't navigate away.
    _suppressNextMapClick = true;
  }
}
let _suppressNextMapClick = false;

function setupPlannerUI() {
  setupModeToggle();
  setupMapDrag();
}

// === Gear editor ===

function _slugId(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || `id-${Date.now().toString(36)}`;
}

function renderGearEditor() {
  panelMode = "gear-edit";
  updateSearchVisibility();
  saveUiState();
  const panel = document.getElementById("panelBody");
  if (!panel) return;
  // Work on a deep clone so Cancel discards cleanly.
  const draft = JSON.parse(JSON.stringify({ telescopes: gear.telescopes || [], cameras: gear.cameras || [] }));
  const FILTER_KEYS = ["Ha", "OIII", "SII", "L", "R", "G", "B", "V"];

  function render() {
    const telRows = draft.telescopes.map((t, i) => `
      <tr data-tel="${i}">
        <td><input type="text" data-field="name"             value="${esc(t.name || "")}" placeholder="Telescope name"></td>
        <td><input type="number" data-field="focal_length_mm" value="${t.focal_length_mm ?? ""}" step="1" style="width:70px"></td>
        <td><input type="number" data-field="aperture_mm"     value="${t.aperture_mm ?? ""}" step="1" style="width:60px"></td>
        <td><button type="button" class="btn-danger" data-del-tel="${i}">✕</button></td>
      </tr>`).join("");

    const camRows = draft.cameras.map((c, i) => {
      const dims = c.sensor_px || [0, 0];
      c.filters = c.filters || {};
      const camFilterKeys = Object.keys(c.filters);
      const filtersBody = camFilterKeys.length === 0
        ? `<tr><td colspan="6" style="color:#78839a;font-size:11px">No filters. Add one below.</td></tr>`
        : camFilterKeys.map(f => {
            const fc = c.filters[f] || {};
            return `<tr data-filter="${esc(f)}">
              <td style="padding-right:4px">${esc(f)}</td>
              <td><input type="number" data-field="default_sub_s" value="${fc.default_sub_s ?? ""}" step="10" style="width:60px" placeholder="sub s"></td>
              <td><input type="number" data-field="gain"          value="${fc.gain ?? -1}" step="1" style="width:48px"></td>
              <td><input type="number" data-field="offset"        value="${fc.offset ?? -1}" step="1" style="width:48px"></td>
              <td><input type="number" data-field="bin"           value="${fc.bin ?? 1}" step="1" min="1" style="width:36px"></td>
              <td><button type="button" class="btn-danger" data-del-filter="${esc(f)}" title="Remove filter">✕</button></td>
            </tr>`;
          }).join("");
      const addOptions = FILTER_KEYS
        .filter(k => !(k in c.filters))
        .map(k => `<option value="${k}">${k}</option>`).join("");
      return `<div class="cam-block" data-cam="${i}" style="border:1px solid #2a3246;border-radius:4px;padding:6px 8px;margin-bottom:8px">
        <div style="display:flex;gap:6px;align-items:center">
          <input type="text"   data-field="name"           value="${esc(c.name || "")}" placeholder="Camera name" style="flex:1;min-width:0">
          <button type="button" class="btn-danger" data-del-cam="${i}">✕</button>
        </div>
        <div style="display:flex;gap:6px;align-items:center;margin-top:4px">
          <input type="number" data-field="pixel_size_um"  value="${c.pixel_size_um ?? ""}" step="0.01" placeholder="px µm" style="flex:1;min-width:0">
          <input type="number" data-field="sensor_w"       value="${dims[0] ?? ""}" step="1" placeholder="w px" style="flex:1;min-width:0">
          <input type="number" data-field="sensor_h"       value="${dims[1] ?? ""}" step="1" placeholder="h px" style="flex:1;min-width:0">
        </div>
        <details style="margin-top:4px" open>
          <summary style="font-size:11px;color:#78839a;cursor:pointer">Filters — ${camFilterKeys.length} (sub s · gain · offset · bin)</summary>
          <table class="goals-table" style="margin-top:4px"><tbody>${filtersBody}</tbody></table>
          <div style="display:flex;gap:4px;align-items:center;margin-top:4px;font-size:11px">
            <select data-role="add-filter-preset" style="width:70px">
              <option value="">Pick…</option>
              ${addOptions}
              <option value="__custom__">Custom…</option>
            </select>
            <input type="text" data-role="add-filter-name" placeholder="or type name" style="flex:1;max-width:100px">
            <button type="button" data-role="add-filter-btn" class="btn-primary" style="font-size:10px;padding:2px 8px">+ Add filter</button>
          </div>
          <div style="font-size:10px;color:#78839a;margin-top:2px">Gain/offset/bin/sub become TS ExposureTemplate fields on sync. TS template mapping is set from the plan editor.</div>
        </details>
      </div>`;
    }).join("");

    panel.innerHTML = `
      <a class="back-link" id="backToPlans" href="#">← Back to plans</a>
      <h3>Edit gear</h3>
      <fieldset>
        <legend>Telescopes <button type="button" id="addTel" class="btn-primary" style="float:right;font-size:10px;padding:2px 8px">+ Add</button></legend>
        <table class="goals-table" style="width:100%">
          <thead><tr><th style="text-align:left">Name</th><th>Focal (mm)</th><th>Aperture (mm)</th><th></th></tr></thead>
          <tbody>${telRows || `<tr><td colspan="4" style="color:#78839a">No telescopes yet.</td></tr>`}</tbody>
        </table>
      </fieldset>
      <fieldset>
        <legend>Cameras <button type="button" id="addCam" class="btn-primary" style="float:right;font-size:10px;padding:2px 8px">+ Add</button></legend>
        ${camRows || `<div style="color:#78839a;font-size:12px">No cameras yet.</div>`}
      </fieldset>
      <div class="plan-editor-actions">
        <button type="button" id="gearSave" class="btn-primary">Save gear</button>
        <button type="button" id="gearCancel">Cancel</button>
        <button type="button" id="gearScanCoverage" style="margin-left:auto" title="Scan coverage manifest for telescopes/cameras not yet in gear">Scan coverage</button>
      </div>`;

    // Telescope row inputs
    panel.querySelectorAll("tr[data-tel]").forEach(tr => {
      const idx = parseInt(tr.dataset.tel, 10);
      tr.querySelectorAll("input[data-field]").forEach(inp => inp.addEventListener("input", e => {
        const key = e.target.dataset.field;
        const v = e.target.value;
        draft.telescopes[idx][key] = (key === "name") ? v : (parseFloat(v) || 0);
      }));
    });
    panel.querySelectorAll("[data-del-tel]").forEach(b => b.addEventListener("click", () => {
      draft.telescopes.splice(parseInt(b.dataset.delTel, 10), 1);
      render();
    }));
    panel.querySelector("#addTel").addEventListener("click", () => {
      draft.telescopes.push({ id: _slugId("telescope-" + (draft.telescopes.length + 1)), name: "New telescope", focal_length_mm: 500, aperture_mm: 80 });
      render();
    });

    // Camera block inputs
    panel.querySelectorAll(".cam-block").forEach(block => {
      const idx = parseInt(block.dataset.cam, 10);
      block.querySelectorAll("input[data-field]").forEach(inp => inp.addEventListener("input", e => {
        const key = e.target.dataset.field;
        const v = e.target.value;
        const cam = draft.cameras[idx];
        if (key === "name") cam.name = v;
        else if (key === "pixel_size_um") cam.pixel_size_um = parseFloat(v) || 0;
        else if (key === "sensor_w") { cam.sensor_px = cam.sensor_px || [0, 0]; cam.sensor_px[0] = parseInt(v) || 0; }
        else if (key === "sensor_h") { cam.sensor_px = cam.sensor_px || [0, 0]; cam.sensor_px[1] = parseInt(v) || 0; }
      }));
      block.querySelectorAll("tr[data-filter]").forEach(tr => {
        const f = tr.dataset.filter;
        tr.querySelectorAll("input[data-field]").forEach(inp => inp.addEventListener("input", e => {
          const key = e.target.dataset.field;
          const v = parseFloat(e.target.value);
          const cam = draft.cameras[idx];
          cam.filters = cam.filters || {};
          cam.filters[f] = cam.filters[f] || {};
          cam.filters[f][key] = isFinite(v) ? v : null;
        }));
      });
      block.querySelectorAll("[data-del-filter]").forEach(b => b.addEventListener("click", () => {
        const f = b.dataset.delFilter;
        const cam = draft.cameras[idx];
        if (cam.filters && f in cam.filters) delete cam.filters[f];
        render();
      }));
      const addBtn = block.querySelector("[data-role=add-filter-btn]");
      if (addBtn) addBtn.addEventListener("click", () => {
        const cam = draft.cameras[idx];
        const presetSel = block.querySelector("[data-role=add-filter-preset]");
        const nameIn = block.querySelector("[data-role=add-filter-name]");
        let name = (nameIn?.value || "").trim();
        const preset = presetSel?.value || "";
        if (!name && preset && preset !== "__custom__") name = preset;
        if (!name) { nameIn?.focus(); return; }
        cam.filters = cam.filters || {};
        if (name in cam.filters) { alert(`Filter "${name}" already exists on ${cam.name || "camera"}.`); return; }
        const isLum = /^L$|lum/i.test(name);
        const isNarrow = /^(Ha|OIII|SII|NII|OI)$/i.test(name);
        cam.filters[name] = {
          ts_template_id: null,
          ts_template_name: null,
          default_sub_s: isNarrow ? 300 : (isLum ? 120 : 120),
          gain: -1, offset: -1, bin: 1,
        };
        render();
      });
    });
    panel.querySelectorAll("[data-del-cam]").forEach(b => b.addEventListener("click", () => {
      draft.cameras.splice(parseInt(b.dataset.delCam, 10), 1);
      render();
    }));
    panel.querySelector("#addCam").addEventListener("click", () => {
      const defaults = {};
      for (const f of FILTER_KEYS) defaults[f] = { ts_template_id: null, ts_template_name: null, default_sub_s: 300, gain: -1, offset: -1, bin: 1 };
      draft.cameras.push({
        id: _slugId("camera-" + (draft.cameras.length + 1)),
        name: "New camera",
        pixel_size_um: 3.76,
        sensor_px: [6000, 4000],
        filters: defaults,
      });
      render();
    });

    panel.querySelector("#gearSave").addEventListener("click", async () => {
      // Ensure each row has a stable id (derived from name if missing)
      for (const t of draft.telescopes) if (!t.id) t.id = _slugId(t.name);
      for (const c of draft.cameras)    if (!c.id) c.id = _slugId(c.name);
      gear.telescopes = draft.telescopes;
      gear.cameras = draft.cameras;
      const ok = await saveGear();
      if (!ok) { alert("Save failed"); return; }
      renderPlanList();
    });
    panel.querySelector("#gearCancel").addEventListener("click", () => renderPlanList());
    panel.querySelector("#backToPlans").addEventListener("click", e => { e.preventDefault(); renderPlanList(); });
    panel.querySelector("#gearScanCoverage")?.addEventListener("click", async () => {
      // Persist any in-progress edits first so the scan merges into the saved state.
      gear.telescopes = draft.telescopes;
      gear.cameras = draft.cameras;
      const saveOk = await saveGear();
      if (!saveOk) { alert("Couldn't save current edits before scanning."); return; }
      const j = await seedGearFromManifest();
      await loadGear();
      draft.telescopes = JSON.parse(JSON.stringify(gear.telescopes));
      draft.cameras = JSON.parse(JSON.stringify(gear.cameras));
      render();
      const addedT = j?.added_telescopes || [];
      const addedC = j?.added_cameras || [];
      if (addedT.length || addedC.length) {
        alert(`Added from coverage:\n• Telescopes: ${addedT.join(", ") || "none"}\n• Cameras: ${addedC.join(", ") || "none"}`);
      } else {
        alert("No new gear found in coverage (all telescopes/cameras already present).");
      }
    });
  }
  render();
}

init();
