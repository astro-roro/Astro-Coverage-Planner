// Search tokenizer + matchers, factored out of app.js so tests/frontend/ can
// import them directly via `node --test`. No build step — the browser loads
// this as an ES module from <script type="module" src="/static/app.js">.
//
// Tokenizer splits `camera:asi2600 filter:Ha "orion neb" hours>3` into tokens.
// Supports:
//   - bareword (free-text substring)
//   - key:value (e.g. class:PNe, tag:needs-work)
//   - key>N / key<N for numeric comparators (fov, hours)
//   - "quoted phrase" preserves spaces, also valid inside key:"…"
//   - leading `-` on any token negates it (e.g. -class:SNR, -tag:done)
// Tokens are ANDed; OR is not supported (use multiple `-` tokens for NOT).
export const SEARCH_KV_KEYS = new Set([
  "object", "name", "filter", "telescope", "tel", "camera", "cam",
  // Cross-catalogue Object-filter keys (handled by catalogObjectMatchesTokens):
  "class", "tag",
]);
export const SEARCH_CMP_KEYS = new Set(["fov", "hours"]);

export function tokenizeSearch(query) {
  if (!query) return [];
  const rawTokens = [];
  // Match leading `-` followed by any token form.
  const re = /(-?(?:[a-zA-Z]+:"[^"]*"|[a-zA-Z]+[:><][^\s]+|"[^"]*"|\S+))/g;
  let m;
  while ((m = re.exec(query)) !== null) {
    rawTokens.push(m[1]);
  }
  const parsed = [];
  for (let raw of rawTokens) {
    if (!raw) continue;
    let negate = false;
    if (raw.startsWith("-") && raw.length > 1) {
      negate = true;
      raw = raw.slice(1);
    }
    // Numeric comparator: key>N or key<N
    const cmp = raw.match(/^([a-zA-Z]+)([><])(.+)$/);
    if (cmp && SEARCH_CMP_KEYS.has(cmp[1].toLowerCase())) {
      const v = parseFloat(cmp[3]);
      if (isFinite(v)) {
        parsed.push({ kind: "cmp", key: cmp[1].toLowerCase(), op: cmp[2], value: v, negate });
        continue;
      }
    }
    // key:value or key:"quoted value"
    const kv = raw.match(/^([a-zA-Z]+):(.*)$/);
    if (kv && SEARCH_KV_KEYS.has(kv[1].toLowerCase())) {
      let val = kv[2];
      if (val.length >= 2 && val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
      if (val) parsed.push({ kind: "kv", key: kv[1].toLowerCase(), value: val.toLowerCase(), negate });
      continue;
    }
    // Bare quoted phrase
    if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
      const v = raw.slice(1, -1);
      if (v) parsed.push({ kind: "text", value: v.toLowerCase(), negate });
      continue;
    }
    parsed.push({ kind: "text", value: raw.toLowerCase(), negate });
  }
  return parsed;
}

// Match a single CatalogObject against tokenized search terms.
// Used by the cross-catalogue Object filter — separate from the
// target-list matcher because the field shape is different.
// Token semantics:
//   text/name      — substring match on obj.name (case-insensitive)
//   class:X        — exact category match
//   tag:X          — obj.tags includes X
// `negate: true` inverts each predicate.
export function catalogObjectMatchesTokens(obj, tokens) {
  if (!tokens || !tokens.length) return true;
  const name = String(obj.name || "").toLowerCase();
  const category = String(obj.category || "").toLowerCase();
  const tags = (obj.tags || []).map(t => String(t).toLowerCase());
  for (const tok of tokens) {
    let matched;
    if (tok.kind === "text") {
      matched = name.includes(tok.value);
    } else if (tok.kind === "kv") {
      switch (tok.key) {
        case "class":
          matched = category === tok.value;
          break;
        case "tag":
          matched = tags.includes(tok.value);
          break;
        case "name":
        case "object":
          matched = name.includes(tok.value);
          break;
        default:
          // Keys handled elsewhere (filter/telescope/camera) — skip
          // silently so a single search string can target both rails.
          matched = true;
      }
    } else if (tok.kind === "cmp") {
      matched = true; // catalog objects don't have hours/fov yet
    } else {
      matched = true;
    }
    if (tok.negate ? matched : !matched) return false;
  }
  return true;
}

export function targetMatchesSearch(t, tokens) {
  if (!tokens.length) return true;
  const objs = (t.objects || []).map(s => String(s).toLowerCase());
  const tels = (t.telescopes || []).map(s => String(s).toLowerCase());
  const cams = (t.cameras || []).map(s => String(s).toLowerCase());
  const filters = t.filters || {};
  const totalH = (function () {
    let s = 0;
    for (const d of Object.values(filters)) s += (d.total_hours || 0);
    return s;
  })();
  const fovMax = Math.max(...((t.fov_arcmin && t.fov_arcmin.length) ? t.fov_arcmin : [0]));

  for (const tok of tokens) {
    let matched;
    if (tok.kind === "text") {
      const v = tok.value;
      matched = objs.some(s => s.includes(v)) ||
                tels.some(s => s.includes(v)) ||
                cams.some(s => s.includes(v));
    } else if (tok.kind === "kv") {
      const v = tok.value;
      switch (tok.key) {
        case "object":
        case "name":
          matched = objs.some(s => s.includes(v));
          break;
        case "filter": {
          const fname = Object.keys(filters).find(f => f.toLowerCase() === v);
          matched = !!(fname && filters[fname].total_hours > 0);
          break;
        }
        case "telescope":
        case "tel":
          matched = tels.some(s => s.includes(v));
          break;
        case "camera":
        case "cam":
          matched = cams.some(s => s.includes(v));
          break;
        case "class":
        case "tag":
          // Catalogue-only keys — neutral for the target list so a
          // shared search string targeting catalogues doesn't hide
          // every target.
          matched = true;
          break;
        default:
          matched = false;
      }
    } else if (tok.kind === "cmp") {
      let lhs;
      if (tok.key === "fov") lhs = fovMax;
      else if (tok.key === "hours") lhs = totalH;
      else { matched = false; }
      if (matched !== false) {
        if (tok.op === ">") matched = lhs > tok.value;
        else if (tok.op === "<") matched = lhs < tok.value;
        else matched = false;
      }
    } else {
      matched = true;
    }
    if (tok.negate ? matched : !matched) return false;
  }
  return true;
}
