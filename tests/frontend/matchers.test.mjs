import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  tokenizeSearch,
  targetMatchesSearch,
  catalogObjectMatchesTokens,
} from "../../static/search.mjs";

const mkTarget = (over = {}) => ({
  objects: ["M31"],
  telescopes: ["RASA 11"],
  cameras: ["ASI2600MM"],
  fov_arcmin: [60, 40],
  filters: {
    Ha: { total_hours: 5 },
    OIII: { total_hours: 0 },
    SII: { total_hours: 2 },
  },
  ...over,
});

const matches = (t, q) => targetMatchesSearch(t, tokenizeSearch(q));
const catMatches = (obj, q) =>
  catalogObjectMatchesTokens(obj, tokenizeSearch(q));

describe("targetMatchesSearch — empty + AND semantics", () => {
  it("returns true when no tokens are present", () => {
    assert.equal(matches(mkTarget(), ""), true);
  });

  it("ANDs multiple tokens — all must match", () => {
    const t = mkTarget();
    assert.equal(matches(t, "m31 rasa"), true);
    assert.equal(matches(t, "m31 ngc7000"), false);
  });
});

describe("targetMatchesSearch — bareword text scope", () => {
  it("matches against any of objects, telescopes, or cameras", () => {
    const t = mkTarget();
    assert.equal(matches(t, "m31"), true, "object");
    assert.equal(matches(t, "rasa"), true, "telescope");
    assert.equal(matches(t, "asi2600"), true, "camera");
    assert.equal(matches(t, "qhy"), false, "none");
  });
});

describe("targetMatchesSearch — key:value", () => {
  const t = mkTarget();

  it("object:/name: only checks the objects field", () => {
    assert.equal(matches(t, "object:m31"), true);
    assert.equal(matches(t, "object:rasa"), false, "telescope shouldn't satisfy object:");
    assert.equal(matches(t, "name:m31"), true);
  });

  it("filter:X requires X exists with >0 hours", () => {
    assert.equal(matches(t, "filter:Ha"), true);
    assert.equal(matches(t, "filter:SII"), true);
    assert.equal(matches(t, "filter:OIII"), false, "0-hour filter should not match");
    assert.equal(matches(t, "filter:LRGB"), false, "absent filter");
  });

  it("telescope:/tel: only check telescopes", () => {
    assert.equal(matches(t, "telescope:rasa"), true);
    assert.equal(matches(t, "tel:rasa"), true);
    assert.equal(matches(t, "tel:asi"), false);
  });

  it("camera:/cam: only check cameras", () => {
    assert.equal(matches(t, "camera:asi"), true);
    assert.equal(matches(t, "cam:asi"), true);
    assert.equal(matches(t, "cam:rasa"), false);
  });

  it("class: and tag: are neutral on targets (catalogue-only keys)", () => {
    // A shared search bar that targets catalogues with class:PNe shouldn't
    // hide every target in the planning rail.
    assert.equal(matches(t, "class:PNe"), true);
    assert.equal(matches(t, "tag:needs-work"), true);
  });
});

describe("targetMatchesSearch — numeric comparators", () => {
  const t = mkTarget();

  it("hours> compares against summed filter total_hours", () => {
    // sum = 5 + 0 + 2 = 7
    assert.equal(matches(t, "hours>3"), true);
    assert.equal(matches(t, "hours>10"), false);
    assert.equal(matches(t, "hours<10"), true);
  });

  it("fov> uses the max of fov_arcmin", () => {
    // max = 60
    assert.equal(matches(t, "fov>30"), true);
    assert.equal(matches(t, "fov>100"), false);
    assert.equal(matches(t, "fov<100"), true);
  });

  it("missing fov_arcmin treats fovMax as 0", () => {
    const t2 = mkTarget({ fov_arcmin: [] });
    assert.equal(matches(t2, "fov>1"), false);
    assert.equal(matches(t2, "fov<1"), true);
  });
});

describe("targetMatchesSearch — negation", () => {
  const t = mkTarget();

  it("excludes targets matching the negated token", () => {
    assert.equal(matches(t, "-m31"), false);
    assert.equal(matches(t, "-ngc7000"), true);
  });

  it("negates kv tokens", () => {
    assert.equal(matches(t, "-tel:rasa"), false);
    assert.equal(matches(t, "-tel:edge"), true);
  });

  it("negates numeric comparators", () => {
    assert.equal(matches(t, "-hours>3"), false);
    assert.equal(matches(t, "-hours>100"), true);
  });
});

describe("targetMatchesSearch — defensive defaults", () => {
  it("handles a target missing optional arrays", () => {
    const t = { filters: {} };
    assert.equal(targetMatchesSearch(t, tokenizeSearch("")), true);
    assert.equal(matches(t, "m31"), false);
    assert.equal(matches(t, "filter:Ha"), false);
  });
});

const mkObj = (over = {}) => ({
  name: "M31",
  category: "galaxy",
  tags: ["bright"],
  ...over,
});

describe("catalogObjectMatchesTokens — empty + AND", () => {
  it("returns true when no tokens", () => {
    assert.equal(catMatches(mkObj(), ""), true);
  });

  it("ANDs tokens", () => {
    const o = mkObj();
    assert.equal(catMatches(o, "m31 class:galaxy"), true);
    assert.equal(catMatches(o, "m31 class:PNe"), false);
  });
});

describe("catalogObjectMatchesTokens — semantics", () => {
  it("text is a substring match on name (case-insensitive)", () => {
    const o = mkObj({ name: "NGC 7635 (Bubble Nebula)" });
    assert.equal(catMatches(o, "bubble"), true);
    assert.equal(catMatches(o, "7635"), true);
    assert.equal(catMatches(o, "lagoon"), false);
  });

  it("class: is exact category match", () => {
    const o = mkObj({ category: "PNe" });
    assert.equal(catMatches(o, "class:PNe"), true);
    assert.equal(catMatches(o, "class:pn"), false, "exact, not substring");
  });

  it("tag: checks tag membership (case-insensitive)", () => {
    const o = mkObj({ tags: ["large", "Bright"] });
    assert.equal(catMatches(o, "tag:large"), true);
    assert.equal(catMatches(o, "tag:bright"), true);
    assert.equal(catMatches(o, "tag:faint"), false);
  });

  it("name:/object: are substring matches on name", () => {
    const o = mkObj({ name: "Orion Nebula" });
    assert.equal(catMatches(o, "name:orion"), true);
    assert.equal(catMatches(o, "object:nebula"), true);
    assert.equal(catMatches(o, "name:crab"), false);
  });

  it("planning-only kv keys (filter, tel, cam) are neutral", () => {
    // A shared search bar targeting the rail with tel:rasa shouldn't hide
    // every catalogue object.
    const o = mkObj();
    assert.equal(catMatches(o, "tel:rasa"), true);
    assert.equal(catMatches(o, "filter:Ha"), true);
    assert.equal(catMatches(o, "cam:asi"), true);
  });

  it("cmp tokens are neutral (catalog objects have no fov/hours yet)", () => {
    const o = mkObj();
    assert.equal(catMatches(o, "hours>100"), true);
    assert.equal(catMatches(o, "fov>1000"), true);
  });
});

describe("catalogObjectMatchesTokens — negation", () => {
  it("excludes matching name", () => {
    const o = mkObj();
    assert.equal(catMatches(o, "-m31"), false);
    assert.equal(catMatches(o, "-crab"), true);
  });

  it("excludes matching class", () => {
    const o = mkObj({ category: "PNe" });
    assert.equal(catMatches(o, "-class:PNe"), false);
    assert.equal(catMatches(o, "-class:SNR"), true);
  });

  it("excludes matching tag", () => {
    const o = mkObj({ tags: ["small"] });
    assert.equal(catMatches(o, "-tag:small"), false);
    assert.equal(catMatches(o, "-tag:large"), true);
  });
});

describe("catalogObjectMatchesTokens — defensive defaults", () => {
  it("handles missing name/category/tags", () => {
    const o = {};
    assert.equal(catMatches(o, ""), true);
    assert.equal(catMatches(o, "m31"), false);
    assert.equal(catMatches(o, "class:any"), false);
    assert.equal(catMatches(o, "tag:any"), false);
  });
});
