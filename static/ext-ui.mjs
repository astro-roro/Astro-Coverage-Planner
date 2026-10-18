// Helpers for the generic extension UI hooks (docs/extensions.md): rail tabs
// declared in the manifest, filled tile footprints, and the plan links in the
// tile panel. Pulled out of app.js so they can be tested without a browser.

// --- Rail tabs ------------------------------------------------------------

const ID_RE = /^[A-Za-z0-9_-]{1,40}$/;

// The side rail's own icons, keyed by the names an extension may use. Several
// names share one drawing. Anything else gets GENERIC_ICON.
const ICON_LIST = "M4 6h16M4 12h16M4 18h10";
const ICON_FILTER = "M4 5h16l-6 8v6l-4-2v-4z";
const ICON_LIBRARY = "M5 4v16M10 4v16M15 5l4 14";
const ICON_ACTIVITY = "M3 12h4l3-7 4 14 3-7h4";
const GENERIC_ICON = "M5 5h14v14H5zM9 12h6M12 9v6";

const ICON_PATHS = {
  list: ICON_LIST, plans: ICON_LIST, targets: ICON_LIST,
  filter: ICON_FILTER, filters: ICON_FILTER,
  library: ICON_LIBRARY, catalogue: ICON_LIBRARY, catalog: ICON_LIBRARY, layers: ICON_LIBRARY,
  activity: ICON_ACTIVITY, pulse: ICON_ACTIVITY, chart: ICON_ACTIVITY, calendar: ICON_ACTIVITY,
};

export function railIconPath(name) {
  const key = typeof name === "string" ? name.trim().toLowerCase() : "";
  return Object.prototype.hasOwnProperty.call(ICON_PATHS, key) ? ICON_PATHS[key] : GENERIC_ICON;
}

// Same rule the server applies (extensions.rail_page_ok): a same-origin path
// under /api/ext/<extension id>, with underscores in the id also accepted as
// hyphens. Checked again here so a manifest from anywhere can't put an
// off-site page in the iframe.
export function railPageOk(extensionId, page) {
  if (typeof extensionId !== "string" || !ID_RE.test(extensionId)) return false;
  if (typeof page !== "string" || !page.startsWith("/") || page.startsWith("//")) return false;
  if (page.includes("\\") || /[\x00-\x1f\x7f]/.test(page)) return false;
  const path = page.split("?")[0].split("#")[0];
  if (/%(2e|2f|5c)/i.test(path)) return false;
  if (path.split("/").some(seg => seg === ".." || seg === ".")) return false;
  const forms = new Set([extensionId, extensionId.replace(/_/g, "-")]);
  for (const f of forms) {
    const prefix = `/api/ext/${f}`;
    if (path === prefix || path.startsWith(prefix + "/")) return true;
  }
  return false;
}

// Flatten a manifest into the tabs to draw: [{key, label, icon, page}].
// `key` is the data-tab value, unique across extensions.
export function railTabsFrom(manifest) {
  const out = [];
  const seen = new Set();
  for (const ext of Array.isArray(manifest) ? manifest : []) {
    if (!ext || typeof ext !== "object" || !Array.isArray(ext.rail_tabs)) continue;
    for (const t of ext.rail_tabs) {
      if (!t || typeof t !== "object") continue;
      if (typeof t.id !== "string" || !ID_RE.test(t.id)) continue;
      if (typeof t.label !== "string" || !t.label.trim()) continue;
      if (!railPageOk(ext.extension, t.page)) continue;
      const key = `ext-${ext.extension}-${t.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ key, label: t.label.trim(), icon: typeof t.icon === "string" ? t.icon : "", page: t.page });
    }
  }
  return out;
}

// --- Filled footprints ----------------------------------------------------

// `color` with an alpha channel for a 0..1 opacity, or null when the colour
// isn't a #rgb / #rrggbb hex we can extend. Callers fall back to outline only.
export function fillColorFor(color, opacity) {
  if (typeof opacity !== "number" || !Number.isFinite(opacity) || opacity < 0 || opacity > 1) return null;
  if (typeof color !== "string") return null;
  let hex = null;
  if (/^#[0-9a-f]{6}$/i.test(color)) hex = color.slice(1);
  else if (/^#[0-9a-f]{3}$/i.test(color)) hex = color.slice(1).split("").map(c => c + c).join("");
  if (!hex) return null;
  const a = Math.round(opacity * 255).toString(16).padStart(2, "0");
  return `#${hex}${a}`;
}

// --- Plan links in the tile panel ----------------------------------------

// HTML for a tile's metadata.plans: [{plan_id, name, detail}]. The name is a
// link (data-plan-id) when plan_id is set and `isKnown(plan_id)` agrees, plain
// text otherwise; detail sits under it as muted text. Everything is escaped.
// Returns "" when there is nothing valid to show.
export function tilePlansHtml(plans, esc, isKnown = () => true) {
  if (!Array.isArray(plans)) return "";
  const rows = [];
  for (const p of plans) {
    if (!p || typeof p !== "object") continue;
    const name = typeof p.name === "string" ? p.name : "";
    if (!name) continue;
    const detail = typeof p.detail === "string" ? p.detail : "";
    const id = typeof p.plan_id === "string" && p.plan_id ? p.plan_id : null;
    const title = id && isKnown(id)
      ? `<a href="#" class="tile-plan-link" data-plan-id="${esc(id)}">${esc(name)}</a>`
      : `<span class="tile-plan-name">${esc(name)}</span>`;
    const sub = detail ? `<div class="tile-plan-detail">${esc(detail)}</div>` : "";
    rows.push(`<li>${title}${sub}</li>`);
  }
  return rows.length ? `<ul class="tile-plans">${rows.join("")}</ul>` : "";
}

// "replace" hides ACP's own tile panel sections so a source can show only its
// own panel_html; "append" (the default) adds panel_html after them.
export function tilePanelModeOf(sourceMeta) {
  return sourceMeta?.tile_panel === "replace" ? "replace" : "append";
}

// Heading for a tile panel: the tile id as the title, with the source label
// as a small muted line above it. A tile with no id falls back to the label.
export function tilePanelTitleHtml(sourceLabel, tileId, esc) {
  const label = typeof sourceLabel === "string" ? sourceLabel : "";
  const id = tileId == null ? "" : String(tileId);
  if (!id) return `<h3>${esc(label)}</h3>`;
  const above = label ? `<div class="tile-source-label">${esc(label)}</div>` : "";
  return `${above}<h3>${esc(id)}</h3>`;
}

const ACTION_SELECTOR = "[data-acp-plan-id],[data-acp-new-plan],[data-acp-goto]";

function degAttr(el, name) {
  const raw = el.getAttribute(name);
  if (raw == null || String(raw).trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

// Reads the data-acp-* action off the element a click landed on (or its
// nearest ancestor inside `root`). Returns null when there is no action or
// its coordinates are unusable, so a malformed attribute does nothing.
export function tilePanelAction(target, root) {
  const el = target?.closest?.(ACTION_SELECTOR);
  if (!el || (root && !root.contains(el))) return null;
  if (el.hasAttribute("data-acp-plan-id")) {
    const id = (el.getAttribute("data-acp-plan-id") || "").trim();
    return id ? { type: "plan", id } : null;
  }
  const ra = degAttr(el, "data-ra"), dec = degAttr(el, "data-dec");
  if (ra == null || dec == null || dec < -90 || dec > 90) return null;
  const raN = ((ra % 360) + 360) % 360;
  if (el.hasAttribute("data-acp-new-plan")) {
    const name = (el.getAttribute("data-name") || "").trim();
    return { type: "new-plan", ra: raN, dec, name };
  }
  const fov = degAttr(el, "data-fov");
  return { type: "goto", ra: raN, dec, fov: fov != null && fov > 0 ? fov : null };
}

// Runs a tile panel action through the supplied handlers. Returns true when
// an action was found, so the caller can stop the click's default.
export function runTilePanelAction(action, { openPlan, newPlan, goto }) {
  if (!action) return false;
  if (action.type === "plan") openPlan(action.id);
  else if (action.type === "new-plan") newPlan(action);
  else goto(action);
  return true;
}
