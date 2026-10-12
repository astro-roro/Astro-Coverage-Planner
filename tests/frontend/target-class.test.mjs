import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  TARGET_CLASSES, targetClassOf, targetClassFieldHtml, setTargetClass,
} from "../../static/target-class.mjs";

describe("A1: target class in the plan editor", () => {
  it("offers survey, deep and pretty", () => {
    assert.deepEqual(TARGET_CLASSES, ["survey", "deep", "pretty"]);
  });

  it("reads a plan with none as survey", () => {
    assert.equal(targetClassOf({}), "survey");
    assert.equal(targetClassOf({ target_class: "giant" }), "survey");
    assert.equal(targetClassOf({ target_class: "deep" }), "deep");
  });

  it("selects the plan's class in the field", () => {
    const html = targetClassFieldHtml({ target_class: "pretty" });
    assert.match(html, /id="f_target_class"/);
    assert.match(html, /<option value="pretty" selected>Pretty<\/option>/);
    assert.doesNotMatch(html, /<option value="survey" selected>/);
    assert.match(targetClassFieldHtml({}), /<option value="survey" selected>/);
  });

  it("stores a chosen class and ignores an unknown one", () => {
    const plan = {};
    setTargetClass(plan, "deep");
    assert.equal(plan.target_class, "deep");
    setTargetClass(plan, "giant");
    assert.equal(plan.target_class, "deep");
  });

  it("is wired into the Target fieldset and saved with the plan", () => {
    const app = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
    assert.match(app, /\$\{targetClassFieldHtml\(editingPlan\)\}/);
    assert.match(app, /#f_target_class"\)\?\.addEventListener\("change"/);
  });
});
