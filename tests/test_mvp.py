"""Integration tests exercise images -> mesh, transfer, watching, and real HTTP."""
import json
import shutil
import struct
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from sample_data.generate import generate
from windows_client.__main__ import watch
from windows_client.scan import load_manifest, write_json
from windows_client.server import create_server
from windows_client.workflow import import_scan, process_scan


class MVPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.source = generate(cls.root / "source", steps=6, size=128, seed="1")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def copy_scan(self):
        return import_scan(self.source, self.root / self._testMethodName)

    def test_images_to_closed_glb_and_detailed_workspace(self):
        scan = self.copy_scan()
        result = process_scan(scan, grid=24)
        self.assertGreater(result["quick"]["voxels"], 100)
        self.assertLess(result["quick"]["voxels"], 24**3/2)
        self.assertEqual(result["detailed"]["image_count"], 24)
        blob = (scan / "outputs/quick/preview.glb").read_bytes()
        self.assertEqual(struct.unpack_from("<4sII", blob), (b"glTF", 2, len(blob)))
        json_length, chunk = struct.unpack_from("<I4s", blob, 12)
        self.assertEqual(chunk, b"JSON")
        document = json.loads(blob[20:20+json_length])
        binary_length, chunk = struct.unpack_from("<I4s", blob, 20+json_length)
        self.assertEqual(chunk, b"BIN\0")
        self.assertEqual(binary_length, document["buffers"][0]["byteLength"])
        offset = 28+json_length
        count = document["accessors"][0]["count"]
        vertices = np.frombuffer(blob, dtype="<f4", count=count*3, offset=offset).reshape(-1, 3)
        normals = np.frombuffer(blob, dtype="<f4", count=count*3, offset=offset+count*12).reshape(-1, 3)
        self.assertTrue(np.isfinite(vertices).all())
        self.assertLessEqual(float(np.max(np.abs(vertices))), 0.121)
        triangles = vertices.reshape(-1, 3, 3)
        winding = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        self.assertTrue((np.sum(winding*normals[::3], axis=1) > 0).all())
        # Every geometric edge in this generated solid must have exactly two incident triangles.
        edges = Counter()
        for tri in triangles:
            points = [tuple(p) for p in tri]
            for a, b in ((0,1),(1,2),(2,0)):
                edges[tuple(sorted((points[a],points[b])))] += 1
        self.assertEqual(set(edges.values()), {2})
        mesh = json.loads((scan / "outputs/quick/mesh.json").read_text())
        np.testing.assert_array_equal(vertices.reshape(-1), mesh["positions"])
        for frame in load_manifest(scan)["frames"]:
            self.assertEqual((scan / frame["file"]).read_bytes(),
                (scan / "outputs/detailed/images" / Path(frame["file"]).name).read_bytes())
        self.assertFalse(json.loads((scan / "outputs/detailed/job.json").read_text())["reconstruction_started"])

    def test_cache_and_image_changes(self):
        scan = self.copy_scan()
        first = process_scan(scan, grid=20)
        modified = (scan / "outputs/result.json").stat().st_mtime_ns
        self.assertEqual(process_scan(scan, grid=20), first)
        self.assertEqual((scan / "outputs/result.json").stat().st_mtime_ns, modified)
        frame = scan / load_manifest(scan)["frames"][0]["file"]
        with Image.open(frame) as image:
            pixels = image.convert("RGB")
        pixels.putpixel((0, 0), (0, 0, 0))
        pixels.save(frame, quality=95)
        second = process_scan(scan, grid=20)
        self.assertNotEqual(second["fingerprint"], first["fingerprint"])

    def test_import_collision_and_source_preservation(self):
        root = self.root / self._testMethodName
        before = (self.source / "manifest.json").read_bytes()
        a, b = import_scan(self.source, root), import_scan(self.source, root)
        self.assertNotEqual(a, b)
        self.assertEqual(load_manifest(b)["scan_id"], b.name)
        self.assertEqual(before, (self.source / "manifest.json").read_bytes())

    def test_incomplete_and_unsafe_manifests(self):
        scan = self.copy_scan()
        manifest = load_manifest(scan)
        manifest["status"] = "capturing"
        write_json(scan / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "complete"):
            process_scan(scan)
        manifest["status"] = "complete"
        manifest["frames"][0]["file"] = "../source/manifest.json"
        write_json(scan / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            load_manifest(scan)

    def test_invalid_silhouette_still_prepares_detailed(self):
        scan = self.copy_scan()
        manifest = load_manifest(scan)
        cameras = {c["id"]: c for c in manifest["cameras"]}
        for frame in manifest["frames"]:
            shutil.copyfile(scan / cameras[frame["camera_id"]]["background"], scan / frame["file"])
        with self.assertRaisesRegex(ValueError, "silhouette"):
            process_scan(scan)
        self.assertFalse((scan / "outputs/result.json").exists())
        self.assertFalse((scan / ".processing").exists())
        self.assertTrue((scan / "outputs/detailed/job.json").exists())

    def test_watcher_waits_for_completion_and_does_not_repeat(self):
        scan = self.copy_scan()
        manifest = load_manifest(scan)
        manifest["status"] = "capturing"
        write_json(scan / "manifest.json", manifest)
        stop = threading.Event()
        finished = threading.Event()
        calls = []
        def complete(path):
            calls.append(path)
            finished.set()
        worker = threading.Thread(target=watch, args=(scan.parent, 20, stop, complete))
        worker.start()
        try:
            time.sleep(0.2)
            self.assertFalse((scan / "outputs/result.json").exists())
            manifest["status"] = "complete"
            write_json(scan / "manifest.json", manifest)
            self.assertTrue(finished.wait(10))
            time.sleep(2.2)
            self.assertEqual(calls, [scan])
        finally:
            stop.set()
            worker.join(10)

    def test_http_generate_process_and_artifacts(self):
        server = create_server(self.root / self._testMethodName, grid=20, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def get(path):
            with urlopen(base + path, timeout=15) as response:
                return response.read()
        def job(body):
            request = Request(base + "/api/jobs", json.dumps(body).encode(),
                              {"Content-Type": "application/json", "Origin": base})
            with urlopen(request, timeout=15) as response:
                self.assertEqual(response.status, 202)
                jid = json.load(response)["job_id"]
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                result = json.loads(get("/api/jobs/" + jid))
                if result["status"] in ("complete", "failed"):
                    self.assertEqual(result["status"], "complete", result)
                    return result
                time.sleep(0.05)
            self.fail("HTTP job timed out")
        try:
            self.assertIn(b"Pi Showcase Scanner", get("/"))
            generated = job({"action": "generate"})
            processed = job({"action": "process", "scan_id": generated["scan_id"]})
            prefix = "/scans/" + processed["scan_id"] + "/outputs/quick/"
            self.assertGreater(len(json.loads(get(prefix + "mesh.json"))["positions"]), 100)
            self.assertTrue(get(prefix + "preview.glb").startswith(b"glTF"))
            self.assertIsNotNone(json.loads(get("/api/scans"))["scans"][0]["result"])
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(base + "/api/jobs", b'{"action":"generate"}', {"Content-Type":"application/json", "Origin":"https://example.com"}))
            self.assertEqual(rejected.exception.code, 403)
            with self.assertRaises(HTTPError):
                get("/scans/" + processed["scan_id"] + "/outputs/quick/../../../../requirements.txt")
        finally:
            server.shutdown()
            thread.join(10)
            server.server_close()
            server.app.executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
