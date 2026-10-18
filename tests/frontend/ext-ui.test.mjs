import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  railIconPath, railPageOk, railTabsFrom, fillColorFor, tilePlansHtml,
  tilePanelModeOf, tilePanelTitleHtml, tilePanelAction, runTilePanelAction,
} from "../../static/ext-ui.mjs";

const esc = s => String(s).replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

describe("railIconPath", () => {
  it("maps known names to the rail's own icons", () => {
    assert.equal(railIconPath("plans"), railIconPath("list"));
    assert.equal(railIconPath("Filters"), railIconPath("filter"));
    assert.notEqual(railIconPath("library"), railIconPath("list"));
  });

  it("falls back to one generic icon for unknown or missing names", () => {
    assert.equal(railIconPath("rocket"), railIconPath(undefined));
    assert.equal(railIconPath(""), railIconPath("constructor"));
    assert.notEqual(railIconPath("rocket"), railIconPath("list"));
  });
});

describe("railPageOk", () => {
  it("accepts paths under the extension's prefix, with hyphenated underscores", () => {
    assert.ok(railPageOk("nights", "/api/ext/nights/ui"));
    assert.ok(railPageOk("nights", "/api/ext/nights/ui?x=1"));
    assert.ok(railPageOk("my_ext", "/api/ext/my-ext/ui"));
  });

  it("rejects other prefixes, other origins and traversal", () => {
    for (const page of [
      "/api/ext/other/ui", "/api/ext/nights-evil/ui", "https://x.test/api/ext/nights/ui",
      "//x.test/api/ext/nights/ui", "javascript:alert(1)", "api/ext/nights/ui",
      "/api/ext/nights/../x", "/api/ext/nights/%2e%2e/x", "/api/ext/nights\\x", null, 3,
    ]) {
      assert.equal(railPageOk("nights", page), false, String(page));
    }
  });
});

describe("railTabsFrom", () => {
  const good = { id: "n", label: "Nights", icon: "calendar", page: "/api/ext/nights/ui" };

  it("builds a unique key per extension and tab", () => {
    const tabs = railTabsFrom([
      { extension: "nights", rail_tabs: [good] },
      { extension: "other", rail_tabs: [{ ...good, page: "/api/ext/other/ui" }] },
    ]);
    assert.deepEqual(tabs.map(t => t.key), ["ext-nights-n", "ext-other-n"]);
    assert.equal(tabs[0].label, "Nights");
  });

  it("drops invalid tabs and tolerates entries with no rail_tabs", () => {
    const tabs = railTabsFrom([
      { extension: "nights", actions: [] },
      { extension: "nights", rail_tabs: [
        good,
        { ...good, id: "off", page: "https://x.test/" },
        { ...good, id: "nolabel", label: " " },
        { ...good, id: "bad id" },
        null,
      ] },
      null,
    ]);
    assert.deepEqual(tabs.map(t => t.key), ["ext-nights-n"]);
  });

  it("returns nothing for a non-array manifest", () => {
    assert.deepEqual(railTabsFrom(undefined), []);
    assert.deepEqual(railTabsFrom({}), []);
  });
});

describe("fillColorFor", () => {
  it("adds an alpha channel to a hex colour", () => {
    assert.equal(fillColorFor("#ff8800", 1), "#ff8800ff");
    assert.equal(fillColorFor("#ff8800", 0), "#ff880000");
    assert.equal(fillColorFor("#f80", 0.5), "#ff880080");
  });

  it("returns null for an unset or invalid opacity, so the outline-only default stays", () => {
    for (const o of [undefined, null, "0.5", NaN, -0.1, 1.1]) {
      assert.equal(fillColorFor("#ff8800", o), null, String(o));
    }
  });

  it("returns null for a colour it can't extend", () => {
    assert.equal(fillColorFor("red", 0.5), null);
    assert.equal(fillColorFor("rgb(1,2,3)", 0.5), null);
    assert.equal(fillColorFor(undefined, 0.5), null);
  });
});

describe("tilePlansHtml", () => {
  it("renders a link when plan_id is set and plain text when it is null", () => {
    const html = tilePlansHtml([
      { plan_id: "p1", name: "M31 mosaic", detail: "3 of 6 panels" },
      { plan_id: null, name: "Unplanned", detail: "" },
    ], esc);
    assert.match(html, /<a href="#" class="tile-plan-link" data-plan-id="p1">M31 mosaic<\/a>/);
    assert.match(html, /class="tile-plan-detail">3 of 6 panels</);
    assert.match(html, /<span class="tile-plan-name">Unplanned<\/span>/);
    assert.doesNotMatch(html, /data-plan-id="null"/);
  });

  it("shows plain text when the plan isn't loaded in ACP", () => {
    const html = tilePlansHtml([{ plan_id: "gone", name: "Old" }], esc, () => false);
    assert.doesNotMatch(html, /<a /);
    assert.match(html, /Old/);
  });

  it("escapes names, details and ids", () => {
    const html = tilePlansHtml([
      { plan_id: '"><img src=x>', name: "<b>n</b>", detail: "<script>x</script>" },
    ], esc);
    assert.doesNotMatch(html, /<b>|<script>|<img/);
    assert.match(html, /&lt;b&gt;n&lt;\/b&gt;/);
  });

  it("ignores malformed input and returns an empty string when nothing is valid", () => {
    assert.equal(tilePlansHtml(undefined, esc), "");
    assert.equal(tilePlansHtml("x", esc), "");
    assert.equal(tilePlansHtml([null, 3, { name: "" }, { plan_id: "p" }], esc), "");
  });
});

describe("tilePanelModeOf", () => {
  it("is replace only for tile_panel 'replace'; everything else appends", () => {
    assert.equal(tilePanelModeOf({ tile_panel: "replace" }), "replace");
    for (const m of [{ tile_panel: "append" }, { tile_panel: null }, { tile_panel: "Replace" }, {}, undefined]) {
      assert.equal(tilePanelModeOf(m), "append", JSON.stringify(m));
    }
  });
});

describe("tilePanelTitleHtml", () => {
  it("shows the tile id as the title with the source label as a muted line above", () => {
    const html = tilePanelTitleHtml("Nights gap 2", "Gap 2: The plans run out", esc);
    assert.equal(html,
      '<div class="tile-source-label">Nights gap 2</div><h3>Gap 2: The plans run out</h3>');
    assert.doesNotMatch(html, /·/);
  });

  it("falls back to the label alone when the tile has no id", () => {
    assert.equal(tilePanelTitleHtml("Src", "", esc), "<h3>Src</h3>");
    assert.equal(tilePanelTitleHtml("Src", undefined, esc), "<h3>Src</h3>");
  });

  it("escapes both strings", () => {
    const html = tilePanelTitleHtml("<i>a</i>", "<b>b</b>", esc);
    assert.doesNotMatch(html, /<i>|<b>/);
  });
});

// Minimal stand-in for a DOM element: attributes plus a parent chain.
function el(attrs, parent = null) {
  const node = {
    parent,
    getAttribute: n => (n in attrs ? attrs[n] : null),
    hasAttribute: n => n in attrs,
    contains: other => { for (let x = other; x; x = x.parent) if (x === node) return true; return false; },
    closest: () => {
      for (let x = node; x; x = x.parent) {
        if (x.hasAttribute?.("data-acp-plan-id") || x.hasAttribute?.("data-acp-new-plan")
          || x.hasAttribute?.("data-acp-goto")) return x;
      }
      return null;
    },
  };
  return node;
}

describe("tilePanelAction", () => {
  const root = el({});

  it("reads a plan id, including from a child of the marked element", () => {
    const btn = el({ "data-acp-plan-id": "p1" }, root);
    const inner = el({}, btn);
    assert.deepEqual(tilePanelAction(inner, root), { type: "plan", id: "p1" });
  });

  it("reads a new-plan action with position and optional name", () => {
    const a = el({ "data-acp-new-plan": "", "data-ra": "10.5", "data-dec": "-20", "data-name": " Gap " }, root);
    assert.deepEqual(tilePanelAction(a, root), { type: "new-plan", ra: 10.5, dec: -20, name: "Gap" });
    const b = el({ "data-acp-new-plan": "", "data-ra": "10", "data-dec": "5" }, root);
    assert.equal(tilePanelAction(b, root).name, "");
  });

  it("reads a goto action with an optional fov and wraps RA into 0..360", () => {
    const a = el({ "data-acp-goto": "", "data-ra": "370", "data-dec": "5", "data-fov": "3" }, root);
    assert.deepEqual(tilePanelAction(a, root), { type: "goto", ra: 10, dec: 5, fov: 3 });
    const b = el({ "data-acp-goto": "", "data-ra": "1", "data-dec": "2" }, root);
    assert.equal(tilePanelAction(b, root).fov, null);
  });

  it("ignores missing, blank or out-of-range coordinates", () => {
    for (const attrs of [
      { "data-acp-goto": "", "data-dec": "1" },
      { "data-acp-goto": "", "data-ra": "", "data-dec": "1" },
      { "data-acp-new-plan": "", "data-ra": "x", "data-dec": "1" },
      { "data-acp-new-plan": "", "data-ra": "1", "data-dec": "91" },
      { "data-acp-plan-id": "  " },
    ]) {
      assert.equal(tilePanelAction(el(attrs, root), root), null, JSON.stringify(attrs));
    }
  });

  it("ignores elements outside the container and plain elements", () => {
    const outside = el({ "data-acp-plan-id": "p1" });
    assert.equal(tilePanelAction(outside, root), null);
    assert.equal(tilePanelAction(el({}, root), root), null);
  });
});

describe("runTilePanelAction", () => {
  const calls = [];
  const handlers = {
    openPlan: id => calls.push(["plan", id]),
    newPlan: a => calls.push(["new", a.ra, a.dec, a.name]),
    goto: a => calls.push(["goto", a.ra, a.dec, a.fov]),
  };

  it("sends each action type to its own handler", () => {
    calls.length = 0;
    assert.ok(runTilePanelAction({ type: "plan", id: "p1" }, handlers));
    assert.ok(runTilePanelAction({ type: "new-plan", ra: 1, dec: 2, name: "n" }, handlers));
    assert.ok(runTilePanelAction({ type: "goto", ra: 3, dec: 4, fov: 5 }, handlers));
    assert.deepEqual(calls, [["plan", "p1"], ["new", 1, 2, "n"], ["goto", 3, 4, 5]]);
  });

  it("does nothing and returns false for no action", () => {
    calls.length = 0;
    assert.equal(runTilePanelAction(null, handlers), false);
    assert.deepEqual(calls, []);
  });
});
