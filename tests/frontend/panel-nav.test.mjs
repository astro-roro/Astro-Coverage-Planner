import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { listModeFor } from "../../static/panel-nav.mjs";

describe("listModeFor", () => {
  it("returns to the plan list from a detail view opened in Planning mode", () => {
    assert.equal(listModeFor(true), "plan-list");
  });

  it("returns to the target list from a detail view opened in Coverage mode", () => {
    assert.equal(listModeFor(false), "list");
  });

  it("treats a missing mode as Coverage", () => {
    assert.equal(listModeFor(undefined), "list");
  });
});
