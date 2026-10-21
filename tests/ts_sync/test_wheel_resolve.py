"""W3, the Python half: the wheel slot a filter is written under
(docs/specs/gear-templates.md section 2), against the case file ACP and the
plugin read too."""
from __future__ import annotations

import json
from pathlib import Path

from nina_ts_sync.filter_canon import resolve_to_wheel

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_w3_the_shared_wheel_cases_resolve_as_the_file_says():
    cases = json.loads((FIXTURES / "wheel_resolve_cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 9
    for case in cases:
        got = resolve_to_wheel(case["filter"], case["slots"])
        assert got == case["expect"], case


def test_w1_w2_the_spec_cases_are_in_the_shared_file():
    cases = json.loads((FIXTURES / "wheel_resolve_cases.json").read_text(encoding="utf-8"))["cases"]
    seen = {(tuple(c["slots"]), c["filter"]): c["expect"] for c in cases}
    wheel = ("L", "R", "G", "B", "H", "O", "S")
    assert [seen[(wheel, f)] for f in ("Ha", "OIII", "SII", "R", "R Stars", "Antlia Ha")] == \
        ["H", "O", "S", "R", None, None]
    assert seen[(("Ha", "H"), "Ha")] == "Ha"
    assert seen[(("Ha",), "ha")] == "Ha"
    assert seen[(("Lum",), "L")] == "Lum"
