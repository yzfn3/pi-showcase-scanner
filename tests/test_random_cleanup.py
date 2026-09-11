import hashlib
import tempfile
import os
import stat
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from sample_data.generate import generate
from pi_node.capture import MockCapture
from windows_client.cleanup import discard_generated
from windows_client.scan import load_manifest
from windows_client.server import App
from windows_client.workflow import import_scan, process_scan


class RandomCleanupTests(unittest.TestCase):
    def test_readonly_generated_files_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan = generate(root, steps=3, size=96)
            process_scan(scan, grid=16)
            image = next((scan / "outputs/detailed/images").glob("*.jpg"))
            os.chmod(image, stat.S_IREAD)
            if os.name == "nt":
                os.chmod(image.parent, stat.S_IREAD)
            discard_generated(scan, root)
            self.assertFalse(scan.exists())
            self.assertFalse((root / ".cleanup" / f"{scan.name}.json").exists())

    def test_new_scans_produce_different_images_and_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            a = generate(Path(temporary), steps=3, size=96)
            b = generate(Path(temporary), steps=3, size=96)
            self.assertNotEqual(load_manifest(a)["object_seed"], load_manifest(b)["object_seed"])
            self.assertNotEqual((a / "raw/step_000_cam_01.jpg").read_bytes(), (b / "raw/step_000_cam_01.jpg").read_bytes())
            for scan in (a, b):
                process_scan(scan, grid=16)
            self.assertNotEqual(hashlib.sha256((a / "outputs/quick/preview.glb").read_bytes()).digest(),
                                hashlib.sha256((b / "outputs/quick/preview.glb").read_bytes()).digest())

    def test_mock_shape_is_consistent_within_scan_and_changes_between_scans(self):
        backend = MockCapture()
        camera = backend.cameras()[0]
        def capture(sid):
            return np.asarray(backend.capture(camera, scan_id=sid, step=0, angle_deg=0, captured_at="now"))[:320]
        first = capture("scan_20260911_000001")
        np.testing.assert_array_equal(first, capture("scan_20260911_000001"))
        self.assertFalse(np.array_equal(first, capture("scan_20260911_000002")))

    def test_next_generation_and_done_delete_files_but_preserve_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = App(root / "library", 16)
            original = generate(root / "external", steps=3, size=96)
            imported = import_scan(original, app.root)
            def run(request):
                jid = app.submit(request)
                deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    with app.lock:
                        job = dict(app.jobs[jid])
                    if job["status"] in ("complete", "failed"):
                        self.assertEqual(job["status"], "complete", job)
                        return job
                    time.sleep(.01)
                self.fail("Job timed out")
            try:
                with patch("windows_client.server.generate", side_effect=lambda destination: generate(destination, steps=3, size=96)):
                    first = app.root / run({"action":"generate"})["scan_id"]
                    run({"action":"process", "scan_id":first.name})
                    self.assertTrue((first / "outputs/detailed/images").exists())
                    second = app.root / run({"action":"generate"})["scan_id"]
                    self.assertFalse(first.exists())
                    self.assertTrue(second.exists())
                    self.assertTrue(imported.exists())
                    run({"action":"discard", "scan_id":second.name})
                    self.assertFalse(second.exists())
                    self.assertTrue(original.exists())
                    self.assertTrue(imported.exists())
                    with self.assertRaisesRegex(ValueError, "imported"):
                        discard_generated(imported, app.root)
                    with self.assertRaisesRegex(ValueError, "inside"):
                        discard_generated(original, app.root)
            finally:
                app.executor.shutdown(wait=True)
