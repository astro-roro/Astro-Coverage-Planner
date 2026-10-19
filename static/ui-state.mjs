// Saving the UI state blob in localStorage. Kept out of app.js so
// tests/frontend/ can import it.

// Keys that other code writes straight into the saved blob. saveUiState builds
// a fresh object each time, so it has to carry these through or they vanish.
export const FOREIGN_UI_STATE_KEYS = ["objectsFilter"];

// Write `state` under `key`, keeping any foreign keys already stored. `state`
// wins when it sets one itself.
export function persistUiState(storage, key, state) {
  let prev = {};
  try { prev = JSON.parse(storage.getItem(key) || "{}") || {}; }
  catch { prev = {}; }
  const next = { ...state };
  for (const k of FOREIGN_UI_STATE_KEYS) {
    if (!(k in next) && k in prev) next[k] = prev[k];
  }
  storage.setItem(key, JSON.stringify(next));
}
