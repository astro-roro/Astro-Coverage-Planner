// Which filter chips start ticked, and how many targets the ticked set hides.
// Kept out of app.js so tests/frontend/ can import it.

// The filter selection to start with. A saved selection (an array, even an
// empty one) is the user's own choice and is kept as is. With nothing saved,
// every filter the archive has data for starts ticked, so a target with only
// LRGB or OSC data is not hidden on first open.
export function initialFilterSelection(saved, available) {
  if (Array.isArray(saved)) return new Set(saved);
  return new Set(available);
}

// How many targets the current filters hide that would show with the filters
// reset. `matchesNow` and `matchesReset` are predicates over a target. Only a
// target that fails now and passes after the reset counts, so targets hidden
// by search, depth or telescope choices are not blamed on the filter chips.
export function filterHiddenCount(targets, matchesNow, matchesReset) {
  let n = 0;
  for (const t of targets) {
    if (!matchesNow(t) && matchesReset(t)) n++;
  }
  return n;
}

// The one-line note above the target list, or "" when no target is hidden.
export function filterHiddenNoteText(n) {
  if (!(n > 0)) return "";
  return `Filters hide ${n} target${n === 1 ? "" : "s"}`;
}
