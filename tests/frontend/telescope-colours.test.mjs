import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  TELESCOPE_FALLBACK,
  TELESCOPE_PALETTE,
  UNKNOWN_TELESCOPE,
  assignTelescopeColours,
  autoTelescopeColour,
  nameHash,
} from "../../static/telescope-colours.mjs";

const RIGS = ["RedCat 51", "Askar FRA400", "Edge HD 8", "Samyang 135"];

describe("assignTelescopeColours", () => {
  it("gives the same answer whatever order the names arrive in", () => {
    assert.deepEqual(assignTelescopeColours(RIGS), assignTelescopeColours([...RIGS].reverse()));
  });

  it("does not reshuffle existing telescopes when an unrelated one is added", () => {
    const before = assignTelescopeColours(RIGS);
    // Pick a newcomer whose home slot none of the existing rigs use, so the
    // only thing that could move them is the old sort-by-name behaviour.
    const used = new Set(Object.values(before));
    let extra = null;
    for (let i = 0; i < 500 && !extra; i++) {
      const name = `Aardvark scope ${i}`;
      if (!used.has(autoTelescopeColour(name))) extra = name;
    }
    assert.ok(extra, "found a newcomer with a free home colour");
    const after = assignTelescopeColours([...RIGS, extra]);
    for (const name of RIGS) assert.equal(after[name], before[name], name);
  });

  it("gives different telescopes different colours while the palette lasts", () => {
    const names = Array.from({ length: TELESCOPE_PALETTE.length }, (_, i) => `Scope ${i}`);
    const colours = Object.values(assignTelescopeColours(names));
    assert.equal(new Set(colours).size, names.length);
  });

  it("uses a saved pick and keeps it out of the automatic pool", () => {
    const home = autoTelescopeColour("RedCat 51");
    const got = assignTelescopeColours(RIGS, { "Samyang 135": home.toUpperCase() });
    assert.equal(got["Samyang 135"], home.toLowerCase());
    assert.notEqual(got["RedCat 51"], home, "RedCat moves off the colour Samyang claimed");
  });

  it("ignores a saved value that is not #rrggbb", () => {
    const got = assignTelescopeColours(["RedCat 51"], { "RedCat 51": "red;background:url(x)" });
    assert.equal(got["RedCat 51"], autoTelescopeColour("RedCat 51"));
  });

  it("keeps Unknown grey", () => {
    assert.equal(assignTelescopeColours([UNKNOWN_TELESCOPE])[UNKNOWN_TELESCOPE], TELESCOPE_FALLBACK);
  });

  it("still colours everything when there are more telescopes than colours", () => {
    const names = Array.from({ length: TELESCOPE_PALETTE.length + 5 }, (_, i) => `Scope ${i}`);
    const got = assignTelescopeColours(names);
    for (const n of names) assert.ok(TELESCOPE_PALETTE.includes(got[n]), n);
  });
});

describe("palette", () => {
  it("has no repeats and only #rrggbb values", () => {
    assert.equal(new Set(TELESCOPE_PALETTE).size, TELESCOPE_PALETTE.length);
    for (const c of TELESCOPE_PALETTE) assert.match(c, /^#[0-9a-f]{6}$/);
  });

  it("reads on the dark sky: every colour is light enough to see", () => {
    const lum = hex => {
      const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    // Contrast against the app's page background, #0a0d12.
    const bg = lum("#0a0d12");
    for (const c of TELESCOPE_PALETTE) {
      const ratio = (lum(c) + 0.05) / (bg + 0.05);
      assert.ok(ratio >= 4.5, `${c} contrast ${ratio.toFixed(2)}`);
    }
  });

  it("keeps every pair of colours far enough apart to tell them apart", () => {
    const lin = v => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    const lab = hex => {
      const [r, g, b] = [1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
      const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
      const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
      const f = t => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
      return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
    };
    for (let i = 0; i < TELESCOPE_PALETTE.length; i++) {
      for (let j = i + 1; j < TELESCOPE_PALETTE.length; j++) {
        const a = lab(TELESCOPE_PALETTE[i]), b = lab(TELESCOPE_PALETTE[j]);
        const d = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        assert.ok(d >= 15, `${TELESCOPE_PALETTE[i]} and ${TELESCOPE_PALETTE[j]} are ${d.toFixed(1)} apart`);
      }
    }
  });

  it("hashes a name the same way every time", () => {
    assert.equal(nameHash("RedCat 51"), nameHash("RedCat 51"));
    assert.notEqual(nameHash("RedCat 51"), nameHash("RedCat 61"));
  });
});
