import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ALADIN_FALLBACK_URL,
  ALADIN_SOURCES,
  ALADIN_URL,
  aladinLoaded,
  describeInitError,
  loadFirstScript,
  startupError,
  webgl2Available,
  withTimeout,
} from "../../static/init-error.mjs";

describe("describeInitError: Aladin Lite never started", () => {
  it("names both script addresses when the library did not load", () => {
    const msg = describeInitError(startupError("aladin-missing"));
    assert.match(msg, /Aladin Lite/);
    assert.ok(msg.includes(ALADIN_URL), "message should name the CDS URL to unblock");
    assert.ok(msg.includes(ALADIN_FALLBACK_URL), "message should name the jsDelivr URL to unblock");
    assert.match(msg, /ad blocker|firewall/);
  });
});

describe("Aladin Lite sources", () => {
  it("tries CDS first, then the jsDelivr copy of the same version", () => {
    assert.deepEqual(ALADIN_SOURCES.map(s => s.url), [ALADIN_URL, ALADIN_FALLBACK_URL]);
    assert.match(ALADIN_URL, /^https:\/\/aladin\.cds\.unistra\.fr\/.*\/3\.8\.2\/aladin\.js$/);
    assert.match(ALADIN_FALLBACK_URL, /^https:\/\/cdn\.jsdelivr\.net\/npm\/aladin-lite@3\.8\.2\/dist\/aladin\.js$/);
  });

  it("loads CDS as a classic script and the npm build as a module", () => {
    // npm's dist/aladin.js is an ES module with no global A. As a classic
    // script it fails on its export and the fallback never works.
    assert.deepEqual(ALADIN_SOURCES.map(s => s.kind), ["script", "module"]);
  });

  it("is loaded by app.js, not by a blocking tag in the template", () => {
    // A plain <script src> to CDS stalls the page for the browser's whole
    // connection timeout when CDS hangs, before any fallback can start.
    const html = readFileSync(new URL("../../templates/index.html", import.meta.url), "utf8");
    assert.ok(!/<script[^>]+aladin[^>]*\.js/i.test(html), "template should not load aladin.js itself");
  });
});

// The two packagings, as the browser would see them. CDS's build is a
// classic script that sets window.A. npm's build is the fixture file: an ES
// module with a default export and no global.
const CDS = "https://cds.example/aladin.js";
const JSD = new URL("../fixtures/aladin-esm.mjs", import.meta.url).href;
const CDS_SOURCE = "window.A = { init: Promise.resolve() };";
const JSD_SOURCE = readFileSync(new URL(JSD), "utf8");
const SOURCES = [{ url: CDS, kind: "script" }, { url: JSD, kind: "module" }];

// A stand-in document that runs each <script> the way a browser does.
// `network[src]` is the file's text, "error" for a refused connection, or
// missing for a host that hangs. A script that fails to parse still fires
// load, as in a browser, and defines nothing.
function fakeDoc(network, scope) {
  const added = [];
  const doc = {
    head: {
      appendChild(el) {
        added.push(el.src);
        const body = network[el.src];
        if (body === undefined) return;
        queueMicrotask(() => {
          if (body === "error") return el.onerror?.(new Event("error"));
          let run = null;
          try { run = new Function("window", body); } catch { /* SyntaxError */ }
          run?.(scope);
          el.onload?.(new Event("load"));
        });
      },
    },
    createElement: () => ({ remove() {} }),
  };
  return { doc, added };
}

describe("loadFirstScript", () => {
  const quiet = fn => async () => {
    const warn = console.warn;
    console.warn = () => {};
    try { await fn(); } finally { console.warn = warn; }
  };
  const refused = () => Promise.reject(new TypeError("Failed to fetch dynamically imported module"));

  it("uses CDS and never asks jsDelivr when CDS answers", quiet(async () => {
    const scope = {};
    const imported = [];
    const { doc, added } = fakeDoc({ [CDS]: CDS_SOURCE }, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope, importModule: u => { imported.push(u); return refused(); } });
    assert.equal(got, CDS);
    assert.deepEqual(added, [CDS]);
    assert.deepEqual(imported, []);
  }));

  it("imports the npm build and takes A from its default export when CDS refuses", quiet(async () => {
    const scope = {};
    const { doc, added } = fakeDoc({ [CDS]: "error" }, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope });
    assert.equal(got, JSD);
    assert.deepEqual(added, [CDS], "the module must not be added as a <script>");
    assert.ok(aladinLoaded(scope));
    assert.equal(scope.A, (await import(JSD)).default);
  }));

  it("gets A from ACP's real source list while CDS is down", quiet(async () => {
    // The jsDelivr address serves the module-shaped file however it is
    // asked for, so loading it any way but import() leaves A undefined.
    const scope = {};
    const { doc } = fakeDoc({ [ALADIN_URL]: "error", [ALADIN_FALLBACK_URL]: JSD_SOURCE }, scope);
    const importModule = u => (u === ALADIN_FALLBACK_URL ? import(JSD) : refused());
    const got = await loadFirstScript(ALADIN_SOURCES, { doc, scope, importModule });
    assert.equal(got, ALADIN_FALLBACK_URL);
    assert.ok(aladinLoaded(scope));
  }));

  it("imports the npm build when CDS hangs past the timeout", quiet(async () => {
    const scope = {};
    const { doc } = fakeDoc({}, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope, timeoutMs: 10 });
    assert.equal(got, JSD);
    assert.ok(aladinLoaded(scope));
  }));

  it("gets no A from the npm build loaded as a classic script", quiet(async () => {
    // The bug this guards against: the export is a syntax error in a classic
    // script, so the tag "loads" and A never appears.
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: "error", [JSD]: JSD_SOURCE }, scope);
    const got = await loadFirstScript([SOURCES[0], { url: JSD, kind: "script" }], { doc, scope });
    assert.equal(got, null);
    assert.equal(aladinLoaded(scope), false);
  }));

  it("gives the import the same time limit when it is not the last source", quiet(async () => {
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: CDS_SOURCE }, scope);
    const hang = () => new Promise(() => {});
    const got = await loadFirstScript([{ url: JSD, kind: "module" }, SOURCES[0]], { doc, scope, timeoutMs: 10, importModule: hang });
    assert.equal(got, CDS);
  }));

  it("moves on when a script loads but does not define A", quiet(async () => {
    // A captive portal or proxy can answer 200 with an HTML page.
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: "<html>Sign in to Wi-Fi</html>" }, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope });
    assert.equal(got, JSD);
  }));

  it("resolves null when every source fails, so init can show the error", quiet(async () => {
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: "error" }, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope, importModule: refused });
    assert.equal(got, null);
    assert.equal(aladinLoaded(scope), false);
  }));

  it("adds no script when A is already defined", quiet(async () => {
    const scope = { A: { init: Promise.resolve() } };
    const { doc, added } = fakeDoc({}, scope);
    const got = await loadFirstScript(SOURCES, { doc, scope });
    assert.equal(got, null);
    assert.deepEqual(added, []);
  }));

});

describe("describeInitError: the map's other failures", () => {
  it("explains WebGL2 when the browser cannot provide a context", () => {
    const msg = describeInitError(startupError("webgl2-missing"));
    assert.match(msg, /WebGL2/);
    assert.match(msg, /hardware acceleration/);
  });

  it("reports the timeout length and points at the console", () => {
    const msg = describeInitError(startupError("aladin-timeout", { ms: 30_000 }));
    assert.match(msg, /30 seconds/);
    assert.match(msg, /F12/);
  });
});

describe("withTimeout", () => {
  it("passes through a promise that settles in time", async () => {
    const v = await withTimeout(Promise.resolve(42), 1000, new Error("late"));
    assert.equal(v, 42);
  });

  it("rejects with the supplied error when the promise never settles", async () => {
    const late = startupError("aladin-timeout", { ms: 10 });
    await assert.rejects(
      withTimeout(new Promise(() => {}), 10, late),
      err => err === late,
    );
  });
});

describe("webgl2Available", () => {
  it("is true when the document can hand out a webgl2 context", () => {
    const doc = { createElement: () => ({ getContext: kind => (kind === "webgl2" ? {} : null) }) };
    assert.equal(webgl2Available(doc), true);
  });

  it("is false when getContext returns null", () => {
    const doc = { createElement: () => ({ getContext: () => null }) };
    assert.equal(webgl2Available(doc), false);
  });

  it("is false when there is no document at all", () => {
    assert.equal(webgl2Available(undefined), false);
  });
});

describe("describeInitError — HTTP failures", () => {
  it("names the status code for a non-ok response", () => {
    const err = new Error("manifest request failed");
    err.status = 500;
    assert.equal(
      describeInitError(err),
      "Server error (500) while loading targets. Reload to try again."
    );
  });

  it("handles a 4xx status the same way as 5xx", () => {
    const err = new Error("manifest request failed");
    err.status = 404;
    assert.equal(
      describeInitError(err),
      "Server error (404) while loading targets. Reload to try again."
    );
  });
});

describe("describeInitError — network failures", () => {
  it("treats a TypeError as unreachable server (fetch's network-error shape)", () => {
    const err = new TypeError("Failed to fetch");
    assert.equal(
      describeInitError(err),
      "Could not reach the server. Check your connection and reload."
    );
  });
});

describe("describeInitError — everything else", () => {
  it("falls back to the error's message", () => {
    const err = new Error("aladin blew up");
    assert.equal(
      describeInitError(err),
      "Something went wrong while loading targets (aladin blew up). Reload to try again."
    );
  });

  it("stringifies non-Error throws", () => {
    assert.equal(
      describeInitError("boom"),
      "Something went wrong while loading targets (boom). Reload to try again."
    );
  });
});
