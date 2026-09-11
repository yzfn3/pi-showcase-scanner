import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from sample_data.generate import generate
from windows_client.photos import photo_catalog, thumbnail
from windows_client.scan import load_manifest
from windows_client.server import create_server
from windows_client.workflow import import_scan, process_scan


class PhotoTests(unittest.TestCase):
    def test_limits_saved_frame_lists_and_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            scan = generate(Path(temp), steps=12, size=96, seed="42")
            default = process_scan(scan, grid=16)
            self.assertEqual(default["quick"]["frames_used"], 48)
            first = photo_catalog(scan)
            self.assertEqual(sum(p["used"] for p in first["items"]), 48)
            self.assertFalse(first["selection_inferred"])
            expanded = process_scan(scan, grid=16, max_frames=48)
            self.assertEqual(expanded["quick"]["frames_used"], 48)
            self.assertNotEqual(default["fingerprint"], expanded["fingerprint"])
            self.assertEqual(set(expanded["quick"]["frame_files"]), {f["file"] for f in load_manifest(scan)["frames"]})
            self.assertTrue(all(p["used"] for p in photo_catalog(scan)["items"]))
            all_frames = process_scan(scan, grid=16, max_frames=0)
            self.assertEqual(all_frames["quick"]["frames_used"], 48)
            fewer = process_scan(scan, grid=16, max_frames=7)
            self.assertEqual(len(fewer["quick"]["frame_files"]), 7)
            self.assertEqual(photo_catalog(scan, kind="used")["total"], 7)
            self.assertEqual(fewer["detailed"]["image_count"], 48)
            for invalid in (-1, True, 2.5, "48", 10001):
                with self.subTest(value=invalid), self.assertRaises(ValueError):
                    process_scan(scan, grid=16, max_frames=invalid)

    def test_imported_photos_thumbnails_paging_and_unprocessed_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = generate(root / "source", steps=3, size=96)
            scan = import_scan(original, root / "library")
            photos = photo_catalog(scan, offset=4, limit=4)
            self.assertEqual((photos["total"], len(photos["items"])), (12,4))
            self.assertTrue(all(p["used"] is None for p in photos["items"]))
            self.assertEqual(photo_catalog(scan, kind="used")["total"], 0)
            self.assertEqual(photo_catalog(scan, kind="backgrounds")["total"], 4)
            filename = photos["items"][0]["file"]
            before = (scan / filename).read_bytes()
            files_before = set(scan.rglob("*"))
            with Image.open(io.BytesIO(thumbnail(scan, filename, size=64))) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(max(image.size), 64)
            self.assertEqual((scan / filename).read_bytes(), before)
            self.assertEqual(files_before, set(scan.rglob("*")))
            with self.assertRaises(ValueError):
                thumbnail(scan, "../manifest.json")
            with self.assertRaises(ValueError):
                thumbnail(scan, filename, size=2000)

    def test_photo_http_endpoints_and_process_option(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scan = generate(root, steps=3, size=96)
            server = create_server(root, grid=16, port=0)
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def get(path):
                with urlopen(base+path, timeout=5) as response:
                    return response.read()
            try:
                request = Request(base+"/api/jobs", json.dumps(dict(action="process",scan_id=scan.name,max_frames=5)).encode(),
                                  {"Content-Type":"application/json", "Origin":base})
                with urlopen(request, timeout=5) as response:
                    jid = json.load(response)["job_id"]
                deadline = time.monotonic()+10
                while time.monotonic()<deadline:
                    job = json.loads(get("/api/jobs/"+jid))
                    if job["status"] in ("complete","failed"):
                        break
                    time.sleep(.02)
                self.assertEqual(job["status"], "complete", job)
                self.assertEqual(job["result"]["quick"]["frames_used"], 5)
                prefix = f"/api/scans/{scan.name}"
                self.assertEqual(json.loads(get(prefix+"/photos?filter=used"))["total"], 5)
                with Image.open(io.BytesIO(get(prefix+"/thumbnail?file=raw/step_000_cam_01.jpg&size=64"))) as image:
                    self.assertEqual(max(image.size),64)
                with self.assertRaises(HTTPError):
                    get(prefix+"/thumbnail?file=../manifest.json")
            finally:
                server.shutdown()
                worker.join(5)
                server.server_close()
                server.app.executor.shutdown(wait=True)
