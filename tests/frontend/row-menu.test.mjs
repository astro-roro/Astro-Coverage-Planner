import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  closeMenu,
  isMenuOpen,
  menuButtonHtml,
  openMenu,
  placeMenu,
  planMenuItems,
  projectMenuItems,
  targetMenuItems,
  targetStatusText,
} from "../../static/row-menu.mjs";

// Just enough DOM for openMenu(): elements that nest, take listeners,
// take focus and report a size.
class FakeEl {
  constructor(doc, tag) {
    this.doc = doc; this.tagName = tag.toUpperCase();
    this.children = []; this.parent = null; this.attrs = {}; this.style = {};
    this.listeners = {}; this.textContent = ""; this.rect = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
  contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; }
  focus() { this.doc.activeElement = this; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter(x => x !== f); }
  getBoundingClientRect() { return this.tagName === "DIV" ? { width: 120, height: 60 } : this.rect; }
  fire(type, props = {}) {
    const ev = { type, target: this, defaultPrevented: false, propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...props };
    for (let n = this; n; n = n.parent) for (const f of n.listeners[type] || []) f(ev);
    return ev;
  }
}
class FakeDoc {
  constructor() { this.body = new FakeEl(this, "body"); this.listeners = {}; this.activeElement = this.body; }
  createElement(t) { return new FakeEl(this, t); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter(x => x !== f); }
  firePointerDown(target) { for (const f of this.listeners.pointerdown || []) f({ target }); }
}
const win = { innerWidth: 800, innerHeight: 600, addEventListener() {}, removeEventListener() {} };

function setup() {
  const doc = new FakeDoc();
  const anchor = doc.body.appendChild(doc.createElement("button"));
  anchor.rect = { left: 700, top: 100, right: 720, bottom: 120, width: 20, height: 20 };
  return { doc, anchor };
}
const menuEl = doc => doc.body.children.find(c => c.attrs.role === "menu");
const itemsOf = doc => menuEl(doc).children;
const ITEMS = [{ id: "a", label: "A" }, { id: "b", label: "B", disabled: true }, { id: "c", label: "C" }];

describe("row menu", () => {
  beforeEach(() => closeMenu());

  it("opens under its button, focuses the first item and marks the button expanded", () => {
    const { doc, anchor } = setup();
    openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win });
    assert.ok(isMenuOpen());
    assert.equal(itemsOf(doc).length, 3);
    assert.equal(doc.activeElement, itemsOf(doc)[0]);
    assert.equal(anchor.attrs["aria-expanded"], "true");
    assert.equal(menuEl(doc).style.left, "600px");
    assert.equal(menuEl(doc).style.top, "122px");
  });

  it("runs the chosen action once, closes and returns focus", () => {
    const { doc, anchor } = setup();
    const got = [];
    openMenu({ items: ITEMS, onSelect: id => got.push(id), anchor, doc, win });
    itemsOf(doc)[2].fire("click");
    assert.deepEqual(got, ["c"]);
    assert.ok(!isMenuOpen());
    assert.equal(menuEl(doc), undefined);
    assert.equal(doc.activeElement, anchor);
    assert.equal(anchor.attrs["aria-expanded"], "false");
  });

  it("ignores a click on a disabled item", () => {
    const { doc, anchor } = setup();
    const got = [];
    openMenu({ items: ITEMS, onSelect: id => got.push(id), anchor, doc, win });
    itemsOf(doc)[1].fire("click");
    assert.deepEqual(got, []);
    assert.ok(isMenuOpen());
  });

  it("moves with the arrow keys, wraps, skips disabled items, and takes Home and End", () => {
    const { doc, anchor } = setup();
    openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win });
    const [a, , c] = itemsOf(doc);
    const key = k => doc.activeElement.fire("keydown", { key: k });
    key("ArrowDown"); assert.equal(doc.activeElement, c);
    key("ArrowDown"); assert.equal(doc.activeElement, a);
    key("ArrowUp"); assert.equal(doc.activeElement, c);
    key("Home"); assert.equal(doc.activeElement, a);
    key("End"); assert.equal(doc.activeElement, c);
  });

  it("closes on Escape, returns focus, and keeps Escape from reaching the page", () => {
    const { doc, anchor } = setup();
    openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win });
    const ev = doc.activeElement.fire("keydown", { key: "Escape" });
    assert.ok(!isMenuOpen());
    assert.equal(doc.activeElement, anchor);
    assert.ok(ev.propagationStopped);
    assert.ok(ev.defaultPrevented);
  });

  it("closes on a press outside, but not on a press inside", () => {
    const { doc, anchor } = setup();
    openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win });
    doc.firePointerDown(itemsOf(doc)[0]);
    assert.ok(isMenuOpen());
    doc.firePointerDown(doc.body);
    assert.ok(!isMenuOpen());
    assert.deepEqual(doc.listeners.pointerdown, []);
  });

  it("keeps one menu open at a time", () => {
    const { doc, anchor } = setup();
    const other = doc.body.appendChild(doc.createElement("button"));
    openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win });
    openMenu({ items: ITEMS, onSelect() {}, anchor: other, doc, win });
    assert.equal(doc.body.children.filter(c => c.attrs.role === "menu").length, 1);
    assert.equal(anchor.attrs["aria-expanded"], "false");
    assert.equal(other.attrs["aria-expanded"], "true");
  });

  it("toggles closed when its own button is pressed again", () => {
    const { doc, anchor } = setup();
    assert.equal(openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win }), true);
    assert.equal(openMenu({ items: ITEMS, onSelect() {}, anchor, doc, win }), false);
    assert.ok(!isMenuOpen());
  });

  it("opens at the pointer for a right-click", () => {
    const { doc } = setup();
    openMenu({ items: ITEMS, onSelect() {}, point: { x: 50, y: 40 }, doc, win });
    assert.equal(menuEl(doc).style.left, "50px");
    assert.equal(menuEl(doc).style.top, "40px");
  });
});

describe("placeMenu keeps the menu on screen", () => {
  const vp = { width: 800, height: 600 };
  const size = { width: 120, height: 60 };
  it("flips above the button near the bottom edge", () => {
    const pos = placeMenu({ anchorRect: { left: 700, right: 720, top: 570, bottom: 590 } }, size, vp);
    assert.equal(pos.top, 570 - 60 - 2);
  });
  it("clamps a button near the left edge", () => {
    const pos = placeMenu({ anchorRect: { left: 0, right: 20, top: 10, bottom: 30 } }, size, vp);
    assert.equal(pos.left, 4);
  });
  it("flips left and up for a right-click in the far corner", () => {
    assert.deepEqual(placeMenu({ point: { x: 790, y: 590 } }, size, vp), { left: 670, top: 530 });
  });
});

describe("which actions each row offers", () => {
  const ids = items => items.filter(i => !i.disabled).map(i => i.id);
  it("plan rows: open and hide, or open and unhide", () => {
    assert.deepEqual(ids(planMenuItems({})), ["open", "hide"]);
    assert.deepEqual(ids(planMenuItems({ selfHidden: true })), ["open", "unhide"]);
    assert.deepEqual(ids(planMenuItems({ selfHidden: true, projectHidden: true })), ["open", "unhide"]);
  });
  it("a plan hidden through its project offers no hide of its own", () => {
    const items = planMenuItems({ projectHidden: true });
    assert.deepEqual(ids(items), ["open"]);
    assert.ok(items.some(i => i.disabled && /project/.test(i.label)));
  });
  it("project rows: hide project or unhide project", () => {
    assert.deepEqual(projectMenuItems({}).map(i => i.label), ["Hide project"]);
    assert.deepEqual(projectMenuItems({ hidden: true }).map(i => i.label), ["Unhide project"]);
  });
  it("target rows: hide, mark, and clear override only when there is one", () => {
    assert.deepEqual(ids(targetMenuItems({})), ["hide", "mark-finished"]);
    assert.deepEqual(ids(targetMenuItems({ hidden: true, finished: true, hasOverride: true })),
      ["unhide", "mark-in-progress", "clear-override"]);
  });
  it("status line names a hand-set mark", () => {
    assert.equal(targetStatusText({ finished: true, hasOverride: true }), "Marked finished by hand");
    assert.equal(targetStatusText({ finished: false, hasOverride: true }), "Marked in-progress by hand");
    assert.equal(targetStatusText({ finished: true }), "All plan goals met");
    assert.equal(targetStatusText({ hasPlans: true }), "Plan goals not yet met");
  });
  it("the button is labelled and announces a menu", () => {
    const html = menuButtonHtml("Plan actions", 'data-x="1"');
    assert.match(html, /class="row-menu-btn"/);
    assert.match(html, /aria-haspopup="menu"/);
    assert.match(html, /aria-label="Plan actions"/);
    assert.match(html, /data-x="1"/);
  });
});
