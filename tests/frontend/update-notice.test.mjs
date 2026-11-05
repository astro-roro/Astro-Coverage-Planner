import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { noticeFor, dismissedVersion, dismiss, DISMISS_KEY } from "../../static/update-notice.mjs";

const URL = "https://github.com/astro-roro/Astro-Coverage-Planner/releases/tag/v1.2.0";
const info = (over = {}) => ({
  available: true, current: "1.1.1", latest: "1.2.0", url: URL, jump: "minor", docker: false, ...over,
});

function memoryStorage() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v) };
}

describe("noticeFor", () => {
  it("shows nothing when no update is known", () => {
    assert.equal(noticeFor(null, null), null);
    assert.equal(noticeFor({ available: false }, null), null);
    assert.equal(noticeFor(info({ jump: null }), null), null);
    assert.equal(noticeFor(info({ jump: "huge" }), null), null);
    assert.equal(noticeFor(info({ latest: null }), null), null);
  });

  it("a patch is soft: no banner, only the help line and dot", () => {
    const n = noticeFor(info({ jump: "patch", latest: "1.1.2" }), null);
    assert.equal(n.level, "patch");
    assert.equal(n.banner, false);
    assert.equal(n.dot, true);
  });

  it("a minor shows a banner", () => {
    const n = noticeFor(info(), null);
    assert.equal(n.level, "minor");
    assert.equal(n.banner, true);
  });

  it("a major shows a banner at the loudest level", () => {
    const n = noticeFor(info({ jump: "major", latest: "2.0.0" }), null);
    assert.equal(n.level, "major");
    assert.equal(n.banner, true);
  });

  it("names the new version and links to its release page", () => {
    const n = noticeFor(info(), null);
    assert.match(n.text, /1\.2\.0/);
    assert.equal(n.url, URL);
    assert.equal(n.linkText, "Download");
  });

  it("tells a Docker install to pull the new image", () => {
    const n = noticeFor(info({ docker: true }), null);
    assert.match(n.text, /Pull the new image/);
    assert.doesNotMatch(noticeFor(info(), null).text, /Pull/);
  });

  it("drops a link that does not point at GitHub", () => {
    assert.equal(noticeFor(info({ url: "https://example.com/x" }), null).url, null);
    assert.equal(noticeFor(info({ url: "javascript:alert(1)" }), null).url, null);
  });
});

describe("dismissal", () => {
  it("hides that version only; a newer release shows again", () => {
    const store = memoryStorage();
    dismiss(store, "1.2.0");
    assert.equal(store.getItem(DISMISS_KEY), "1.2.0");
    assert.equal(noticeFor(info(), dismissedVersion(store)), null);
    assert.notEqual(noticeFor(info({ latest: "1.3.0" }), dismissedVersion(store)), null);
  });

  it("survives storage that throws", () => {
    const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    assert.equal(dismissedVersion(broken), null);
    assert.doesNotThrow(() => dismiss(broken, "1.2.0"));
  });
});
