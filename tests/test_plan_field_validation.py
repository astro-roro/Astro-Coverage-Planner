"""Plan and gear fields the page writes into HTML are checked on the way in:
priority is one of the fixed words, and the numbers are finite numbers.
The page also escapes them, checked as text in the last class here."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARKUP = '<img src=x onerror="alert(1)">'


def _fresh():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    for cache in ("_plans_cache", "_gear_cache"):
        setattr(app_module, cache, None)
        setattr(app_module, cache + "_mtime", None)


class TestPlanFields(unittest.TestCase):
    def setUp(self):
        _fresh()
        self.client = app.test_client()

    def _post(self, **fields):
        return self.client.post("/api/plans", json={"id": "p1", **fields})

    def test_priority_must_be_a_known_word(self):
        r = self._post(priority=MARKUP)
        self.assertEqual(r.status_code, 400)
        self.assertIn("priority", r.get_json()["error"])

    def test_known_priorities_save(self):
        for word in ("low", "normal", "high"):
            with self.subTest(word=word):
                self.assertEqual(self._post(priority=word).status_code, 200)

    def test_numbers_must_be_finite_numbers(self):
        for key in ("min_altitude_deg", "meridian_window_min"):
            for bad in (MARKUP, "30", True, [30]):
                with self.subTest(key=key, bad=bad):
                    r = self._post(**{key: bad})
                    self.assertEqual(r.status_code, 400)
                    self.assertIn(key, r.get_json()["error"])

    def test_good_numbers_save(self):
        r = self._post(min_altitude_deg=30, meridian_window_min=0.5)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_missing_fields_still_save(self):
        self.assertEqual(self._post().status_code, 200)


class TestTelescopeFocalLength(unittest.TestCase):
    def setUp(self):
        _fresh()
        self.client = app.test_client()

    def test_markup_focal_length_is_refused(self):
        r = self.client.post("/api/gear", json={
            "telescopes": [{"id": "t1", "name": "Scope", "focal_length_mm": MARKUP}],
            "cameras": []})
        self.assertEqual(r.status_code, 400)
        self.assertIn("focal_length_mm", r.get_json()["error"])

    def test_numeric_focal_length_saves(self):
        r = self.client.post("/api/gear", json={
            "telescopes": [{"id": "t1", "name": "Scope", "focal_length_mm": 600.0}],
            "cameras": []})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))


class TestPageEscapes(unittest.TestCase):
    """The five places that wrote a stored value into HTML unescaped."""

    def setUp(self):
        self.js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_no_unescaped_interpolation_left(self):
        for raw in (
            r"plan-pri-\$\{pri\}",
            r"\$\{priLabel\} priority",
            r"\(\$\{t\.focal_length_mm\}mm\)",
            r'value="\$\{editingPlan\.min_altitude_deg',
            r'value="\$\{editingPlan\.meridian_window_min',
            r'ext-diff-meta">\$\{\(p\.filters',
        ):
            with self.subTest(pattern=raw):
                self.assertIsNone(re.search(raw, self.js))

    def test_escaped_forms_present(self):
        for wanted in (
            "plan-pri-${esc(pri)}",
            "${esc(t.focal_length_mm)}mm",
            "${esc(editingPlan.min_altitude_deg ?? 30)}",
            "${esc(editingPlan.meridian_window_min ?? 0)}",
            '${esc((p.filters || []).join(", "))}',
        ):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, self.js)


if __name__ == "__main__":
    unittest.main()
