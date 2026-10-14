// The Activity tab's behaviour: fetching, polling, scrolling and clicks.
// What each piece looks like is decided by activity.mjs; app.js passes in the
// two things only it can do (open a plan, pan the map).

import {
  CHIPS,
  DEFAULT_CHIPS,
  collapseRows,
  esc,
  lastNightHtml,
  mergeNights,
  needsYouHtml,
  nightHeading,
  pageState,
  railState,
  renderRowHtml,
  visibleRows,
} from "./activity.mjs";

const POLL_MS = 60000;
const FIRST_PAGE = 5;
const PREFS_DELAY_MS = 500;

async function getJson(url) {
  const r = await fetch(url);
  if (r.status === 404) return { gone: true };
  if (!r.ok) throw new Error(`${url} answered ${r.status}`);
  return r.json();
}

async function post(url, body) {
  const init = { method: "POST" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return fetch(url, init);
}

export function setupActivity({ openPlan, showOnMap }) {
  const body = document.getElementById("activityBody");
  const badge = document.getElementById("railActivityBadge");
  const dot = document.getElementById("railActivityDot");
  if (!body) return { onTabShown() {} };

  const st = {
    visible: false,
    gone: false,
    loaded: false,
    loading: false,
    nights: [],
    total: 0,
    nextBefore: null,
    summary: null,
    chips: { ...DEFAULT_CHIPS },
    highlightSince: undefined, // last_seen as it was when the tab was opened
    seenMax: null,          // max_updated_at the loaded nights already include
    openNights: new Map(),  // night -> open, once the user or a default chose
    prefsTimer: null,
    pollTimer: null,
    observer: null,
    error: null,
  };

  function paintRail() {
    const s = railState(st.summary);
    if (badge) {
      badge.hidden = !(s && s.badge);
      badge.textContent = s && s.badge ? String(s.badge) : "";
    }
    if (dot) dot.hidden = !(s && s.dot);
  }

  function defaultOpen(n, index) {
    return index === 0 || n.events.some(e => e.type === "master_built");
  }

  function nightHtml(n, index) {
    const isOpen = st.openNights.has(n.night) ? st.openNights.get(n.night) : defaultOpen(n, index);
    const rows = collapseRows(visibleRows(n.events, st.chips));
    const lines = rows.map(r => renderRowHtml(r, { lastSeen: st.highlightSince })).join("");
    const hidden = n.events.length - visibleRows(n.events, st.chips).length;
    const more = n.more > 0
      ? `<li class="ev-more"><button type="button" class="btn" data-act="night-more" data-night="${esc(n.night)}">${esc(n.more)} more</button></li>`
      : "";
    const sum = `${n.events.length + n.more} event${n.events.length + n.more === 1 ? "" : "s"}` +
      (hidden ? ` · ${hidden} hidden by filters` : "");
    return `<details class="night" data-night="${esc(n.night)}"${isOpen ? " open" : ""}>` +
      `<summary><h3>${esc(nightHeading(n.night))}</h3><span class="sum">${esc(sum)}</span></summary>` +
      `<ol class="events">${lines || '<li class="ev-empty">Nothing to show under the current filters.</li>'}${more}</ol></details>`;
  }

  function chipsHtml() {
    const chips = CHIPS.map(c => (
      `<button type="button" class="chip" data-act="chip" data-chip="${esc(c.id)}" aria-pressed="${st.chips[c.id] !== false}">${esc(c.label)}</button>`
    )).join("");
    return `<div class="chip-row"><div class="chips" role="group" aria-label="Show event types">${chips}</div>` +
      `<button type="button" class="linkbtn" data-act="chips-all">Show everything</button></div>`;
  }

  function render() {
    if (st.gone) { body.innerHTML = '<div class="empty-note">The Activity tab is switched off on this server.</div>'; return; }
    if (st.error && !st.loaded) { body.innerHTML = `<div class="panel-error">${esc(st.error)}</div>`; return; }
    const s = st.summary || {};
    const page = pageState({ shown: st.nights.length, total: st.total });
    const nights = st.nights.map(nightHtml).join("");
    body.innerHTML =
      `<div class="act-scroll">${lastNightHtml(s.last_night)}${needsYouHtml(s.open || [], s.open_total || 0)}` +
      `${chipsHtml()}${nights || (st.loaded ? '<div class="empty-note">No activity yet. Events appear here as plans change, syncs run and nights are imaged.</div>' : "")}` +
      `<div class="more"><span class="count">${esc(page.text)}</span>` +
      (page.showLoadOlder ? '<button type="button" class="btn" data-act="older">Load older nights</button>' : "") +
      `</div><div id="activitySentinel" aria-hidden="true" style="height:1px"></div></div>`;
    watchSentinel();
  }

  function watchSentinel() {
    if (st.observer) st.observer.disconnect();
    const sentinel = document.getElementById("activitySentinel");
    const root = body.closest(".side-tab");
    if (!sentinel || typeof IntersectionObserver === "undefined") return;
    st.observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) loadOlder();
    }, { root, rootMargin: "0px 0px 120px 0px" });
    st.observer.observe(sentinel);
  }

  async function loadSummary() {
    const s = await getJson("/api/activity/summary");
    if (s.gone) { st.gone = true; stopPolling(); return null; }
    st.summary = s;
    paintRail();
    return s;
  }

  async function loadFirstPage() {
    st.loading = true;
    try {
      const [page, prefs] = await Promise.all([
        getJson(`/api/activity?nights=${FIRST_PAGE}`), getJson("/api/activity/prefs")]);
      if (page.gone) { st.gone = true; stopPolling(); return; }
      st.nights = page.nights;
      st.total = page.total_nights;
      st.nextBefore = page.next_before;
      st.highlightSince = page.last_seen || undefined;
      if (!prefs.gone && prefs.chips) st.chips = { ...DEFAULT_CHIPS, ...prefs.chips };
      const s = await loadSummary();
      st.seenMax = s ? s.max_updated_at : null;
      st.loaded = true;
      st.error = null;
    } catch (err) {
      st.error = "Could not load the activity. " + (err && err.message ? err.message : "");
    } finally {
      st.loading = false;
    }
    render();
  }

  async function loadOlder() {
    if (st.loading || !st.nextBefore) return;
    st.loading = true;
    try {
      const page = await getJson(`/api/activity?nights=${FIRST_PAGE}&before=${encodeURIComponent(st.nextBefore)}`);
      if (!page.gone) {
        st.nights = mergeNights(st.nights, page.nights);
        st.total = page.total_nights;
        st.nextBefore = page.next_before;
      }
    } catch { /* the button stays, so the user can try again */ }
    st.loading = false;
    render();
  }

  // Refetch what is on screen, so late events and updates show in nights
  // already loaded.
  async function refreshLoaded() {
    try {
      const count = Math.min(30, Math.max(FIRST_PAGE, st.nights.length));
      const page = await getJson(`/api/activity?nights=${count}`);
      if (page.gone) return;
      st.nights = mergeNights(st.nights, page.nights);
      st.total = page.total_nights;
    } catch { /* the next poll tries again */ }
  }

  async function nightMore(night) {
    const n = st.nights.find(x => x.night === night);
    if (!n) return;
    try {
      const page = await getJson(`/api/activity/night/${encodeURIComponent(night)}?offset=${n.events.length}`);
      if (!page.gone) {
        const byId = new Map(n.events.map(e => [e.id, e]));
        for (const e of page.events) byId.set(e.id, e);
        st.nights = st.nights.map(x => (x.night === night
          ? { night, events: [...byId.values()], more: page.more } : x));
      }
    } catch { /* leave the button for another try */ }
    render();
  }

  async function poll() {
    if (st.gone || document.hidden) return;
    try {
      const before = st.summary && st.summary.max_updated_at;
      const s = await loadSummary();
      if (!s) return;
      if (st.visible && st.loaded && s.max_updated_at !== before) {
        await refreshLoaded();
        st.seenMax = s.max_updated_at;
      }
      if (st.visible && st.loaded) render();
    } catch { /* offline; try again next tick */ }
  }

  function startPolling() {
    if (!st.pollTimer && !st.gone) st.pollTimer = setInterval(poll, POLL_MS);
  }

  function stopPolling() {
    if (st.pollTimer) { clearInterval(st.pollTimer); st.pollTimer = null; }
  }

  function savePrefsSoon() {
    if (st.prefsTimer) clearTimeout(st.prefsTimer);
    st.prefsTimer = setTimeout(() => {
      st.prefsTimer = null;
      fetch("/api/activity/prefs", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chips: st.chips }),
      }).catch(() => { /* the chips still work for this visit */ });
    }, PREFS_DELAY_MS);
  }

  async function copyText(btn) {
    const text = btn.dataset.text || "";
    const code = btn.parentNode && btn.parentNode.querySelector("code");
    const done = label => {
      btn.textContent = label;
      btn.classList.add("ok");
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("ok"); }, 2000);
    };
    const select = () => {
      if (!code) return;
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      done("Ctrl+C");
    };
    try {
      await navigator.clipboard.writeText(text);
      done("Copied");
    } catch {
      select();
    }
  }

  body.addEventListener("click", ev => {
    const el = ev.target.closest("[data-act]");
    if (!el || !body.contains(el)) return;
    const act = el.dataset.act;
    if (act === "plan" || act === "map") ev.preventDefault();
    if (act === "plan") openPlan(el.dataset.planId);
    else if (act === "map") showOnMap(Number(el.dataset.ra), Number(el.dataset.dec));
    else if (act === "copy") copyText(el);
    else if (act === "older") loadOlder();
    else if (act === "night-more") nightMore(el.dataset.night);
    else if (act === "chip") {
      const id = el.dataset.chip;
      st.chips[id] = st.chips[id] === false;
      savePrefsSoon();
      render();
    } else if (act === "chips-all") {
      st.chips = Object.fromEntries(CHIPS.map(c => [c.id, true]));
      savePrefsSoon();
      render();
    } else if (act === "dismiss") {
      post(`/api/activity/events/${encodeURIComponent(el.dataset.id)}/dismiss`)
        .then(() => loadSummary()).then(render).catch(() => {});
    }
  });

  body.addEventListener("toggle", ev => {
    const d = ev.target;
    if (d && d.matches && d.matches("details.night")) st.openNights.set(d.dataset.night, d.open);
  }, true);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });

  async function onTabShown(shown) {
    // app.js calls this on every layout change, so act on the opening only.
    const wasShown = st.visible;
    st.visible = shown;
    if (!shown || wasShown || st.gone) return;
    await loadFirstPage();
    // The previous visit's marker was kept for highlighting; now move it on.
    try { await post("/api/activity/seen"); await loadSummary(); } catch { /* try next visit */ }
  }

  loadSummary().catch(() => {});
  startPolling();
  return { onTabShown };
}
