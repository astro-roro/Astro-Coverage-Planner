import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { listModeFor, createNavStack } from "../../static/panel-nav.mjs";

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

describe("createNavStack", () => {
  const entry = { kind: "tile", sourceId: "s", tileId: "t1", title: "t1", dest: "plan:p1" };

  it("has no back when empty", () => {
    const nav = createNavStack();
    assert.equal(nav.peek(), null);
    assert.equal(nav.bind("plan:p1"), null);
    assert.equal(nav.pop(), null);
  });

  it("gives the pushed entry to the screen it was pushed for, again on re-render", () => {
    const nav = createNavStack();
    nav.push(entry);
    assert.equal(nav.bind("plan:p1").tileId, "t1");
    assert.equal(nav.bind("plan:p1").tileId, "t1");
    assert.equal(nav.size, 1);
  });

  it("pop returns the entry and leaves nothing behind", () => {
    const nav = createNavStack();
    nav.push(entry);
    assert.equal(nav.pop().sourceId, "s");
    assert.equal(nav.peek(), null);
    assert.equal(nav.bind("plan:p1"), null);
  });

  it("drops the entry when a different screen renders", () => {
    const nav = createNavStack();
    nav.push(entry);
    assert.equal(nav.bind("plan:other"), null);
    assert.equal(nav.size, 0);
    assert.equal(nav.bind("plan:p1"), null);
  });

  it("clear (a list opening) removes the entry", () => {
    const nav = createNavStack();
    nav.push(entry);
    nav.clear();
    assert.equal(nav.bind("plan:p1"), null);
  });
});
