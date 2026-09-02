// Turns a thrown error from init() into a short, user-facing message.
// Factored out of app.js so tests/frontend/ can exercise it directly via
// `node --test`. See app.js's init() for where this is used: without it,
// any exception during startup (bad response, network drop, a third-party
// script throwing) left the "Loading targets…" placeholder on screen
// forever with no visible explanation (reported in GitHub issue #46).

// Where templates/index.html loads Aladin Lite from. Named in the error so a
// user behind an ad blocker or firewall knows exactly which address to allow.
export const ALADIN_URL = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";

// How long init() waits for Aladin Lite's own `A.init` promise before giving
// up. The library fetches a WebAssembly bundle on first use, so allow for a
// slow link, but not forever: a hung promise is the silent-blank-page case.
export const ALADIN_INIT_TIMEOUT_MS = 30_000;

// Startup failures we can name precisely. Each becomes an Error with `code`
// set so describeInitError can pick the right message.
export function startupError(code, extra) {
  const err = new Error(code);
  err.code = code;
  if (extra) Object.assign(err, extra);
  return err;
}

// Resolve with `promise`, or reject with `err` if it takes longer than `ms`.
export function withTimeout(promise, ms, err) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(err), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// True when the browser can hand out a WebGL2 context. Aladin Lite v3 renders
// through WebGL2 and never initialises without it (hardware acceleration off,
// remote desktop, a VM without GPU passthrough, old Safari).
export function webgl2Available(doc = globalThis.document) {
  try {
    const canvas = doc.createElement("canvas");
    return !!canvas.getContext("webgl2");
  } catch {
    return false;
  }
}

export function describeInitError(err) {
  if (err && err.code === "aladin-missing") {
    return `The sky map library (Aladin Lite) did not load from ${ALADIN_URL}. ` +
      "An ad blocker, firewall, or offline network is the usual cause. " +
      "Allow that address and reload.";
  }
  if (err && err.code === "webgl2-missing") {
    return "This browser has no WebGL2, which the sky map (Aladin Lite) needs. " +
      "Turn on hardware acceleration in the browser settings, or try another browser. " +
      "Remote desktop sessions and virtual machines without GPU support often lack it.";
  }
  if (err && err.code === "aladin-timeout") {
    const secs = Math.round((err.ms || ALADIN_INIT_TIMEOUT_MS) / 1000);
    return `The sky map (Aladin Lite) did not start within ${secs} seconds. ` +
      "Check the browser console (press F12, then reload) for the underlying error, " +
      "and make sure hardware acceleration is on.";
  }
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
