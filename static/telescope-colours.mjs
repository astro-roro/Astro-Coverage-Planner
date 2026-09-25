// Telescope colours for coverage footprints, legend swatches and plan outlines.
//
// A colour Rohan picks is saved in gear.json under telescope_colours and always
// wins. Every other telescope gets an automatic colour from a hash of its name,
// so adding a telescope no longer reshuffles the rest the way sorting by name
// did. When two names hash to the same slot, the later one alphabetically moves
// on to the next free colour, and picked colours are kept out of the automatic
// pool. A new telescope can therefore move an existing one only when both want
// the same slot and the new name sorts first. Picking a colour pins it for good.

// Muted, mid-light colours that read on the dark sky without glaring. Order
// alternates warm and cool so a probe to the next slot changes hue a lot. No
// two sit closer than 15 in CIE Lab (tests/frontend/telescope-colours.test.mjs).
export const TELESCOPE_PALETTE = [
  "#6fa8dc", // soft blue
  "#e0a370", // apricot
  "#7cc4a0", // sage
  "#c39bd3", // lavender
  "#d9c36a", // straw
  "#6cbcc4", // teal
  "#e08e9b", // rose
  "#a3b86c", // olive
  "#9aa5e6", // periwinkle
  "#ef8a62", // coral
  "#e9e4d4", // ivory
  "#c5e17a", // lime
];
export const TELESCOPE_FALLBACK = "#888888";
export const UNKNOWN_TELESCOPE = "Unknown";

const HEX = /^#[0-9a-fA-F]{6}$/;

// FNV-1a over UTF-16 code units: small, stable across browsers and releases.
export function nameHash(name) {
  let h = 0x811c9dc5;
  const s = String(name ?? "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

// The automatic colour a name would get with nothing else competing for it.
export function autoTelescopeColour(name) {
  return TELESCOPE_PALETTE[nameHash(name) % TELESCOPE_PALETTE.length];
}

// names: telescope names to colour. chosen: {name: "#rrggbb"} saved picks.
// Returns {name: colour}. "Unknown" is always the fallback grey.
export function assignTelescopeColours(names, chosen = {}) {
  const out = {};
  const taken = new Set();
  const auto = [];
  for (const name of [...new Set(names)].sort()) {
    if (name === UNKNOWN_TELESCOPE) { out[name] = TELESCOPE_FALLBACK; continue; }
    const pick = chosen && chosen[name];
    if (typeof pick === "string" && HEX.test(pick)) {
      out[name] = pick.toLowerCase();
      taken.add(pick.toLowerCase());
    } else {
      auto.push(name);
    }
  }
  const n = TELESCOPE_PALETTE.length;
  for (const name of auto) {
    const home = nameHash(name) % n;
    let colour = TELESCOPE_PALETTE[home];
    for (let k = 0; k < n; k++) {
      const c = TELESCOPE_PALETTE[(home + k) % n];
      if (!taken.has(c)) { colour = c; break; }
    }
    // More telescopes than colours: share the home colour rather than fail.
    out[name] = colour;
    taken.add(colour);
  }
  return out;
}
