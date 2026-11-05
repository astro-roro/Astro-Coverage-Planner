// The upload progress card at the top of the Activity tab: transfers another
// tool reports to POST /api/uploads/<id>/progress (docs/api.md, "Upload
// progress"). Pure functions, so `node --test` can check them; activity-ui.mjs
// fetches and places the result.

const pad = n => String(n).padStart(2, "0");

function hhmm(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function sizeText(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return i === 0 ? `${Math.round(n)} B` : `${n.toFixed(1)} ${units[i]}`;
}

// Fraction done from 0 to 1, by bytes when both figures are known, else by
// files, else null.
export function fractionDone(t) {
  const by = (d, total) => (typeof d === "number" && typeof total === "number" && total > 0
    ? Math.min(1, Math.max(0, d / total)) : null);
  const bytes = by(t.bytes_done, t.bytes_total);
  return bytes !== null ? bytes : by(t.files_done, t.files_total);
}

// Only running and paused transfers get a card. Empty string when none.
export function uploadCardHtml(transfers, esc) {
  const active = (Array.isArray(transfers) ? transfers : [])
    .filter(t => t && (t.state === "running" || t.state === "paused"));
  if (!active.length) return "";
  const rows = active.map(t => {
    const frac = fractionDone(t);
    const pct = frac === null ? null : Math.round(frac * 100);
    const bits = [];
    if (typeof t.files_total === "number") bits.push(`${t.files_done ?? 0} of ${t.files_total} files`);
    if (typeof t.bytes_total === "number") bits.push(`${sizeText(t.bytes_done ?? 0)} of ${sizeText(t.bytes_total)}`);
    const times = [];
    if (t.started_at) times.push(`started ${hhmm(t.started_at)}`);
    if (t.eta && t.state === "running") times.push(`done about ${hhmm(t.eta)}`);
    if (t.cutoff) times.push(`must finish by ${hhmm(t.cutoff)}`);
    const state = t.state === "paused" ? "Paused" : "Running";
    return `<li class="up-row" data-state="${esc(t.state)}">` +
      `<div class="up-head"><span class="up-name">${esc(t.label || t.id)}</span>` +
      `<span class="up-state">${esc(state)}${pct === null ? "" : ` · ${pct}%`}</span></div>` +
      (pct === null ? "" : `<div class="up-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${pct}%"></span></div>`) +
      (bits.length ? `<div class="up-figs">${esc(bits.join(" · "))}</div>` : "") +
      (times.length ? `<div class="up-times">${esc(times.join(" · "))}</div>` : "") +
      `</li>`;
  }).join("");
  return `<section class="upload-card" aria-label="Uploads in progress"><h3>Uploads</h3><ul>${rows}</ul></section>`;
}
