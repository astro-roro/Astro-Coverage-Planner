// H2 and H8: pages an extension shows inside ACP's panels (editor sections,
// Activity cards), the plan reload message, and taking server changes into
// the open editor.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { pagesFrom, withPlanId, adoptServerChanges, bridgeRequest } from "../../static/ext-ui.mjs";

describe("pagesFrom", () => {
  const manifest = [
    { extension: "my_tool", editor_sections: [
      { id: "notes", label: "Notes", page: "/api/ext/my-tool/notes", height: 200 },
      { id: "bad", label: "Bad", page: "https://elsewhere.example/" },
      { id: "nolabel", page: "/api/ext/my_tool/x" },
      { id: "notes", label: "Again", page: "/api/ext/my_tool/again" },
    ] },
    { extension: "other", activity_cards: [{ id: "card", page: "/api/ext/other/card", height: 9999 }] },
    null,
  ];

  it("keeps valid editor sections and needs a label for them", () => {
    assert.deepEqual(pagesFrom(manifest, "editor_sections", true), [
      { key: "ext-my_tool-notes", extension: "my_tool", id: "notes", label: "Notes",
        page: "/api/ext/my-tool/notes", height: 200 },
    ]);
  });

  it("takes activity cards without a label and drops a height out of range", () => {
    assert.deepEqual(pagesFrom(manifest, "activity_cards"), [
      { key: "ext-other-card", extension: "other", id: "card", label: "", page: "/api/ext/other/card", height: null },
    ]);
  });

  it("returns nothing for a missing manifest", () => {
    assert.deepEqual(pagesFrom(undefined, "editor_sections", true), []);
  });
});

describe("withPlanId", () => {
  it("adds plan_id with ? or &, encoded, before any hash", () => {
    assert.equal(withPlanId("/api/ext/a/s", "p 1"), "/api/ext/a/s?plan_id=p%201");
    assert.equal(withPlanId("/api/ext/a/s?x=1", "p1"), "/api/ext/a/s?x=1&plan_id=p1");
    assert.equal(withPlanId("/api/ext/a/s#top", "p1"), "/api/ext/a/s?plan_id=p1#top");
  });
});

describe("acp:plan:reload", () => {
  const frame = {};
  const ev = data => ({ origin: "http://acp", source: frame, data });
  it("is accepted from an extension frame with a plan id", () => {
    assert.deepEqual(bridgeRequest(ev({ type: "acp:plan:reload", plan_id: "p1" }), [frame], "http://acp"),
      { kind: "plan-reload", planId: "p1" });
  });
  it("is refused without a plan id, from another frame or origin", () => {
    assert.equal(bridgeRequest(ev({ type: "acp:plan:reload" }), [frame], "http://acp"), null);
    assert.equal(bridgeRequest(ev({ type: "acp:plan:reload", plan_id: "p1" }), [{}], "http://acp"), null);
    assert.equal(bridgeRequest(ev({ type: "acp:plan:reload", plan_id: "p1" }), [frame], "http://evil"), null);
  });
});

describe("adoptServerChanges", () => {
  it("takes what the server changed and keeps the user's unsaved edits", () => {
    const loaded = { id: "p1", priority: "normal", notes: "a", checklist: { x: false }, updated_at: "t1" };
    const editing = { ...loaded, notes: "typed but not saved", checklist: { x: false } };
    const fresh = { id: "p1", priority: "normal", notes: "a", checklist: { x: true }, extra: 1, updated_at: "t2" };
    const taken = adoptServerChanges(editing, loaded, fresh);
    assert.deepEqual(taken.sort(), ["checklist", "extra"]);
    assert.equal(editing.notes, "typed but not saved");
    assert.deepEqual(editing.checklist, { x: true });
    assert.equal(editing.extra, 1);
    assert.equal(editing.updated_at, "t2");
    assert.deepEqual(loaded, fresh);
  });

  it("removes a key the server removed", () => {
    const loaded = { id: "p1", gone: 1 };
    const editing = { id: "p1", gone: 1 };
    assert.deepEqual(adoptServerChanges(editing, loaded, { id: "p1" }), ["gone"]);
    assert.ok(!("gone" in editing));
  });
});
