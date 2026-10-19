import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { persistUiState } from "../../static/ui-state.mjs";

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: k => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)); },
  };
}

const KEY = "acp.uiState.v1";

describe("persistUiState", () => {
  it("carries objectsFilter through a save", () => {
    const objectsFilter = { initialised: true, types: ["PN", "SNR"], text: "m1" };
    const s = fakeStorage({ [KEY]: JSON.stringify({ filters: ["Ha"], objectsFilter }) });
    persistUiState(s, KEY, { filters: ["Ha", "L"], minHours: 2 });
    const saved = JSON.parse(s.getItem(KEY));
    assert.deepEqual(saved.objectsFilter, objectsFilter);
    assert.deepEqual(saved.filters, ["Ha", "L"]);
    assert.equal(saved.minHours, 2);
  });

  it("survives repeated saves", () => {
    const objectsFilter = { initialised: true, types: ["HII"], text: "" };
    const s = fakeStorage({ [KEY]: JSON.stringify({ objectsFilter }) });
    persistUiState(s, KEY, { a: 1 });
    persistUiState(s, KEY, { a: 2 });
    assert.deepEqual(JSON.parse(s.getItem(KEY)).objectsFilter, objectsFilter);
  });

  it("drops stale keys it does not own", () => {
    const s = fakeStorage({ [KEY]: JSON.stringify({ old: 1 }) });
    persistUiState(s, KEY, { a: 1 });
    assert.deepEqual(JSON.parse(s.getItem(KEY)), { a: 1 });
  });

  it("lets the new state win when it sets objectsFilter itself", () => {
    const s = fakeStorage({ [KEY]: JSON.stringify({ objectsFilter: { text: "old" } }) });
    persistUiState(s, KEY, { objectsFilter: { text: "new" } });
    assert.equal(JSON.parse(s.getItem(KEY)).objectsFilter.text, "new");
  });

  it("writes cleanly over corrupt or missing storage", () => {
    const bad = fakeStorage({ [KEY]: "{not json" });
    persistUiState(bad, KEY, { a: 1 });
    assert.deepEqual(JSON.parse(bad.getItem(KEY)), { a: 1 });
    const empty = fakeStorage();
    persistUiState(empty, KEY, { a: 1 });
    assert.deepEqual(JSON.parse(empty.getItem(KEY)), { a: 1 });
  });
});
