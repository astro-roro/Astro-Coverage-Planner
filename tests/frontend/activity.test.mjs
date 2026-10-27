import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  DEFAULT_CHIPS,
  chipOf,
  collapseRows,
  isNewRow,
  isQuietProgress,
  lastNightHtml,
  masterRowStrings,
  mergeNights,
  needsYouHtml,
  nightHeading,
  pageState,
  railState,
  renderRowHtml,
  visibleRows,
} from "../../static/activity.mjs";

const css = readFileSync(new URL("../../static/style.css", import.meta.url), "utf8");

const row = (over = {}) => ({
  id: 1, source: "acp", key: "k", type: "plan_edited", at: "2026-10-13T19:10:00+11:00",
  night: "2026-10-13", title: "A title", detail: null, severity: "info", refs: {}, data: {},
  needs_you: false, received_at: "2026-10-13T08:10:00.000000Z",
  updated_at: "2026-10-13T08:10:00.000000Z", ...over,
});

describe("renderRowHtml", () => {
  it("39: escapes every event string and draws no element from them", () => {
    const html = renderRowHtml(row({
      title: "<img src=x onerror=alert(1)>",
      detail: "<script>boom()</script>",
      refs: { plan_id: '"><b>', target: "<i>t</i>", project: "<u>p</u>" },
    }));
    assert.ok(!html.includes("<img"));
    assert.ok(!html.includes("<script"));
    assert.ok(!html.includes("<b>"));
    assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  });

  it("shows the clock time from the event's own offset", () => {
    assert.ok(renderRowHtml(row()).includes(">19:10<"));
  });

  it("links a plan, the map, the AAC job and the upload review page", () => {
    const html = renderRowHtml(row({
      refs: { plan_id: "M42", ra_deg: 83.8, dec_deg: -5.4, aac_job: "f17d6737", upload_id: "abc123" },
    }));
    assert.ok(html.includes('data-act="plan"'));
    assert.ok(html.includes('data-ra="83.8"'));
    assert.ok(html.includes("AAC job f17d6737"));
    assert.ok(html.includes('href="/ext/nina-ts-sync/import/uploads/abc123"'));
  });

  it("draws a master with a copy button for each of its three strings", () => {
    const html = renderRowHtml(row({
      type: "master_built",
      refs: { master: { path: "/p/full_master_Ha.fit", mac_folder: "/Volumes/S/masters/",
                        windows_folder: "\\\\S\\S\\masters\\", file_name: "full_master_Ha.fit" } },
    }));
    assert.equal((html.match(/data-act="copy"/g) || []).length, 3);
    assert.ok(html.includes("/Volumes/S/masters/"));
  });

  it("marks a row new when it changed after the previous last_seen", () => {
    const r = row({ updated_at: "2026-10-13T08:10:00.000000Z" });
    assert.ok(renderRowHtml(r, { lastSeen: "2026-10-13T08:00:00.000000Z" }).includes("is-new"));
    assert.ok(!renderRowHtml(r, { lastSeen: "2026-10-13T09:00:00.000000Z" }).includes("is-new"));
    assert.equal(isNewRow(r, null), true);
  });

  it("an error row says so", () => {
    assert.ok(renderRowHtml(row({ severity: "error" })).includes("sev-error"));
  });
});

describe("railState", () => {
  it("40: badge, then dot, then nothing", () => {
    assert.deepEqual(railState({ needs_you: 2, has_new: true }), { badge: 2 });
    assert.deepEqual(railState({ needs_you: 0, has_new: true }), { dot: true });
    assert.equal(railState({ needs_you: 0, has_new: false }), null);
    assert.equal(railState(null), null);
  });
});

describe("visibleRows", () => {
  it("41: a switched-off chip hides its rows but not its errors", () => {
    const rows = [
      row({ id: 1, type: "imaging_target" }),
      row({ id: 2, type: "imaging_target", severity: "error" }),
      row({ id: 3, type: "plan_edited" }),
    ];
    const out = visibleRows(rows, { ...DEFAULT_CHIPS, imaging: false });
    assert.deepEqual(out.map(r => r.id), [2, 3]);
  });

  it("site and focus is off by default and unknown types fall under plans", () => {
    const rows = [row({ id: 1, type: "meridian_flip" }), row({ id: 2, type: "mystery" })];
    assert.deepEqual(visibleRows(rows, DEFAULT_CHIPS).map(r => r.id), [2]);
    assert.equal(chipOf("mystery"), "plans");
    assert.equal(chipOf("master_ready"), "masters");
  });
});

describe("pageState", () => {
  it("42: says how many nights are showing and when to offer more", () => {
    assert.deepEqual(pageState({ shown: 5, total: 7 }),
      { text: "Showing 5 of 7 nights", showLoadOlder: true });
    assert.deepEqual(pageState({ shown: 7, total: 7 }),
      { text: "Showing 7 of 7 nights", showLoadOlder: false });
  });

  it("reads right for one night and for none", () => {
    assert.equal(pageState({ shown: 1, total: 1 }).text, "Showing 1 of 1 night");
    assert.equal(pageState({ shown: 0, total: 0 }).text, "No activity yet");
  });
});

describe("masterRowStrings", () => {
  it("43: Mac folder, Windows folder, file name, in that order", () => {
    assert.deepEqual(masterRowStrings({
      path: "/p/x.fit", mac_folder: "/Volumes/S/m/", windows_folder: "\\\\S\\m\\", file_name: "x.fit",
    }), ["/Volumes/S/m/", "\\\\S\\m\\", "x.fit"]);
  });

  it("gives empty strings for a master with no folders", () => {
    assert.deepEqual(masterRowStrings({}), ["", "", ""]);
  });
});

describe("collapseRows", () => {
  const edit = (id, plan) => row({
    id, type: "plan_edited", title: "min altitude 20 to 30",
    refs: { plan_id: plan, project: "PNe Survey" },
    data: { changes: { min_altitude_deg: { from: 20, to: 30 } } },
  });

  it("folds consecutive identical edits across a project into one line", () => {
    const out = collapseRows([edit(1, "a"), edit(2, "b"), edit(3, "c")]);
    assert.equal(out.length, 1);
    assert.equal(out[0].title, "3 PNe Survey plans: min altitude 20 to 30");
    assert.equal(out[0].collapsed, 3);
  });

  it("leaves different changes, other projects and non-edits alone", () => {
    const other = { ...edit(4, "d"), refs: { plan_id: "d", project: "Other" } };
    const different = { ...edit(5, "e"), data: { changes: { priority: { from: "low", to: "high" } } } };
    const out = collapseRows([edit(1, "a"), other, different, row({ id: 6, type: "scan_finished" })]);
    assert.equal(out.length, 4);
  });
});

describe("quiet progress grouping", () => {
  const prog = (id, plan, delta) => row({
    id, type: "progress_report", title: `${plan}: whatever`, refs: { plan_id: plan, ra_deg: 1, dec_deg: 2 },
    data: { filters: { Ha: { hours: 1, goal: 5, delta_h: delta }, OIII: { hours: 1, goal: 5, delta_h: 0 } } },
  });

  it("decides quiet from the deltas, not the title", () => {
    assert.equal(isQuietProgress(prog(1, "a", 0)), true);
    assert.equal(isQuietProgress({ ...prog(1, "a", 0), title: "a: +9.00 h reported" }), true);
    assert.equal(isQuietProgress({ ...prog(1, "a", 0.5), title: "a: progress reported, no new hours" }), false);
    assert.equal(isQuietProgress(row({ type: "progress_report", data: {} })), false);
    assert.equal(isQuietProgress(row({ type: "plan_edited" })), false);
  });

  it("folds a run of quiet reports into one group and keeps every plan inside it", () => {
    const out = collapseRows([prog(1, "a", 0), prog(2, "b", 0), prog(3, "c", 0)]);
    assert.equal(out.length, 1);
    assert.equal(out[0].type, "progress_group");
    assert.equal(out[0].title, "3 plans reported progress, no new hours");
    assert.deepEqual(out[0].rows.map(r => r.id), [1, 2, 3]);
  });

  it("a report with hours or another event ends the run", () => {
    const out = collapseRows([prog(1, "a", 0), prog(2, "b", 0), prog(3, "c", 0.4),
      prog(4, "d", 0), row({ id: 5, type: "scan_finished" }), prog(6, "e", 0), prog(7, "f", 0)]);
    assert.deepEqual(out.map(r => r.type),
      ["progress_group", "progress_report", "progress_report", "scan_finished", "progress_group"]);
    assert.equal(out[2].id, 4);
  });

  it("a lone quiet report stays a plain row", () => {
    const out = collapseRows([prog(1, "a", 0)]);
    assert.equal(out[0].type, "progress_report");
  });

  it("draws the group closed by default, with each plan's links inside, and open on request", () => {
    const g = collapseRows([prog(1, "a", 0), prog(2, "b", 0)])[0];
    const closed = renderRowHtml(g);
    assert.ok(closed.includes("2 plans reported progress, no new hours"));
    assert.ok(!/<details[^>]* open/.test(closed));
    assert.equal((closed.match(/data-act="plan"/g) || []).length, 2);
    assert.equal((closed.match(/data-act="map"/g) || []).length, 2);
    assert.ok(/<details[^>]* open/.test(renderRowHtml(g, { openGroups: new Set([g.id]) })));
  });

  it("counts real events, not group rows", () => {
    const events = [prog(1, "a", 0), prog(2, "b", 0), prog(3, "c", 0)];
    assert.equal(visibleRows(events, DEFAULT_CHIPS).length, 3);
    assert.equal(collapseRows(visibleRows(events, DEFAULT_CHIPS)).length, 1);
  });
});

describe("mergeNights", () => {
  it("merges events by id so late events and updates land in nights already shown", () => {
    const have = [{ night: "2026-10-13", more: 0, events: [row({ id: 1, title: "old" })] }];
    const got = [{ night: "2026-10-13", more: 0, events: [row({ id: 1, title: "new" }), row({ id: 2 })] },
                 { night: "2026-10-12", more: 0, events: [row({ id: 3 })] }];
    const out = mergeNights(have, got);
    assert.deepEqual(out.map(n => n.night), ["2026-10-13", "2026-10-12"]);
    assert.deepEqual(out[0].events.map(e => [e.id, e.title]), [[2, "A title"], [1, "new"]]);
  });
});

describe("mergeNights with a complete refetch", () => {
  it("drops an event that was deleted when the refetched night is complete", () => {
    const have = [{ night: "2026-10-13", more: 0, events: [row({ id: 1 }), row({ id: 2 })] }];
    const got = [{ night: "2026-10-13", more: 0, events: [row({ id: 2 })] }];
    assert.deepEqual(mergeNights(have, got)[0].events.map(e => e.id), [2]);
  });

  it("keeps loaded events when the refetch is only the first page of a long night", () => {
    const have = [{ night: "2026-10-13", more: 0, events: [row({ id: 1 }), row({ id: 201 })] }];
    const got = [{ night: "2026-10-13", more: 50, events: [row({ id: 1 })] }];
    assert.deepEqual(mergeNights(have, got)[0].events.map(e => e.id), [201, 1]);
  });
});

describe("mergeNights order", () => {
  it("puts the newest event of a night first", () => {
    const got = [{ night: "2026-10-13", more: 0, events: [
      row({ id: 1, at: "2026-10-13T19:00:00+11:00" }),
      row({ id: 2, at: "2026-10-14T03:00:00+11:00" }),
      row({ id: 3, at: "2026-10-13T23:00:00+11:00" })] }];
    const have = [{ night: "2026-10-13", more: 0, events: [row({ id: 9 })] }];
    assert.deepEqual(mergeNights(have, got)[0].events.map(e => e.id), [2, 3, 1]);
  });
});

describe("headings and boxes", () => {
  it("names a night from its start date", () => {
    assert.equal(nightHeading("2026-10-13"), "Tue 13 to Wed 14 Oct");
    assert.equal(nightHeading("2026-12-31"), "Thu 31 Dec to Fri 1 Jan");
  });

  it("Last night lists each target's filters against goal and a finished mark", () => {
    const html = lastNightHtml({ night: "2026-10-13", total_h: 2.3, targets: [{
      plan_id: "P<1>", target: "G7.7-3.7", hours: 2.3, finished: true,
      filters: [{ filter: "Ha", delta_h: 2.3, hours: 12.5, goal: 30, done: false }] }] });
    assert.ok(html.includes("G7.7-3.7"));
    assert.ok(html.includes("12.5 / 30 h"));
    assert.ok(html.includes("Finished"));
    assert.ok(!html.includes("P<1>"));
    assert.equal(lastNightHtml(null), "");
  });

  it("keeps each filter's name, bar and figures in one group that wraps whole", () => {
    const html = lastNightHtml({ night: "2026-10-15", total_h: 0.3, targets: [{ target: "Helix Nebula", hours: 0.3, plan_id: "p",
      filters: [{ filter: "B", delta_h: 0.06, hours: 0.06, goal: 0.06, done: true },
                { filter: "Ha", delta_h: 0.2, hours: 0.2, goal: 5 }] }] });
    assert.equal((html.match(/class="fl"/g) || []).length, 2);
    assert.match(html, /<span class="fl"><span class="dot f-ha"><\/span><span>Ha \+0\.2 h<\/span><span class="bar"/);
    assert.match(css, /\.activity \.shot-row \.fl \{[^}]*display: flex[^}]*white-space: nowrap/);
  });

  it("Needs you lists items with a dismiss and says how many more", () => {
    const html = needsYouHtml([row({ id: 9, needs_you: true, title: "<b>x</b>" })], 53);
    assert.ok(html.includes('data-act="dismiss"'));
    assert.ok(html.includes("&lt;b&gt;x&lt;/b&gt;"));
    assert.ok(html.includes("and 52 more"));
    assert.ok(needsYouHtml([], 0).includes("Nothing needs you"));
  });
});
