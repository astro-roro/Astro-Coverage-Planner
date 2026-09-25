import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { currentMonthScore, upcomingSeasonScore } from "../../static/target-chip.mjs";

// Twelve monthly bins from a list of labels, Jan first, month index i+1.
function bins(labels, hours = {}) {
  return labels.map((label, i) => ({
    month: i + 1,
    label,
    hours_above_min: hours[i + 1] ?? (label === "not_visible" ? 0 : 3),
  }));
}

// Reproduces the ordering `_bestMonthCompare` in app.js applies: this
// month's label rank, then this month's hours, then total hours banked.
function compare(a, b, nowMonth) {
  const sa = currentMonthScore(a.bins, nowMonth);
  const sb = currentMonthScore(b.bins, nowMonth);
  if (sb.rank !== sa.rank) return sb.rank - sa.rank;
  if (sb.hours !== sa.hours) return sb.hours - sa.hours;
  return b.total - a.total;
}

describe("currentMonthScore", () => {
  it("scores this month's bin, not the target's peak month", () => {
    // Peaks great in February (month 2), poor in September (month 9).
    const labels = Array(12).fill("partial");
    labels[1] = "great";
    const s = currentMonthScore(bins(labels), 9);
    assert.equal(s.rank, 1); // partial, September's own bin
  });

  it("returns rank -1 when there is no bin for this month", () => {
    assert.deepEqual(currentMonthScore(null, 9), { rank: -1, hours: 0 });
    assert.deepEqual(currentMonthScore([{ month: 1, label: "great", hours_above_min: 5 }], 9),
      { rank: -1, hours: 0 });
  });
});

describe("best-this-month ordering", () => {
  it("ranks a target great in September above one that only peaks in February", () => {
    const febLabels = Array(12).fill("partial");
    febLabels[1] = "great"; // February
    const febPeak = { bins: bins(febLabels), total: 40 };
    const sepGreat = { bins: bins(Array(12).fill("great")), total: 5 };
    const order = [febPeak, sepGreat].sort((a, b) => compare(a, b, 9));
    assert.equal(order[0], sepGreat);
  });

  it("breaks a tied label rank by this month's hours above minimum", () => {
    const lowHours = { bins: bins(Array(12).fill("great"), { 9: 2 }), total: 100 };
    const highHours = { bins: bins(Array(12).fill("great"), { 9: 6 }), total: 1 };
    const order = [lowHours, highHours].sort((a, b) => compare(a, b, 9));
    assert.equal(order[0], highHours);
  });

  it("breaks a tied label and hours by total integration hours", () => {
    const fewer = { bins: bins(Array(12).fill("great"), { 9: 4 }), total: 5 };
    const more = { bins: bins(Array(12).fill("great"), { 9: 4 }), total: 20 };
    const order = [fewer, more].sort((a, b) => compare(a, b, 9));
    assert.equal(order[0], more);
  });
});

// sort=number: ascending by target_id, the same comparator app.js runs.
describe("number ordering", () => {
  it("sorts ascending by target_id", () => {
    const targets = [{ target_id: 90 }, { target_id: 3 }, { target_id: 17 }];
    const order = targets.slice().sort((a, b) => (a.target_id || 0) - (b.target_id || 0));
    assert.deepEqual(order.map(t => t.target_id), [3, 17, 90]);
  });
});

// sort=name: alphabetical, numeric-aware, the same comparator app.js runs
// (_nameCompare: localeCompare with numeric:true over the first object name).
describe("name ordering", () => {
  const nameCompare = (a, b) =>
    (a.objects?.[0] || "(no name)").localeCompare(b.objects?.[0] || "(no name)", "en-AU", { numeric: true });

  it("sorts alphabetically", () => {
    const targets = [{ objects: ["Vela 1"] }, { objects: ["Gum 23"] }, { objects: ["NGC 300"] }];
    const order = targets.slice().sort(nameCompare);
    assert.deepEqual(order.map(t => t.objects[0]), ["Gum 23", "NGC 300", "Vela 1"]);
  });

  it("treats embedded numbers numerically, not lexically", () => {
    const targets = [{ objects: ["NGC 2467"] }, { objects: ["NGC 300"] }];
    const order = targets.slice().sort(nameCompare);
    assert.deepEqual(order.map(t => t.objects[0]), ["NGC 300", "NGC 2467"]);
  });

  it("falls back to a stable placeholder for untagged targets", () => {
    const targets = [{ objects: ["Aardvark Nebula"] }, { objects: [] }];
    const order = targets.slice().sort(nameCompare);
    assert.deepEqual(order.map(t => t.objects[0] || "(no name)"), ["(no name)", "Aardvark Nebula"]);
  });
});

describe("upcomingSeasonScore", () => {
  it("sums label rank and hours over the three months after this one, not this month", () => {
    // September (9) itself is partial; Oct/Nov/Dec are great.
    const labels = Array(12).fill("partial");
    labels[9] = "great"; labels[10] = "great"; labels[11] = "great"; // Oct, Nov, Dec
    const score = upcomingSeasonScore(bins(labels, { 10: 4, 11: 4, 12: 4 }), 9);
    assert.equal(score, 3 * (400 + 4));
  });

  it("returns -1 with no bins", () => {
    assert.equal(upcomingSeasonScore(null, 9), -1);
  });
});

describe("best-upcoming ordering", () => {
  it("ranks a target great for the whole coming season above one only great this single month", () => {
    // sepOnly: great in September, partial for Oct/Nov/Dec.
    const sepLabels = Array(12).fill("partial");
    sepLabels[8] = "great";
    const sepOnly = { bins: bins(sepLabels), total: 50 };
    // wholeSeason: partial in September, great across Oct/Nov/Dec.
    const seasonLabels = Array(12).fill("partial");
    seasonLabels[9] = "great"; seasonLabels[10] = "great"; seasonLabels[11] = "great";
    const wholeSeason = { bins: bins(seasonLabels), total: 5 };
    const order = [sepOnly, wholeSeason].sort((a, b) =>
      upcomingSeasonScore(b.bins, 9) - upcomingSeasonScore(a.bins, 9));
    assert.equal(order[0], wholeSeason);
  });

  it("breaks a tied season score by total integration hours", () => {
    const labels = Array(12).fill("great");
    const fewer = { bins: bins(labels), total: 5 };
    const more = { bins: bins(labels), total: 20 };
    const cmp = (a, b) => {
      const diff = upcomingSeasonScore(b.bins, 9) - upcomingSeasonScore(a.bins, 9);
      return diff !== 0 ? diff : b.total - a.total;
    };
    const order = [fewer, more].sort(cmp);
    assert.equal(order[0], more);
  });
});
