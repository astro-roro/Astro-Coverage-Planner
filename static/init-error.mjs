// Turns a thrown error from init() into a short, user-facing message.
// Factored out of app.js so tests/frontend/ can exercise it directly via
// `node --test`. See app.js's init() for where this is used: without it,
// any exception during startup (bad response, network drop, a third-party
// script throwing) left the "Loading targets…" placeholder on screen
// forever with no visible explanation (reported in GitHub issue #46).

// Where app.js loads Aladin Lite from, in order. CDS publishes the library,
// so it comes first; jsDelivr serves the same 3.8.2 build from npm and keeps
// the map working when every CDS host is down (as on 2026-10-29). Both are
// named in the error so a user behind an ad blocker or firewall knows
// exactly which addresses to allow.
export const ALADIN_URL = "https://aladin.cds.unistra.fr/AladinLite/api/v3/3.8.2/aladin.js";
export const ALADIN_FALLBACK_URL = "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js";

// The two copies are packaged differently. CDS serves a classic script that
// sets the global `A`. npm's dist/aladin.js is an ES module: it ends in
// `export { P as default }` and sets no global, and as a classic <script> it
// fails to parse on that export. So the fallback is imported, and its
// default export becomes `A`.
export const ALADIN_SOURCES = [
  { url: ALADIN_URL, kind: "script" },
  { url: ALADIN_FALLBACK_URL, kind: "module" },
];

// How long to wait on one script address before trying the next. When CDS
// is down its hosts hang rather than refuse, and the browser's own connection
// timeout runs past a minute. The last address gets no limit of ours, since
// there is nothing left to fall back to.
export const ALADIN_SCRIPT_TIMEOUT_MS = 15_000;

// True once Aladin Lite's script has run and defined its global `A`.
export function aladinLoaded(scope = globalThis) {
  return !!(scope && scope.A && scope.A.init);
}

// Load each source in turn until `isLoaded()` turns true. A "script" source
// is added as a classic <script> and must set the global itself. A "module"
// source is imported, and its default export is stored as `A` on `scope`.
// Resolves with the URL that worked, or null when none did. Never rejects:
// the caller decides what a missing library means.
//
// A script given up on for slowness can still arrive later and run. Removing
// its element does not reliably stop that, so a late CDS copy may redefine
// `A` after the fallback copy. Both are the same version, which keeps that
// rare case harmless in practice.
export async function loadFirstScript(sources, {
  doc = globalThis.document,
  scope = globalThis,
  isLoaded = () => aladinLoaded(scope),
  importModule = url => import(url),
  timeoutMs = ALADIN_SCRIPT_TIMEOUT_MS,
} = {}) {
  if (isLoaded()) return null;
  for (let i = 0; i < sources.length; i++) {
    const { url, kind } = sources[i];
    const limit = i === sources.length - 1 ? 0 : timeoutMs;
    try {
      if (kind === "module") {
        const mod = await withLimit(importModule(url), limit);
        if (mod?.default) scope.A = mod.default;
      } else {
        await addScript(doc, url, limit);
      }
    } catch (err) {
      console.warn(`Aladin Lite did not load from ${url}:`, err?.message || err);
      continue;
    }
    if (isLoaded()) return url;
    console.warn(`Aladin Lite from ${url} loaded but did not provide A`);
  }
  return null;
}

function noResponse(ms) {
  return new Error(`no response within ${Math.round(ms / 1000)} seconds`);
}

function withLimit(promise, ms) {
  return ms > 0 ? withTimeout(promise, ms, noResponse(ms)) : promise;
}

function addScript(doc, src, timeoutMs) {
  return new Promise((resolve, reject) => {
    const el = doc.createElement("script");
    let timer;
    const done = fn => arg => {
      clearTimeout(timer);
      el.onload = el.onerror = null;
      fn(arg);
    };
    el.onload = done(resolve);
    el.onerror = done(() => {
      el.remove?.();
      reject(new Error("network error"));
    });
    if (timeoutMs > 0) {
      timer = setTimeout(done(() => {
        el.remove?.();
        reject(noResponse(timeoutMs));
      }), timeoutMs);
    }
    el.src = src;
    doc.head.appendChild(el);
  });
}

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

const MAP_ERROR_CODES = new Set(["aladin-missing", "webgl2-missing", "aladin-timeout"]);

// The message shown in the map area when the sky map cannot start. The
// panels still load in that case (see static/no-map.mjs), so say so.
export function describeMapError(err) {
  const why = err && MAP_ERROR_CODES.has(err.code)
    ? describeInitError(err)
    : `The sky map (Aladin Lite) failed to start (${(err && err.message) || String(err)}). Reload to try again.`;
  return `${why} Your targets and plans still work in the side panel.`;
}

export function describeInitError(err) {
  if (err && err.code === "aladin-missing") {
    return `The sky map library (Aladin Lite) did not load from ${ALADIN_URL} ` +
      `or from the backup copy at ${ALADIN_FALLBACK_URL}. ` +
      "An ad blocker, firewall, or offline network is the usual cause. " +
      "Allow those addresses and reload.";
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
