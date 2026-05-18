import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { tokenizeSearch } from "../../static/search.mjs";

describe("tokenizeSearch — empty + whitespace", () => {
  it("returns [] for null/undefined/empty/whitespace", () => {
    assert.deepEqual(tokenizeSearch(null), []);
    assert.deepEqual(tokenizeSearch(undefined), []);
    assert.deepEqual(tokenizeSearch(""), []);
    assert.deepEqual(tokenizeSearch("   "), []);
  });
});

describe("tokenizeSearch — barewords", () => {
  it("splits on whitespace into text tokens", () => {
    assert.deepEqual(tokenizeSearch("orion crab"), [
      { kind: "text", value: "orion", negate: false },
      { kind: "text", value: "crab", negate: false },
    ]);
  });

  it("lowercases bareword values", () => {
    assert.deepEqual(tokenizeSearch("ORION"), [
      { kind: "text", value: "orion", negate: false },
    ]);
  });

  it("preserves embedded punctuation in barewords", () => {
    assert.deepEqual(tokenizeSearch("ngc-2244 m31"), [
      { kind: "text", value: "ngc-2244", negate: false },
      { kind: "text", value: "m31", negate: false },
    ]);
  });
});

describe("tokenizeSearch — quoted phrases", () => {
  it("preserves spaces inside a quoted phrase", () => {
    assert.deepEqual(tokenizeSearch('"orion nebula"'), [
      { kind: "text", value: "orion nebula", negate: false },
    ]);
  });

  it("treats the quoted phrase as a single token alongside barewords", () => {
    assert.deepEqual(tokenizeSearch('m31 "orion nebula" crab'), [
      { kind: "text", value: "m31", negate: false },
      { kind: "text", value: "orion nebula", negate: false },
      { kind: "text", value: "crab", negate: false },
    ]);
  });

  it("skips empty quoted phrases", () => {
    assert.deepEqual(tokenizeSearch('""'), []);
  });
});

describe("tokenizeSearch — key:value", () => {
  it("parses each KV key into a kv token (lowercased)", () => {
    const cases = [
      ["object:m31", "object", "m31"],
      ["name:NGC2244", "name", "ngc2244"],
      ["filter:Ha", "filter", "ha"],
      ["telescope:rasa", "telescope", "rasa"],
      ["tel:rasa", "tel", "rasa"],
      ["camera:asi2600", "camera", "asi2600"],
      ["cam:asi2600", "cam", "asi2600"],
      ["class:PNe", "class", "pne"],
      ["tag:needs-work", "tag", "needs-work"],
    ];
    for (const [input, key, value] of cases) {
      assert.deepEqual(
        tokenizeSearch(input),
        [{ kind: "kv", key, value, negate: false }],
        `input=${input}`
      );
    }
  });

  it("supports key:\"quoted value\" preserving spaces", () => {
    assert.deepEqual(tokenizeSearch('object:"orion nebula"'), [
      { kind: "kv", key: "object", value: "orion nebula", negate: false },
    ]);
  });

  it("is case-insensitive on the key name", () => {
    assert.deepEqual(tokenizeSearch("Filter:Ha"), [
      { kind: "kv", key: "filter", value: "ha", negate: false },
    ]);
  });

  it("treats unknown keys as bareword text (falls through)", () => {
    // 'foo:bar' isn't in SEARCH_KV_KEYS or SEARCH_CMP_KEYS — kept as text
    assert.deepEqual(tokenizeSearch("foo:bar"), [
      { kind: "text", value: "foo:bar", negate: false },
    ]);
  });

  it("drops empty-value kv tokens", () => {
    assert.deepEqual(tokenizeSearch("filter:"), []);
  });
});

describe("tokenizeSearch — numeric comparators", () => {
  it("parses fov>N and hours>N as cmp tokens", () => {
    assert.deepEqual(tokenizeSearch("fov>30"), [
      { kind: "cmp", key: "fov", op: ">", value: 30, negate: false },
    ]);
    assert.deepEqual(tokenizeSearch("hours<2.5"), [
      { kind: "cmp", key: "hours", op: "<", value: 2.5, negate: false },
    ]);
  });

  it("falls back to text when the right-hand side isn't numeric", () => {
    const toks = tokenizeSearch("fov>abc");
    assert.equal(toks.length, 1);
    assert.equal(toks[0].kind, "text");
  });

  it("ignores unknown comparator keys", () => {
    // 'ra' isn't in SEARCH_CMP_KEYS, so it's not a cmp.
    const toks = tokenizeSearch("ra>30");
    assert.equal(toks.length, 1);
    assert.equal(toks[0].kind, "text");
  });
});

describe("tokenizeSearch — negation", () => {
  it("strips leading - and sets negate:true on text tokens", () => {
    assert.deepEqual(tokenizeSearch("-orion"), [
      { kind: "text", value: "orion", negate: true },
    ]);
  });

  it("negates kv tokens", () => {
    assert.deepEqual(tokenizeSearch("-class:SNR"), [
      { kind: "kv", key: "class", value: "snr", negate: true },
    ]);
  });

  it("negates cmp tokens", () => {
    assert.deepEqual(tokenizeSearch("-hours>3"), [
      { kind: "cmp", key: "hours", op: ">", value: 3, negate: true },
    ]);
  });

  it("negates quoted phrases", () => {
    assert.deepEqual(tokenizeSearch('-"orion nebula"'), [
      { kind: "text", value: "orion nebula", negate: true },
    ]);
  });

  it("treats a bare '-' as a non-negating token (no slice)", () => {
    const toks = tokenizeSearch("-");
    assert.equal(toks.length, 1);
    assert.equal(toks[0].negate, false);
  });
});

describe("tokenizeSearch — mixed real-world queries", () => {
  it('handles the docstring example: camera:asi2600 filter:Ha "orion neb" hours>3', () => {
    assert.deepEqual(
      tokenizeSearch('camera:asi2600 filter:Ha "orion neb" hours>3'),
      [
        { kind: "kv", key: "camera", value: "asi2600", negate: false },
        { kind: "kv", key: "filter", value: "ha", negate: false },
        { kind: "text", value: "orion neb", negate: false },
        { kind: "cmp", key: "hours", op: ">", value: 3, negate: false },
      ]
    );
  });

  it("combines negation + kv + cmp in one query", () => {
    assert.deepEqual(tokenizeSearch("-tag:done filter:Ha hours>2"), [
      { kind: "kv", key: "tag", value: "done", negate: true },
      { kind: "kv", key: "filter", value: "ha", negate: false },
      { kind: "cmp", key: "hours", op: ">", value: 2, negate: false },
    ]);
  });

  it("survives unicode in bareword values", () => {
    assert.deepEqual(tokenizeSearch("café αβγ"), [
      { kind: "text", value: "café", negate: false },
      { kind: "text", value: "αβγ", negate: false },
    ]);
  });
});
