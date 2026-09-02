// Turns a thrown error from init() into a short, user-facing message.
// Factored out of app.js so tests/frontend/ can exercise it directly via
// `node --test`. See app.js's init() for where this is used: without it,
// any exception during startup (bad response, network drop, a third-party
// script throwing) left the "Loading targets…" placeholder on screen
// forever with no visible explanation (reported in GitHub issue #46).
export function describeInitError(err) {
  if (err && typeof err.status === "number") {
    return `Server error (${err.status}) while loading targets. Reload to try again.`;
  }
  if (err instanceof TypeError) {
    // fetch() rejects with a TypeError on network failure (offline, DNS,
    // CORS, connection refused) rather than resolving with a bad status.
    return "Could not reach the server. Check your connection and reload.";
  }
  const detail = (err && err.message) || String(err);
  return `Something went wrong while loading targets (${detail}). Reload to try again.`;
}
