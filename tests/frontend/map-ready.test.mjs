import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createMapGate } from "../../static/map-ready.mjs";
import {
  MAP_CONTROL_IDS, MAP_CONTROL_LOADING_TITLE, enableMapControls, pauseMapControls,
} from "../../static/no-map.mjs";

const APP_JS = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");

describe("createMapGate: map-dependent work waits for the map", () => {
  it("queues work while the map loads and runs it in order once ready", () => {
    const gate = createMapGate();
    const calls = [];
    gate.whenReady(() => calls.push("a"));
    gate.whenReady(() => calls.push("b"));
    assert.deepEqual(calls, []);
    assert.equal(gate.pending, 2);
    gate.markReady();
    assert.deepEqual(calls, ["a", "b"]);
    assert.equal(gate.pending, 0);
  });

  it("runs work straight away once the map is ready", () => {
    const gate = createMapGate();
    gate.markReady();
    let ran = false;
    gate.whenReady(() => { ran = true; });
    assert.equal(ran, true);
  });

  it("runs queued work once, even if markReady is called twice", () => {
    const gate = createMapGate();
    let n = 0;
    gate.whenReady(() => n++);
    gate.markReady();
    gate.markReady();
    assert.equal(n, 1);
  });

  it("keeps going when one queued call throws", () => {
    const gate = createMapGate();
    const calls = [];
    const orig = console.error;
    console.error = () => {};
    try {
      gate.whenReady(() => { throw new Error("boom"); });
      gate.whenReady(() => calls.push("after"));
      gate.markReady();
    } finally { console.error = orig; }
    assert.deepEqual(calls, ["after"]);
  });

  it("drops queued and later work when the map fails", () => {
    const gate = createMapGate();
    let n = 0;
    gate.whenReady(() => n++);
    gate.markFailed();
    gate.whenReady(() => n++);
    gate.markReady();
    assert.equal(n, 0);
    assert.equal(gate.state, "failed");
    assert.equal(gate.pending, 0);
  });
});

describe("map controls while the map loads", () => {
  function fakeDoc() {
    const els = Object.fromEntries(MAP_CONTROL_IDS.map(id => [id, { disabled: false, title: "orig", dataset: {} }]));
    return { els, getElementById: id => els[id] || null };
  }

  it("switches the map-only controls off, then restores them and their titles", () => {
    const doc = fakeDoc();
    pauseMapControls(doc);
    for (const id of MAP_CONTROL_IDS) {
      assert.equal(doc.els[id].disabled, true);
      assert.equal(doc.els[id].title, MAP_CONTROL_LOADING_TITLE);
    }
    enableMapControls(doc);
    for (const id of MAP_CONTROL_IDS) {
      assert.equal(doc.els[id].disabled, false);
      assert.equal(doc.els[id].title, "orig");
    }
  });
});

describe("init() ordering in app.js", () => {
  const start = APP_JS.indexOf("async function init()");
  const body = APP_JS.slice(start, APP_JS.indexOf("\nfunction startWithoutMap"));

  it("does not await the map before starting the panels", () => {
    assert.ok(start > 0);
    assert.ok(!/await\s+startMap\(\)/.test(body), "init must not await startMap()");
    assert.ok(body.indexOf("await startPanels()") > 0);
    assert.ok(body.indexOf("startMap().then") > 0);
  });

  it("puts the stand-ins and placeholder in place before the panels run", () => {
    assert.ok(body.indexOf("showMapLoading()") > 0);
    assert.ok(body.indexOf("showMapLoading()") < body.indexOf("await startPanels()"));
  });

  it("marks the gate failed and shows the map error when the map does not start", () => {
    assert.match(body, /mapGate\.markFailed\(\);\s*startWithoutMap\(mapError\)/);
  });

  it("runs the saved map view and the redraw through the gate once the map is up", () => {
    assert.match(APP_JS, /mapGate\.whenReady\(applySavedMapView\)/);
    assert.match(body, /mapGate\.whenReady\(redrawMapContent\);\s*mapGate\.markReady\(\)/);
  });
});
