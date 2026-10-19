import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  SITE_PROMPT_SEEN_KEY,
  coordsForForm,
  isBuiltInDefaultSite,
  markPromptSeen,
  readPromptSeen,
  shouldPromptForSite,
} from "../../static/site-prompt.mjs";

const maunaKea = { id: "mauna_kea", name: "Mauna Kea, Hawaii", lat: 19.82, lon: -155.47 };
const home = { id: "home", name: "Home", lat: -33.87, lon: 151.21 };

describe("isBuiltInDefaultSite", () => {
  it("matches the seeded Mauna Kea site", () => {
    assert.equal(isBuiltInDefaultSite(maunaKea), true);
  });
  it("does not match a user site", () => {
    assert.equal(isBuiltInDefaultSite(home), false);
  });
  it("does not match Mauna Kea after the user edited its coordinates", () => {
    assert.equal(isBuiltInDefaultSite({ ...maunaKea, lat: -33.87, lon: 151.21 }), false);
  });
  it("copes with no site", () => {
    assert.equal(isBuiltInDefaultSite(null), false);
    assert.equal(isBuiltInDefaultSite(undefined), false);
  });
});

describe("shouldPromptForSite", () => {
  it("prompts when turning on with the default site and not seen", () => {
    assert.equal(shouldPromptForSite({ turningOn: true, site: maunaKea, seen: false }), true);
  });
  it("does not prompt when turning off", () => {
    assert.equal(shouldPromptForSite({ turningOn: false, site: maunaKea, seen: false }), false);
  });
  it("does not prompt once seen", () => {
    assert.equal(shouldPromptForSite({ turningOn: true, site: maunaKea, seen: true }), false);
  });
  it("does not prompt for a user site", () => {
    assert.equal(shouldPromptForSite({ turningOn: true, site: home, seen: false }), false);
  });
});

describe("prompt seen flag", () => {
  const fake = () => {
    const m = new Map();
    return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
  };
  it("round trips under acp.site_prompt_seen", () => {
    const s = fake();
    assert.equal(readPromptSeen(s), false);
    markPromptSeen(s);
    assert.equal(s.getItem("acp.site_prompt_seen"), "1");
    assert.equal(SITE_PROMPT_SEEN_KEY, "acp.site_prompt_seen");
    assert.equal(readPromptSeen(s), true);
  });
  it("survives storage that throws", () => {
    const boom = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    assert.equal(readPromptSeen(boom), false);
    assert.doesNotThrow(() => markPromptSeen(boom));
  });
});

describe("coordsForForm", () => {
  it("rounds to four decimals as strings", () => {
    assert.deepEqual(coordsForForm({ latitude: -33.868819, longitude: 151.209296 }),
      { lat: "-33.8688", lon: "151.2093" });
  });
  it("rejects unusable coordinates", () => {
    assert.equal(coordsForForm(null), null);
    assert.equal(coordsForForm({ latitude: NaN, longitude: 1 }), null);
    assert.equal(coordsForForm({ latitude: 95, longitude: 1 }), null);
    assert.equal(coordsForForm({ latitude: 1, longitude: 200 }), null);
  });
});
