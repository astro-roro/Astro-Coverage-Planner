// When to ask the user to set their observing site. Kept out of app.js so
// tests/frontend/ can import it.

// The site the server seeds and falls back to. Time-aware altitudes computed
// for it are wrong for nearly everyone, so switching Time-aware on while it is
// the active site is the moment to ask. A copy the user has edited (moved to
// their own coordinates) is no longer the built-in default.
export const BUILT_IN_DEFAULT_SITE = Object.freeze({
  id: "mauna_kea",
  lat: 19.82,
  lon: -155.47,
});

export const SITE_PROMPT_SEEN_KEY = "acp.site_prompt_seen";

export function isBuiltInDefaultSite(site) {
  return !!site
    && site.id === BUILT_IN_DEFAULT_SITE.id
    && site.lat === BUILT_IN_DEFAULT_SITE.lat
    && site.lon === BUILT_IN_DEFAULT_SITE.lon;
}

// True when the site prompt should open: Time-aware has just been switched on,
// the active site is the built-in default, and the prompt has not been shown in
// this browser before.
export function shouldPromptForSite({ turningOn, site, seen }) {
  return !!turningOn && !seen && isBuiltInDefaultSite(site);
}

// Whether the prompt was shown before. Storage that throws counts as not seen
// on read and is ignored on write, so a blocked localStorage never breaks the
// toggle (it may nag each time, which beats failing).
export function readPromptSeen(storage) {
  try { return storage.getItem(SITE_PROMPT_SEEN_KEY) === "1"; }
  catch { return false; }
}

export function markPromptSeen(storage) {
  try { storage.setItem(SITE_PROMPT_SEEN_KEY, "1"); }
  catch { /* storage unavailable, ignore */ }
}

// Latitude and longitude for the form inputs, rounded to the input's 4 decimal
// places. Returns null when the browser gave back something unusable.
export function coordsForForm(coords) {
  const lat = Number(coords?.latitude);
  const lon = Number(coords?.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat: lat.toFixed(4), lon: lon.toFixed(4) };
}
