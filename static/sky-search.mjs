// "What's here?" search: what imaging targets sit in the part of the sky the
// map is showing. Everything here is pure or takes its fetch as an argument,
// so the tests can feed it recorded replies instead of calling CDS.
//
// Two kinds of source, both queried straight from the browser (both answer
// with Access-Control-Allow-Origin: *, checked 2026-10-18, so no proxy):
//   - SIMBAD TAP (ADQL), restricted to extended object types.
//   - VizieR cone search (ASU, tab-separated) over named nebula catalogues.
// The VizieR TAP service answered 503 for a whole session while its cone
// search was fine, which is why the cone search is used.

import { tangentOffsetToRaDec } from "./sky-geometry.mjs";

const D2R = Math.PI / 180;

export const SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync";
export const VIZIER_ASU = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv";

// Half the view's diagonal may not pass this. Wider than that and the answer
// is a list of thousands, so the panel asks for a zoom instead.
export const MAX_RADIUS_DEG = 15;
export const DEFAULT_MIN_PX = 20;
const SIMBAD_LIMIT = 1000;
// A giant object does not swallow everything within a fifth of its size.
const MAX_MERGE_ARCMIN = 30;
const VIZIER_LIMIT = 500;

// Display order and names for the groups the results are filed under.
export const GROUPS = [
  { id: "emission", label: "Emission nebulae and HII regions" },
  { id: "reflection", label: "Reflection nebulae" },
  { id: "dark", label: "Dark nebulae and clouds" },
  { id: "pn", label: "Planetary nebulae" },
  { id: "snr", label: "Supernova remnants" },
  { id: "shell", label: "Bubbles and shells" },
  { id: "nebula", label: "Other nebulae" },
  { id: "galaxy", label: "Galaxies" },
];

// SIMBAD object-type codes (otypedef, checked 2026-10-18) per group, with the
// plain name shown in the Type column. Stars, clusters, radio and X-ray
// sources, dense cores and molecular clouds (thousands of CO clumps that are
// not imaging targets) are left out on purpose.
const SIMBAD_TYPES = {
  HII: ["emission", "HII region"],
  EmO: ["emission", "Emission object"],
  SFR: ["emission", "Star-forming region"],
  RNe: ["reflection", "Reflection nebula"],
  DNe: ["dark", "Dark nebula"],
  glb: ["dark", "Globule"],
  PN: ["pn", "Planetary nebula"],
  "PN?": ["pn", "Planetary nebula (candidate)"],
  SNR: ["snr", "Supernova remnant"],
  "SR?": ["snr", "Supernova remnant (candidate)"],
  bub: ["shell", "Bubble"],
  sh: ["shell", "Shell"],
  GNe: ["nebula", "Nebula"],
  ISM: ["nebula", "Interstellar medium"],
  flt: ["nebula", "Filament"],
  CGb: ["nebula", "Cometary globule"],
  G: ["galaxy", "Galaxy"],
  GiG: ["galaxy", "Galaxy"],
  GiP: ["galaxy", "Galaxy in pair"],
  GiC: ["galaxy", "Galaxy"],
  BiC: ["galaxy", "Brightest cluster galaxy"],
  IG: ["galaxy", "Interacting galaxies"],
  PaG: ["galaxy", "Pair of galaxies"],
  EmG: ["galaxy", "Emission-line galaxy"],
  SBG: ["galaxy", "Starburst galaxy"],
  bCG: ["galaxy", "Blue compact galaxy"],
  H2G: ["galaxy", "HII galaxy"],
  LSB: ["galaxy", "Low surface brightness galaxy"],
  SyG: ["galaxy", "Seyfert galaxy"],
  Sy1: ["galaxy", "Seyfert 1 galaxy"],
  Sy2: ["galaxy", "Seyfert 2 galaxy"],
  rG: ["galaxy", "Radio galaxy"],
  LIN: ["galaxy", "LINER galaxy"],
};

const num = v => {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};
const clean = v => (v === null || v === undefined ? "" : String(v).replace(/\s+/g, " ").trim());
const positive = v => (v !== null && v > 0 ? v : null);

// ---- view -> search cone -------------------------------------------------

// centre is [ra, dec] degrees and fov is [width, height] degrees, as Aladin's
// getRaDec() and getFoV() return them. Returns the cone to search, or
// { tooWide: true, radiusDeg } when the view is wider than MAX_RADIUS_DEG.
export function viewCone(centre, fov) {
  const [ra, dec] = centre || [];
  const [w, h] = fov || [];
  if (![ra, dec, w, h].every(Number.isFinite)) return null;
  const radiusDeg = Math.hypot(w, h) / 2;
  if (radiusDeg > MAX_RADIUS_DEG) return { tooWide: true, radiusDeg };
  return { ra, dec, radiusDeg };
}

export function angSepArcmin(ra1, dec1, ra2, dec2) {
  const p1 = dec1 * D2R, p2 = dec2 * D2R, dl = (ra2 - ra1) * D2R;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(a))) / D2R * 60;
}

// ---- queries -------------------------------------------------------------

function coneAdql(ra, dec, radiusDeg) {
  const f = x => Number(x).toFixed(5);
  return `CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',${f(ra)},${f(dec)},${f(radiusDeg)}))=1`;
}

// SIMBAD's TAP service has no way to sort nulls last, and most nebulae have no
// size on file. So there are two queries: objects with a size, biggest first,
// and objects without one, limited to types that are nebulae for certain. Dark
// clouds, globules and galaxies with no size are mostly noise and are skipped.
const UNSIZED_OK = ["HII", "EmO", "SFR", "RNe", "PN", "PN?", "SNR", "SR?", "bub", "sh", "GNe", "ISM", "flt"];

export function buildSimbadAdql(ra, dec, radiusDeg, { sized = true, limit = SIMBAD_LIMIT } = {}) {
  const list = ts => ts.map(t => `'${t}'`).join(",");
  const where = sized
    ? `galdim_majaxis IS NOT NULL AND otype IN (${list(Object.keys(SIMBAD_TYPES))})`
    : `galdim_majaxis IS NULL AND otype IN (${list(UNSIZED_OK)})`;
  return `SELECT TOP ${Math.floor(limit)} main_id, otype, ra, dec, galdim_majaxis, galdim_minaxis, galdim_angle `
    + `FROM basic WHERE ${coneAdql(ra, dec, radiusDeg)} AND ${where}`
    + (sized ? " ORDER BY galdim_majaxis DESC" : "");
}

// SIMBAD names carry a "NAME " prefix and padded catalogue numbers.
export function cleanSimbadName(id) {
  return clean(id).replace(/^NAME\s+/, "");
}

// A SIMBAD JSON reply ({ metadata: [{name}], data: [[...]] }) to objects.
export function normaliseSimbad(reply) {
  const cols = (reply?.metadata || []).map(m => m.name);
  const at = name => cols.indexOf(name);
  const out = [];
  for (const row of reply?.data || []) {
    const ra = num(row[at("ra")]), dec = num(row[at("dec")]);
    const info = SIMBAD_TYPES[clean(row[at("otype")])];
    if (ra === null || dec === null || !info) continue;
    const major = positive(num(row[at("galdim_majaxis")]));
    const minor = positive(num(row[at("galdim_minaxis")]));
    out.push({
      name: cleanSimbadName(row[at("main_id")]),
      aliases: [],
      group: info[0],
      typeLabel: info[1],
      catalogue: "SIMBAD",
      catalogues: ["SIMBAD"],
      ra, dec, major, minor: minor ?? null,
      pa: num(row[at("galdim_angle")]),
    });
  }
  return out;
}

// Parse VizieR's tab-separated cone-search reply: comment lines start with #,
// then a header row, a units row, a dashes row and the data.
export function parseVizierTsv(text) {
  const lines = String(text || "").split(/\r?\n/).filter(l => !l.startsWith("#"));
  const start = lines.findIndex(l => l.trim() !== "");
  if (start < 0) return [];
  const header = lines[start].split("\t").map(h => h.trim());
  const rows = [];
  for (const line of lines.slice(start + 3)) {
    if (line.trim() === "") { if (rows.length) break; continue; }
    const cells = line.split("\t");
    const row = {};
    header.forEach((h, i) => { row[h] = (cells[i] ?? "").trim(); });
    rows.push(row);
  }
  return rows;
}

const arcsecToArcmin = v => (v === null ? null : v / 60);

// Each catalogue: the VizieR table, the columns to ask for, the group its
// objects are filed under, and a function from a row to name, aliases and size
// (arcmin). Sizes are diameters. Checked against VizieR on 2026-10-18.
export const VIZIER_CATALOGUES = [
  {
    key: "sh2", label: "Sharpless", source: "VII/20", cols: ["Sh2", "Diam"],
    group: "emission", typeLabel: "HII region (Sharpless)",
    norm: r => ({ name: `Sh2-${clean(r.Sh2)}`, major: num(r.Diam) }),
  },
  {
    key: "rcw", label: "RCW", source: "VII/216", cols: ["RCW", "MajAxis", "MinAxis", "IDs"],
    group: "emission", typeLabel: "Ha emission region (RCW)",
    norm: r => ({
      name: `RCW ${clean(r.RCW)}`, major: num(r.MajAxis), minor: num(r.MinAxis),
      aliases: clean(r.IDs).split(";").map(clean).filter(Boolean),
    }),
  },
  {
    key: "lbn", label: "LBN", source: "VII/9", cols: ["Seq", "Diam1", "Diam2", "Name"],
    group: "nebula", typeLabel: "Bright nebula (Lynds)",
    norm: r => ({
      name: `LBN ${clean(r.Seq)}`, major: num(r.Diam1), minor: num(r.Diam2),
      aliases: clean(r.Name) ? [clean(r.Name)] : [],
    }),
  },
  {
    key: "ldn", label: "LDN", source: "VII/7A", cols: ["LDN", "Area"],
    group: "dark", typeLabel: "Dark nebula (Lynds)",
    // Area is in square degrees: take the diameter of the circle of that area.
    norm: r => {
      const a = num(r.Area);
      return { name: `LDN ${clean(r.LDN)}`, major: a && a > 0 ? 2 * Math.sqrt(a / Math.PI) * 60 : null };
    },
  },
  {
    key: "barnard", label: "Barnard", source: "VII/220A", cols: ["Barn", "Diam"],
    group: "dark", typeLabel: "Dark nebula (Barnard)",
    norm: r => ({ name: `B ${clean(r.Barn)}`, major: num(r.Diam) }),
  },
  {
    key: "vdb", label: "vdB", source: "VII/21", cols: ["VdB", "BRadMax", "RRadMax"],
    group: "reflection", typeLabel: "Reflection nebula (van den Bergh)",
    // The two columns are radii of the blue and red nebulosity.
    norm: r => {
      const b = num(r.BRadMax), rr = num(r.RRadMax);
      return { name: `vdB ${clean(r.VdB)}`, major: b === null ? null : 2 * b, minor: rr === null ? null : 2 * rr };
    },
  },
  {
    key: "mash1", label: "MASH", source: "V/127A/mash1", cols: ["PNG", "Name", "MajDiam", "MinDiam"],
    group: "pn", typeLabel: "Planetary nebula (MASH)",
    norm: r => ({
      name: clean(r.Name) || `PN ${clean(r.PNG)}`, major: arcsecToArcmin(num(r.MajDiam)),
      minor: arcsecToArcmin(num(r.MinDiam)), aliases: clean(r.PNG) ? [`PN ${clean(r.PNG)}`] : [],
    }),
  },
  {
    key: "mash2", label: "MASH-II", source: "V/127A/mash2", cols: ["PNG", "Name", "MajDiam", "MinDiam"],
    group: "pn", typeLabel: "Planetary nebula (MASH-II)",
    norm: r => ({
      name: clean(r.Name) || `PN ${clean(r.PNG)}`, major: arcsecToArcmin(num(r.MajDiam)),
      minor: arcsecToArcmin(num(r.MinDiam)), aliases: clean(r.PNG) ? [`PN ${clean(r.PNG)}`] : [],
    }),
  },
  {
    key: "green", label: "Green SNR", source: "VII/284/snrs", cols: ["SNR", "MajDiam", "MinDiam", "Names"],
    group: "snr", typeLabel: "Supernova remnant (Green)",
    norm: r => ({
      name: `SNR ${clean(r.SNR)}`, major: num(r.MajDiam), minor: num(r.MinDiam),
      aliases: clean(r.Names) ? [clean(r.Names)] : [],
    }),
  },
  {
    key: "wise", label: "WISE HII", source: "J/ApJS/212/1/wisecat", cols: ["WISE", "Cl", "Rad", "HIIName", "Name"],
    sort: "-Rad", // the catalogue is dense in the plane: keep the biggest if cut short
    group: "emission", typeLabel: "HII region (WISE)",
    // Rad is a radius in arcsec.
    norm: r => {
      const rad = num(r.Rad);
      // Names come as "G209.010-19.380; Orion A": the last is the one people use.
      const names = [r.HIIName, r.Name].flatMap(v => clean(v).split(";").map(clean)).filter(Boolean);
      const wise = `WISE ${clean(r.WISE)}`;
      return {
        name: names.length ? names[names.length - 1] : wise,
        major: rad === null ? null : (2 * rad) / 60,
        aliases: names.length ? [...names.slice(0, -1), wise] : [],
      };
    },
  },
];

export function buildVizierUrl(cat, ra, dec, radiusDeg, limit = VIZIER_LIMIT) {
  const f = x => Number(x).toFixed(5);
  const q = [
    ["-source", cat.source],
    ["-c", `${f(ra)},${f(dec)}`],
    ["-c.rd", f(radiusDeg)],
    ["-out.max", String(limit)],
    ["-out.add", "_RAJ,_DEJ"],
    ["-out", cat.cols.join(",")],
    ...(cat.sort ? [["-sort", cat.sort]] : []),
  ];
  return `${VIZIER_ASU}?${q.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")}`;
}

export function normaliseVizier(cat, rows) {
  const out = [];
  for (const r of rows) {
    const ra = num(r._RAJ2000), dec = num(r._DEJ2000);
    if (ra === null || dec === null) continue;
    const n = cat.norm(r);
    out.push({
      name: n.name, aliases: n.aliases || [], group: cat.group, typeLabel: cat.typeLabel,
      catalogue: cat.label, catalogues: [cat.label],
      ra, dec, major: positive(n.major ?? null), minor: positive(n.minor ?? null), pa: null,
    });
  }
  return out;
}

async function fetchWithTimeout(fetchImpl, url, init, timeoutMs) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetchImpl(url, { ...init, signal: ctl.signal });
    if (!r.ok) throw new Error(`answered ${r.status}`);
    return r;
  } catch (e) {
    if (e?.name === "AbortError") throw new Error(`timed out after ${Math.round(timeoutMs / 1000)} s`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// Every source resolves to { label, ok, items, truncated, error }. One that
// fails reports its error and leaves the others alone.
export async function searchSimbad(cone, { fetchImpl = fetch, timeoutMs = 25000 } = {}) {
  const base = { label: "SIMBAD", items: [], truncated: false };
  const ask = async sized => {
    const body = new URLSearchParams({
      request: "doQuery", lang: "adql", format: "json",
      query: buildSimbadAdql(cone.ra, cone.dec, cone.radiusDeg, { sized }),
    });
    const r = await fetchWithTimeout(fetchImpl, SIMBAD_TAP, { method: "POST", body }, timeoutMs);
    const reply = await r.json();
    return { items: normaliseSimbad(reply), full: (reply.data || []).length >= SIMBAD_LIMIT };
  };
  try {
    const [a, b] = await Promise.all([ask(true), ask(false)]);
    return { ...base, ok: true, items: [...a.items, ...b.items], truncated: a.full || b.full };
  } catch (e) {
    return { ...base, ok: false, error: String(e?.message || e) };
  }
}

export async function searchVizier(cat, cone, { fetchImpl = fetch, timeoutMs = 25000 } = {}) {
  const base = { label: cat.label, items: [], truncated: false };
  try {
    const r = await fetchWithTimeout(fetchImpl, buildVizierUrl(cat, cone.ra, cone.dec, cone.radiusDeg), {}, timeoutMs);
    const rows = parseVizierTsv(await r.text());
    return { ...base, ok: true, items: normaliseVizier(cat, rows), truncated: rows.length >= VIZIER_LIMIT };
  } catch (e) {
    return { ...base, ok: false, error: String(e?.message || e) };
  }
}

// Runs every source at once. onSource is called as each one finishes so the
// panel can say which are still waiting.
export async function searchAll(cone, opts = {}, onSource = () => {}) {
  const jobs = [
    searchSimbad(cone, opts),
    ...VIZIER_CATALOGUES.map(c => searchVizier(c, cone, opts)),
  ].map(p => p.then(res => { onSource(res); return res; }));
  return Promise.all(jobs);
}

// ---- rigs and the size filter --------------------------------------------

// Plate scale in arcsec per pixel, the same formula the plan editor uses.
export function pixelScale(focalMm, pixelUm) {
  const fl = Number(focalMm), px = Number(pixelUm);
  if (!(fl > 0) || !(px > 0)) return 0;
  return 206.265 * px / fl;
}

export function rigInfo(tel, cam) {
  const scale = pixelScale(tel?.focal_length_mm, cam?.pixel_size_um);
  const dims = cam?.sensor_px;
  const has = scale > 0 && Array.isArray(dims) && dims.length >= 2;
  return {
    scale,
    fovW: has ? dims[0] * scale / 60 : 0,
    fovH: has ? dims[1] * scale / 60 : 0,
  };
}

// Every telescope with every camera, each telescope's default camera first.
export function listRigs(gear) {
  const rigs = [];
  for (const tel of gear?.telescopes || []) {
    const cams = [...(gear?.cameras || [])].sort(
      (a, b) => (b.id === tel.default_camera_id) - (a.id === tel.default_camera_id));
    for (const cam of cams) {
      rigs.push({
        id: `${tel.id}|${cam.id}`, telescopeId: tel.id, cameraId: cam.id,
        label: `${tel.name} + ${cam.name}`, ...rigInfo(tel, cam),
      });
    }
  }
  return rigs.filter(r => r.scale > 0);
}

// The smallest object, in arcmin, worth showing: minPx pixels across.
export function minSizeArcmin(scaleArcsecPerPx, minPx) {
  return scaleArcsecPerPx > 0 && minPx > 0 ? minPx * scaleArcsecPerPx / 60 : 0;
}

// Objects of unknown size stay, because hiding them would hide most dark
// nebulae and some HII regions. minArcmin 0 keeps everything.
export function filterBySize(items, minArcmin) {
  if (!(minArcmin > 0)) return items;
  return items.filter(o => o.major === null || o.major >= minArcmin);
}

// ---- merging catalogues --------------------------------------------------

// Folds objects that appear in several catalogues into one. Two objects are
// the same when they are the same kind of thing, their centres are within max(1', 0.2 x the larger size) and,
// when both sizes are known, they differ by less than a factor of four, so a
// planetary nebula inside a supernova remnant is not swallowed by it.
// The biggest known size is kept as the primary; the rest become aliases.
// Messier, NGC and IC names are what people know an object by, then the
// classical catalogues. Bracketed survey codes and WISE/MSX/GAL designations
// are what SIMBAD falls back on, so they come last.
// Only objects of the same family are folded together: a dark cloud and a
// planetary nebula at the same spot are two things, not one named twice.
const FAMILY = { emission: "bright", reflection: "bright", nebula: "bright", shell: "bright" };
const familyOf = g => FAMILY[g] || g;

function nameRank(name) {
  if (/^M \d/.test(name)) return 0;
  if (/^(NGC|IC) \d/.test(name)) return 1;
  return /^(\[|WISE |MSX|GAL |TGU |\*)/.test(name) ? 3 : 2;
}

export function dedupe(items) {
  const order = [...items].sort((a, b) => {
    if ((a.major === null) !== (b.major === null)) return a.major === null ? 1 : -1;
    return (b.major ?? 0) - (a.major ?? 0);
  });
  const kept = [];
  for (const o of order) {
    const hit = kept.find(k => {
      if (familyOf(k.group) !== familyOf(o.group)) return false;
      const reach = Math.min(MAX_MERGE_ARCMIN, Math.max(1, 0.2 * (k.major ?? 0)));
      if (angSepArcmin(k.ra, k.dec, o.ra, o.dec) > reach) return false;
      if (k.major !== null && o.major !== null && k.major / o.major > 4) return false;
      return true;
    });
    if (!hit) {
      kept.push({ ...o, aliases: [...o.aliases], catalogues: [...o.catalogues] });
      continue;
    }
    for (const n of [o.name, ...o.aliases]) {
      if (n && n !== hit.name && !hit.aliases.includes(n)) hit.aliases.push(n);
    }
    // SIMBAD's own type reads better than a one-off catalogue's ("HII region"
    // over "Bright nebula (Lynds)").
    if (o.catalogues.includes("SIMBAD") && !hit.catalogues.includes("SIMBAD")) {
      hit.typeLabel = o.typeLabel;
      hit.group = o.group;
    }
    for (const c of o.catalogues) if (!hit.catalogues.includes(c)) hit.catalogues.push(c);
    if (hit.major === null && o.major !== null) hit.major = o.major;
    if (hit.minor === null && o.minor !== null) hit.minor = o.minor;
    if (hit.pa === null && o.pa !== null) hit.pa = o.pa;
  }
  for (const k of kept) {
    const names = [k.name, ...k.aliases];
    const best = names.reduce((m, n) => (nameRank(n) < nameRank(m) ? n : m), k.name);
    if (best !== k.name) k.aliases = [k.name, ...k.aliases.filter(n => n !== best)];
    k.name = best;
  }
  return kept;
}

export function withDistances(items, centre) {
  return items.map(o => ({ ...o, distArcmin: angSepArcmin(centre[0], centre[1], o.ra, o.dec) }));
}

// ---- outline -------------------------------------------------------------

// Points of an ellipse on the sky, for the temporary outline overlay. major
// and minor are full diameters in arcmin and pa is degrees from north through
// east. An object with no size gets a small circle so it still shows.
export function outlinePoints(ra, dec, majorArcmin, minorArcmin, paDeg, n = 48) {
  const a = (majorArcmin > 0 ? majorArcmin : 3) / 2 / 60;
  const b = (minorArcmin > 0 ? minorArcmin : majorArcmin > 0 ? majorArcmin : 3) / 2 / 60;
  const pa = (Number.isFinite(paDeg) ? paDeg : 0) * D2R;
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = (2 * Math.PI * i) / n;
    const u = a * Math.cos(t), v = b * Math.sin(t);
    const east = u * Math.sin(pa) + v * Math.cos(pa);
    const north = u * Math.cos(pa) - v * Math.sin(pa);
    pts.push(tangentOffsetToRaDec(ra, dec, east, north));
  }
  return pts;
}

export function formatArcmin(v) {
  if (v === null || v === undefined) return "size unknown";
  if (v >= 100) return `${Math.round(v)}'`;
  if (v >= 10) return `${v.toFixed(0)}'`;
  if (v >= 1) return `${v.toFixed(1)}'`;
  return `${(v * 60).toFixed(0)}"`;
}
