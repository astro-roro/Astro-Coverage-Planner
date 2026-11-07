import { test } from "node:test";
import assert from "node:assert/strict";
import {
  planScheduled, panelScheduled, panelSwitchOn, setPlanScheduled, setPanelScheduled, panelList,
} from "../../static/target-active.mjs";

test("a plan with neither field is scheduled, every panel too", () => {
  const plan = {};
  assert.equal(planScheduled(plan), true);
  assert.equal(panelScheduled(plan, "1,1"), true);
});

test("turning the plan off stores false, and on removes the field", () => {
  const plan = {};
  setPlanScheduled(plan, false);
  assert.deepEqual(plan, { target_active: false });
  assert.equal(panelScheduled(plan, "1,1"), false);
  setPlanScheduled(plan, true);
  assert.deepEqual(plan, {});
});

test("a panel off is kept in the map, and the map goes when every panel is on", () => {
  const plan = {};
  setPanelScheduled(plan, "2,1", false);
  assert.deepEqual(plan.panel_active, { "2,1": false });
  assert.equal(panelScheduled(plan, "2,1"), false);
  assert.equal(panelScheduled(plan, "1,1"), true);
  setPanelScheduled(plan, "2,1", true);
  assert.equal("panel_active" in plan, false);
});

test("the panel switch keeps its own value while the plan is off", () => {
  const plan = { target_active: false, panel_active: { "1,2": false } };
  assert.equal(panelSwitchOn(plan, "1,1"), true);
  assert.equal(panelSwitchOn(plan, "1,2"), false);
  assert.equal(panelScheduled(plan, "1,1"), false);
});

test("panels are listed in the order the sync numbers them", () => {
  assert.deepEqual(panelList(2, 2).map(p => p.key), ["1,1", "1,2", "2,1", "2,2"]);
  assert.equal(panelList(2, 2)[2].label, "Panel 3 (R2C1)");
});
