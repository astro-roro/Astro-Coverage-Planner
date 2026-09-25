import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { pickButtonState } from "../../static/replaceable-buttons.mjs";

const NINA_META = {
  id: "sync-to-nina",
  requiresTsDb: true,
  hintWhenUnavailable: "Plans reach NINA through the NINA TS sync extension on the imaging PC.",
};

const NINA_EXTENSION = {
  extension: "nina_ts_sync",
  actions: [{ id: "sync", replaces: "sync-to-nina", label: "Sync with NINA" }],
};

describe("pickButtonState", () => {
  it("uses the core default when no extension replaces the button", () => {
    const state = pickButtonState(NINA_META, [], true);
    assert.equal(state.mode, "default");
    assert.equal(state.hint, null);
  });

  it("uses the extension's replacement when the TS db is available", () => {
    const state = pickButtonState(NINA_META, [NINA_EXTENSION], true);
    assert.equal(state.mode, "extension");
    assert.equal(state.action.label, "Sync with NINA");
  });

  it("falls back to the default with a hint when the TS db is unavailable", () => {
    const state = pickButtonState(NINA_META, [NINA_EXTENSION], false);
    assert.equal(state.mode, "default");
    assert.equal(state.hint, NINA_META.hintWhenUnavailable);
  });

  it("ignores requiresTsDb for a meta that does not set it", () => {
    const meta = { id: "sync-to-nina" };
    const state = pickButtonState(meta, [NINA_EXTENSION], false);
    assert.equal(state.mode, "extension");
  });

  it("ignores actions replacing a different core id", () => {
    const other = { extension: "x", actions: [{ id: "y", replaces: "other-button", label: "Y" }] };
    const state = pickButtonState(NINA_META, [other], true);
    assert.equal(state.mode, "default");
  });
});
