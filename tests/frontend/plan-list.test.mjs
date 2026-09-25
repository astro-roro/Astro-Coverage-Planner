import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_PLAN_STATES,
  filterPlans,
  parseSavedStates,
  planHoursLeft,
  planProgress,
  planState,
  projectRollups,
  sortPlans,
  stateCounts,
  stateMixText,
} from "../../static/plan-list.mjs";

function plan(id, { name = id, project = "", state, priority = "normal", goals = {}, mosaic } = {}) {
  const filter_goals = {};
  for (const [f, [t, a]] of Object.entries(goals)) filter_goals[f] = { target_hours: t, actual_hours: a };
  const p = { id, project_name: project, priority, filter_goals, target: { name } };
  if (state) p.state = state;
  if (mosaic) p.target.mosaic = mosaic;
  return p;
}

const PLANS = [
  plan("a", { name: "Helix", project: "PNe", state: "active", priority: "high", goals: { Ha: [5, 1] } }),
  plan("b", { name: "Dumbbell", project: "PNe", state: "draft", goals: { OIII: [2, 0] } }),
  plan("c", { name: "Witch Head", project: "Dark", state: "inactive", goals: { L: [9, 0] } }),
  plan("d", { name: "M42", project: "", state: "closed", priority: "low", goals: { Ha: [1, 3] } }),
  plan("e", { name: "Old plan", project: "Dark" }),
];

describe("planState", () => {
  it("treats a plan with no state as active", () => {
    assert.equal(planState(PLANS[4]), "active");
  });
});

describe("stateCounts", () => {
  it("counts each state, with no state counted as active", () => {
    assert.deepEqual(stateCounts(PLANS), { active: 2, draft: 1, inactive: 1, closed: 1 });
  });
});

describe("filterPlans", () => {
  it("shows only active and draft by default", () => {
    const ids = filterPlans(PLANS, { states: DEFAULT_PLAN_STATES }).map(p => p.id);
    assert.deepEqual(ids, ["a", "b", "e"]);
  });

  it("shows everything when every chip is on", () => {
    assert.equal(filterPlans(PLANS, { states: ["active", "draft", "inactive", "closed"] }).length, 5);
  });

  it("shows nothing when every chip is off", () => {
    assert.equal(filterPlans(PLANS, { states: new Set() }).length, 0);
  });

  it("searches plan names, case blind", () => {
    const ids = filterPlans(PLANS, { states: ["active", "draft", "inactive", "closed"], query: "helix" }).map(p => p.id);
    assert.deepEqual(ids, ["a"]);
  });

  it("searches project names", () => {
    const ids = filterPlans(PLANS, { states: ["active", "draft", "inactive", "closed"], query: "dark" }).map(p => p.id);
    assert.deepEqual(ids, ["c", "e"]);
  });

  it("applies the search and the chips together", () => {
    const ids = filterPlans(PLANS, { states: DEFAULT_PLAN_STATES, query: "dark" }).map(p => p.id);
    assert.deepEqual(ids, ["e"]);
  });
});

describe("planHoursLeft and planProgress", () => {
  it("counts hours left per goal, ignoring goals already met", () => {
    const p = plan("x", { goals: { Ha: [5, 1], OIII: [2, 4] } });
    assert.equal(planHoursLeft(p), 4);
  });

  it("multiplies by the mosaic panel count", () => {
    const p = plan("x", { goals: { Ha: [5, 1] }, mosaic: { rows: 2, cols: 3 } });
    assert.equal(planHoursLeft(p), 24);
    assert.deepEqual(planProgress(p), { done: 6, total: 30 });
  });

  it("caps progress at the goal so an overshoot can't hide an empty filter", () => {
    const p = plan("x", { goals: { Ha: [1, 10], OIII: [1, 0] } });
    assert.deepEqual(planProgress(p), { done: 1, total: 2 });
  });

  it("copes with a plan that has no goals", () => {
    assert.equal(planHoursLeft({ id: "z" }), 0);
    assert.deepEqual(planProgress({ id: "z" }), { done: 0, total: 0 });
  });
});

describe("sortPlans", () => {
  it("sorts by priority, then name", () => {
    assert.deepEqual(sortPlans(PLANS, "priority").map(p => p.id), ["a", "b", "e", "c", "d"]);
  });

  it("sorts by name", () => {
    assert.deepEqual(sortPlans(PLANS, "name").map(p => p.id), ["b", "a", "d", "e", "c"]);
  });

  it("sorts by hours left, most first", () => {
    assert.deepEqual(sortPlans(PLANS, "hours_left").map(p => p.id), ["c", "a", "b", "d", "e"]);
  });

  it("falls back to priority for a sort it doesn't know", () => {
    assert.deepEqual(sortPlans(PLANS, "panels_up_now").map(p => p.id), sortPlans(PLANS, "priority").map(p => p.id));
  });

  it("doesn't reorder the list it was given", () => {
    const before = PLANS.map(p => p.id);
    sortPlans(PLANS, "name");
    assert.deepEqual(PLANS.map(p => p.id), before);
  });
});

describe("projectRollups", () => {
  it("groups by project and drops projects with nothing showing", () => {
    const shown = filterPlans(PLANS, { states: DEFAULT_PLAN_STATES });
    const names = projectRollups(PLANS, shown, "name").map(r => r.name);
    assert.deepEqual(names, ["Dark", "PNe"]);
  });

  it("puts plans with no project under (no project)", () => {
    const rows = projectRollups(PLANS, PLANS, "name");
    assert.ok(rows.some(r => r.name === "(no project)" && r.plans[0].id === "d"));
  });

  it("sums hours left and progress over the plans showing", () => {
    const shown = filterPlans(PLANS, { states: DEFAULT_PLAN_STATES });
    const pne = projectRollups(PLANS, shown, "name").find(r => r.name === "PNe");
    assert.equal(pne.hoursLeft, 6);
    assert.equal(pne.done, 1);
    assert.equal(pne.total, 7);
    assert.equal(pne.plans.length, 2);
    assert.equal(pne.totalPlans, 2);
  });

  it("keeps the state mix and total over every plan in the project", () => {
    const shown = filterPlans(PLANS, { states: DEFAULT_PLAN_STATES });
    const dark = projectRollups(PLANS, shown, "name").find(r => r.name === "Dark");
    assert.equal(dark.plans.length, 1);
    assert.equal(dark.totalPlans, 2);
    assert.deepEqual(dark.stateMix, { active: 1, draft: 0, inactive: 1, closed: 0 });
    assert.equal(stateMixText(dark.stateMix), "1 active, 1 inactive");
  });

  it("sorts projects by top priority, then plan count", () => {
    const names = projectRollups(PLANS, PLANS, "priority").map(r => r.name);
    assert.deepEqual(names, ["PNe", "Dark", "(no project)"]);
  });

  it("sorts projects by hours left, most first", () => {
    const names = projectRollups(PLANS, PLANS, "hours_left").map(r => r.name);
    assert.deepEqual(names, ["Dark", "PNe", "(no project)"]);
  });
});

describe("parseSavedStates", () => {
  it("defaults to active and draft when nothing is saved", () => {
    assert.deepEqual([...parseSavedStates(undefined)], ["active", "draft"]);
  });

  it("drops names it doesn't know", () => {
    assert.deepEqual([...parseSavedStates(["closed", "bogus"])], ["closed"]);
  });

  it("falls back to the default when the save is empty", () => {
    assert.deepEqual([...parseSavedStates([])], ["active", "draft"]);
  });
});
