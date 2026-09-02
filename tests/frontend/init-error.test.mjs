import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { describeInitError } from "../../static/init-error.mjs";

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
