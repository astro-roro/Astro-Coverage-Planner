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
let overlay = null;     // main target footprints (polygons)
let centerCat = null;   // target-center markers (click/hover source)
let filterBadgeCat = null; // single catalog of "filter badges" (one source per target, custom draw)
let catOverlays = {};   // catalog overlays (Phase 3)
let selectedFilters = new Set(["Ha", "SII", "OIII"]);
let selectedTelescopes = new Set(); // populated after manifest loads
let telescopeColor = {};  // telescope name → color
let filterLogic = "any";
let minHours = 0;
let gapMode = false;
let currentSite = { lat: -33.87, lon: 151.21, height: 20 };
let panelMode = "list"; // "list" | "detail"
let searchTokens = [];  // parsed tokens from the search box
let selectedTargetId = null; // target_id while in detail view, null otherwise

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

  if (s.gapMode) {
    gapMode = true;
    const btn = document.getElementById("gapMode");
    if (btn) btn.style.background = "#663";
  }

  if (typeof s.projection === "string" && s.projection) {
    const proj = document.getElementById("projSel");
    if (proj) proj.value = s.projection;
    if (aladin) aladin.setProjection(s.projection);
  }

  if (typeof s.frame === "string" && s.frame) {
    const fr = document.getElementById("frameSel");
    if (fr) fr.value = s.frame;
    if (aladin) aladin.setFrame(s.frame);
  }

  if (typeof s.site === "string" && s.site) {
    const siteSel = document.getElementById("siteSel");
    if (siteSel) {
      siteSel.value = s.site;
      if (s.site === "custom") {
        document.getElementById("latIn").style.display = "";
        document.getElementById("lonIn").style.display = "";
        if (s.customLat) {
          document.getElementById("latIn").value = s.customLat;
          currentSite.lat = parseFloat(s.customLat);
        }
        if (s.customLon) {
          document.getElementById("lonIn").value = s.customLon;
          currentSite.lon = parseFloat(s.customLon);
        }
      } else {
        const opt = siteSel.selectedOptions[0];
        if (opt && opt.dataset.lat) {
          currentSite = {
            lat: parseFloat(opt.dataset.lat),
            lon: parseFloat(opt.dataset.lon),
            height: parseFloat(opt.dataset.height || 0),
          };
        }
      }
    }
  }

  if (Array.isArray(s.catalogs)) {
    for (const id of ["cat_green", "cat_smgps", "cat_emu", "cat_hii"]) {
      const cb = document.getElementById(id);
      if (cb) cb.checked = s.catalogs.includes(id);
    }
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

function saveUiState() {
  try {
    const state = {
      search: document.getElementById("searchInput")?.value || "",
      filters: [...selectedFilters],
      filterLogic,
      minHours,
      telescopes: [...selectedTelescopes],
      catalogs: ["cat_green", "cat_smgps", "cat_emu", "cat_hii"]
        .filter(id => document.getElementById(id)?.checked),
      projection: document.getElementById("projSel")?.value || "",
      frame: document.getElementById("frameSel")?.value || "",
      site: document.getElementById("siteSel")?.value || "",
      customLat: document.getElementById("latIn")?.value || "",
      customLon: document.getElementById("lonIn")?.value || "",
      gapMode,
      selectedTargetId,
    };
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(state));
  } catch { /* localStorage full / disabled — ignore */ }
}

// --- Search tokenizer ---
// Splits `camera:asi2600 filter:Ha "orion neb" hours>3` into tokens.
// Supports: bareword (free-text), key:value, key>N / key<N for numeric comparators.
// Quoted phrases preserve spaces: key:"with spaces" or just "bare phrase".
const SEARCH_KV_KEYS = new Set(["object", "name", "filter", "telescope", "tel", "camera", "cam"]);
const SEARCH_CMP_KEYS = new Set(["fov", "hours"]);

function tokenizeSearch(query) {
  if (!query) return [];
  // Split the query into raw tokens, preserving quoted values inside key:"..." pairs
  // and as bare "quoted phrase" tokens.
  const rawTokens = [];
  const re = /([a-zA-Z]+:"[^"]*"|[a-zA-Z]+[:><][^\s]+|"[^"]*"|\S+)/g;
  let m;
  while ((m = re.exec(query)) !== null) {
    rawTokens.push(m[1]);
  }
  const parsed = [];
  for (const raw of rawTokens) {
    if (!raw) continue;
    // Numeric comparator: key>N or key<N
    const cmp = raw.match(/^([a-zA-Z]+)([><])(.+)$/);
    if (cmp && SEARCH_CMP_KEYS.has(cmp[1].toLowerCase())) {
      const v = parseFloat(cmp[3]);
      if (isFinite(v)) {
        parsed.push({ kind: "cmp", key: cmp[1].toLowerCase(), op: cmp[2], value: v });
        continue;
      }
    }
    // key:value or key:"quoted value"
    const kv = raw.match(/^([a-zA-Z]+):(.*)$/);
    if (kv && SEARCH_KV_KEYS.has(kv[1].toLowerCase())) {
      let val = kv[2];
      if (val.length >= 2 && val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
      if (val) parsed.push({ kind: "kv", key: kv[1].toLowerCase(), value: val.toLowerCase() });
      continue;
    }
    // Bare quoted phrase
    if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
      const v = raw.slice(1, -1);
      if (v) parsed.push({ kind: "text", value: v.toLowerCase() });
      continue;
    }
    // Bareword — matches object, telescope, camera (substring, OR)
    parsed.push({ kind: "text", value: raw.toLowerCase() });
  }
  return parsed;
}

function targetMatchesSearch(t, tokens) {
  if (!tokens.length) return true;
  const objs = (t.objects || []).map(s => String(s).toLowerCase());
  const tels = (t.telescopes || []).map(s => String(s).toLowerCase());
  const cams = (t.cameras || []).map(s => String(s).toLowerCase());
  const filters = t.filters || {};
  const totalH = (function () {
    let s = 0;
    for (const d of Object.values(filters)) s += (d.total_hours || 0);
    return s;
  })();
  const fovMax = Math.max(...((t.fov_arcmin && t.fov_arcmin.length) ? t.fov_arcmin : [0]));

  for (const tok of tokens) {
    if (tok.kind === "text") {
      const v = tok.value;
      if (!objs.some(s => s.includes(v)) &&
          !tels.some(s => s.includes(v)) &&
          !cams.some(s => s.includes(v))) return false;
      continue;
    }
    if (tok.kind === "kv") {
      const v = tok.value;
      switch (tok.key) {
        case "object":
        case "name":
          if (!objs.some(s => s.includes(v))) return false;
          break;
        case "filter": {
          const fname = Object.keys(filters).find(f => f.toLowerCase() === v);
          if (!fname || !(filters[fname].total_hours > 0)) return false;
          break;
        }
        case "telescope":
        case "tel":
          if (!tels.some(s => s.includes(v))) return false;
          break;
        case "camera":
        case "cam":
          if (!cams.some(s => s.includes(v))) return false;
          break;
        default:
          return false;
      }
      continue;
    }
    if (tok.kind === "cmp") {
      let lhs;
      if (tok.key === "fov") lhs = fovMax;
      else if (tok.key === "hours") lhs = totalH;
      else return false;
      if (tok.op === ">" && !(lhs > tok.value)) return false;
      if (tok.op === "<" && !(lhs < tok.value)) return false;
      continue;
    }
  }
  return true;
}

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

  // Filter dots
  const filters = (src.data && src.data.filters) || {};
  for (let i = 0; i < n; i++) {
    const f = FILTER_DOT_ORDER[i];
    const has = (filters[f]?.total_hours || 0) > 0;
    ctx.globalAlpha = has ? 1.0 : 0.2;
    ctx.fillStyle = FILTER_COLORS[f];
    const cx = inset + PAD_X + DOT_R + i * (DOT_R * 2 + DOT_GAP);
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

  // Telescope toggle: if no telescopes selected (before init), allow all.
  // If a target has no telescope tag, leave it visible regardless.
  const tel = telescopeOf(t);
  if (tel && selectedTelescopes.size > 0 && !selectedTelescopes.has(tel)) return false;

  if (gapMode) {
    return hasAny("Ha") && !hasAny("SII") && (hrs.Ha || 0) >= minHours;
  }

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

function summariseFilters(t) {
  const pairs = Object.entries(t.filters)
    .filter(([f, d]) => (d.total_hours || 0) > 0)
    .sort((a, b) => (b[1].total_hours || 0) - (a[1].total_hours || 0));
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

function renderTargetList() {
  panelMode = "list";
  selectedTargetId = null;
  saveUiState();
  const panel = document.getElementById("panelBody");
  if (!panel || !manifest) return;

  const matches = manifest.targets.filter(targetMatches);
  matches.sort((a, b) => totalHoursOf(b) - totalHoursOf(a));

  const rows = matches.map(t => {
    const name = esc(t.objects?.[0] || "(no name)");
    const tel = telescopeOf(t);
    const swatch = telescopeColor[tel] || TELESCOPE_FALLBACK;
    const total = totalHoursOf(t).toFixed(1);
    const dots = filterDotsHtml(t.filters || {});
    return `<li class="target-row" data-target-id="${t.target_id}">
        <span class="tr-swatch" style="background:${esc(swatch)}" title="${esc(tel)}"></span>
        <span class="tr-name">#${t.target_id} ${name}</span>
        <span class="tr-dots">${dots}</span>
        <span class="tr-hours">${total}h</span>
      </li>`;
  }).join("");

  const empty = `<li class="tr-empty">No targets match current filters.</li>`;

  panel.innerHTML = `
    <div class="panel-list">
      <h3>Targets <span class="tr-count">${matches.length} of ${manifest.targets.length}</span></h3>
      <ul class="target-list">${rows || empty}</ul>
    </div>`;

  panel.querySelectorAll(".target-row").forEach(row => {
    row.addEventListener("click", () => {
      const id = parseInt(row.dataset.targetId, 10);
      const t = manifest.targets.find(x => x.target_id === id);
      if (t) renderTargetPanel(t);
    });
  });
}

function renderTargetPanel(t) {
  panelMode = "detail";
  selectedTargetId = t.target_id;
  saveUiState();
  const panel = document.getElementById("panelBody");
  const filtersSorted = Object.entries(t.filters)
    .sort((a, b) => (b[1].total_hours || 0) - (a[1].total_hours || 0));

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

  panel.innerHTML = `
    <div>
      <a class="back-link" id="backToList" href="#">← Back to list</a>
      <h3>Target #${t.target_id}: ${objs}</h3>
      <div>${filterPills}</div>

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

let catalogsData = {}; // {green_snrs: [...], smgps_candidates: [...], ...}

function redrawFootprints() {
  if (!overlay || !manifest) return;
  overlay.removeAll();
  if (centerCat) centerCat.removeAll();
  if (filterBadgeCat) filterBadgeCat.removeAll();

  let shown = 0;
  const centerSources = [];
  const badgeSources = [];

  for (const t of manifest.targets) {
    if (!targetMatches(t)) continue;
    if (!t.corners_icrs || t.corners_icrs.length < 3) continue;
    const deepest = deepestFilter(t.filters, minHours);
    if (!deepest) continue;

    const tel = telescopeOf(t);
    const borderColor = telescopeColor[tel] || TELESCOPE_FALLBACK;
    const fillColor = (FILTER_COLORS[deepest] || "#888") + "20";

    // Aladin v3: polygon from [[ra,dec],...] — border = telescope, fill = filter-priority
    const poly = A.polygon(t.corners_icrs, {
      color: borderColor,
      lineWidth: 2.5,
      fillColor,
    });
    poly._target = t;
    overlay.add(poly);

    // Clickable center marker (invisible-ish dot that carries the target id)
    const src = A.marker(t.center_ra_deg, t.center_dec_deg, {
      popupTitle: `#${t.target_id} ${t.objects?.[0] || "(no name)"}`,
      popupDesc: summariseFilters(t),
      useMarkerDefaultIcon: false,
      color: borderColor, shape: "circle", sourceSize: 8,
    });
    src.data = { target_id: t.target_id };
    centerSources.push(src);

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
  if (centerCat) centerCat.addSources(centerSources);
  if (filterBadgeCat) filterBadgeCat.addSources(badgeSources);
  const cs = document.getElementById("coverageStats");
  if (cs) {
    cs.innerHTML = `<div style="margin-top:10px;font-size:12px;color:#a2aec2">Showing <strong>${shown}</strong> of ${manifest.targets.length} targets.</div>`;
  }
  if (panelMode === "list") renderTargetList();
}

function setupCatalogOverlays() {
  const cfg = [
    { id: "cat_green", name: "green_snrs",       color: "#ff3030", size: 12, marker: "square" },
    { id: "cat_smgps", name: "smgps_candidates", color: "#ff9900", size: 10, marker: "triangle" },
    { id: "cat_emu",   name: "emu_candidates",   color: "#ffff33", size: 10, marker: "plus" },
    { id: "cat_hii",   name: "anderson_hii",     color: "#66cc66", size:  6, marker: "dot" },
  ];
  for (const c of cfg) {
    const cb = document.getElementById(c.id);
    if (!cb) continue;
    cb.addEventListener("change", () => {
      drawCatalogOverlay(c, cb.checked);
      saveUiState();
    });
  }
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
  for (const e of catalogsData[cfg.name]) {
    if (e.ra_deg == null) continue;
    const src = A.source(e.ra_deg, e.dec_deg, { name: e.name, catalog: cfg.name, ...e });
    ovr.addSources([src]);
  }
}

async function loadCatalogs() {
  try {
    const r = await fetch("/api/catalogs");
    catalogsData = await r.json();
    // if any catalog toggle is already checked, redraw
    for (const id of ["cat_green", "cat_smgps", "cat_emu", "cat_hii"]) {
      const cb = document.getElementById(id);
      if (cb && cb.checked) cb.dispatchEvent(new Event("change"));
    }
    return catalogsData;
  } catch (e) {
    console.warn("catalogs not available yet:", e);
    return {};
  }
}

function setupFilterUI() {
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      searchTokens = tokenizeSearch(searchInput.value);
      saveUiState();
      redrawFootprints();
    });
  }

  for (const cb of document.querySelectorAll(".filters input[type=checkbox][data-f]")) {
    cb.addEventListener("change", () => {
      if (cb.checked) selectedFilters.add(cb.dataset.f);
      else selectedFilters.delete(cb.dataset.f);
      saveUiState();
      redrawFootprints();
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
    redrawFootprints();
  });
  document.getElementById("gapMode").addEventListener("click", () => {
    gapMode = !gapMode;
    document.getElementById("gapMode").style.background = gapMode ? "#663" : "";
    saveUiState();
    redrawFootprints();
  });
  document.getElementById("exportCsv").addEventListener("click", () => {
    window.location = "/api/export/priority";
  });

  document.getElementById("projSel").addEventListener("change", e => {
    aladin.setProjection(e.target.value);
    saveUiState();
  });
  document.getElementById("frameSel").addEventListener("change", e => {
    aladin.setFrame(e.target.value);
    saveUiState();
  });

  const siteSel = document.getElementById("siteSel");
  siteSel.addEventListener("change", () => {
    const opt = siteSel.selectedOptions[0];
    if (opt.value === "custom") {
      document.getElementById("latIn").style.display = "";
      document.getElementById("lonIn").style.display = "";
      saveUiState();
      return;
    }
    currentSite = {
      lat: parseFloat(opt.dataset.lat),
      lon: parseFloat(opt.dataset.lon),
      height: parseFloat(opt.dataset.height || 0),
    };
    document.getElementById("latIn").style.display = "none";
    document.getElementById("lonIn").style.display = "none";
    saveUiState();
    updateObsNow();
  });
  document.getElementById("latIn").addEventListener("change", e => {
    currentSite.lat = parseFloat(e.target.value);
    saveUiState();
    updateObsNow();
  });
  document.getElementById("lonIn").addEventListener("change", e => {
    currentSite.lon = parseFloat(e.target.value);
    saveUiState();
    updateObsNow();
  });
}

async function updateObsNow() {
  try {
    const now = new Date().toISOString();
    const r = await fetch(`/api/observability?lat=${currentSite.lat}&lon=${currentSite.lon}&time=${now}`);
    const d = await r.json();
    if (!d.targets) return;
    const n_above30 = d.targets.filter(x => x.alt_deg >= 30).length;
    const n_above60 = d.targets.filter(x => x.alt_deg >= 60).length;
    document.getElementById("obsNow").textContent =
      `@${currentSite.lat},${currentSite.lon}: ${n_above30} targets >30° alt, ${n_above60} >60° alt (UTC ${now.slice(11,16)})`;
  } catch (e) {
    document.getElementById("obsNow").textContent = "(observability offline)";
  }
}

function init() {
  A.init.then(async () => {
    aladin = A.aladin("#aladin-lite-div", {
      fov: 180,
      projection: "AIT",
      cooFrame: "ICRSd",
      survey: "P/DSS2/color",
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

    centerCat = A.catalog({ name: "Target centers", sourceSize: 8, shape: "circle", color: "#ffffff" });
    aladin.addCatalog(centerCat);

    // One catalog for filter-coverage badges (custom shape draws a pill of 7 dots).
    // sourceSize must be >= ~8 or Aladin's default shape prelude errors with
    // "negative radius" in arc(). We override with our own shape function.
    filterBadgeCat = A.catalog({
      name: "filter_badges",
      shape: filterBadgeShape,
      sourceSize: 18,
    });
    aladin.addCatalog(filterBadgeCat);

    // Click on any marker → render panel; works for both target centers and catalog overlays
    aladin.on("objectClicked", src => {
      if (!src) return;
      if (src.data?.target_id != null) {
        const t = manifest.targets.find(x => x.target_id === src.data.target_id);
        if (t) renderTargetPanel(t);
        return;
      }
      // catalog marker
      const tip = document.getElementById("tooltip");
      tip.textContent = src.data?.name ? `catalog: ${src.data.catalog || ""} ${src.data.name}` : "";
    });
    aladin.on("objectHovered", src => {
      if (!src) return;
      if (src.data?.target_id != null) {
        const t = manifest.targets.find(x => x.target_id === src.data.target_id);
        if (t) document.getElementById("tooltip").textContent =
          `#${t.target_id} ${t.objects?.[0] || ""} — ${summariseFilters(t)}`;
      } else if (src.data?.name) {
        document.getElementById("tooltip").textContent = `${src.data.catalog || ""} ${src.data.name}`;
      }
    });

    // Load manifest
    const r = await fetch("/api/manifest");
    manifest = await r.json();

    // Assign telescope colors and build toggle UI
    const { map, sorted } = assignTelescopeColors(manifest.targets);
    telescopeColor = map;
    selectedTelescopes = new Set(sorted); // all enabled by default
    renderTelescopeLegend(sorted);

    setupFilterUI();
    setupCatalogOverlays();

    // Restore previous session state before the first draw so the map
    // reflects saved filters/telescopes/search immediately.
    applyUiStatePreManifest();
    applyUiStatePostManifest();

    redrawFootprints();
    loadCatalogs();
    updateObsNow();
    setInterval(updateObsNow, 60_000);
  });
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

init();
