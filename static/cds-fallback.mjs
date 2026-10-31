// Keeps the browser-side CDS calls working when CDS (Strasbourg) is down, as
// it was from 2026-10-29. Two things use it: the "What's here?" sky search
// (SIMBAD and VizieR have Harvard mirrors) and the map background (ESASky
// serves DSS2 colour). Both switch only when CDS does not answer within a few
// seconds, remember which side answered for the rest of the browser session,
// and try CDS again after 30 minutes. Nothing here touches the server.

// How long CDS gets to answer before the mirror is tried.
export const CDS_PATIENCE_MS = 6000;

// How long a "CDS is down" verdict lasts before CDS is tried again.
export const CDS_RETRY_MS = 30 * 60 * 1000;

const STORAGE_PREFIX = "acp.cdsDown.";

// Remembers, per service, when CDS last failed to answer. Backed by
// sessionStorage so it lasts for the browser session and no longer, with a
// plain in-memory fallback for browsers that refuse storage.
export function createDownMemory({
  now = () => Date.now(),
  storage = defaultStorage(),
  retryMs = CDS_RETRY_MS,
} = {}) {
  const mem = new Map();
  const read = key => {
    try {
      const raw = storage?.getItem(STORAGE_PREFIX + key);
      if (raw !== null && raw !== undefined) return Number(raw);
    } catch { /* storage refused, use memory */ }
    return mem.has(key) ? mem.get(key) : null;
  };
  return {
    // True while CDS should be skipped for `key`.
    isDown(key) {
      const since = read(key);
      return Number.isFinite(since) && since !== null && now() - since < retryMs;
    },
    markDown(key) {
      mem.set(key, now());
      try { storage?.setItem(STORAGE_PREFIX + key, String(now())); } catch { /* memory copy is enough */ }
    },
    markUp(key) {
      mem.delete(key);
      try { storage?.removeItem(STORAGE_PREFIX + key); } catch { /* ignore */ }
    },
  };
}

function defaultStorage() {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }
}

// The memory the page shares. Tests build their own.
export const downMemory = createDownMemory();

// Try each address in turn and return the first answer. `urls` is
// { cds, mirror }. While CDS is remembered as down the mirror goes first and
// CDS is the last resort. CDS gets `patienceMs` before the mirror is tried;
// the mirror gets `mirrorTimeoutMs` (the caller's full limit by default). `attempt(url, timeoutMs)` must
// resolve with the answer or throw. Throws the last error when both fail.
export async function withMirror(key, urls, attempt, {
  memory = downMemory,
  patienceMs = CDS_PATIENCE_MS,
  timeoutMs = 25000,
  mirrorTimeoutMs = timeoutMs,
} = {}) {
  const cdsFirst = !memory.isDown(key);
  const order = cdsFirst ? ["cds", "mirror"] : ["mirror", "cds"];
  let lastErr;
  for (const side of order) {
    const limit = side === "cds" ? Math.min(patienceMs, timeoutMs) : mirrorTimeoutMs;
    try {
      const out = await attempt(urls[side], limit);
      if (side === "cds") memory.markUp(key); else if (cdsFirst) memory.markDown(key);
      return out;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

// ---- map background -------------------------------------------------------

// Where DSS2 colour comes from when CDS cannot be reached, in the order tried.
// IRSA mirrors the CDS HiPS tree (and answered faster in a 2026-10-31 check
// from Brutix); ESASky serves its own copy and is the last resort.
export const IRSA_DSS2_COLOUR_URL = "https://irsa.ipac.caltech.edu/data/hips/CDS/DSS2/color/";
export const ESASKY_DSS2_COLOUR_URL = "https://skies.esac.esa.int/DSSColor/";
export const BACKGROUND_FALLBACKS = [
  { key: "irsa", memoryKey: "hips-irsa", url: IRSA_DSS2_COLOUR_URL, name: "DSS2 colour (IRSA)", credit: "IRSA" },
  { key: "esasky", memoryKey: "hips-esasky", url: ESASKY_DSS2_COLOUR_URL, name: "DSS2 colour (ESASky)", credit: "ESASky" },
];

// A small file on the CDS tile host. If it comes back, the tile host is up.
export const CDS_HIPS_PROBE_URL = "https://alasky.cds.unistra.fr/DSS/DSSColor/properties";

// True when `url` answered within `patienceMs`.
export async function cdsTilesAnswer({ fetchImpl = fetch, patienceMs = CDS_PATIENCE_MS, url = CDS_HIPS_PROBE_URL } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), patienceMs);
  try {
    const r = await fetchImpl(url, { signal: ctl.signal });
    return !!r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// Only surveys that come from CDS need the fallback.
export const isCdsSurvey = id => typeof id === "string" && id.startsWith("CDS/");

// The fallback currently in use for CDS surveys, from memory alone: null when
// CDS is not remembered as down. Used for the caption, with no request.
export function rememberedFallback(memory = downMemory) {
  if (!memory.isDown("hips")) return null;
  return BACKGROUND_FALLBACKS.find(f => !memory.isDown(f.memoryKey))
    ?? BACKGROUND_FALLBACKS[BACKGROUND_FALLBACKS.length - 1];
}

// Decide which background the map should show for survey `id`. Resolves
// "cds" to leave the survey alone, or the key of the fallback to use. While
// CDS is remembered as down there is no request for it, and fallbacks
// remembered as down are skipped. The last fallback is never probed: with
// nothing left behind it, trying it costs nothing.
// `probe(url)` resolves true when that address answers in time.
export async function chooseBackground(id, { memory = downMemory, probe = url => cdsTilesAnswer({ url: probeUrlFor(url) }) } = {}) {
  if (!isCdsSurvey(id)) return "cds";
  if (!memory.isDown("hips")) {
    if (await probe(CDS_HIPS_PROBE_URL)) {
      memory.markUp("hips");
      return "cds";
    }
    memory.markDown("hips");
  }
  for (const f of BACKGROUND_FALLBACKS.slice(0, -1)) {
    if (memory.isDown(f.memoryKey)) continue;
    if (await probe(f.url)) {
      memory.markUp(f.memoryKey);
      return f.key;
    }
    memory.markDown(f.memoryKey);
  }
  return BACKGROUND_FALLBACKS[BACKGROUND_FALLBACKS.length - 1].key;
}

const probeUrlFor = url => (url.endsWith("/properties") ? url : url + "properties");

// Put the right background on the map for survey `id`. `setLayer(layer)`
// applies a base layer; `layerFor(id)` builds the normal layer;
// `makeFallbackLayer(fallback)` builds one from BACKGROUND_FALLBACKS.
// Resolves with the choice made. The normal layer is applied first so the map
// never waits on a probe, then replaced if CDS turns out to be down and
// `stillWanted()` is still true (the user may have picked another survey
// while the probes ran). While CDS is remembered as down the fallback goes on
// at once, with no probe for CDS.
export async function applyBackground(id, { setLayer, layerFor, makeFallbackLayer, memory = downMemory, probe, stillWanted = () => true }) {
  const find = key => BACKGROUND_FALLBACKS.find(f => f.key === key);
  if (isCdsSurvey(id) && memory.isDown("hips")) {
    const f = rememberedFallback(memory);
    setLayer(makeFallbackLayer(f));
    return f.key;
  }
  setLayer(layerFor(id));
  const choice = await chooseBackground(id, probe ? { memory, probe } : { memory });
  if (choice !== "cds" && stillWanted()) setLayer(makeFallbackLayer(find(choice)));
  return choice;
}
