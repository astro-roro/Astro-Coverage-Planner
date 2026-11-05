import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { chipOf, durationText, gbText, uploadCardHtml, whenText } from "../../static/activity.mjs";

const NOW = "2026-11-05T08:50:00+11:00";
const base = {
  night_id: "2026-11-04", started_at: "2026-11-05T08:00:00+11:00",
  window_end: "2026-11-05T17:00:00+11:00", path: "vpn",
  files_done: 40, files_total: 100, bytes_done: 2.56e9, bytes_total: 6.4e9,
  percent: 40, elapsed_s: 3000, avg_mbps: 6.8, recent_mbps: 7.1,
  eta: "2026-11-05T10:05:00+11:00", eta_after_cutoff: false, state: "uploading",
};

describe("upload card", () => {
  it("draws nothing when no upload is running", () => {
    assert.equal(uploadCardHtml([], NOW), "");
    assert.equal(uploadCardHtml(undefined, NOW), "");
  });

  it("shows x/y files, GB, rates, start and finish", () => {
    const html = uploadCardHtml([base], NOW);
    assert.match(html, /UPLOADING TO THE NAS/);
    assert.match(html, /Wed 4 to Thu 5 Nov/);
    assert.match(html, /Files <b>40 \/ 100<\/b>/);
    assert.match(html, /<b>2\.6<\/b> of <b>6\.4<\/b> GB/);
    assert.match(html, /Started <b>08:00<\/b> over VPN/);
    assert.match(html, /Average <b>6\.8 Mbps<\/b>/);
    assert.match(html, /Last 5 min <b>7\.1 Mbps<\/b>/);
    assert.match(html, /Finish <b>10:05<\/b>/);
    assert.match(html, /aria-valuenow="40"/);
    assert.doesNotMatch(html, /cutoff/);
  });

  it("warns when the finish lands after the cutoff", () => {
    const html = uploadCardHtml([{ ...base, eta: "2026-11-05T19:30:00+11:00", eta_after_cutoff: true }], NOW);
    assert.match(html, /Finish <b>19:30<\/b> <span class="pill bad">after the 17:00 cutoff<\/span>/);
  });

  it("says paused at the cutoff and when it carries on", () => {
    const html = uploadCardHtml([{ ...base, state: "paused", eta: null }], "2026-11-05T17:05:00+11:00");
    assert.match(html, /class="up paused"/);
    assert.match(html, /Paused at the 17:00 cutoff\. It carries on with the next morning's run\./);
    assert.doesNotMatch(html, /Finish/);
  });

  it("says measuring before there is a rate", () => {
    const html = uploadCardHtml([{ ...base, avg_mbps: null, recent_mbps: null, eta: null }], NOW);
    assert.match(html, /Average <b>measuring<\/b>/);
    assert.match(html, /Finish <b>working it out<\/b>/);
  });

  it("escapes what it is sent", () => {
    const html = uploadCardHtml([{ ...base, night_id: "<img src=x onerror=alert(1)>" }], NOW);
    assert.doesNotMatch(html, /<img/);
  });

  it("names the day when a time is not today", () => {
    assert.equal(whenText("2026-11-06T09:10:00+11:00", NOW), "Fri 09:10");
    assert.equal(whenText("2026-11-05T09:10:00+11:00", NOW), "09:10");
  });

  it("formats sizes and durations", () => {
    assert.equal(gbText(6.4e9), "6.4");
    assert.equal(gbText(250e9), "250");
    assert.equal(durationText(4800), "1 h 20 min");
    assert.equal(durationText(300), "5 min");
  });

  it("files the finished event under Data landed", () => {
    assert.equal(chipOf("upload_finished"), "data");
  });
});
