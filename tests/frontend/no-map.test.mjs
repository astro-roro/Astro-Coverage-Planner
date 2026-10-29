import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  MAP_CONTROL_IDS,
  MAP_CONTROL_OFF_TITLE,
  disableMapControls,
  noMapLibrary,
  noMapStub,
} from "../../static/no-map.mjs";
import { describeMapError, startupError } from "../../static/init-error.mjs";

const APP_JS = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");

describe("noMapStub: the map stand-in", () => {
  it("accepts every call the panels make and returns undefined", () => {
    const aladin = noMapStub();
    assert.equal(aladin.gotoRaDec(83.8, -5.4), undefined);
    assert.equal(aladin.addOverlay({}), undefined);
    assert.equal(aladin.setImageSurvey("CDS/P/DSS2/color"), undefined);
    assert.equal(aladin.on("click", () => {}), undefined);
  });

  it("reads back as 'map not ready' where app.js reads a value", () => {
    // The patterns app.js uses in panMapTo, skySearchView, currentSeamView
    // and the plan editor's drag handles.
    const aladin = noMapStub();
    const cur = aladin.getRaDec();
    assert.ok(!(cur && cur.length === 2));
    const fov = aladin.getFoV?.();
    assert.ok(!fov);
    assert.equal(aladin.world2pix(10, 20), undefined);
    assert.equal(typeof aladin.animateToRaDec, "function");
  });

  it("is not a promise, so awaiting it never hangs", async () => {
    const stub = noMapStub();
    assert.equal(stub.then, undefined);
    assert.equal(await stub, stub);
  });

  it("is not iterable or numeric", () => {
    const stub = noMapStub();
    assert.equal(stub[Symbol.iterator], undefined);
    assert.equal(stub[Symbol.toPrimitive], undefined);
  });

  it("ignores property writes instead of throwing", () => {
    const stub = noMapStub();
    assert.doesNotThrow(() => { stub.someFlag = true; });
  });
});

describe("noMapLibrary: the stand-in for the global A", () => {
  it("builds shapes the overlay stand-ins accept", () => {
    const A = noMapLibrary();
    const overlay = A.graphicOverlay({ color: "#ff4d4d" });
    assert.doesNotThrow(() => overlay.add(A.polygon([[0, 0], [1, 0], [1, 1]], { color: "#fff" })));
    const cat = A.catalog({ name: "plan_markers" });
    assert.doesNotThrow(() => cat.addSources([A.source(10, 20, { name: "M42" })]));
    assert.doesNotThrow(() => noMapStub().addMOC(A.MOCFromURL("https://example/Moc.fits")));
  });

  it("has no init promise to wait on", () => {
    assert.equal(noMapLibrary().init, undefined);
    assert.equal(noMapLibrary().then, undefined);
  });
});

describe("disableMapControls", () => {
  function fakeDoc(ids) {
    const els = Object.fromEntries(ids.map(id => [id, { disabled: false, title: "orig" }]));
    return { els, getElementById: id => els[id] || null };
  }

  it("turns off the map-only toolbar controls and says why", () => {
    const doc = fakeDoc([...MAP_CONTROL_IDS, "siteSel", "timeAwareToggle"]);
    disableMapControls(doc);
    for (const id of MAP_CONTROL_IDS) {
      assert.equal(doc.els[id].disabled, true, id);
      assert.equal(doc.els[id].title, MAP_CONTROL_OFF_TITLE, id);
    }
  });

  it("leaves the Site picker and Time-aware toggle on, since the panels use them", () => {
    const doc = fakeDoc([...MAP_CONTROL_IDS, "siteSel", "timeAwareToggle"]);
    disableMapControls(doc);
    assert.equal(doc.els.siteSel.disabled, false);
    assert.equal(doc.els.timeAwareToggle.disabled, false);
  });

  it("covers projection, frame, survey switching and sky search", () => {
    for (const id of ["projSel", "frameSel", "skySel", "skyMore", "skySearchBtn"]) {
      assert.ok(MAP_CONTROL_IDS.includes(id), id);
    }
  });

  it("tolerates a page without the controls", () => {
    assert.doesNotThrow(() => disableMapControls(fakeDoc([])));
    assert.doesNotThrow(() => disableMapControls(undefined));
  });
});

describe("describeMapError: the message in the map area", () => {
  for (const code of ["aladin-missing", "webgl2-missing", "aladin-timeout"]) {
    it(`keeps the specific cause for ${code} and says the panels still work`, () => {
      const msg = describeMapError(startupError(code, { ms: 30_000 }));
      assert.match(msg, /Aladin Lite/);
      assert.match(msg, /targets and plans still work/);
    });
  }

  it("blames the map, not the targets, for any other startup error", () => {
    const msg = describeMapError(new Error("wasm exploded"));
    assert.match(msg, /sky map \(Aladin Lite\) failed to start \(wasm exploded\)/);
    assert.doesNotMatch(msg, /while loading targets/);
    assert.match(msg, /targets and plans still work/);
  });
});

describe("app.js startup without A", () => {
  const body = name => {
    const start = APP_JS.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `app.js should define ${name}`);
    // Up to the next top-level or init-level function declaration.
    const rest = APP_JS.slice(start + 1);
    const end = rest.search(/\n {0,2}(async )?function /);
    return APP_JS.slice(start, start + 1 + (end < 0 ? rest.length : end));
  };

  it("starts the panels whether or not the map started", () => {
    const init = body("init");
    const mapAt = init.indexOf("await startMap()");
    const fallbackAt = init.indexOf("if (mapError) startWithoutMap(mapError)");
    const panelsAt = init.indexOf("await startPanels()");
    assert.ok(mapAt >= 0 && fallbackAt > mapAt && panelsAt > fallbackAt,
      "init should run startMap, then the no-map fallback, then startPanels");
    // Only init's own statements, at two-space indent; handlers nested
    // inside it return from themselves, not from init.
    assert.doesNotMatch(init.slice(0, panelsAt), /^ {2}(if \(.*\) )?return\b/m,
      "nothing before startPanels may return out of init");
  });

  it("gives every overlay and catalog buildMap creates a stand-in", () => {
    // A new overlay added to buildMap without a stand-in would be null when
    // the map is down, and the first panel redraw would throw on it.
    const made = [...body("buildMap").matchAll(/^\s+(\w+) = A\.(?:graphicOverlay|catalog)\(/gm)].map(m => m[1]);
    assert.ok(made.length >= 5, `expected buildMap's overlays, found ${made}`);
    const fallback = body("startWithoutMap");
    for (const name of ["aladin", ...made]) {
      assert.match(fallback, new RegExp(`\\b${name} = noMapStub\\(\\)`), `${name} needs a stand-in`);
    }
  });

  it("shows the map error in the map area, not over the targets panel", () => {
    const fallback = body("startWithoutMap");
    assert.match(fallback, /getElementById\("aladin-lite-div"\)/);
    assert.doesNotMatch(fallback, /panelBody/);
  });
});
