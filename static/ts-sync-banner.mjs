// Formats the nina-ts-sync extension's pending-uploads response into the
// lines shown in the top-of-page banner. Factored out of app.js so
// tests/frontend/ can exercise the formatting without a DOM. See app.js's
// loadTsSyncBanner() for where this is used.
//
// The extension is optional: ACP runs with or without it, and the route
// this feeds off is a 404 when it isn't loaded. This module only shapes
// data it's already been handed; the fetch and the "say nothing on
// failure" behaviour live in app.js.

// At most this many uploads get their own line; the rest are folded into
// "and N more" so a busy install can't push the banner into a wall of text.
const MAX_LINES = 3;

// One upload -> its banner line text (no HTML, no link).
export function describeUpload(upload) {
  const machine = (upload && upload.machine) || "A NINA machine";
  if (upload && upload.state === "checking") {
    return `${machine} sent TS changes. ACP is still reading them.`;
  }
  const counts = (upload && upload.counts) || {};
  const parts = [];
  if (counts.new) parts.push(`${counts.new} new`);
  if (counts.updated) parts.push(`${counts.updated} updated`);
  if (counts.conflicts) parts.push(`${counts.conflicts} need a choice`);
  const detail = parts.length ? `: ${parts.join(", ")}.` : ".";
  return `${machine} sent TS changes to review${detail}`;
}

// uploads[] (already filtered to checking/ready by the server, newest
// first) -> { lines: [{text, url}], more: <count beyond MAX_LINES> }.
// `url` is the review link for a ready upload, null for one still
// checking (there's nothing to review yet).
export function summariseUploads(uploads) {
  const list = Array.isArray(uploads) ? uploads : [];
  const shown = list.slice(0, MAX_LINES);
  const lines = shown.map(u => ({
    text: describeUpload(u),
    url: u && u.state === "ready" ? (u.review_url || null) : null,
  }));
  return { lines, more: Math.max(0, list.length - shown.length) };
}
