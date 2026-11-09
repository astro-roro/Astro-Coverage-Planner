import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  rotationUnknown, rotationUnknownButtonHtml, wireRotationUnknown,
  ROTATION_UNKNOWN_NOTE, ROTATION_UNKNOWN_TIP,
} from "../../static/rotation-note.mjs";

const pos = { center_ra_deg: 11.08, center_dec_deg: 41.4 };

describe("rotationUnknown", () => {
  it("is true for source none with a position", () => {
    assert.equal(rotationUnknown({ ...pos, rotation_source: "none", rotation_deg: 0 }), true);
  });
  it("is true when the source is missing and no angle exists", () => {
    assert.equal(rotationUnknown({ ...pos }), true);
  });
  it("is false for a solved or header angle", () => {
    for (const src of ["wcs", "wcs_mixed", "header"]) {
      assert.equal(rotationUnknown({ ...pos, rotation_source: src, rotation_deg: 12 }), false, src);
    }
  });
  it("is false without a position", () => {
    assert.equal(rotationUnknown({ rotation_source: "none" }), false);
    assert.equal(rotationUnknown(null), false);
  });
});

describe("rotation icon", () => {
  it("is a focusable button with the tooltip and a hidden note", () => {
    const html = rotationUnknownButtonHtml();
    assert.match(html, /<button type="button"/);
    assert.ok(html.includes(`title="${ROTATION_UNKNOWN_TIP}"`));
    assert.ok(html.includes(ROTATION_UNKNOWN_NOTE));
    assert.match(html, /id="rotUnknownNote"[^>]* hidden/);
  });

  it("click toggles the note and aria-expanded", () => {
    const attrs = {};
    const btn = { listeners: {}, addEventListener(e, f) { this.listeners[e] = f; }, setAttribute(k, v) { attrs[k] = v; } };
    const note = { hidden: true };
    const root = { querySelector: s => (s === "#rotUnknownBtn" ? btn : note) };
    wireRotationUnknown(root);
    btn.listeners.click();
    assert.equal(note.hidden, false);
    assert.equal(attrs["aria-expanded"], "true");
    btn.listeners.click();
    assert.equal(note.hidden, true);
    assert.equal(attrs["aria-expanded"], "false");
  });
});
