import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { describeUpload, summariseUploads } from "../../static/ts-sync-banner.mjs";

describe("describeUpload", () => {
  it("names new and conflicting counts for a ready upload", () => {
    const text = describeUpload({
      machine: "VOYAGER", state: "ready",
      counts: { new: 32, updated: 0, conflicts: 46 },
    });
    assert.equal(text, "VOYAGER sent TS changes to review: 32 new, 46 need a choice.");
  });

  it("includes updated counts when present", () => {
    const text = describeUpload({
      machine: "HAYABUSA", state: "ready",
      counts: { new: 1, updated: 3, conflicts: 0 },
    });
    assert.equal(text, "HAYABUSA sent TS changes to review: 1 new, 3 updated.");
  });

  it("says still reading for a checking upload, no counts", () => {
    const text = describeUpload({ machine: "VOYAGER", state: "checking" });
    assert.equal(text, "VOYAGER sent TS changes. ACP is still reading them.");
  });

  it("falls back to a generic name when the upload has none", () => {
    const text = describeUpload({ state: "checking" });
    assert.match(text, /^A NINA machine sent/);
  });

  it("has no colon when a ready upload has no non-zero counts", () => {
    const text = describeUpload({ machine: "X", state: "ready", counts: {} });
    assert.equal(text, "X sent TS changes to review.");
  });
});

describe("summariseUploads", () => {
  it("returns an empty summary for an empty or missing list", () => {
    assert.deepEqual(summariseUploads([]), { lines: [], more: 0 });
    assert.deepEqual(summariseUploads(undefined), { lines: [], more: 0 });
  });

  it("carries a review_url only for ready uploads", () => {
    const { lines } = summariseUploads([
      { machine: "A", state: "ready", counts: { new: 1 }, review_url: "/ext/nina-ts-sync/import/uploads/1" },
      { machine: "B", state: "checking" },
    ]);
    assert.equal(lines[0].url, "/ext/nina-ts-sync/import/uploads/1");
    assert.equal(lines[1].url, null);
  });

  it("caps at three lines and reports the rest as more", () => {
    const uploads = Array.from({ length: 5 }, (_, i) => (
      { machine: `M${i}`, state: "checking" }
    ));
    const { lines, more } = summariseUploads(uploads);
    assert.equal(lines.length, 3);
    assert.equal(more, 2);
  });

  it("more is zero when everything fits", () => {
    const { more } = summariseUploads([{ machine: "A", state: "checking" }]);
    assert.equal(more, 0);
  });
});
