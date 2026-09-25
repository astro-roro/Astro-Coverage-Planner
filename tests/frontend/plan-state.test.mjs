import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { planStateBadgeLabel } from "../../static/plan-state.mjs";

describe("planStateBadgeLabel", () => {
  it("shows a badge for an inactive plan", () => {
    assert.equal(planStateBadgeLabel("inactive"), "Inactive");
  });

  it("shows a badge for a draft plan", () => {
    assert.equal(planStateBadgeLabel("draft"), "Draft");
  });

  it("shows a badge for a closed plan", () => {
    assert.equal(planStateBadgeLabel("closed"), "Closed");
  });

  it("shows no badge for an active plan, so the usual list is unchanged", () => {
    assert.equal(planStateBadgeLabel("active"), null);
  });

  it("shows no badge when state is absent, same as active", () => {
    assert.equal(planStateBadgeLabel(undefined), null);
  });
});
