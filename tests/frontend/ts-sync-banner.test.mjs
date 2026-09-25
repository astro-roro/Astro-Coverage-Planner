import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { describeUpload, summariseUploads, formatExpiry } from "../../static/ts-sync-banner.mjs";

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

  it("omits the expiry when expires_iso is absent, for both old and new servers", () => {
    const ready = describeUpload({ machine: "X", state: "ready", counts: { new: 1 } });
    assert.equal(ready, "X sent TS changes to review: 1 new.");
    const checking = describeUpload({ machine: "X", state: "checking" });
    assert.equal(checking, "X sent TS changes. ACP is still reading them.");
  });

  it("appends the expiry when expires_iso is present", () => {
    const now = new Date(2026, 8, 25, 9, 0);
    const text = describeUpload(
      { machine: "X", state: "ready", counts: { new: 1 }, expires_iso: "2026-09-26T05:40:00" },
      now,
    );
    assert.equal(text, "X sent TS changes to review: 1 new. Expires 5:40 am tomorrow.");
  });

  it("appends the expiry to a still-checking upload too", () => {
    const now = new Date(2026, 8, 25, 9, 0);
    const text = describeUpload(
      { machine: "X", state: "checking", expires_iso: "2026-09-25T15:40:00" },
      now,
    );
    assert.equal(text, "X sent TS changes. ACP is still reading them. Expires 3:40 pm today.");
  });
});

describe("formatExpiry", () => {
  const now = new Date(2026, 8, 25, 9, 0);

  it("says today for a time later the same day", () => {
    assert.equal(formatExpiry("2026-09-25T15:40:00", now), "3:40 pm today");
  });

  it("says tomorrow for a time the following calendar day", () => {
    assert.equal(formatExpiry("2026-09-26T05:40:00", now), "5:40 am tomorrow");
  });

  it("names the date further out", () => {
    // Locale is pinned to en-AU in formatExpiry, so this reads the same
    // regardless of the machine's default locale (see PR #100 CI failure:
    // the GitHub runner's en-US default produced "Sep 30" instead).
    const text = formatExpiry("2026-09-30T12:00:00", now);
    assert.equal(text, "12:00 pm on Wed, 30 Sept");
  });

  it("returns null for a missing or unparseable timestamp", () => {
    assert.equal(formatExpiry(undefined, now), null);
    assert.equal(formatExpiry("not-a-date", now), null);
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
