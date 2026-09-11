"""Mock node + real HTTP receiver integration and failure recovery."""
import hashlib
import json
import logging
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from PIL import Image
import numpy as np
from flask import request
from werkzeug.serving import make_server

from pi_node.app import create_app
from pi_node.capture import MockCapture
from pi_node.service import ScanService
from preview3d.pipeline import foreground
from windows_client.node_api import NodeClient, NodeClientError, transfer_records
from windows_client.scan import load_manifest, write_json
from windows_client.workflow import process_scan


@contextmanager
def running_node(root, backend=None, corrupt=None):
    app = create_app(root, backend)
    if corrupt is not None:
        @app.after_request
        def interrupt_file(response):
            if "/files/raw/step_000_cam_01.jpg" in request.path and corrupt["remaining"] > 0:
                corrupt["remaining"] -= 1
                response.direct_passthrough = False
                response.set_data(response.get_data()[:100])
            return response
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    client = NodeClient(f"http://127.0.0.1:{server.server_port}", timeout=2, retries=1)
    try:
        yield client, app
    finally:
        client.close()
        server.shutdown()
        thread.join(10)
        server.server_close()
        app.extensions["scan_service"].close()


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_http_capture_receive_and_reconstruction(self):
        with running_node(self.root / "node") as (client, app):
            self.assertTrue(client.health()["mock_mode"])
            cameras = client.json("GET", "/cameras")["cameras"]
            self.assertEqual([c["id"] for c in cameras], [f"cam_{i:02d}" for i in range(1,5)])
            start = client.start(steps=3, delay_between_steps_ms=100)
            sid = start["scan_id"]
            self.assertIn(start["state"], ("pending", "capturing"))
            with self.assertRaisesRegex(NodeClientError, "409"):
                client.json("GET", f"/scans/{sid}/manifest")
            complete = client.wait(sid, poll_interval=.03, timeout=10)
            self.assertEqual((complete["current_step"], complete["total_steps"], complete["files_captured"]), (3,3,12))
            self.assertEqual(client.json("GET", "/scans")["scans"][0]["state"], "complete")
            received = client.download_scan(sid, self.root / "received")
            manifest = load_manifest(received)
            self.assertEqual(len(manifest["files"]), 16)
            for item in manifest["files"]:
                data = (received / item["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertEqual(data, (self.root / "node" / sid / item["file"]).read_bytes())
            frame = manifest["frames"][0]
            with Image.open(received / frame["file"]) as image:
                self.assertEqual(image.size, (320,376))
                colors = np.asarray(image.crop((0,320,320,376))).reshape(-1,3)
                self.assertGreater(len(np.unique(colors, axis=0)), 10)
            self.assertEqual(foreground(received, frame, manifest["cameras"][0]).shape, (320,320))
            result = process_scan(received, grid=24)
            self.assertGreater(result["quick"]["triangles"], 100)
            self.assertEqual(result["detailed"]["image_count"], 12)
            self.assertTrue((received / "outputs/quick/preview.glb").read_bytes().startswith(b"glTF"))
            with client.session.get(client.node + f"/api/v1/scans/{sid}/files/step_000_cam_01.jpg", timeout=2) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, (received / frame["file"]).read_bytes())
            with self.assertRaisesRegex(NodeClientError, "404"):
                client.json("GET", f"/scans/{sid}/files/request.json")

    def test_busy_duplicate_ids_idempotency_and_restart(self):
        root = self.root / "node"
        with running_node(root) as (client, app):
            params = dict(scan_id="scan_20260910_120000", steps=3, delay_between_steps_ms=100)
            first = client.start(**params, key="retry-key")
            self.assertEqual(client.start(**params, key="retry-key")["scan_id"], first["scan_id"])
            with self.assertRaisesRegex(NodeClientError, "409"):
                client.start(steps=3)
            with self.assertRaisesRegex(NodeClientError, "409"):
                client.start(steps=4, key="retry-key")
            client.wait(first["scan_id"], poll_interval=.03)
            with self.assertRaisesRegex(NodeClientError, "409"):
                client.start(**params)
            snapshot = (root / first["scan_id"] / "manifest.json").read_bytes()
            second = client.start(steps=3)
            self.assertNotEqual(second["scan_id"], first["scan_id"])
            client.wait(second["scan_id"], poll_interval=.03)
            self.assertFalse((root / first["scan_id"]).exists())
            self.assertTrue(client.json("GET", "/scan/status/"+first["scan_id"])["deleted"])
        with running_node(root) as (client, app):
            self.assertTrue(client.start(**params, key="retry-key")["deleted"])
            self.assertEqual(len(client.json("GET", "/scans")["scans"]), 1)
            client.json("DELETE", "/scans/"+second["scan_id"])
            self.assertFalse((root / second["scan_id"]).exists())
            self.assertEqual(client.json("GET", "/scans")["scans"], [])

    def test_transfer_retry_resume_and_local_no_overwrite(self):
        corrupt = {"remaining": 20}
        with running_node(self.root / "node", corrupt=corrupt) as (client, app):
            sid = client.start(steps=3)["scan_id"]
            client.wait(sid, poll_interval=.03)
            destination = self.root / "received" / sid
            with self.assertRaisesRegex(NodeClientError, "Transfer failed"):
                client.download_scan(sid, destination.parent)
            self.assertFalse((destination / "manifest.json").exists())
            self.assertFalse((destination / ".receiving").exists())
            self.assertTrue((destination / "raw/step_000_cam_01.jpg.part").exists())
            original_time = (destination / "backgrounds/cam_01.jpg").stat().st_mtime_ns
            corrupt["remaining"] = 1  # One more truncated response; the bounded retry must recover.
            client.download_scan(sid, destination.parent)
            self.assertEqual((destination / "backgrounds/cam_01.jpg").stat().st_mtime_ns, original_time)
            client.download_scan(sid, destination.parent)
            self.assertEqual((destination / "backgrounds/cam_01.jpg").stat().st_mtime_ns, original_time)
            protected = destination / "raw/step_000_cam_01.jpg"
            protected.write_bytes(b"user changed this file")
            with self.assertRaisesRegex(NodeClientError, "refusing to overwrite"):
                client.download_scan(sid, destination.parent)
            self.assertEqual(protected.read_bytes(), b"user changed this file")
            other = self.root / "other" / sid
            other.mkdir(parents=True)
            (other / "precious.txt").write_text("keep me")
            with self.assertRaisesRegex(NodeClientError, "different scan"):
                client.download_scan(sid, other.parent)
            self.assertEqual((other / "precious.txt").read_text(), "keep me")

    def test_capture_failure_and_poll_timeout(self):
        class BrokenCamera(MockCapture):
            def capture(self, *args, **kwargs):
                raise RuntimeError("Simulated camera failure")
        with running_node(self.root / "broken", backend=BrokenCamera()) as (client, app):
            sid = client.start(steps=3)["scan_id"]
            with self.assertRaisesRegex(NodeClientError, "Simulated camera failure"):
                client.wait(sid, poll_interval=.03)
            with self.assertRaisesRegex(NodeClientError, "409"):
                client.download_scan(sid, self.root / "received")
        with running_node(self.root / "slow") as (client, app):
            sid = client.start(steps=3, delay_between_steps_ms=500)["scan_id"]
            with self.assertRaises(NodeClientError):
                client.wait(sid, timeout=.02, poll_interval=.01)

    def test_restart_marks_interrupted_capture_failed(self):
        sid = "scan_20260910_120000"
        folder = self.root / "node" / sid
        write_json(folder / "status.json", dict(scan_id=sid, state="capturing", current_step=1,
                                                total_steps=12, files_captured=4, error=None))
        write_json(folder / "request.json", dict(parameters={}, idempotency_key=None))
        service = ScanService(folder.parent, MockCapture())
        try:
            state = service.status(sid)
            self.assertEqual(state["state"], "failed")
            self.assertIn("stopped", state["error"])
        finally:
            service.close()

    def test_bad_requests_and_untrusted_manifest_paths(self):
        with running_node(self.root / "node") as (client, app):
            for body in ([], {"steps": 0}, {"steps": "12"}, {"cameras": True}, {"mode":"arducam"},
                         {"scan_id":"../escape"}, {"extra": 1}, {"output_format":"png"}):
                with self.subTest(body=body), self.assertRaisesRegex(NodeClientError, "400"):
                    client.json("POST", "/scan/start", body=body)
            with self.assertRaisesRegex(NodeClientError, "404"):
                client.wait("scan_20260910_000000")
            sid = client.start(steps=3)["scan_id"]
            client.wait(sid, poll_interval=.03)
            manifest = client.json("GET", f"/scans/{sid}/manifest")
            for path in ("../escape.jpg", "raw/../../escape.jpg", "raw/C:escape.jpg", "raw\\escape.jpg"):
                changed = json.loads(json.dumps(manifest))
                changed["files"][0]["file"] = path
                with self.subTest(path=path), self.assertRaises(NodeClientError):
                    transfer_records(changed, sid)


if __name__ == "__main__":
    unittest.main()
