"""Live-page thumbnails: kept on disk, listed in shooting.json, uploaded once."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shooting_publish as sp  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fakejpeg"
# conftest swaps sp._fetch_thumb for a refusal in every test; keep the real one.
REAL_FETCH = sp._fetch_thumb


def _entry(ra=10.68, dec=41.27, fov=(120.0, 80.0)):
    return {"project_name": "M31", "center_ra_deg": ra, "center_dec_deg": dec, "fov_arcmin": list(fov)}


def _doc(*entries):
    return {"version": 1, "projects": list(entries)}


class Thumbs(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def test_url_matches_the_page(self):
        url = sp.thumb_url(_entry())
        self.assertIn("hips=CDS%2FP%2FDSS2%2Fcolor", url)
        self.assertIn("fov=2.600", url)  # 120 arcmin = 2 deg, times 1.3
        self.assertIn("width=480", url)
        self.assertIn("height=270", url)
        self.assertIn("projection=TAN", url)
        self.assertIn("format=jpg", url)

    def test_thumb_field_appears_when_file_exists(self):
        doc = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG) as f:
            sp.attach_thumbs(doc, self.td)
        key = sp.thumb_key(doc["projects"][0])
        self.assertEqual(doc["projects"][0]["thumb"], f"thumbs/{key}.jpg")
        self.assertEqual((self.td / "thumbs" / f"{key}.jpg").read_bytes(), JPEG)
        self.assertEqual(f.call_count, 1)

    def test_fetch_failure_leaves_field_absent_and_retries_twice(self):
        doc = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", side_effect=OSError("down")) as f:
            sp.attach_thumbs(doc, self.td)  # must not raise
        self.assertNotIn("thumb", doc["projects"][0])
        self.assertEqual(f.call_count, 2)

    def test_fetch_false_never_fetches_but_lists_existing(self):
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(_doc(_entry()), self.td)
        doc = _doc(_entry(), _entry(ra=50.0))
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG) as f:
            sp.attach_thumbs(doc, self.td, fetch=False)
        self.assertEqual(f.call_count, 0)
        self.assertIn("thumb", doc["projects"][0])
        self.assertNotIn("thumb", doc["projects"][1])

    def test_outage_stops_after_first_target(self):
        doc = _doc(_entry(), _entry(ra=50.0))
        with mock.patch.object(sp, "_fetch_thumb", side_effect=OSError("down")) as f:
            sp.attach_thumbs(doc, self.td)
        self.assertEqual(f.call_count, 2)

    def test_existing_thumb_kept_and_not_refetched(self):
        doc = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(doc, self.td)
        doc2 = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", side_effect=OSError("down")) as f:
            sp.attach_thumbs(doc2, self.td)
        self.assertEqual(f.call_count, 0)
        self.assertEqual(doc2["projects"][0]["thumb"], doc["projects"][0]["thumb"])
        self.assertTrue((self.td / "thumbs" / Path(doc["projects"][0]["thumb"]).name).exists())

    def test_existing_thumb_survives_failed_fetch_for_another_target(self):
        keep = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(keep, self.td)
        doc = _doc(_entry(), _entry(ra=99.0))
        with mock.patch.object(sp, "_fetch_thumb", side_effect=OSError("down")):
            sp.attach_thumbs(doc, self.td)
        self.assertIn("thumb", doc["projects"][0])
        self.assertNotIn("thumb", doc["projects"][1])
        self.assertTrue((self.td / "thumbs" / Path(keep["projects"][0]["thumb"]).name).exists())

    def test_changed_pointing_refetches(self):
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(_doc(_entry()), self.td)
        moved = _doc(_entry(ra=11.5))
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG) as f:
            sp.attach_thumbs(moved, self.td)
        self.assertEqual(f.call_count, 1)
        self.assertIn("thumb", moved["projects"][0])

    def test_changed_field_refetches(self):
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(_doc(_entry()), self.td)
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG) as f:
            sp.attach_thumbs(_doc(_entry(fov=(200.0, 80.0))), self.td)
        self.assertEqual(f.call_count, 1)

    def test_non_jpeg_body_is_a_failure(self):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"<html>503</html>"
        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            with self.assertRaises(ValueError):
                REAL_FETCH("https://example.invalid/x")


def _thumb_puts(events):
    return [e for e in events if e[0] == "put" and "/thumbs/" in e[2]]


class Upload(unittest.TestCase):
    def _run_push(self, td, dest="u@h:/srv/live/shooting.json"):
        import paramiko
        events = []

        class FakeSftp:
            def stat(self, p): events.append(("stat", p)); raise IOError("missing")
            def mkdir(self, p): events.append(("mkdir", p))
            def put(self, local, remote): events.append(("put", Path(local).name, remote))
            def posix_rename(self, a, b): events.append(("rename", b))
            def close(self): pass

        class FakeClient:
            def load_host_keys(self, f): pass
            def set_missing_host_key_policy(self, p): pass
            def connect(self, host, **kw): pass
            def save_host_keys(self, f): pass
            def open_sftp(self): return FakeSftp()
            def close(self): pass

        orig = paramiko.SSHClient
        paramiko.SSHClient = FakeClient
        try:
            r = sp.push_document(td / "shooting.json", dest, ssh_key="/k")
        finally:
            paramiko.SSHClient = orig
        self.assertEqual(r.returncode, 0, r.stderr)
        return events

    def test_upload_list_has_new_thumbs_only(self):
        td = Path(tempfile.mkdtemp())
        doc = _doc(_entry(), _entry(ra=50.0))
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(doc, td)
        sp.write_document(doc, td)
        first = self._run_push(td)
        self.assertEqual(len(_thumb_puts(first)), 2)
        self.assertIn(("mkdir", "/srv/live/thumbs"), first)
        # thumbs go up before shooting.json moves into place
        renames = [e[1] for e in first if e[0] == "rename"]
        self.assertEqual(renames[-1], "/srv/live/shooting.json")

        second = self._run_push(td)
        self.assertEqual(_thumb_puts(second), [])

        # one new target: only its thumb goes up
        doc3 = _doc(_entry(), _entry(ra=50.0), _entry(ra=70.0))
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(doc3, td)
        sp.write_document(doc3, td)
        third = self._run_push(td)
        self.assertEqual(len(_thumb_puts(third)), 1)

    def test_changed_file_is_uploaded_again(self):
        td = Path(tempfile.mkdtemp())
        doc = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", return_value=JPEG):
            sp.attach_thumbs(doc, td)
        sp.write_document(doc, td)
        self._run_push(td)
        (td / "thumbs" / Path(doc["projects"][0]["thumb"]).name).write_bytes(JPEG + b"new")
        again = self._run_push(td)
        self.assertEqual(len(_thumb_puts(again)), 1)

    def test_publish_survives_cds_outage(self):
        td = Path(tempfile.mkdtemp())
        doc = _doc(_entry())
        with mock.patch.object(sp, "_fetch_thumb", side_effect=OSError("down")):
            sp.attach_thumbs(doc, td)
        path = sp.write_document(doc, td)
        self.assertNotIn("thumb", json.loads(path.read_text())["projects"][0])
        events = self._run_push(td)
        self.assertEqual(_thumb_puts(events), [])
        self.assertFalse([e for e in events if e[0] == "mkdir"])


if __name__ == "__main__":
    unittest.main()
