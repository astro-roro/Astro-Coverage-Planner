// H4: the Activity tab takes its chips from GET /api/activity/types, so event
// types an extension registers are filed under the right chip.
import { describe, it, after } from "node:test";
import assert from "node:assert/strict";
import * as act from "../../static/activity.mjs";

const original = act.CHIPS.map(c => ({ ...c, types: [...c.types] }));
after(() => act.setChipDefs(original));

describe("setChipDefs", () => {
  it("files a registered type under its chip and adds a new chip, switched on", () => {
    assert.equal(act.chipOf("backup_done"), "plans");
    const defs = [...original, { id: "backups", label: "Backups", types: ["backup_done"] }];
    assert.equal(act.setChipDefs(defs), true);
    assert.equal(act.chipOf("backup_done"), "backups");
    assert.equal(act.DEFAULT_CHIPS.backups, true);
    assert.equal(act.DEFAULT_CHIPS.site, false);
    assert.ok(act.CHIPS.some(c => c.id === "backups"));
  });

  it("an unknown type still falls back to Plans", () => {
    assert.equal(act.chipOf("never_registered"), "plans");
  });

  it("ignores a broken list and drops bad entries", () => {
    const before = act.CHIPS;
    assert.equal(act.setChipDefs(null), false);
    assert.equal(act.setChipDefs([{ id: "Bad Id", label: "x", types: [] }]), false);
    assert.equal(act.CHIPS, before);
    act.setChipDefs([{ id: "plans", label: "Plans", types: ["plan_edited", "<script>"] }, { id: 3 }]);
    assert.deepEqual(act.CHIPS, [{ id: "plans", label: "Plans", types: ["plan_edited"] }]);
  });
});
