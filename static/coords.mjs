// Sexagesimal-aware RA/Dec parsing and formatting. Pure functions, no DOM.
// Every parser returns degrees or null and never throws.

const NUM_RE = /\d+(?:\.\d*)?|\.\d+/g;

// Lowercase, trim, and fold the many typographic variants onto ASCII.
function normalise(text) {
  return String(text)
    .toLowerCase()
    .replace(/[−‒–—﹣－]/g, "-")
    .replace(/[º˚]/g, "°")
    .replace(/[′’‘ʹ´`]/g, "'")
    .replace(/[″”“ʺ]/g, '"')
    .replace(/ /g, " ")
    .trim();
}

// Reads one angle written as up to three sexagesimal groups. Returns the
// sign and magnitude separately so that "-00 30 00" keeps its minus.
function scan(s) {
  const m = /^([+-])?\s*([\s\S]*)$/.exec(s);
  const rest = m[2];
  const nums = rest.match(NUM_RE);
  if (!nums || nums.length > 3) return null;
  const residual = rest.replace(NUM_RE, "#");
  if (/[^#\s:hdms°'"]/.test(residual)) return null;
  const hours = residual.includes("h");
  const deg = /[d°]/.test(residual);
  if (hours && deg) return null;
  if (!hours && !deg && /[ms]/.test(residual)) return null;
  for (let i = 0; i < nums.length - 1; i++) {
    if (!/^\d+$/.test(nums[i])) return null;
  }
  const v = nums.map(Number);
  if (v.some(x => !isFinite(x))) return null;
  if (v.length > 1 && v[1] >= 60) return null;
  if (v.length > 2 && v[2] >= 60) return null;
  const mag = v[0] + (v[1] || 0) / 60 + (v[2] || 0) / 3600;
  return { sign: m[1] === "-" ? -1 : 1, mag, n: v.length, hours, deg };
}

const clean = x => (x === 0 ? 0 : x);

export function parseRA(text) {
  try {
    if (text == null) return null;
    const r = scan(normalise(text));
    if (!r || r.sign < 0) return null;
    let deg;
    if (r.deg) {
      deg = r.mag;
    } else if (r.hours || r.n >= 2) {
      if (r.mag > 24) return null;
      deg = r.mag * 15;
    } else {
      deg = r.mag;
    }
    if (deg > 360) return null;
    return clean(deg % 360);
  } catch (e) {
    return null;
  }
}

export function parseDec(text) {
  try {
    if (text == null) return null;
    const r = scan(normalise(text));
    if (!r || r.hours) return null;
    if (r.mag > 90) return null;
    return clean(r.sign * r.mag);
  } catch (e) {
    return null;
  }
}

const COMPACT_RE = /^j?(\d{2})(\d{2})(\d{2}(?:\.\d+)?)?\s*([+-])\s*(\d{2})(\d{2})(\d{2}(?:\.\d+)?)?$/;

function parseCompact(s) {
  const m = COMPACT_RE.exec(s);
  if (!m) return null;
  // The seconds are present on both halves or on neither.
  if ((m[3] === undefined) !== (m[7] === undefined)) return null;
  const ra = parseRA(`${m[1]}h${m[2]}m${m[3] === undefined ? "" : m[3] + "s"}`);
  const dec = parseDec(`${m[4]}${m[5]}d${m[6]}m${m[7] === undefined ? "" : m[7] + "s"}`);
  return ra === null || dec === null ? null : { ra, dec };
}

// Finds where the RA text ends and the Dec text begins. Order of preference:
// a "dec" label, a comma or semicolon, the first sign that follows a number,
// a run of two or more spaces, then splitting the number groups in half.
function splitPair(s) {
  const label = /\bdec(?:lination)?\b/.exec(s);
  if (label) {
    return [s.slice(0, label.index).replace(/\bra\b/, ""), s.slice(label.index + label[0].length)];
  }
  s = s.replace(/\bra\b\s*[:=]?/, "");
  const sep = /[,;]/.exec(s);
  if (sep) return [s.slice(0, sep.index), s.slice(sep.index + 1)];
  for (let i = 1; i < s.length; i++) {
    if ((s[i] === "-" || s[i] === "+") && /\d/.test(s.slice(0, i))) {
      return [s.slice(0, i), s.slice(i)];
    }
  }
  const gap = /\s{2,}/.exec(s);
  if (gap && /\d/.test(s.slice(0, gap.index)) && /\d/.test(s.slice(gap.index))) {
    return [s.slice(0, gap.index), s.slice(gap.index)];
  }
  const found = [...s.matchAll(NUM_RE)];
  if ([2, 4, 6].includes(found.length)) {
    const half = found[found.length / 2 - 1];
    let cut = half.index + half[0].length;
    // Keep a unit letter or symbol attached to the number it follows.
    while (cut < s.length && /[hdms°'":]/.test(s[cut])) cut++;
    return [s.slice(0, cut), s.slice(cut)];
  }
  return null;
}

export function parseCoordPair(text) {
  try {
    if (text == null) return null;
    const s = normalise(text).replace(/^[(\[]+|[)\]]+$/g, "").trim();
    if (!s) return null;
    const compact = parseCompact(s);
    if (compact) return compact;
    const parts = splitPair(s);
    if (!parts) return null;
    const ra = parseRA(parts[0].replace(/[\s:]+$/, ""));
    const dec = parseDec(parts[1].replace(/^[\s:]+/, ""));
    return ra === null || dec === null ? null : { ra, dec };
  } catch (e) {
    return null;
  }
}

const pad2 = n => String(n).padStart(2, "0");

// Splits a non-negative total in hundredths of a unit into whole units,
// minutes and hundredths-of-seconds, so rounding can never print "60.00".
function sexa(centi) {
  const unit = Math.floor(centi / 360000);
  const rem = centi - unit * 360000;
  const min = Math.floor(rem / 6000);
  const sec = (rem - min * 6000) / 100;
  return [unit, min, sec];
}

export function deg2hms(deg) {
  if (deg == null || !isFinite(deg)) return "";
  const d = ((deg % 360) + 360) % 360;
  const centi = Math.round((d / 15) * 360000) % (24 * 360000);
  const [h, m, s] = sexa(centi);
  return `${pad2(h)}h ${pad2(m)}m ${s.toFixed(2)}s`;
}

export function deg2dms(deg) {
  if (deg == null || !isFinite(deg)) return "";
  const centi = Math.round(Math.abs(deg) * 360000);
  const [d, m, s] = sexa(centi);
  const sign = deg < 0 && centi > 0 ? "-" : "+";
  return `${sign}${pad2(d)}° ${pad2(m)}' ${s.toFixed(2)}"`;
}

export function formatRA(deg, { sexagesimal = false } = {}) {
  if (deg == null || !isFinite(deg)) return "";
  return sexagesimal ? deg2hms(deg) : Number(deg).toFixed(4);
}

export function formatDec(deg, { sexagesimal = false } = {}) {
  if (deg == null || !isFinite(deg)) return "";
  return sexagesimal ? deg2dms(deg) : Number(deg).toFixed(4);
}
