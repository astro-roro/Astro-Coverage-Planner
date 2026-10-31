import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createDownMemory, applyBackground, BUNDLED_OFFLINE_SKY, rememberedFallback } from "../../static/cds-fallback.mjs";
import { createSkyBanner, MESSAGES } from "../../static/sky-banner.mjs";
import { createSkyBackground } from "../../static/sky-background.mjs";

const CDS = "CDS/P/DSS2/color";
const CDS_PROBE = "alasky.cds.unistra.fr";
const IRSA = "irsa.ipac.caltech.edu";
const ESASKY = "skies.esac.esa.int";

function fakeEls() {
  const handlers = [];
  return {
    root: { hidden: true },
    text: { textContent: "" },
    retry: { disabled: false, addEventListener: (ev, fn) => handlers.push(fn), press: () => handlers.forEach(h => h()) },
  };
}

// A probe the test controls: each address answers up, down, or stays pending
// until `settle` is called. Calls are logged.
function controlledProbe() {
  const state = new Map();     // fragment -> "up" | "down" | "pending"
  const waiting = [];
  const calls = [];
  const probe = url => {
    calls.push(url);
    for (const [frag, mode] of state) {
      if (!url.includes(frag)) continue;
      if (mode === "up") return Promise.resolve(true);
      if (mode === "down") return Promise.resolve(false);
      return new Promise(res => waiting.push({ frag, res }));
    }
    return Promise.resolve(false);
  };
  return {
    probe, calls,
    set: (frag, mode) => state.set(frag, mode),
    settle(frag, value) { waiting.filter(w => w.frag === frag).forEach(w => w.res(value)); },
  };
}

function setup({ all = "down" } = {}) {
  const els = fakeEls();
  const banner = createSkyBanner(els, () => bg.retry());
  const layers = [];
  const timers = [];
  const p = controlledProbe();
  for (const f of [CDS_PROBE, IRSA, ESASKY]) p.set(f, all);
  const memory = createDownMemory({ now: () => 1_000_000, storage: null });
  const bg = createSkyBackground({
    setLayer: l => layers.push(l),
    layerFor: id => ({ survey: id }),
    makeFallbackLayer: f => ({ fallback: f.key }),
    banner, memory, probe: p.probe,
    schedule: fn => { timers.push(fn); return timers.length; },
    unschedule: h => { timers[h - 1] = null; },
  });
  return { els, banner, bg, layers, p, timers, memory };
}

const tick = () => new Promise(r => setImmediate(r));

describe("sky banner", () => {
  it("never shows while a source is still pending", async () => {
    const t = setup();
    t.p.set(CDS_PROBE, "pending");
    const run = t.bg.show(CDS);
    await tick();
    assert.equal(t.els.root.hidden, true);
    t.p.settle(CDS_PROBE, false);
    // CDS has failed but the two mirrors are being asked: IRSA answers, ESASky is slow.
    t.p.set(IRSA, "pending");
    t.p.set(ESASKY, "pending");
    await tick();
    assert.equal(t.els.root.hidden, true, "hidden while the mirrors are being asked");
    t.p.settle(IRSA, false);
    await tick();
    assert.equal(t.els.root.hidden, true, "hidden while ESASky has not answered");
    t.p.settle(ESASKY, false);
    assert.equal(await run, "offline");
    assert.equal(t.els.root.hidden, false);
  });

  it("shows the plain CDS message after every source fails, and leaves the map's background alone", async () => {
    const t = setup();
    assert.equal(await t.bg.show(CDS), "offline");
    assert.equal(t.banner.kind, "cds");
    assert.equal(t.els.text.textContent, MESSAGES.cds);
    assert.equal(t.els.text.textContent, "The sky survey servers (CDS) are not responding, so the map has no sky background right now. Your data and plans are fine. Try again later.");
    assert.deepEqual(t.layers, [{ survey: CDS }], "no fallback layer was put on the map");
  });

  it("stays hidden when CDS answers, and when only a mirror answers", async () => {
    const a = setup();
    a.p.set(CDS_PROBE, "up");
    await a.bg.show(CDS);
    assert.equal(a.els.root.hidden, true);
    const b = setup();
    b.p.set(IRSA, "up");
    assert.equal(await b.bg.show(CDS), "irsa");
    assert.equal(b.els.root.hidden, true);
  });

  it("clears by itself as soon as a remote source answers again", async () => {
    const t = setup();
    await t.bg.show(CDS);
    assert.equal(t.els.root.hidden, false);
    assert.equal(typeof t.timers[0], "function", "a re-check is scheduled while the banner is up");
    await t.timers[0]();
    assert.equal(t.els.root.hidden, false, "still down, still shown");
    t.p.set(IRSA, "up");
    await t.timers[0]();
    assert.equal(t.els.root.hidden, true);
    assert.deepEqual(t.layers.at(-1), { fallback: "irsa" });
    assert.equal(t.timers[0], null, "the re-check stops once the banner is gone");
  });

  it("does not flicker the map or the banner during a quiet re-check", async () => {
    const t = setup();
    await t.bg.show(CDS);
    const before = t.layers.length;
    await t.timers[0]();
    assert.equal(t.layers.length, before);
    assert.equal(t.els.root.hidden, false);
  });

  it("Retry reruns the whole chain and clears the banner on success", async () => {
    const t = setup();
    await t.bg.show(CDS);
    t.p.calls.length = 0;
    t.p.set(CDS_PROBE, "up");
    t.els.retry.press();
    await tick(); await tick();
    assert.ok(t.p.calls.some(u => u.includes(CDS_PROBE)), "CDS was asked again");
    assert.equal(t.els.root.hidden, true);
    assert.equal(t.els.retry.disabled, false);
  });

  it("Retry asks sources the session had written off", async () => {
    const t = setup();
    await t.bg.show(CDS);
    t.p.calls.length = 0;
    await t.bg.retry();
    assert.ok(t.p.calls.some(u => u.includes(CDS_PROBE)));
    assert.ok(t.p.calls.some(u => u.includes(IRSA)));
    assert.ok(t.p.calls.some(u => u.includes(ESASKY)));
    assert.equal(t.els.root.hidden, false, "all still down, banner stays");
  });

  it("gives a user-picked survey that fails alone the second message and keeps the pick", async () => {
    const t = setup({ all: "up" });
    t.p.set("hips.example.org", "down");
    t.layers.length = 0;
    await t.bg.show(CDS);
    assert.equal(t.els.root.hidden, true);
    assert.equal(await t.bg.checkPicked("https://hips.example.org/survey"), false);
    assert.equal(t.banner.kind, "survey");
    assert.equal(t.els.text.textContent, MESSAGES.survey);
    assert.match(MESSAGES.survey, /^That survey's server is not responding/);
    assert.ok(!t.layers.some(l => l.fallback), "the pick was not swapped for a fallback");
  });

  it("does not show the picked-survey message while that server is still loading, and clears when it answers", async () => {
    const t = setup({ all: "up" });
    t.p.set("hips.example.org", "pending");
    const run = t.bg.checkPicked("https://hips.example.org/survey");
    await tick();
    assert.equal(t.els.root.hidden, true);
    t.p.settle("hips.example.org", true);
    await run;
    assert.equal(t.els.root.hidden, true);
  });

  it("a newer choice cancels an older check, so a stale answer cannot raise the banner", async () => {
    const t = setup();
    t.p.set(CDS_PROBE, "pending");
    const first = t.bg.show(CDS);
    await tick();
    t.p.set(CDS_PROBE, "up");
    await t.bg.show("CDS/P/2MASS/color");
    t.p.settle(CDS_PROBE, false);
    t.p.set(IRSA, "down");
    t.p.set(ESASKY, "down");
    assert.equal(await first, undefined);
    assert.equal(t.els.root.hidden, true);
  });

  it("ships no bundled sky, so the caption names no fallback when all sources are down", () => {
    assert.equal(BUNDLED_OFFLINE_SKY, null);
    const mem = createDownMemory({ now: () => 1, storage: null });
    mem.markDown("hips"); mem.markDown("hips-irsa"); mem.markDown("hips-esasky");
    assert.equal(rememberedFallback(mem).key, "offline");
    assert.equal(rememberedFallback(mem).credit, undefined);
  });

  it("the offline hook puts a bundled sky on the map as the last fallback when one is set", async () => {
    const layers = [];
    const mem = createDownMemory({ now: () => 1, storage: null });
    const choice = await applyBackground(CDS, {
      setLayer: l => layers.push(l), layerFor: id => ({ survey: id }),
      makeFallbackLayer: f => ({ fallback: f.key, url: f.url }),
      memory: mem, probe: async () => false,
      offlineSky: { url: "/static/some-sky/", name: "Some sky", credit: "Someone" },
    });
    assert.equal(choice, "offline");
    assert.deepEqual(layers.at(-1), { fallback: "offline", url: "/static/some-sky/" });
  });
});
