// Lets the interface start before the sky map has. Map-dependent work is
// handed to whenReady(): it runs at once if the map is already up, waits in
// a queue if the map is still loading, and is dropped if the map failed.
// app.js uses this so the panels never wait on Aladin Lite or the survey
// tiles, and so work that needs the map (drawing footprints, restoring the
// saved survey) still happens, in order, once the map exists.

export function createMapGate() {
  let state = "loading"; // "loading" | "ready" | "failed"
  let queue = [];

  return {
    get state() { return state; },
    get ready() { return state === "ready"; },
    get pending() { return queue.length; },

    /** Run fn now if the map is ready, after it is ready if loading, never if it failed. */
    whenReady(fn) {
      if (state === "ready") { run(fn); return; }
      if (state === "loading") queue.push(fn);
    },

    /** The map is up. Flush the queue in the order it was filled. */
    markReady() {
      if (state !== "loading") return;
      state = "ready";
      const work = queue;
      queue = [];
      for (const fn of work) run(fn);
    },

    /** The map will not come up. Queued work is dropped. */
    markFailed() {
      if (state !== "loading") return;
      state = "failed";
      queue = [];
    },
  };
}

// One failing callback must not stop the ones after it.
function run(fn) {
  try { fn(); } catch (err) { console.error("Map-ready work failed:", err); }
}
