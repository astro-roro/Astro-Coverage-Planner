// What ACP uses in place of Aladin Lite when the sky map cannot start (the
// library did not load, no WebGL2, or A.init failed or timed out). Rohan
// still needs the Targets and Plans panels then, to read and edit targets
// and plans while the map is down.
//
// The panels call into the map in dozens of places: redrawing footprints,
// hover highlights, panning to a selection, plan markers. Rather than guard
// each one, app.js hands them stand-ins whose every method does nothing and
// returns undefined. Call sites that read a value back (getRaDec, getFoV,
// world2pix) already treat undefined as "map not ready".

// Never a thenable: `await` on a stand-in, or returning one from an async
// function, must not hang waiting on a `then` that never calls back.
const NOT_METHODS = new Set(["then"]);

const noop = () => undefined;

/**
 * An object whose every method is a no-op returning undefined. Symbol keys
 * (iterators, toPrimitive) and `then` read as undefined, so the stand-in
 * never pretends to be iterable, a number, or a promise.
 */
export function noMapStub() {
  return new Proxy({}, {
    get(_target, key) {
      if (typeof key !== "string" || NOT_METHODS.has(key)) return undefined;
      return noop;
    },
    set: () => true,
  });
}

/**
 * A stand-in for the global `A` from Aladin Lite. Its factories (polygon,
 * source, catalog, graphicOverlay, MOCFromURL, imageHiPS) return stand-ins,
 * so code that builds a shape and adds it to an overlay keeps working.
 */
export function noMapLibrary() {
  const stub = noMapStub();
  return new Proxy({}, {
    get(_target, key) {
      if (typeof key !== "string" || NOT_METHODS.has(key)) return undefined;
      // `init` is a promise in the real library; there is nothing to wait for.
      if (key === "init") return undefined;
      return () => stub;
    },
  });
}

// Toolbar controls that only act on the map. The Site picker and the
// Time-aware toggle feed the panels too, so they stay on.
export const MAP_CONTROL_IDS = ["projSel", "frameSel", "skySel", "skyMore", "skySearchBtn"];

export const MAP_CONTROL_OFF_TITLE = "The sky map did not start, so this control is off. Reload to try again.";

/** Turn off the map-only toolbar controls and say why on hover. */
export function disableMapControls(doc = globalThis.document) {
  for (const id of MAP_CONTROL_IDS) {
    const el = doc?.getElementById?.(id);
    if (!el) continue;
    el.disabled = true;
    el.title = MAP_CONTROL_OFF_TITLE;
  }
}
