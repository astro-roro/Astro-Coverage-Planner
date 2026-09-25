// The "⋯" menu that holds a row's actions: plan rows, project rows,
// coverage target rows, the target detail panel, and right-click on the map.
//
// The item builders say which actions each kind of row offers; the app maps
// an item id to the route it already had. openMenu() draws one menu at a
// time, anchored under its button or at the pointer, and keeps it on screen.

export const MENU_BTN_CLASS = "row-menu-btn";

// `attrs` is an already escaped string of data-* attributes that tells the
// app which row the button belongs to.
export function menuButtonHtml(label, attrs = "") {
  return `<button type="button" class="${MENU_BTN_CLASS}" aria-haspopup="menu" aria-expanded="false"
      aria-label="${label}" title="${label}" ${attrs}>⋯</button>`;
}

// A plan hidden through its project is unhidden from the project row, so its
// own menu says so rather than offering a Hide that would change nothing.
export function planMenuItems({ selfHidden = false, projectHidden = false } = {}) {
  const items = [{ id: "open", label: "Open" }];
  if (selfHidden) items.push({ id: "unhide", label: "Unhide" });
  else if (projectHidden) items.push({ id: "unhide-project-note", label: "Hidden with its project", disabled: true });
  else items.push({ id: "hide", label: "Hide", title: "Hide this plan from the list and the map. Its state is not changed." });
  return items;
}

export function projectMenuItems({ hidden = false } = {}) {
  return hidden
    ? [{ id: "unhide", label: "Unhide project", title: "Show this project and its plans again" }]
    : [{ id: "hide", label: "Hide project", title: "Hide this project and its plans from the list. Their states are not changed." }];
}

export function targetMenuItems({ hidden = false, finished = false, hasOverride = false } = {}) {
  const items = [
    hidden
      ? { id: "unhide", label: "Unhide", title: "Show this target in the list and on the map again" }
      : { id: "hide", label: "Hide", title: "Leave this target out of the list, the map and gap finding" },
    finished
      ? { id: "mark-in-progress", label: "Mark in-progress" }
      : { id: "mark-finished", label: "Mark finished" },
  ];
  if (hasOverride) {
    items.push({ id: "clear-override", label: "Clear override", title: "Remove the manual mark and go back to what the plans say" });
  }
  return items;
}

// One line for the detail panel, in place of the old row of buttons.
export function targetStatusText({ finished = false, hasOverride = false, hasPlans = false } = {}) {
  if (hasOverride) return finished ? "Marked finished by hand" : "Marked in-progress by hand";
  if (finished) return "All plan goals met";
  return hasPlans ? "Plan goals not yet met" : "No plan set, treated as unfinished";
}

// Where to put a menu of `size` so it stays inside the viewport. With an
// anchor rect it drops below the button, right edges aligned, and flips
// above when there is no room below. With a point it opens at the pointer
// and flips left or up near the far edges.
export function placeMenu({ anchorRect = null, point = null }, size, viewport, margin = 4) {
  let left, top;
  if (anchorRect) {
    left = anchorRect.right - size.width;
    top = anchorRect.bottom + 2;
    if (top + size.height > viewport.height - margin) top = anchorRect.top - size.height - 2;
  } else {
    left = point.x;
    top = point.y;
    if (left + size.width > viewport.width - margin) left = point.x - size.width;
    if (top + size.height > viewport.height - margin) top = point.y - size.height;
  }
  const clamp = (v, extent, room) => Math.max(margin, Math.min(v, room - extent - margin));
  return { left: clamp(left, size.width, viewport.width), top: clamp(top, size.height, viewport.height) };
}

let current = null;

export function isMenuOpen() { return current != null; }

// Close whichever menu is open. `restoreFocus` sends focus back to the
// button or row that opened it.
export function closeMenu({ restoreFocus = false } = {}) {
  const m = current;
  if (!m) return;
  current = null;
  m.teardown();
  m.el.remove();
  m.anchor?.setAttribute("aria-expanded", "false");
  if (restoreFocus) m.returnFocus?.focus?.();
}

// Open a menu. Pass `anchor` (the ⋯ button) or `point` ({x, y}, for a
// right-click). Opening again from the anchor that is already open closes it,
// so the button toggles. Returns true when a menu is left open.
export function openMenu({
  items, onSelect, anchor = null, point = null, returnFocus = anchor, label = "Actions",
  doc = globalThis.document, win = globalThis.window,
}) {
  if (current && anchor && current.anchor === anchor) { closeMenu({ restoreFocus: true }); return false; }
  closeMenu();
  const el = doc.createElement("div");
  el.className = "row-menu";
  el.setAttribute("role", "menu");
  el.setAttribute("aria-label", label);
  const buttons = items.map(it => {
    const b = doc.createElement("button");
    b.type = "button";
    b.className = "row-menu-item";
    b.setAttribute("role", "menuitem");
    b.setAttribute("tabindex", "-1");
    b.textContent = it.label;
    if (it.title) b.title = it.title;
    if (it.disabled) b.setAttribute("aria-disabled", "true");
    b.addEventListener("click", ev => {
      ev.stopPropagation?.();
      if (it.disabled) return;
      closeMenu({ restoreFocus: true });
      onSelect(it.id);
    });
    el.appendChild(b);
    return b;
  });
  const enabled = buttons.filter((_, i) => !items[i].disabled);
  doc.body.appendChild(el);

  const r = el.getBoundingClientRect();
  const pos = placeMenu(
    { anchorRect: anchor ? anchor.getBoundingClientRect() : null, point },
    { width: r.width, height: r.height },
    { width: win?.innerWidth ?? 1e6, height: win?.innerHeight ?? 1e6 },
  );
  el.style.left = `${pos.left}px`;
  el.style.top = `${pos.top}px`;

  const move = step => {
    if (!enabled.length) return;
    const i = enabled.indexOf(doc.activeElement);
    const next = step === "first" ? 0
      : step === "last" ? enabled.length - 1
      : (i + step + enabled.length) % enabled.length;
    enabled[next].focus();
  };
  const onKey = ev => {
    const k = ev.key;
    if (k === "ArrowDown") move(1);
    else if (k === "ArrowUp") move(-1);
    else if (k === "Home") move("first");
    else if (k === "End") move("last");
    else if (k === "Escape" || k === "Tab") closeMenu({ restoreFocus: true });
    else return;
    // Escape must not also reach the app's own Escape (go up one panel level).
    ev.preventDefault();
    ev.stopPropagation();
  };
  const onOutside = ev => {
    const t = ev.target;
    if (el.contains(t)) return;
    // The anchor's own click toggles the menu; let that handle it.
    if (anchor && anchor.contains(t)) return;
    closeMenu();
  };
  // The menu is placed in viewport coordinates, so any scroll or resize
  // would leave it floating in the wrong spot.
  const onShift = ev => { if (!el.contains(ev.target)) closeMenu(); };
  const onResize = () => closeMenu();
  el.addEventListener("keydown", onKey);
  doc.addEventListener("pointerdown", onOutside, true);
  doc.addEventListener("scroll", onShift, true);
  win?.addEventListener?.("resize", onResize);

  anchor?.setAttribute("aria-expanded", "true");
  current = {
    el, anchor, returnFocus,
    teardown() {
      el.removeEventListener("keydown", onKey);
      doc.removeEventListener("pointerdown", onOutside, true);
      doc.removeEventListener("scroll", onShift, true);
      win?.removeEventListener?.("resize", onResize);
    },
  };
  move("first");
  return true;
}
