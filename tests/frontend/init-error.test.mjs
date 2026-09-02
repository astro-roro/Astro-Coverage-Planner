import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  ALADIN_URL,
  describeInitError,
  startupError,
  webgl2Available,
  withTimeout,
} from "../../static/init-error.mjs";

describe("describeInitError: Aladin Lite never started", () => {
  it("names the CDN address when the library script did not load", () => {
    const msg = describeInitError(startupError("aladin-missing"));
    assert.match(msg, /Aladin Lite/);
    assert.ok(msg.includes(ALADIN_URL), "message should name the CDN URL to unblock");
    assert.match(msg, /ad blocker|firewall/);
  });

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
