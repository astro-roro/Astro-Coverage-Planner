import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  TOUR_KEY,
  autoStartBlocked,
  buildSteps,
  placeCard,
  readTourState,
  resolveSteps,
  shouldAutoStart,
  writeTourState,
} from "../../static/tour.mjs";

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: k => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
  };
}

const throwingStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
};

// Every selector resolves, standing in for a page where every control shows.
const everything = sel => ({ sel });

describe("buildSteps", () => {
  it("runs all seven steps for a populated library", () => {
    const ids = buildSteps({ emptyLibrary: false }).map(s => s.id);
    assert.deepEqual(ids, ["map", "main", "filters", "library", "planning", "time", "nina"]);
  });

  it("runs only steps 1, 6 and 7 for an empty library", () => {
    const ids = buildSteps({ emptyLibrary: true }).map(s => s.id);
    assert.deepEqual(ids, ["map", "time", "nina"]);
  });

  it("uses the empty-library copy on step 1", () => {
    const [empty] = buildSteps({ emptyLibrary: true });
    const [full] = buildSteps({ emptyLibrary: false });
    assert.match(empty.body, /once you scan your archive/);
    assert.match(full.body, /every field you have imaged/);
  });

  it("names the first rail button for the mode it is in", () => {
    const cov = buildSteps({ planningMode: false }).find(s => s.id === "main");
    const plan = buildSteps({ planningMode: true }).find(s => s.id === "main");
    assert.equal(cov.title, "Targets");
    assert.equal(plan.title, "Plans");
  });

  it("has no em dashes in any copy", () => {
    for (const ctx of [{}, { emptyLibrary: true }, { planningMode: true }]) {
      for (const s of buildSteps(ctx)) {
        for (const text of [s.title, s.body, ...s.anchors.map(a => a.body || "")]) {
          assert.doesNotMatch(text, /\u2014/);
        }
      }
    }
  });
});

describe("step 7 anchor", () => {
  const step7 = ctx => resolveSteps(buildSteps(ctx), everything).at(-1);

  it("points at Sync to NINA in Planning mode", () => {
    assert.equal(step7({ planningMode: true }).selector, "#planSync");
  });

  it("points at the Activity button in Coverage mode", () => {
    assert.equal(step7({ planningMode: false }).selector, '#panelRail .rail-btn[data-tab="activity"]');
  });

  it("falls back to Activity in Planning mode when Sync is not showing", () => {
    const steps = resolveSteps(buildSteps({ planningMode: true }), sel => (sel === "#planSync" ? null : {}));
    assert.equal(steps.at(-1).selector, '#panelRail .rail-btn[data-tab="activity"]');
    assert.match(steps.at(-1).body, /Sync to NINA/);
  });

  it("falls back to the Planning switch when Activity is switched off", () => {
    const steps = resolveSteps(buildSteps({ planningMode: false }), sel => (sel.includes("activity") ? null : {}));
    assert.equal(steps.at(-1).selector, "#modePlanning");
  });
});

describe("resolveSteps", () => {
  it("skips a step whose anchor is missing or hidden", () => {
    const steps = resolveSteps(buildSteps({}), sel => (sel.includes('"library"') ? null : {}));
    assert.deepEqual(steps.map(s => s.id), ["map", "main", "filters", "planning", "time", "nina"]);
  });

  it("returns nothing when no anchor shows", () => {
    assert.deepEqual(resolveSteps(buildSteps({}), () => null), []);
  });

  it("keeps the element it found, for the ring", () => {
    const steps = resolveSteps(buildSteps({ emptyLibrary: true }), everything);
    assert.equal(steps[0].el.sel, "#aladin-lite-div");
  });
});

describe("tour state in localStorage", () => {
  it("reads nothing from an empty store", () => {
    assert.equal(readTourState(fakeStorage()), null);
  });

  it("writes version 1 with seen and skipped", () => {
    const st = fakeStorage();
    writeTourState(st, { skipped: true });
    assert.deepEqual(JSON.parse(st.data[TOUR_KEY]), { version: 1, seen: true, skipped: true });
    writeTourState(st, { skipped: false });
    assert.deepEqual(readTourState(st), { version: 1, seen: true, skipped: false });
  });

  it("ignores bad JSON and other versions", () => {
    assert.equal(readTourState(fakeStorage({ [TOUR_KEY]: "{nope" })), null);
    assert.equal(readTourState(fakeStorage({ [TOUR_KEY]: '{"version":2,"seen":true}' })), null);
  });

  it("survives a store that throws on every call", () => {
    assert.equal(readTourState(throwingStorage), null);
    assert.doesNotThrow(() => writeTourState(throwingStorage, { skipped: false }));
    assert.equal(readTourState(null), null);
  });
});

describe("shouldAutoStart", () => {
  it("starts when the flag is absent", () => {
    assert.equal(shouldAutoStart(fakeStorage()), true);
  });

  it("does not start once the tour has been seen, finished or skipped", () => {
    for (const skipped of [true, false]) {
      const st = fakeStorage();
      writeTourState(st, { skipped });
      assert.equal(shouldAutoStart(st), false);
    }
  });

  it("starts when storage is blocked, since nothing can say it was seen", () => {
    assert.equal(shouldAutoStart(throwingStorage), true);
  });
});

describe("autoStartBlocked", () => {
  const doc = ({ modal = false, banner = null } = {}) => ({
    querySelector: () => (modal ? {} : null),
    getElementById: () => (banner === null ? null : { hidden: !banner }),
  });

  it("is clear with no modal and no banner", () => {
    assert.equal(autoStartBlocked(doc()), false);
    assert.equal(autoStartBlocked(doc({ banner: false })), false);
  });

  it("waits while a modal is open", () => {
    assert.equal(autoStartBlocked(doc({ modal: true })), true);
  });

  it("waits while the TS upload banner shows", () => {
    assert.equal(autoStartBlocked(doc({ banner: true })), true);
  });
});

describe("placeCard", () => {
  const vp = { width: 1200, height: 800 };
  const card = { width: 340, height: 160 };

  it("centres the card and names the control when the anchor is off screen", () => {
    const p = placeCard({ left: 100, top: 900, right: 200, bottom: 930, width: 100, height: 30 }, card, vp);
    assert.equal(p.mode, "centred");
  });

  it("treats a missing rect as off screen", () => {
    assert.equal(placeCard(null, card, vp).mode, "centred");
  });

  it("keeps the card inside the viewport beside a rail button", () => {
    const rect = { left: 1140, top: 60, right: 1200, bottom: 120, width: 60, height: 60 };
    const p = placeCard(rect, card, vp);
    assert.equal(p.mode, "anchored");
    assert.ok(p.left >= 8 && p.left + p.width <= vp.width - 8);
    assert.ok(p.top >= 8 && p.top + card.height <= vp.height - 8);
  });

  it("goes full width on a phone, below a top-bar anchor", () => {
    const phone = { width: 390, height: 800 };
    const p = placeCard({ left: 200, top: 40, right: 300, bottom: 70, width: 100, height: 30 }, card, phone, { phone: true });
    assert.equal(p.width, 390 - 16);
    assert.ok(p.top >= 70);
  });

  it("sits at the top on a phone for a bottom-bar anchor", () => {
    const phone = { width: 390, height: 800 };
    const p = placeCard({ left: 0, top: 740, right: 78, bottom: 796, width: 78, height: 56 }, card, phone, { phone: true });
    assert.equal(p.top, 8);
  });
});
