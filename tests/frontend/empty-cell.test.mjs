import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const lines = src.split("\n");

// Patterns where ", " stands in for a missing value (as opposed to
// .join(", "), which is a real separator). Commit cc2aecb turned em dash
// placeholders into these by accident.
const COMMA_PLACEHOLDER = [
  /(\?\?|\|\||\s\?|\s:|return)\s*["'], ["']/,
  /<(span|td)[^>]*>, <\/(span|td)>/,
];

// The old goal table's "no TS template" cell. It goes away with the table in
// phase 3 of docs/specs/goal-panel-and-ts-order.md, so it is not fixed here.
const GOAL_TABLE_LINE = /class="tmpl-sel"/;

describe("EMPTY_CELL", () => {
  it("is an en dash", () => {
    assert.match(src, /^const EMPTY_CELL = "–";$/m);
  });

  it("is used for the missing-value placeholders", () => {
    const uses = src.split("EMPTY_CELL").length - 1;
    assert.ok(uses >= 18, `expected at least 17 uses plus the definition, found ${uses}`);
  });

  it("leaves no lone comma standing in for a missing value", () => {
    const bad = [];
    lines.forEach((line, i) => {
      if (GOAL_TABLE_LINE.test(line)) return;
      if (COMMA_PLACEHOLDER.some(re => re.test(line))) bad.push(`${i + 1}: ${line.trim()}`);
    });
    assert.deepEqual(bad, []);
  });
});
