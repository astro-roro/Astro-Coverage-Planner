import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  MAX_RADIUS_DEG,
  VIZIER_CATALOGUES,
  angSepArcmin,
  buildSimbadAdql,
  buildVizierUrl,
  cleanSimbadName,
  dedupe,
  filterBySize,
  formatArcmin,
  listRigs,
  minSizeArcmin,
  normaliseSimbad,
  normaliseVizier,
  outlinePoints,
  parseVizierTsv,
  pixelScale,
  rigInfo,
  searchAll,
  searchSimbad,
  viewCone,
  withDistances,
} from "../../static/sky-search.mjs";

const fx = JSON.parse(readFileSync(new URL("../fixtures/sky_search_m42.json", import.meta.url), "utf-8"));
const cat = key => VIZIER_CATALOGUES.find(c => c.key === key);

// Answers from the recording instead of the network.
function recordedFetch({ failSimbad = false, hang = false } = {}) {
  return async (url, init = {}) => {
    if (hang) return new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    if (String(url).includes("sim-tap")) {
      if (failSimbad) return new Response("busy", { status: 503 });
      const sized = String(init.body).includes("IS+NOT+NULL");
      return new Response(JSON.stringify(fx[sized ? "simbad-sized" : "simbad-unsized"]));
    }
    const source = decodeURIComponent(String(url).match(/-source=([^&]+)/)[1]);
    return new Response(fx.vizier[source] ?? "");
  };
}

describe("viewCone", () => {
  it("uses the half diagonal of the view as the radius", () => {
    const c = viewCone([83.8, -5.4], [6, 8]);
    assert.equal(c.radiusDeg, 5);
    assert.equal(c.ra, 83.8);
  });
  it("asks for a zoom when the view is wider than the cap", () => {
    const c = viewCone([0, 0], [30, 20]);
    assert.equal(c.tooWide, true);
    assert.ok(c.radiusDeg > MAX_RADIUS_DEG);
  });
  it("returns null for a view it cannot read", () => {
    assert.equal(viewCone(null, [1, 1]), null);
    assert.equal(viewCone([1, 1], [NaN, 1]), null);
  });
});

describe("angSepArcmin", () => {
  it("is zero for the same point and 60 for one degree", () => {
    assert.equal(angSepArcmin(10, 20, 10, 20), 0);
    assert.ok(Math.abs(angSepArcmin(10, 20, 10, 21) - 60) < 1e-6);
  });
  it("shrinks east-west separations with cos(dec)", () => {
    assert.ok(Math.abs(angSepArcmin(0, 60, 1, 60) - 30) < 0.1);
  });
});

describe("ADQL builder", () => {
  it("builds a cone and a type list for the sized query", () => {
    const q = buildSimbadAdql(83.82, -5.39, 0.7);
    assert.match(q, /^SELECT TOP 1000 main_id, otype, ra, dec, galdim_majaxis, galdim_minaxis, galdim_angle FROM basic WHERE /);
    assert.match(q, /CONTAINS\(POINT\('ICRS',ra,dec\),CIRCLE\('ICRS',83\.82000,-5\.39000,0\.70000\)\)=1/);
    assert.match(q, /galdim_majaxis IS NOT NULL/);
    assert.match(q, /otype IN \('HII'/);
    assert.match(q, /ORDER BY galdim_majaxis DESC$/);
  });
  it("limits the unsized query to types that are nebulae for certain", () => {
    const q = buildSimbadAdql(1, 2, 3, { sized: false });
    assert.match(q, /galdim_majaxis IS NULL/);
    assert.ok(!q.includes("'G'"));
    assert.ok(!q.includes("'DNe'"));
    assert.ok(q.includes("'PN'"));
  });
  it("never lets a non-number into the query", () => {
    const q = buildSimbadAdql("1'; DROP", 2, 3);
    assert.ok(!q.includes("DROP"));
  });
});

describe("VizieR url", () => {
  it("asks for the table, cone, columns and a row cap", () => {
    const u = buildVizierUrl(cat("sh2"), 83.8, -5.4, 2);
    assert.ok(u.startsWith("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?"));
    assert.ok(u.includes("-source=VII%2F20"));
    assert.ok(u.includes("-c=83.80000%2C-5.40000"));
    assert.ok(u.includes("-c.rd=2.00000"));
    assert.ok(u.includes("-out=Sh2%2CDiam"));
    assert.ok(u.includes("-out.max=500"));
  });
  it("sorts the dense WISE catalogue biggest first", () => {
    assert.ok(buildVizierUrl(cat("wise"), 0, 0, 1).includes("-sort=-Rad"));
    assert.ok(!buildVizierUrl(cat("sh2"), 0, 0, 1).includes("-sort"));
  });
});

describe("parseVizierTsv", () => {
  it("reads rows after the header, units and dashes lines", () => {
    const rows = parseVizierTsv("#c\n\n_RAJ2000\tSh2\tDiam\ndeg\t \tarcmin\n---\t---\t---\n83.8\t  281\t 90\n84.0\t282\t\n\n#x\n");
    assert.deepEqual(rows, [
      { _RAJ2000: "83.8", Sh2: "281", Diam: "90" },
      { _RAJ2000: "84.0", Sh2: "282", Diam: "" },
    ]);
  });
  it("gives no rows for an empty reply", () => {
    assert.deepEqual(parseVizierTsv(""), []);
    assert.deepEqual(parseVizierTsv("#only comments\n"), []);
  });
});

describe("catalogue normalisation", () => {
  const row = (key, o) => normaliseVizier(cat(key), [{ _RAJ2000: "10", _DEJ2000: "-5", ...o }])[0];
  it("names Sharpless objects and keeps the diameter", () => {
    const o = row("sh2", { Sh2: " 281", Diam: " 90" });
    assert.equal(o.name, "Sh2-281");
    assert.equal(o.major, 90);
    assert.equal(o.group, "emission");
  });
  it("turns an LDN area in square degrees into a diameter in arcmin", () => {
    const o = row("ldn", { LDN: "1641", Area: "1.000" });
    assert.ok(Math.abs(o.major - 2 * Math.sqrt(1 / Math.PI) * 60) < 1e-9);
  });
  it("doubles the van den Bergh radii", () => {
    const o = row("vdb", { VdB: "52", BRadMax: "4.5", RRadMax: "" });
    assert.equal(o.major, 9);
    assert.equal(o.minor, null);
  });
  it("converts MASH diameters from arcsec", () => {
    const o = row("mash1", { PNG: "G355.6-02.3 ", Name: "PHR1744-3355 ", MajDiam: "57.0", MinDiam: "25.0" });
    assert.equal(o.name, "PHR1744-3355");
    assert.equal(o.major, 57 / 60);
    assert.deepEqual(o.aliases, ["PN G355.6-02.3"]);
  });
  it("takes the last WISE name and doubles the radius", () => {
    const o = row("wise", { WISE: "G209.010-19.380", Rad: "600", HIIName: "G209.010-19.380; Orion A", Name: "" });
    assert.equal(o.name, "Orion A");
    assert.equal(o.major, 20);
    assert.ok(o.aliases.includes("WISE G209.010-19.380"));
  });
  it("keeps Green names as aliases", () => {
    const o = row("green", { SNR: "G000.0+00.0", MajDiam: "3.5", MinDiam: "2.5", Names: "Sgr A East" });
    assert.equal(o.name, "SNR G000.0+00.0");
    assert.deepEqual(o.aliases, ["Sgr A East"]);
  });
  it("drops rows with no position and zero or missing sizes", () => {
    assert.equal(normaliseVizier(cat("sh2"), [{ _RAJ2000: "", _DEJ2000: "1", Sh2: "1" }]).length, 0);
    assert.equal(row("sh2", { Sh2: "5", Diam: "0" }).major, null);
  });
  it("normalises every catalogue from the recorded replies without throwing", () => {
    for (const c of VIZIER_CATALOGUES) {
      const items = normaliseVizier(c, parseVizierTsv(fx.vizier[c.source] ?? ""));
      for (const o of items) {
        assert.ok(o.name && Number.isFinite(o.ra) && Number.isFinite(o.dec), `${c.key}: ${JSON.stringify(o)}`);
      }
    }
  });
});

describe("SIMBAD normalisation", () => {
  const reply = {
    metadata: [{ name: "main_id" }, { name: "otype" }, { name: "ra" }, { name: "dec" },
      { name: "galdim_majaxis" }, { name: "galdim_minaxis" }, { name: "galdim_angle" }],
    data: [
      ["NAME Orion Nebula", "HII", 83.82, -5.39, 90, 60, 45],
      ["M  31", "G", 10.68, 41.27, 190, 60, 35],
      ["* alf Ori", "*", 88.8, 7.4, null, null, null],
      ["Sh 2-1", "HII", 1, 2, null, null, null],
    ],
  };
  it("maps types to groups, strips NAME and drops types it does not list", () => {
    const items = normaliseSimbad(reply);
    assert.deepEqual(items.map(i => i.name), ["Orion Nebula", "M 31", "Sh 2-1"]);
    assert.deepEqual(items.map(i => i.group), ["emission", "galaxy", "emission"]);
    assert.equal(items[0].pa, 45);
    assert.equal(items[2].major, null);
  });
  it("cleans names", () => {
    assert.equal(cleanSimbadName("NAME  Crab   Nebula"), "Crab Nebula");
  });
});

describe("rigs and size filter", () => {
  const gear = {
    telescopes: [{ id: "t1", name: "RedCat", focal_length_mm: 250, default_camera_id: "c2" }],
    cameras: [
      { id: "c1", name: "ASI294", pixel_size_um: 4.63, sensor_px: [4144, 2822] },
      { id: "c2", name: "ASI2600", pixel_size_um: 3.76, sensor_px: [6248, 4176] },
      { id: "c3", name: "Broken", pixel_size_um: 0, sensor_px: [10, 10] },
    ],
  };
  it("computes plate scale the way the plan editor does", () => {
    assert.ok(Math.abs(pixelScale(250, 3.76) - 206.265 * 3.76 / 250) < 1e-9);
    assert.equal(pixelScale(0, 3.76), 0);
  });
  it("computes the field of view in arcmin", () => {
    const r = rigInfo(gear.telescopes[0], gear.cameras[1]);
    assert.ok(Math.abs(r.fovW - 6248 * r.scale / 60) < 1e-9);
    assert.ok(Math.abs(r.fovW - 322.9) < 0.2);
  });
  it("lists each telescope with each camera, default camera first, skipping unusable pairs", () => {
    const rigs = listRigs(gear);
    assert.deepEqual(rigs.map(r => r.cameraId), ["c2", "c1"]);
    assert.equal(rigs[0].label, "RedCat + ASI2600");
  });
  it("turns a pixel minimum into arcmin", () => {
    assert.ok(Math.abs(minSizeArcmin(3.1, 20) - 62 / 60) < 1e-9);
    assert.equal(minSizeArcmin(0, 20), 0);
    assert.equal(minSizeArcmin(3.1, 0), 0);
  });
  it("hides small objects, keeps unknown sizes, and keeps all when no minimum is set", () => {
    const items = [{ major: 0.5 }, { major: 2 }, { major: null }, { major: 1 }];
    assert.deepEqual(filterBySize(items, 1).map(o => o.major), [2, null, 1]);
    assert.equal(filterBySize(items, 0).length, 4);
  });
});

describe("dedupe", () => {
  const o = (name, ra, dec, major, extra = {}) => ({
    name, aliases: [], group: "emission", typeLabel: "HII region", catalogue: "X", catalogues: ["X"],
    ra, dec, major, minor: null, pa: null, ...extra,
  });
  it("merges the same object from several catalogues and lists the other names", () => {
    const out = dedupe([
      o("Sh2-281", 83.82, -5.39, 90, { catalogues: ["Sharpless"] }),
      o("LBN 974", 83.83, -5.40, 85, { catalogues: ["LBN"] }),
      o("M 42", 83.82, -5.39, null, { catalogues: ["SIMBAD"], typeLabel: "HII region (SIMBAD)" }),
    ]);
    assert.equal(out.length, 1);
    assert.equal(out[0].name, "M 42");
    assert.deepEqual(out[0].aliases.sort(), ["LBN 974", "Sh2-281"]);
    assert.deepEqual(out[0].catalogues.sort(), ["LBN", "SIMBAD", "Sharpless"]);
    assert.equal(out[0].major, 90);
  });
  it("keeps the larger size as the primary and fills a missing size", () => {
    const out = dedupe([o("A", 10, 10, null), o("B", 10, 10.001, 12)]);
    assert.equal(out[0].major, 12);
  });
  it("does not merge objects further apart than the reach", () => {
    // 1 arcmin reach for a small object: 2 arcmin apart stays separate.
    const out = dedupe([o("A", 10, 10, 3), o("B", 10, 10 + 2 / 60, 3)]);
    assert.equal(out.length, 2);
  });
  it("uses 0.2 x size as the reach for big objects", () => {
    const out = dedupe([o("Big", 10, 10, 100), o("Near", 10, 10 + 15 / 60, 90)]);
    assert.equal(out.length, 1);
  });
  it("does not let a big object swallow a much smaller one", () => {
    const out = dedupe([o("Big", 10, 10, 100), o("Small", 10, 10 + 5 / 60, 10)]);
    assert.equal(out.length, 2);
  });
  it("does not merge across families", () => {
    const out = dedupe([o("Cloud", 10, 10, 30, { group: "dark" }), o("PN", 10, 10, null, { group: "pn" })]);
    assert.equal(out.length, 2);
  });
  it("prefers a recognisable name over a survey code", () => {
    const out = dedupe([
      o("[MML2017] 4808", 10, 10, 20),
      o("NGC 1976", 10, 10, null),
      o("Sh2-281", 10, 10, null),
    ]);
    assert.equal(out[0].name, "NGC 1976");
  });
  it("does not change its input", () => {
    const a = o("A", 10, 10, 5);
    dedupe([a, o("B", 10, 10, 4)]);
    assert.deepEqual(a.aliases, []);
  });
});

describe("withDistances and outline", () => {
  it("adds the distance from the view centre", () => {
    const [x] = withDistances([{ ra: 10, dec: 11 }], [10, 10]);
    assert.ok(Math.abs(x.distArcmin - 60) < 1e-6);
  });
  it("draws an ellipse with the right extent along the position angle", () => {
    const pts = outlinePoints(100, 0, 120, 60, 90, 72);
    const ras = pts.map(p => p[0]), decs = pts.map(p => p[1]);
    // PA 90 puts the 2 degree major axis east-west.
    assert.ok(Math.abs((Math.max(...ras) - Math.min(...ras)) - 2) < 0.01);
    assert.ok(Math.abs((Math.max(...decs) - Math.min(...decs)) - 1) < 0.01);
  });
  it("draws a circle of the major size when there is no minor axis, and a small circle with no size", () => {
    const c = outlinePoints(100, 0, 60, null, null, 36);
    assert.ok(Math.abs((Math.max(...c.map(p => p[1])) - Math.min(...c.map(p => p[1]))) - 1) < 0.01);
    const d = outlinePoints(100, 0, null, null, null, 36);
    assert.ok(Math.max(...d.map(p => p[1])) - Math.min(...d.map(p => p[1])) < 0.1);
  });
  it("formats sizes", () => {
    assert.equal(formatArcmin(null), "size unknown");
    assert.equal(formatArcmin(120), "120'");
    assert.equal(formatArcmin(2.5), "2.5'");
    assert.equal(formatArcmin(0.5), "30\"");
  });
});

describe("searching with recorded replies", () => {
  const cone = fx.cone;
  it("collects every source and finds objects near M42", async () => {
    const seen = [];
    const res = await searchAll(cone, { fetchImpl: recordedFetch() }, r => seen.push(r.label));
    assert.equal(res.length, 1 + VIZIER_CATALOGUES.length);
    assert.equal(seen.length, res.length);
    assert.ok(res.every(r => r.ok));
    const merged = dedupe(res.flatMap(r => r.items));
    const names = merged.flatMap(m => [m.name, ...m.aliases]);
    assert.ok(names.includes("Sh2-281"));
    assert.ok(names.includes("LDN 1640"));
    const m42 = merged.find(m => [m.name, ...m.aliases].includes("Sh2-281"));
    assert.ok(m42.catalogues.length >= 2, "Sharpless 281 merges with at least one other source");
  });
  it("reports a failed source without losing the others", async () => {
    const res = await searchAll(cone, { fetchImpl: recordedFetch({ failSimbad: true }) });
    const simbad = res.find(r => r.label === "SIMBAD");
    assert.equal(simbad.ok, false);
    assert.match(simbad.error, /503/);
    assert.ok(res.filter(r => r.ok).length >= VIZIER_CATALOGUES.length);
  });
  it("gives up on a source that hangs", async () => {
    const r = await searchSimbad(cone, { fetchImpl: recordedFetch({ hang: true }), timeoutMs: 20 });
    assert.equal(r.ok, false);
    assert.match(r.error, /timed out/);
  });
  it("flags a reply that hit the row cap", async () => {
    const rows = Array.from({ length: 1000 }, (_, i) => ["x" + i, "HII", 1, 1, 5, null, null]);
    const full = { metadata: fx["simbad-sized"].metadata, data: rows };
    const fetchImpl = async () => new Response(JSON.stringify(full));
    const r = await searchSimbad(cone, { fetchImpl });
    assert.equal(r.truncated, true);
  });
});
