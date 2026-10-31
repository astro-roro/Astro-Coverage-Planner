import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  CDS_RETRY_MS,
  BACKGROUND_FALLBACKS,
  IRSA_DSS2_COLOUR_URL,
  ESASKY_DSS2_COLOUR_URL,
  rememberedFallback,
  applyBackground,
  cdsTilesAnswer,
  chooseBackground,
  createDownMemory,
  isCdsSurvey,
  withMirror,
} from "../../static/cds-fallback.mjs";
import {
  SIMBAD_TAP,
  SIMBAD_TAP_MIRROR,
  VIZIER_ASU,
  VIZIER_ASU_MIRROR,
  VIZIER_CATALOGUES,
  searchSimbad,
  searchVizier,
} from "../../static/sky-search.mjs";

const clock = (t = 1_000_000) => {
  const c = { t, now: () => c.t };
  return c;
};
const memoryAt = c => createDownMemory({ now: c.now, storage: null });
const hang = (url, init) => new Promise((_, rej) =>
  init.signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" }))));

describe("withMirror", () => {
  const urls = { cds: "https://cds/x", mirror: "https://mirror/x" };
  const attemptWith = (log, bad) => async (url) => {
    log.push(url);
    if (bad.includes(url)) throw new Error("down");
    return url;
  };

  it("does not touch the mirror when CDS answers", async () => {
    const log = [], mem = memoryAt(clock());
    const out = await withMirror("simbad", urls, attemptWith(log, []), { memory: mem });
    assert.equal(out, urls.cds);
    assert.deepEqual(log, [urls.cds]);
    assert.equal(mem.isDown("simbad"), false);
  });

  it("retries the same request on the mirror when CDS fails", async () => {
    const log = [], mem = memoryAt(clock());
    const out = await withMirror("simbad", urls, attemptWith(log, [urls.cds]), { memory: mem });
    assert.equal(out, urls.mirror);
    assert.deepEqual(log, [urls.cds, urls.mirror]);
    assert.equal(mem.isDown("simbad"), true);
  });

  it("gives CDS only the patience limit, and the mirror the full limit", async () => {
    const limits = [];
    await withMirror("simbad", urls, async (url, limit) => {
      limits.push(limit);
      if (url === urls.cds) throw new Error("slow");
      return "ok";
    }, { memory: memoryAt(clock()), patienceMs: 6000, timeoutMs: 25000 });
    assert.deepEqual(limits, [6000, 25000]);
  });

  it("goes straight to the mirror for the rest of the session", async () => {
    const c = clock(), mem = memoryAt(c), log = [];
    await withMirror("simbad", urls, attemptWith(log, [urls.cds]), { memory: mem });
    log.length = 0;
    c.t += CDS_RETRY_MS - 1000;
    await withMirror("simbad", urls, attemptWith(log, [urls.cds]), { memory: mem });
    assert.deepEqual(log, [urls.mirror]);
  });

  it("tries CDS again after 30 minutes and forgets the verdict when it answers", async () => {
    const c = clock(), mem = memoryAt(c), log = [];
    await withMirror("simbad", urls, attemptWith(log, [urls.cds]), { memory: mem });
    log.length = 0;
    c.t += CDS_RETRY_MS + 1;
    const out = await withMirror("simbad", urls, attemptWith(log, []), { memory: mem });
    assert.equal(out, urls.cds);
    assert.deepEqual(log, [urls.cds]);
    assert.equal(mem.isDown("simbad"), false);
  });

  it("falls back to CDS when the mirror fails while CDS is remembered as down", async () => {
    const c = clock(), mem = memoryAt(c);
    mem.markDown("simbad");
    const log = [];
    const out = await withMirror("simbad", urls, attemptWith(log, [urls.mirror]), { memory: mem });
    assert.equal(out, urls.cds);
    assert.deepEqual(log, [urls.mirror, urls.cds]);
  });

  it("throws the last error when both fail", async () => {
    await assert.rejects(
      withMirror("simbad", urls, attemptWith([], [urls.cds, urls.mirror]), { memory: memoryAt(clock()) }),
      /down/,
    );
  });

  it("keeps each service separate", async () => {
    const mem = memoryAt(clock());
    mem.markDown("simbad");
    assert.equal(mem.isDown("vizier"), false);
  });

  it("remembers through sessionStorage and survives a storage that throws", () => {
    const store = new Map();
    const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) };
    const c = clock();
    createDownMemory({ now: c.now, storage }).markDown("hips");
    assert.equal(createDownMemory({ now: c.now, storage }).isDown("hips"), true);
    const broken = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };
    const mem = createDownMemory({ now: c.now, storage: broken });
    mem.markDown("hips");
    assert.equal(mem.isDown("hips"), true);
  });
});

describe("sky search uses the Harvard mirrors", () => {
  const cone = { ra: 83.8, dec: -5.4, radiusDeg: 1 };
  const simbadReply = JSON.stringify({ metadata: [{ name: "oid" }, { name: "main_id" }, { name: "otype" }, { name: "ra" }, { name: "dec" }, { name: "major" }, { name: "minor" }, { name: "angle" }], data: [] });

  it("SIMBAD: a CDS that hangs sends the same query to the mirror", async () => {
    const hosts = [];
    const fetchImpl = async (url, init) => {
      hosts.push(url);
      if (url === SIMBAD_TAP) return hang(url, init);
      return new Response(simbadReply);
    };
    const r = await searchSimbad(cone, { fetchImpl, memory: memoryAt(clock()), patienceMs: 20 });
    assert.equal(r.ok, true);
    assert.ok(hosts.includes(SIMBAD_TAP));
    assert.ok(hosts.includes(SIMBAD_TAP_MIRROR));
  });

  it("SIMBAD: a CDS that answers never reaches the mirror", async () => {
    const hosts = [];
    const fetchImpl = async url => { hosts.push(url); return new Response(simbadReply); };
    const r = await searchSimbad(cone, { fetchImpl, memory: memoryAt(clock()) });
    assert.equal(r.ok, true);
    assert.ok(hosts.every(h => h === SIMBAD_TAP));
  });

  it("SIMBAD: later searches skip CDS once it has failed", async () => {
    const mem = memoryAt(clock());
    const first = async url => (url === SIMBAD_TAP ? new Response("no", { status: 503 }) : new Response(simbadReply));
    await searchSimbad(cone, { fetchImpl: first, memory: mem });
    const hosts = [];
    await searchSimbad(cone, { fetchImpl: async url => { hosts.push(url); return new Response(simbadReply); }, memory: mem });
    assert.ok(hosts.length > 0 && hosts.every(h => h === SIMBAD_TAP_MIRROR));
  });

  it("SIMBAD: reports the error only when both fail", async () => {
    const fetchImpl = async () => new Response("busy", { status: 503 });
    const r = await searchSimbad(cone, { fetchImpl, memory: memoryAt(clock()) });
    assert.equal(r.ok, false);
    assert.match(r.error, /503/);
  });

  it("VizieR: a CDS timeout uses the Harvard cone search with the same query string", async () => {
    const cat = VIZIER_CATALOGUES[0];
    const urls = [];
    const fetchImpl = async (url, init) => {
      urls.push(url);
      if (url.startsWith(VIZIER_ASU)) return hang(url, init);
      return new Response("");
    };
    const r = await searchVizier(cat, cone, { fetchImpl, memory: memoryAt(clock()), patienceMs: 20 });
    assert.equal(r.ok, true);
    assert.equal(urls.length, 2);
    assert.ok(urls[1].startsWith(VIZIER_ASU_MIRROR + "?"));
    assert.equal(urls[1].split("?")[1], urls[0].split("?")[1]);
  });

  it("VizieR: the mirror gets the short limit too, then the old error shows", async () => {
    const limits = [];
    const fetchImpl = async (url, init) => {
      limits.push(url.startsWith(VIZIER_ASU_MIRROR) ? "mirror" : "cds");
      return hang(url, init);
    };
    const t0 = Date.now();
    const r = await searchVizier(VIZIER_CATALOGUES[0], cone, { fetchImpl, memory: memoryAt(clock()), patienceMs: 20, timeoutMs: 5000 });
    assert.ok(Date.now() - t0 < 1000, "the mirror was not given the full 5 s");
    assert.deepEqual(limits, ["cds", "mirror"]);
    assert.equal(r.ok, false);
    assert.match(r.error, /timed out/);
  });

  it("VizieR: a CDS success does not reach the mirror", async () => {
    const urls = [];
    const fetchImpl = async url => { urls.push(url); return new Response(""); };
    await searchVizier(VIZIER_CATALOGUES[0], cone, { fetchImpl, memory: memoryAt(clock()) });
    assert.equal(urls.length, 1);
    assert.ok(urls[0].startsWith(VIZIER_ASU));
  });
});

describe("map background", () => {
  const layerFor = id => ({ survey: id });
  const makeFallbackLayer = f => ({ fallback: f.key, url: f.url });
  const CDS = "CDS/P/DSS2/color";
  // A probe that answers only for the addresses listed.
  const probeOnly = (...up) => async url => up.some(u => url.startsWith(u));
  const cdsUp = "https://alasky.cds.unistra.fr";

  it("recognises CDS survey ids", () => {
    assert.equal(isCdsSurvey(CDS), true);
    assert.equal(isCdsSurvey(ESASKY_DSS2_COLOUR_URL), false);
    assert.equal(isCdsSurvey(undefined), false);
  });

  it("switches the base layer to IRSA first when CDS fails", async () => {
    const layers = [], mem = memoryAt(clock());
    const choice = await applyBackground(CDS, {
      setLayer: l => layers.push(l), layerFor, makeFallbackLayer, memory: mem, probe: probeOnly(IRSA_DSS2_COLOUR_URL),
    });
    assert.equal(choice, "irsa");
    assert.deepEqual(layers, [layerFor(CDS), { fallback: "irsa", url: IRSA_DSS2_COLOUR_URL }]);
    assert.equal(mem.isDown("hips"), true);
  });

  it("uses ESASky second, when IRSA does not answer either", async () => {
    const layers = [], mem = memoryAt(clock());
    const choice = await applyBackground(CDS, {
      setLayer: l => layers.push(l), layerFor, makeFallbackLayer, memory: mem, probe: probeOnly(),
    });
    assert.equal(choice, "esasky");
    assert.deepEqual(layers.at(-1), { fallback: "esasky", url: ESASKY_DSS2_COLOUR_URL });
    assert.equal(mem.isDown("hips-irsa"), true);
  });

  it("leaves the CDS layer alone when the probe answers", async () => {
    const layers = [];
    const choice = await applyBackground(CDS, {
      setLayer: l => layers.push(l), layerFor, makeFallbackLayer, memory: memoryAt(clock()), probe: probeOnly(cdsUp),
    });
    assert.equal(choice, "cds");
    assert.deepEqual(layers, [layerFor(CDS)]);
  });

  it("goes straight to the remembered fallback, without probing, while CDS is down", async () => {
    const mem = memoryAt(clock());
    mem.markDown("hips");
    const layers = [];
    let probed = 0;
    await applyBackground("CDS/P/Mellinger/color", {
      setLayer: l => layers.push(l), layerFor, makeFallbackLayer, memory: mem, probe: async () => { probed++; return true; },
    });
    assert.equal(probed, 0);
    assert.deepEqual(layers, [{ fallback: "irsa", url: IRSA_DSS2_COLOUR_URL }]);
    mem.markDown("hips-irsa");
    assert.equal(rememberedFallback(mem).key, "esasky");
  });

  it("names no fallback for the caption while CDS is up", () => {
    assert.equal(rememberedFallback(memoryAt(clock())), null);
    assert.deepEqual(BACKGROUND_FALLBACKS.map(f => f.credit), ["IRSA", "ESASky"]);
  });

  it("probes CDS again after 30 minutes", async () => {
    const c = clock(), mem = memoryAt(c);
    mem.markDown("hips");
    c.t += CDS_RETRY_MS + 1;
    assert.equal(await chooseBackground(CDS, { memory: mem, probe: probeOnly(cdsUp) }), "cds");
    assert.equal(mem.isDown("hips"), false);
  });

  it("does not swap if the user picked another survey while the probe ran", async () => {
    const layers = [];
    await applyBackground(CDS, {
      setLayer: l => layers.push(l), layerFor, makeFallbackLayer, memory: memoryAt(clock()),
      probe: probeOnly(IRSA_DSS2_COLOUR_URL), stillWanted: () => false,
    });
    assert.deepEqual(layers, [layerFor(CDS)]);
  });

  it("never probes for a survey that does not come from CDS", async () => {
    let probed = 0;
    const choice = await chooseBackground("https://example.org/hips", { memory: memoryAt(clock()), probe: async () => { probed++; return false; } });
    assert.equal(choice, "cds");
    assert.equal(probed, 0);
  });

  it("treats a slow tile host as down", async () => {
    assert.equal(await cdsTilesAnswer({ fetchImpl: hang, patienceMs: 20 }), false);
    assert.equal(await cdsTilesAnswer({ fetchImpl: async () => new Response("", { status: 500 }) }), false);
    assert.equal(await cdsTilesAnswer({ fetchImpl: async () => new Response("ok") }), true);
  });
});
