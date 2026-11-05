// The upload progress card at the top of the Activity tab.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { uploadCardHtml, fractionDone, sizeText } from "../../static/upload-card.mjs";

const esc = s => String(s).replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

describe("uploadCardHtml", () => {
  it("is empty with no running or paused transfer", () => {
    assert.equal(uploadCardHtml([], esc), "");
    assert.equal(uploadCardHtml([{ id: "a", state: "finished" }], esc), "");
    assert.equal(uploadCardHtml(null, esc), "");
  });

  it("shows a running transfer with its figures and a bar", () => {
    const html = uploadCardHtml([{ id: "a", label: "Lights", state: "running", files_done: 3,
      files_total: 10, bytes_done: 250e6, bytes_total: 1e9 }], esc);
    assert.ok(html.includes("Lights"));
    assert.ok(html.includes("Running · 25%"));
    assert.ok(html.includes("3 of 10 files"));
    assert.ok(html.includes("250.0 MB of 1.0 GB"));
    assert.ok(html.includes('aria-valuenow="25"'));
  });

  it("marks a paused one and leaves out the bar when nothing is known", () => {
    const html = uploadCardHtml([{ id: "b", state: "paused" }], esc);
    assert.ok(html.includes("Paused"));
    assert.ok(!html.includes("up-bar"));
  });

  it("escapes what the sender wrote", () => {
    const html = uploadCardHtml([{ id: "c", label: "<img src=x>", state: "running" }], esc);
    assert.ok(html.includes("&lt;img src=x&gt;"));
    assert.ok(!html.includes("<img"));
  });
});

describe("fractionDone and sizeText", () => {
  it("prefers bytes, then files, and clamps", () => {
    assert.equal(fractionDone({ bytes_done: 5, bytes_total: 10, files_done: 1, files_total: 10 }), 0.5);
    assert.equal(fractionDone({ files_done: 2, files_total: 4 }), 0.5);
    assert.equal(fractionDone({ files_done: 9, files_total: 4 }), 1);
    assert.equal(fractionDone({}), null);
  });
  it("writes sizes in powers of a thousand", () => {
    assert.equal(sizeText(999), "999 B");
    assert.equal(sizeText(1500), "1.5 KB");
    assert.equal(sizeText(2.5e12), "2.5 TB");
    assert.equal(sizeText(undefined), "?");
  });
});
