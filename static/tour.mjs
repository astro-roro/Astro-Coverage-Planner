// The first-run tour: seven short steps, each pointing at one control.
// The step list, the saved flag and the card placement are plain functions
// so tests/frontend/ can check them without a DOM. createTour() at the end
// is the browser half, and app.js hands it the few things only app.js
// knows (whether the library is empty, whether Planning is on, how to close
// the phone sheet).
//
// The tour only points. It never switches mode or opens a panel, so a user
// who skips it finds the app exactly as they left it.

export const TOUR_KEY = "acp.tour.v1";
export const TOUR_VERSION = 1;

// Below this width the rail is a bottom tab bar and the panel a sheet.
export const PHONE_QUERY = "(max-width: 640px)";

// --- Saved flag ---------------------------------------------------------
// localStorage can be missing, blocked or full. Every read and write is
// wrapped, like every other acp.* key, so a blocked store means "never
// seen" on read and a silent no-op on write.

export function readTourState(storage) {
  try {
    const raw = storage?.getItem(TOUR_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (!s || s.version !== TOUR_VERSION) return null;
    return { version: TOUR_VERSION, seen: s.seen === true, skipped: s.skipped === true };
  } catch {
    return null;
  }
}

export function writeTourState(storage, { skipped }) {
  const state = { version: TOUR_VERSION, seen: true, skipped: !!skipped };
  try { storage?.setItem(TOUR_KEY, JSON.stringify(state)); }
  catch { /* localStorage full or disabled, ignore */ }
  return state;
}

export function shouldAutoStart(storage) {
  return !readTourState(storage)?.seen;
}

// --- Steps --------------------------------------------------------------
// Each step lists candidate anchors in order of preference. The first one
// that is on the page and visible wins; a step with none is skipped.
// `name` says where the control is, for the centred card shown when the
// anchor exists but can't be brought on screen.

const RAIL = tab => `#panelRail .rail-btn[data-tab="${tab}"]`;

export function buildSteps({ emptyLibrary = false, planningMode = false } = {}) {
  const map = {
    id: "map",
    title: "The sky map",
    body: emptyLibrary
      ? "Your imaged fields will appear here once you scan your archive. The demo script fills it with five examples."
      : "This is the whole sky, with every field you have imaged drawn on it in your telescope's colour.",
    anchors: [{ selector: "#aladin-lite-div", name: "the sky map" }],
  };
  const main = planningMode
    ? {
        id: "main",
        title: "Plans",
        body: "Your plans and projects. Click a row to open it, or click a plan on the map.",
        anchors: [{ selector: RAIL("main"), name: "the Plans button on the rail" }],
      }
    : {
        id: "main",
        title: "Targets",
        body: "Everything in your library, with hours per filter. Click a row to fly to it, or click a field on the map.",
        anchors: [{ selector: RAIL("main"), name: "the Targets button on the rail" }],
      };
  const filters = {
    id: "filters",
    title: "Filters",
    body: "Choose which filters and telescopes the map shows. If something seems missing, check here first.",
    anchors: [{ selector: RAIL("filters"), name: "the Filters button on the rail" }],
  };
  const library = {
    id: "library",
    title: "Library",
    body: "Catalogues, survey footprints and the gap finder live here, for spotting objects you have not shot yet.",
    anchors: [{ selector: RAIL("library"), name: "the Library button on the rail" }],
  };
  const planning = {
    id: "planning",
    title: "Planning",
    body: "Switch to Planning to frame a new target, set filter goals and mosaics, and see plans on the map.",
    anchors: [{ selector: "#modePlanning", name: "the Planning switch in the top bar" }],
  };
  const time = {
    id: "time",
    title: "Site and Time-aware",
    body: "Set your site, then turn this on to see what is up tonight and when each target peaks.",
    anchors: [{ selector: "#timeAwareToggle", name: "the Time-aware button in the top bar" }],
  };
  const activityAnchor = {
    selector: RAIL("activity"),
    name: "the Activity button on the rail",
    body: "Follow each night's progress here. In Planning, Sync to NINA sends your plans to NINA Target Scheduler.",
  };
  const nina = {
    id: "nina",
    title: "NINA and Activity",
    body: "Send plans to NINA Target Scheduler, then follow each night's progress in Activity.",
    anchors: planningMode
      ? [{ selector: "#planSync", name: "Sync to NINA in the plan list" }, activityAnchor]
      : [
          activityAnchor,
          // Activity can be switched off with ACP_ACTIVITY=off. Fall back to
          // the Planning switch, the way the audit first proposed.
          {
            selector: "#modePlanning",
            name: "the Planning switch in the top bar",
            body: "In Planning, Sync to NINA sends your plans to NINA Target Scheduler.",
          },
        ],
  };
  return emptyLibrary
    ? [map, time, nina]
    : [map, main, filters, library, planning, time, nina];
}

// Pick an anchor for each step, dropping steps with none. `findAnchor` is
// given a selector and returns the element when it is present and shown,
// otherwise null.
export function resolveSteps(steps, findAnchor) {
  const out = [];
  for (const step of steps) {
    for (const a of step.anchors) {
      const el = findAnchor(a.selector);
      if (!el) continue;
      out.push({
        id: step.id,
        title: step.title,
        body: a.body || step.body,
        selector: a.selector,
        name: a.name,
        el,
      });
      break;
    }
  }
  return out;
}

// --- Card placement -----------------------------------------------------
// Returns where the card goes for an anchor's bounding box. `mode` is
// "anchored" (ring and card beside it), or "centred" when the anchor is off
// screen, in which case the card names the control instead.

const GAP = 12;
const EDGE = 8;

export function isOffScreen(rect, vw, vh) {
  return !rect || rect.width <= 0 || rect.height <= 0
    || rect.bottom <= 0 || rect.right <= 0 || rect.top >= vh || rect.left >= vw;
}

export function placeCard(rect, card, viewport, { phone = false } = {}) {
  const { width: vw, height: vh } = viewport;
  const w = phone ? vw - EDGE * 2 : card.width;
  const h = card.height;
  if (isOffScreen(rect, vw, vh)) {
    return { mode: "centred", left: Math.max(EDGE, (vw - w) / 2), top: Math.max(EDGE, (vh - h) / 2), width: w };
  }
  const clampX = x => Math.max(EDGE, Math.min(vw - w - EDGE, x));
  const clampY = y => Math.max(EDGE, Math.min(vh - h - EDGE, y));
  const large = rect.width > vw * 0.5 && rect.height > vh * 0.5;
  if (phone) {
    // Full width. At the top of the screen, unless that would cover a
    // top-bar anchor, in which case it sits just below the anchor.
    const inTopHalf = !large && rect.top + rect.height / 2 < vh / 2;
    const top = inTopHalf ? clampY(rect.bottom + GAP) : (large ? clampY(rect.top + GAP) : EDGE);
    return { mode: "anchored", left: EDGE, top, width: w };
  }
  if (large) {
    // The map: sit near the top of it, centred across, so the middle of the
    // sky (and the welcome banner, on an empty library) stays visible.
    return { mode: "anchored", left: clampX(rect.left + (rect.width - w) / 2), top: clampY(rect.top + GAP * 2), width: w };
  }
  const centreX = rect.left + rect.width / 2 - w / 2;
  const centreY = rect.top + rect.height / 2 - h / 2;
  if (rect.bottom + GAP + h <= vh - EDGE) return { mode: "anchored", left: clampX(centreX), top: rect.bottom + GAP, width: w };
  if (rect.left - GAP - w >= EDGE) return { mode: "anchored", left: rect.left - GAP - w, top: clampY(centreY), width: w };
  if (rect.top - GAP - h >= EDGE) return { mode: "anchored", left: clampX(centreX), top: rect.top - GAP - h, width: w };
  return { mode: "anchored", left: clampX(rect.right + GAP), top: clampY(centreY), width: w };
}

// --- Browser half -------------------------------------------------------

// Anything that should hold off an automatic start: an open <dialog>, the
// unsaved-plan prompt, or the TS upload banner.
export function autoStartBlocked(doc) {
  if (doc.querySelector("dialog[open], .dirty-modal-backdrop")) return true;
  const banner = doc.getElementById("tsSyncBanner");
  return !!(banner && !banner.hidden);
}

export function createTour({ doc = document, win = window, storage, getContext, closeSheet }) {
  let steps = [];
  let index = 0;
  let ring = null;
  let card = null;
  let shade = null;
  let returnFocus = null;
  let waitObservers = [];

  const isPhone = () => !!win.matchMedia?.(PHONE_QUERY).matches;
  const reducedMotion = () => !!win.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  function findAnchor(selector) {
    const el = doc.querySelector(selector);
    if (!el || el.closest("[hidden]")) return null;
    if (!el.getClientRects().length) return null;
    const cs = win.getComputedStyle(el);
    if (cs.visibility === "hidden") return null;
    return el;
  }

  function build() {
    shade = doc.createElement("div");
    shade.className = "tour-shade";
    ring = doc.createElement("div");
    ring.className = "tour-ring";
    ring.setAttribute("aria-hidden", "true");
    card = doc.createElement("div");
    card.className = "tour-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "false");
    card.setAttribute("aria-labelledby", "tourTitle");
    card.setAttribute("aria-describedby", "tourBody");
    card.innerHTML = `
      <div class="tour-count" id="tourCount"></div>
      <h3 id="tourTitle"></h3>
      <p id="tourBody"></p>
      <div class="tour-actions">
        <button type="button" class="link-btn" data-tour="skip">Skip</button>
        <span class="tour-spacer"></span>
        <button type="button" data-tour="back">Back</button>
        <button type="button" class="primary" data-tour="next">Next</button>
      </div>`;
    card.addEventListener("click", e => {
      const act = e.target.closest("[data-tour]")?.dataset.tour;
      if (act === "skip") end(true);
      else if (act === "back") show(index - 1, -1);
      else if (act === "next") (index >= steps.length - 1 ? end(false) : show(index + 1, 1));
    });
    doc.body.append(shade, ring, card);
    doc.addEventListener("keydown", onKey, true);
    win.addEventListener("resize", position);
  }

  function teardown() {
    doc.removeEventListener("keydown", onKey, true);
    win.removeEventListener("resize", position);
    shade?.remove(); ring?.remove(); card?.remove();
    shade = ring = card = null;
  }

  // Capture phase, so app.js's own Esc (go up one panel level) never sees
  // the press that closed the tour.
  function onKey(e) {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopImmediatePropagation();
    end(true);
  }

  function position() {
    if (!card) return;
    const step = steps[index];
    const phone = isPhone();
    const vw = win.innerWidth, vh = win.innerHeight;
    let rect = step.el.isConnected ? step.el.getBoundingClientRect() : null;
    if (phone && rect && isOffScreen(rect, vw, vh)) {
      // A top-bar control can sit on a wrapped second or third row.
      step.el.scrollIntoView?.({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      rect = step.el.getBoundingClientRect();
    }
    card.classList.toggle("is-phone", phone);
    const box = card.getBoundingClientRect();
    const p = placeCard(rect, { width: 340, height: box.height || 160 }, { width: vw, height: vh }, { phone });
    const body = card.querySelector("#tourBody");
    if (p.mode === "centred") {
      body.textContent = `${step.body} Look for ${step.name}.`;
      ring.hidden = true;
      shade.hidden = false;
    } else {
      body.textContent = step.body;
      ring.hidden = false;
      shade.hidden = true;
      const pad = 4;
      Object.assign(ring.style, {
        left: `${rect.left - pad}px`, top: `${rect.top - pad}px`,
        width: `${rect.width + pad * 2}px`, height: `${rect.height + pad * 2}px`,
      });
    }
    Object.assign(card.style, { left: `${p.left}px`, top: `${p.top}px`, width: `${p.width}px` });
  }

  // `dir` is the way the user is moving, so a step whose anchor has gone
  // since the tour started is passed over in that direction.
  function show(i, dir = 1) {
    if (isPhone()) closeSheet?.();
    while (i >= 0 && i < steps.length) {
      const s = steps[i];
      if (s.el.isConnected && findAnchor(s.selector) === s.el) break;
      i += dir;
    }
    if (i < 0) i = index;
    if (i >= steps.length) { end(false); return; }
    index = i;
    const step = steps[index];
    card.querySelector("#tourCount").textContent = `${index + 1} of ${steps.length}`;
    card.querySelector("#tourTitle").textContent = step.title;
    card.querySelector('[data-tour="back"]').hidden = index === 0;
    card.querySelector('[data-tour="next"]').textContent = index === steps.length - 1 ? "Done" : "Next";
    position();
    card.querySelector('[data-tour="next"]').focus({ preventScroll: true });
  }

  function start() {
    if (card) return;
    stopWaiting();
    // Close the phone sheet first, so which anchors are visible doesn't
    // change between choosing the steps and showing them.
    if (isPhone()) closeSheet?.();
    steps = resolveSteps(buildSteps(getContext()), findAnchor);
    if (!steps.length) return;
    returnFocus = doc.activeElement;
    // Seen from the first step. Anything short of Done, including a reload
    // halfway through, counts as a skip.
    writeTourState(storage, { skipped: true });
    build();
    index = 0;
    show(0, 1);
  }

  function end(skipped) {
    if (!card) return;
    writeTourState(storage, { skipped });
    teardown();
    if (returnFocus?.isConnected) returnFocus.focus?.({ preventScroll: true });
    returnFocus = null;
  }

  function stopWaiting() {
    for (const o of waitObservers) o.disconnect();
    waitObservers = [];
  }

  // Start once per browser. If a dialog or the TS upload banner is open,
  // wait for it to close; if it never does, the tour waits for the next
  // session.
  function maybeAutoStart() {
    if (!shouldAutoStart(storage)) return;
    if (!autoStartBlocked(doc)) { start(); return; }
    if (typeof win.MutationObserver !== "function") return;
    const check = () => {
      if (autoStartBlocked(doc)) return;
      stopWaiting();
      if (shouldAutoStart(storage)) start();
    };
    const attrs = new win.MutationObserver(check);
    attrs.observe(doc.body, { subtree: true, attributes: true, attributeFilter: ["open", "hidden"] });
    const kids = new win.MutationObserver(check);
    kids.observe(doc.body, { childList: true });
    waitObservers = [attrs, kids];
  }

  return { start, end, maybeAutoStart, isActive: () => !!card };
}
