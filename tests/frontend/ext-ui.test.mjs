import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  railIconPath, railPageOk, railTabsFrom, fillColorFor, tilePlansHtml,
  tilePanelModeOf, tilePanelTitleHtml, tilePanelAction, runTilePanelAction,
  tileStyleFrom, resolveTileDraw,
  retryDelayMs, sourcesNeedRetry, extensionStatusModel, sourceState,
  railTabWidthPx, railWidthDefault, layerStateMessage, bridgeRequest,
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

describe("tileStyleFrom", () => {
  it("keeps valid keys", () => {
    assert.deepEqual(
      tileStyleFrom({ style: { color: "#ff8800", fill_opacity: 0.3, line_width: 2 } }),
      { color: "#ff8800", fill_opacity: 0.3, line_width: 2 });
    assert.deepEqual(tileStyleFrom({ style: { color: "#ff8800", line_width: 0.5 } }),
      { color: "#ff8800", line_width: 0.5 });
  });

  it("keeps fill_opacity null as outline only", () => {
    assert.deepEqual(tileStyleFrom({ style: { fill_opacity: null } }), { fill_opacity: null });
  });

  it("ignores each invalid key without affecting the others", () => {
    assert.deepEqual(
      tileStyleFrom({ style: { color: "red", fill_opacity: 1.5, line_width: 7 } }), {});
    assert.deepEqual(
      tileStyleFrom({ style: { color: "#abc", fill_opacity: "0.5", line_width: 0.4 } }), { color: "#aabbcc" });
    assert.deepEqual(tileStyleFrom({ style: { line_width: NaN, fill_opacity: NaN } }), {});
  });

  it("returns nothing for a missing or malformed style", () => {
    for (const m of [undefined, {}, { style: null }, { style: "x" }, { style: [1] }]) {
      assert.deepEqual(tileStyleFrom(m), {}, JSON.stringify(m));
    }
  });
});

describe("resolveTileDraw", () => {
  it("uses the base colour, source fill and default width with no tile style", () => {
    assert.deepEqual(resolveTileDraw({}, "#111111", 0.2),
      { color: "#111111", fillOpacity: 0.2, lineWidth: 0.5 });
    assert.deepEqual(resolveTileDraw(undefined, "#111111", undefined, 1),
      { color: "#111111", fillOpacity: undefined, lineWidth: 1 });
  });

  it("lets the tile style beat the facet or priority colour, source fill and default width", () => {
    assert.deepEqual(
      resolveTileDraw({ style: { color: "#abc", fill_opacity: 0.6, line_width: 3 } }, "#111111", 0.2),
      { color: "#aabbcc", fillOpacity: 0.6, lineWidth: 3 });
  });

  it("treats a null tile fill as outline only even when the source fills", () => {
    assert.equal(resolveTileDraw({ style: { fill_opacity: null } }, "#111111", 0.2).fillOpacity, null);
    assert.equal(fillColorFor("#111111", null), null);
  });

  it("falls back per key when a tile key is invalid", () => {
    assert.deepEqual(
      resolveTileDraw({ style: { color: "blue", fill_opacity: 2, line_width: 9 } }, "#111111", 0.2),
      { color: "#111111", fillOpacity: 0.2, lineWidth: 0.5 });
  });
});

describe("retry backoff", () => {
  it("backs off 2, 4, 8 s and stops after five tries", () => {
    assert.deepEqual([0, 1, 2, 3, 4].map(retryDelayMs), [2000, 4000, 8000, 8000, 8000]);
    assert.equal(retryDelayMs(5), null);
    assert.equal(retryDelayMs(-1), null);
    assert.equal(retryDelayMs(undefined), null);
  });

  it("retries only while a source is loading or errored", () => {
    assert.equal(sourcesNeedRetry([{ status: "ready" }, { status: "empty" }, {}]), false);
    assert.equal(sourcesNeedRetry([{ status: "ready" }, { status: "loading" }]), true);
    assert.equal(sourcesNeedRetry([{ status: "error" }]), true);
    assert.equal(sourcesNeedRetry(undefined), false);
    assert.equal(sourceState({}), "ready");
    assert.equal(sourceState({ status: "weird" }), "ready");
  });
});

describe("extensionStatusModel", () => {
  const manifest = [{ extension: "nights" }, { extension: "other_ext" }, null];

  it("groups sources under their extension, matching underscores to hyphens", () => {
    const m = extensionStatusModel(manifest, [
      { id: "a", label: "A", status: "ready", extension: "nights" },
      { id: "b", label: "B", status: "ready", extension: "other-ext" },
    ]);
    assert.deepEqual(m.groups.map(g => [g.id, g.sources.length]), [["nights", 1], ["other_ext", 1]]);
    assert.equal(m.settled, true);
    assert.equal(m.summary, "Extensions: 2 loaded");
  });

  it("rolls an extension up to error, then loading, then empty, then ready", () => {
    const m = extensionStatusModel([{ extension: "n" }], [
      { id: "a", status: "ready", extension: "n" },
      { id: "b", status: "error", error: "TypeError: x", extension: "n" },
      { id: "c", status: "loading", extension: "n" },
    ]);
    assert.equal(m.groups[0].state, "error");
    assert.equal(m.groups[0].sources[1].reason, "TypeError: x");
    assert.equal(m.settled, false);
    assert.equal(m.loaded, 0);
    const l = extensionStatusModel([{ extension: "n" }], [{ id: "c", status: "loading", extension: "n" }]);
    assert.equal(l.groups[0].state, "loading");
    const e = extensionStatusModel([{ extension: "n" }], [{ id: "c", status: "empty", extension: "n" }]);
    assert.equal(e.groups[0].state, "empty");
    assert.equal(e.settled, true);
  });

  it("lists an extension with no sources as ready, and unowned sources under Other sources", () => {
    const m = extensionStatusModel([{ extension: "n" }], [{ id: "x", label: "X", status: "ready" }]);
    assert.equal(m.groups[0].state, "ready");
    assert.equal(m.groups[1].label, "Other sources");
    assert.equal(m.groups[1].sources[0].label, "X");
  });
});

describe("railTabWidthPx", () => {
  it("is normal unless wide", () => {
    assert.equal(railTabWidthPx(false, 420, 1600, false), 420);
  });
  it("is three times normal, held to 70% of the window", () => {
    assert.equal(railTabWidthPx(true, 400, 2000, false), 1200);
    assert.equal(railTabWidthPx(true, 400, 1200, false), 840);
  });
  it("never goes below normal, and is full width on a phone", () => {
    assert.equal(railTabWidthPx(true, 400, 400, false), 400);
    assert.equal(railTabWidthPx(true, 400, 390, true), 390);
    assert.equal(railTabWidthPx(false, 400, 390, true), 390);
  });
  it("reads the manifest default", () => {
    assert.equal(railWidthDefault({ width: "wide" }), "wide");
    assert.equal(railWidthDefault({ width: "huge" }), "normal");
    assert.equal(railWidthDefault(undefined), "normal");
  });
  it("railTabsFrom carries a valid width through", () => {
    const tab = { id: "n", label: "N", page: "/api/ext/nights/ui" };
    const tabs = railTabsFrom([{ extension: "nights", rail_tabs: [
      { ...tab, width: "wide" }, { ...tab, id: "m", width: "x" }] }]);
    assert.deepEqual(tabs.map(t => t.width), ["wide", "normal"]);
  });
});

describe("layerStateMessage", () => {
  it("lists every tile source with its enabled state", () => {
    const msg = layerStateMessage(
      [{ id: "a", label: "A", color: "#abc", extension: "nights" }, { id: "b" }],
      { a: { enabled: true } }, 1);
    assert.deepEqual(msg, {
      type: "acp:layers", timeAware: true,
      layers: [
        { id: "a", label: "A", enabled: true, color: "#abc", kind: "tiles", extension: "nights" },
        { id: "b", label: "b", enabled: false, color: "", kind: "tiles", extension: "" },
      ],
    });
  });
  it("copes with no sources", () => {
    assert.deepEqual(layerStateMessage(undefined, {}, false),
      { type: "acp:layers", timeAware: false, layers: [] });
  });
});

describe("bridgeRequest", () => {
  const frame = {};
  const origin = "https://acp.test";
  const ev = (data, over = {}) => ({ origin, source: frame, data, ...over });

  it("accepts the three request types from a rail iframe at our origin", () => {
    assert.deepEqual(bridgeRequest(ev({ type: "acp:layers:get" }), [frame], origin), { kind: "get" });
    assert.deepEqual(bridgeRequest(ev({ type: "acp:layer:set", id: "a", enabled: false }), [frame], origin),
      { kind: "layer-set", id: "a", enabled: false });
    assert.deepEqual(bridgeRequest(ev({ type: "acp:time-aware:set", enabled: true }), [frame], origin),
      { kind: "time-aware-set", enabled: true });
  });

  it("rejects another origin, a window that isn't a rail iframe, and no source", () => {
    const get = { type: "acp:layers:get" };
    assert.equal(bridgeRequest(ev(get, { origin: "https://evil.test" }), [frame], origin), null);
    assert.equal(bridgeRequest(ev(get, { source: {} }), [frame], origin), null);
    assert.equal(bridgeRequest(ev(get, { source: null }), [frame], origin), null);
    assert.equal(bridgeRequest(ev(get), [], origin), null);
    assert.equal(bridgeRequest(null, [frame], origin), null);
  });

  it("rejects malformed payloads", () => {
    for (const d of [
      null, "acp:layers:get", [], {}, { type: "other" },
      { type: "acp:layer:set", id: "a", enabled: "yes" },
      { type: "acp:layer:set", id: "", enabled: true },
      { type: "acp:layer:set", id: 3, enabled: true },
      { type: "acp:layer:set", id: "x".repeat(201), enabled: true },
      { type: "acp:time-aware:set" },
      { type: "acp:time-aware:set", enabled: 1 },
    ]) {
      assert.equal(bridgeRequest(ev(d), [frame], origin), null, JSON.stringify(d));
    }
  });
});
