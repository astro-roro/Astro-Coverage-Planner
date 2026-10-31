// Ties the background chain (cds-fallback.mjs) to the banner (sky-banner.mjs).
// One controller per map. app.js supplies the Aladin calls; tests supply stubs.
//
// The banner rules, from Rohan's brief:
//   - never while any source is still being asked;
//   - after every remote source for the current sky has failed, say so;
//   - clear by itself the moment a remote source answers again;
//   - a survey the user picked that fails alone gets its own message and is
//     kept, not swapped.

import {
  applyBackground,
  chooseBackground,
  forgetVerdicts,
  downMemory,
  cdsTilesAnswer,
} from "./cds-fallback.mjs";

// While the banner is up, ask again this often so it can clear itself.
export const RECHECK_MS = 60_000;

export function createSkyBackground({
  setLayer,
  layerFor,
  makeFallbackLayer,
  banner,
  memory = downMemory,
  probe,
  onChange = () => {},
  schedule = (fn, ms) => setInterval(fn, ms),
  unschedule = h => clearInterval(h),
  recheckMs = RECHECK_MS,
}) {
  let run = 0;
  let currentId = null;
  let timer = null;
  let pickedUrl = null;
  let current = null;       // the settled choice for currentId: "cds", "irsa", "esasky", "offline", or null while unknown

  const probeFn = probe || (url => cdsTilesAnswer({ url: url.endsWith("/properties") ? url : url + "properties" }));

  const stopWatching = () => {
    if (timer !== null) { unschedule(timer); timer = null; }
  };
  const startWatching = () => {
    if (timer === null) timer = schedule(() => recheck(), recheckMs);
  };

  function conclude(choice) {
    current = choice;
    if (choice === "offline") {
      banner.show("cds");
      startWatching();
    } else {
      banner.hide();
      stopWatching();
    }
    onChange(choice);
  }

  // Show survey `id`. Resolves with the final choice, or undefined if a newer
  // call replaced this one first.
  async function show(id) {
    const mine = ++run;
    currentId = id;
    pickedUrl = null;
    current = null;
    banner.hide();
    stopWatching();
    let final;
    await applyBackground(id, {
      setLayer, layerFor, makeFallbackLayer, memory, probe,
      stillWanted: () => mine === run,
      onChoice: choice => { if (mine === run) { final = choice; conclude(choice); } },
    });
    return mine === run ? final : undefined;
  }

  // Ask every source again from scratch. Used by the Retry button.
  async function retry() {
    if (currentId === null && !pickedUrl) return undefined;
    banner.setBusy(true);
    try {
      if (pickedUrl) return await checkPicked(pickedUrl, { quiet: true });
      forgetVerdicts(memory);
      return await show(currentId);
    } finally {
      banner.setBusy(false);
    }
  }

  // The quiet version for the timer: leaves the map and the banner alone until
  // the answer is known, and only acts if a remote source is back.
  async function recheck() {
    if (pickedUrl) { await checkPicked(pickedUrl, { quiet: true }); return; }
    if (currentId === null || banner.kind !== "cds") return;
    const mine = run;
    forgetVerdicts(memory);
    const choice = await chooseBackground(currentId, { memory, probe: probeFn });
    if (mine !== run) return;
    if (choice !== "offline") await show(currentId);
  }

  // A survey chosen in Aladin's own picker that is not one of ours. Its server
  // is asked once; if it does not answer, say so and keep the choice. `quiet`
  // is for the timer's re-asks: the banner stays up until the answer is known.
  async function checkPicked(url, { quiet = false } = {}) {
    if (!url) return undefined;
    const mine = ++run;
    currentId = null;
    current = null;
    pickedUrl = url;
    if (!quiet) { stopWatching(); banner.hide(); }
    const ok = await probeFn(url);
    if (mine !== run) return undefined;
    if (ok) {
      banner.hide();
      stopWatching();
    } else {
      banner.show("survey");
      startWatching();
    }
    return ok;
  }

  return {
    show, retry, recheck, checkPicked, dispose: stopWatching,
    // The settled choice, so the Sky control can say when the Gaia sky is up.
    get current() { return current; },
    // The survey the user asked for. The fallback never replaces it, so this
    // is what gets saved as their sky. Null after a pick from Aladin's picker.
    get requestedId() { return currentId; },
  };
}
