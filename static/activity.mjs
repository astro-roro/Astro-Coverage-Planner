// The Activity tab's rendering rules, as pure functions so `node --test` can
// check them without a browser (docs/specs/activity-tab.md, "The tab").
// activity-ui.mjs does the fetching and the DOM; nothing here touches either.

export const ACTIVITY_PANEL_W = 560;

// Chip id, label and event types. Mirrors CHIPS in activity.py.
export const CHIPS = [
  { id: "plans", label: "Plans", types: ["plan_created", "plan_edited", "plan_deleted",
    "project_settings", "gear_changed", "counts_corrected", "other"] },
  { id: "syncs", label: "Syncs", types: ["sync_for_tonight", "rig_synced", "ts_upload_received",
    "ts_upload_applied", "ts_upload_discarded", "ts_push"] },
  { id: "imaging", label: "Imaging", types: ["imaging_target", "goal_reached", "progress_report",
    "progress_unknown", "session_start", "session_end"] },
  { id: "site", label: "Site and focus", types: ["safety", "meridian_flip", "autofocus_summary", "park"] },
  { id: "data", label: "Data landed", types: ["transfer_landed", "ingest"] },
  { id: "processing", label: "Processing", types: ["calibration", "threshold_decision",
    "master_queued", "accepted_counts", "darks_built", "aac_alert", "aac_recovered"] },
  { id: "masters", label: "Masters", types: ["master_built", "master_ready"] },
  { id: "coverage", label: "Coverage", types: ["scan_finished", "scan_error", "coverage_changed"] },
];

export const DEFAULT_CHIPS = Object.fromEntries(CHIPS.map(c => [c.id, c.id !== "site"]));

const CHIP_BY_TYPE = new Map(CHIPS.flatMap(c => c.types.map(t => [t, c.id])));

// An unknown type is stored as "other", which belongs to Plans.
export function chipOf(type) {
  return CHIP_BY_TYPE.get(type) || "plans";
}

export function esc(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// The badge counts open needs-you items. With none, a dot says something is new.
export function railState(summary) {
  if (!summary) return null;
  if (summary.needs_you > 0) return { badge: summary.needs_you };
  if (summary.has_new) return { dot: true };
  return null;
}

// A switched-off chip hides its rows, except the ones that went wrong.
export function visibleRows(rows, chips) {
  return rows.filter(r => r.severity === "error" || chips[chipOf(r.type)] !== false);
}

export function pageState({ shown, total }) {
  if (!total) return { text: "No activity yet", showLoadOlder: false };
  return {
    text: `Showing ${shown} of ${total} night${total === 1 ? "" : "s"}`,
    showLoadOlder: shown < total,
  };
}

// Mac folder, Windows folder, file name: the order the three Copy buttons use.
export function masterRowStrings(master) {
  const m = master || {};
  return [m.mac_folder || "", m.windows_folder || "", m.file_name || ""];
}

// A row is new when it arrived or changed after the previous visit.
export function isNewRow(row, lastSeen) {
  const stamp = [row.received_at, row.updated_at].filter(Boolean).sort().pop();
  if (!stamp) return false;
  return !lastSeen || stamp > lastSeen;
}

const ICONS = {
  plans: '<path d="M3 4h10M3 8h10M3 12h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>',
  syncs: '<path d="M3 6a5 5 0 0 1 9-1.5M13 10a5 5 0 0 1-9 1.5M12.5 2v3h-3M3.5 14v-3h3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  imaging: '<circle cx="8" cy="8" r="2" fill="currentColor"/><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  site: '<path d="M2 13h12M4 13V8l4-4 4 4v5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
  data: '<path d="M8 2v9M4.5 7.5 8 11l3.5-3.5M3 14h10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  processing: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 5v3l2 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>',
  masters: '<rect x="3" y="3" width="10" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3 6.5h10" stroke="currentColor" stroke-width="1.5"/>',
  coverage: '<path d="M2 12 6 6l3 4 2-3 3 5z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
};

function iconHtml(chip) {
  return `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS[chip] || ICONS.plans}</svg>`;
}

// The clock time as the sender's offset shows it, which ACP sets to the
// observatory's zone when it stores the event.
function clockOf(at) {
  const m = /T(\d\d:\d\d)/.exec(at || "");
  return m ? m[1] : "";
}

function finite(v) {
  return v !== null && v !== "" && v !== undefined && Number.isFinite(Number(v));
}

function linksHtml(refs) {
  const r = refs || {};
  const out = [];
  if (r.plan_id) {
    out.push(`<a href="#" data-act="plan" data-plan-id="${esc(r.plan_id)}">Plan</a>`);
  }
  if (finite(r.ra_deg) && finite(r.dec_deg)) {
    out.push(`<a href="#" data-act="map" data-ra="${esc(r.ra_deg)}" data-dec="${esc(r.dec_deg)}">Show on map</a>`);
  }
  if (r.aac_job) out.push(`<span class="pill dim">AAC job ${esc(r.aac_job)}</span>`);
  if (r.upload_id) {
    const href = `/ext/nina-ts-sync/import/uploads/${encodeURIComponent(r.upload_id)}`;
    out.push(`<a href="${esc(href)}" target="_blank" rel="noopener">Review</a>`);
  }
  return out.join("");
}

function masterBlockHtml(master) {
  const [mac, win, file] = masterRowStrings(master);
  const loc = (label, text, what) => (
    `<div class="loc"><span class="lbl">${label}</span><code>${esc(text)}</code>` +
    `<button type="button" class="cp" data-act="copy" data-text="${esc(text)}" aria-label="Copy ${what}">Copy</button></div>`);
  return `<div class="master">${loc("Mac", mac, "Mac folder path")}` +
    `${loc("Windows", win, "Windows folder path")}${loc("File", file, "file name")}</div>`;
}

export function renderRowHtml(row, { lastSeen = undefined } = {}) {
  const chip = chipOf(row.type);
  const classes = ["ev", `t-${chip}`];
  if (row.severity === "error") classes.push("sev-error");
  else if (row.severity === "warn") classes.push("sev-warn");
  if (lastSeen !== undefined && isNewRow(row, lastSeen)) classes.push("is-new");
  const meta = [];
  if (row.severity === "error") meta.push('<span class="pill bad">Error</span>');
  if (row.detail) meta.push(`<span class="detail">${esc(row.detail)}</span>`);
  const links = linksHtml(row.refs);
  if (links) meta.push(links);
  const master = row.refs && row.refs.master ? masterBlockHtml(row.refs.master) : "";
  return `<li class="${classes.join(" ")}" data-id="${esc(row.id)}">` +
    `<time>${esc(clockOf(row.at))}</time><span class="ico">${iconHtml(chip)}</span>` +
    `<div class="txt"><span class="what">${esc(row.title)}</span>` +
    (meta.length ? `<span class="meta">${meta.join("")}</span>` : "") +
    `${master}</div></li>`;
}

function changesKey(row) {
  const changes = (row.data && row.data.changes) || {};
  return JSON.stringify(Object.keys(changes).sort().map(k => [k, changes[k].from, changes[k].to]));
}

// Consecutive edits that made the same change across one project read as one
// line: "46 PNe Survey plans: min altitude 20 to 30".
export function collapseRows(rows) {
  const out = [];
  for (const row of rows) {
    const prev = out[out.length - 1];
    const same = prev && row.type === "plan_edited" && prev.type === "plan_edited"
      && row.refs && row.refs.project && prev.refs && prev.refs.project === row.refs.project
      && prev._key === changesKey(row);
    if (same) {
      prev.collapsed += 1;
      prev.title = `${prev.collapsed} ${row.refs.project} plans: ${prev._title}`;
      prev.ids.push(row.id);
      continue;
    }
    if (row.type === "plan_edited" && row.refs && row.refs.project) {
      out.push({ ...row, collapsed: 1, ids: [row.id], _key: changesKey(row), _title: row.title });
    } else {
      out.push(row);
    }
  }
  // A line that stayed single goes back to being the plain row.
  return out.map(r => {
    if (r.collapsed !== undefined && r.collapsed < 2) {
      const { collapsed, ids, _key, _title, ...plain } = r;
      return plain;
    }
    if (r.collapsed !== undefined) {
      const { _key, _title, ...rest } = r;
      return { ...rest, refs: { ...rest.refs, plan_id: undefined } };
    }
    return r;
  });
}

function whenOf(row) {
  const t = Date.parse(row.at);
  return Number.isFinite(t) ? t : 0;
}

// Fold freshly fetched nights into the ones on screen, by event id, so late
// events and updates land in nights already shown.
export function mergeNights(have, got) {
  const byNight = new Map(have.map(n => [n.night, { ...n, events: [...n.events] }]));
  for (const incoming of got) {
    const cur = byNight.get(incoming.night);
    if (!cur) {
      byNight.set(incoming.night, { ...incoming, events: [...incoming.events] });
      continue;
    }
    // A night that came back complete (nothing more to fetch) is the whole
    // truth, so an event deleted since the last fetch drops out. A night that
    // was cut at its first page keeps the extra events already loaded.
    const byId = new Map((incoming.more ? cur.events : []).map(e => [e.id, e]));
    for (const e of incoming.events) byId.set(e.id, e);
    const events = [...byId.values()].sort((a, b) => whenOf(b) - whenOf(a) || b.id - a.id);
    const extra = events.length - incoming.events.length;
    byNight.set(incoming.night, { night: incoming.night, events,
      more: Math.max(0, (incoming.more || 0) - Math.max(0, extra)) });
  }
  return [...byNight.values()].sort((a, b) => (a.night < b.night ? 1 : -1));
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "Tue 13 to Wed 14 Oct": a night is named by the evening it started.
export function nightHeading(night) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(night || "");
  if (!m) return night || "";
  const start = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  const end = new Date(start.getTime() + 24 * 3600 * 1000);
  const a = `${DAYS[start.getUTCDay()]} ${start.getUTCDate()}`;
  const b = `${DAYS[end.getUTCDay()]} ${end.getUTCDate()} ${MONTHS[end.getUTCMonth()]}`;
  return start.getUTCMonth() === end.getUTCMonth()
    ? `${a} to ${b}`
    : `${a} ${MONTHS[start.getUTCMonth()]} to ${b}`;
}

const fmtH = n => String(Math.round(Number(n) * 10) / 10);

function filterDot(name) {
  return `<span class="dot f-${esc(String(name).toLowerCase().replace(/[^a-z0-9]/g, ""))}"></span>`;
}

export function lastNightHtml(last) {
  if (!last || !last.targets || !last.targets.length) return "";
  const rows = last.targets.map(t => {
    const filters = t.filters.map(f => {
      const hasGoal = finite(f.goal) && Number(f.goal) > 0 && finite(f.hours);
      const pct = hasGoal ? Math.min(100, Math.round((Number(f.hours) / Number(f.goal)) * 100)) : null;
      const tail = hasGoal ? `<span class="bar" aria-label="${esc(fmtH(f.hours))} of ${esc(fmtH(f.goal))} h">` +
        `<i class="${f.done ? "done" : ""}" style="width:${pct}%"></i></span>${esc(fmtH(f.hours))} / ${esc(fmtH(f.goal))} h` : "";
      return `${filterDot(f.filter)}${esc(f.filter)} +${esc(fmtH(f.delta_h))} h${tail}`;
    }).join(" ");
    const links = linksHtml({ plan_id: t.plan_id });
    return `<div class="shot-row" role="listitem"><span class="name">${esc(t.target)}</span>` +
      `<span class="hrs">${esc(fmtH(t.hours))} h</span>` +
      `<div class="detail">${filters}${t.finished ? '<span class="pill good">Finished</span>' : ""}${links}</div></div>`;
  }).join("");
  return `<fieldset class="box"><legend>LAST NIGHT · ${esc(nightHeading(last.night).toUpperCase())}</legend>` +
    `<div class="night-line"><span>Lights <b>${esc(fmtH(last.total_h))} h</b></span>` +
    `<span>Targets <b>${esc(last.targets.length)}</b></span></div>` +
    `<div class="shot" role="list" aria-label="Hours shot per target">${rows}</div></fieldset>`;
}

export function needsYouHtml(open, total) {
  const items = (open || []).map(e => {
    const links = linksHtml(e.refs);
    return `<div class="need${e.severity === "error" ? " bad" : ""}" data-id="${esc(e.id)}">` +
      `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 15 14H1z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>` +
      `<path d="M8 6v4M8 11.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>` +
      `<div><p>${esc(e.title)}</p>${e.detail ? `<p class="detail">${esc(e.detail)}</p>` : ""}` +
      `<div class="acts">${links}</div></div>` +
      `<button type="button" class="x" data-act="dismiss" data-id="${esc(e.id)}" aria-label="Dismiss" title="Dismiss">&times;</button></div>`;
  }).join("");
  const more = total > (open || []).length ? `<div class="detail">and ${total - open.length} more</div>` : "";
  const body = items ? `<div class="needs">${items}${more}</div>`
    : '<div class="empty-note">Nothing needs you.</div>';
  return `<fieldset class="box"><legend>NEEDS YOU</legend>${body}</fieldset>`;
}
