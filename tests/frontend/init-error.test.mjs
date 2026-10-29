import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ALADIN_FALLBACK_URL,
  ALADIN_URL,
  ALADIN_URLS,
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

describe("Aladin Lite script addresses", () => {
  it("tries CDS first, then the jsDelivr copy of the same version", () => {
    assert.deepEqual(ALADIN_URLS, [ALADIN_URL, ALADIN_FALLBACK_URL]);
    assert.match(ALADIN_URL, /^https:\/\/aladin\.cds\.unistra\.fr\/.*\/3\.8\.2\/aladin\.js$/);
    assert.match(ALADIN_FALLBACK_URL, /^https:\/\/cdn\.jsdelivr\.net\/npm\/aladin-lite@3\.8\.2\/dist\/aladin\.js$/);
  });

  it("is loaded by app.js, not by a blocking tag in the template", () => {
    // A plain <script src> to CDS stalls the page for the browser's whole
    // connection timeout when CDS hangs, before any fallback can start.
    const html = readFileSync(new URL("../../templates/index.html", import.meta.url), "utf8");
    assert.ok(!/<script[^>]+aladin[^>]*\.js/i.test(html), "template should not load aladin.js itself");
  });
});

// A stand-in document. Each <script> it is handed reports back through
// `outcomes[src]`: "load" (and optionally defines A), "error", or "hang".
function fakeDoc(outcomes, scope) {
  const added = [];
  const doc = {
    head: {
      appendChild(el) {
        added.push(el.src);
        const what = outcomes[el.src] || "hang";
        queueMicrotask(() => {
          if (what === "error") el.onerror?.(new Event("error"));
          if (what === "load" || what === "load-defines-A") {
            if (what === "load-defines-A") scope.A = { init: Promise.resolve() };
            el.onload?.(new Event("load"));
          }
        });
      },
    },
    createElement: () => ({ remove() {} }),
  };
  return { doc, added };
}

describe("loadFirstScript", () => {
  const CDS = "https://cds.example/aladin.js";
  const JSD = "https://jsd.example/aladin.js";
  const quiet = fn => async () => {
    const warn = console.warn;
    console.warn = () => {};
    try { await fn(); } finally { console.warn = warn; }
  };

  it("uses CDS and never asks jsDelivr when CDS answers", quiet(async () => {
    const scope = {};
    const { doc, added } = fakeDoc({ [CDS]: "load-defines-A" }, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, CDS);
    assert.deepEqual(added, [CDS]);
  }));

  it("falls back to jsDelivr when CDS refuses the connection", quiet(async () => {
    const scope = {};
    const { doc, added } = fakeDoc({ [CDS]: "error", [JSD]: "load-defines-A" }, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, JSD);
    assert.deepEqual(added, [CDS, JSD]);
  }));

  it("falls back to jsDelivr when CDS hangs past the timeout", quiet(async () => {
    const scope = {};
    const { doc, added } = fakeDoc({ [CDS]: "hang", [JSD]: "load-defines-A" }, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, timeoutMs: 10, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, JSD);
    assert.deepEqual(added, [CDS, JSD]);
  }));

  it("moves on when a script loads but does not define A", quiet(async () => {
    // A captive portal or proxy can answer 200 with an HTML page.
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: "load", [JSD]: "load-defines-A" }, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, JSD);
  }));

  it("resolves null when every address fails, so init can show the error", quiet(async () => {
    const scope = {};
    const { doc } = fakeDoc({ [CDS]: "error", [JSD]: "error" }, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, null);
    assert.equal(aladinLoaded(scope), false);
  }));

  it("adds no script when A is already defined", quiet(async () => {
    const scope = { A: { init: Promise.resolve() } };
    const { doc, added } = fakeDoc({}, scope);
    const got = await loadFirstScript([CDS, JSD], { doc, isLoaded: () => aladinLoaded(scope) });
    assert.equal(got, null);
    assert.deepEqual(added, []);
  }));

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
