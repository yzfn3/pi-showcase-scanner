"""Physical capture contract exercised without camera hardware."""
import json
import io
import logging
from contextlib import redirect_stdout
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from pi_node.app import create_app
from pi_node.config import NodeError, capture_interval, validate_request
from pi_node.camera_backends.rpicam_backend import RpicamBackend
from pi_node.camera_backends.mock_backend import MockCapture
from pi_node.scan_manager import ScanService
from windows_client.client import main as client_main
from windows_client.node_api import transfer_records, NodeClientError
from windows_client.workflow import process_scan, import_scan
from windows_client.photos import photo_catalog, thumbnail
from windows_client.scan import load_manifest, write_json
from tests.test_pi_node import running_node


class PhysicalTests(unittest.TestCase):
    def setUp(self):
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def test_interval_and_validation(self):
        self.assertEqual(capture_interval(60, 12), 5)
        self.assertEqual(capture_interval(60, 24), 2.5)
        self.assertFalse(validate_request({})["timed"])
        self.assertTrue(validate_request({"rotation_seconds": 60})["timed"])
        self.assertTrue(validate_request({}, "rpicam")["timed"])
        for body in ({"split_combined_output": True}, {"rotation_seconds": float("nan")},
                     {"capture_width": 640}, {"gain": -1}, {"awb": "; echo bad"},
                     {"camera_count": 5}, {"use_backgrounds": "false"}):
            with self.subTest(body=body), self.assertRaises(NodeError):
                validate_request(body)

    def test_missing_command_health_and_graceful_start(self):
        backend = RpicamBackend(which=lambda name: None)
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Path(temp)/"scans", backend)
            try:
                http = app.test_client()
                health = http.get('/api/v1/health').get_json()
                self.assertEqual(health['backend'], 'rpicam')
                self.assertFalse(health['ready'])
                self.assertFalse(health['camera_command_found'])
                self.assertIn('Neither rpicam-still', health['last_error'])
                self.assertEqual(http.post('/api/v1/scan/start', json={}).status_code, 503)
                self.assertIn('error', http.get('/api/v1/cameras').get_json())
            finally:
                app.extensions['scan_service'].close()

    def test_command_preference_flags_and_failures(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if '--list-cameras' in args:
                return SimpleNamespace(returncode=0, stdout='Available cameras\n0 : imx219 [3280x2464]\n1 : imx477 [4056x3040]', stderr='')
            Image.new('RGB', (128, 96), 'white').save(args[args.index('--output')+1], format='JPEG')
            return SimpleNamespace(returncode=0, stdout='', stderr='capture complete')
        adapter = RpicamBackend(which=lambda name: '/usr/bin/'+name, runner=run)
        self.assertTrue(adapter.command.endswith('rpicam-still'))
        self.assertEqual(len(adapter.diagnose()['cameras']), 2)
        settings = validate_request(dict(capture_width=128, capture_height=96, exposure_time=10000,
                                        gain=1, awb='daylight', focus_mode='manual', lens_position=1), 'rpicam')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'capture.jpg'
            adapter.capture_file(path, {'device_index': 1}, settings)
            self.assertTrue(path.is_file())
            command, options = calls[-1]
            self.assertFalse(options['shell'])
            for flag in ('--shutter', '--gain', '--awb', '--autofocus-mode', '--lens-position', '--width', '--height'):
                self.assertIn(flag, command)
            with self.assertRaisesRegex(NodeError, 'overwrite'):
                adapter.capture_file(path, {}, settings)
        fallback = RpicamBackend(which=lambda name: '/bin/libcamera-still' if name == 'libcamera-still' else None)
        self.assertTrue(fallback.command.endswith('libcamera-still'))
        for error in (PermissionError('permission denied'), subprocess.TimeoutExpired('rpicam', 1)):
            with patch.object(adapter, 'runner', side_effect=error):
                self.assertIsNotNone(adapter.diagnose()['error'])
        with tempfile.TemporaryDirectory() as temp, patch.object(adapter, 'runner', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
            with self.assertRaisesRegex(NodeError, 'no image'):
                adapter.capture_file(Path(temp)/'empty.jpg', {}, settings)

    def test_backgrounds_and_progress_and_combined_download(self):
        with tempfile.TemporaryDirectory() as temp, running_node(Path(temp)/'node/scans') as (client, app):
            health = client.health()
            self.assertTrue(health['ready'])
            self.assertEqual(health['backend'], 'mock')
            settings = dict(camera_count=4, combined_quad_output=True)
            bg = client.capture_backgrounds(**settings)
            client.wait(bg['scan_id'], poll_interval=.02)
            self.assertTrue((Path(temp)/'node/backgrounds/current/index.json').is_file())
            sid = client.start(steps=3, combined_quad_output=True)['scan_id']
            state = client.wait(sid, poll_interval=.02)
            for field in ('elapsed_seconds', 'estimated_remaining_seconds', 'files_captured'):
                self.assertGreaterEqual(state[field], 0)
            self.assertEqual(state['files_captured'], 3)
            self.assertEqual(state['estimated_remaining_seconds'], 0)
            scan = client.download_scan(sid, Path(temp)/'received')
            m = load_manifest(scan)
            for key in ('backend', 'camera_command', 'camera_count', 'steps', 'rotation_seconds',
                        'capture_interval_seconds', 'capture_width', 'capture_height', 'combined_quad_output',
                        'split_combined_output', 'backgrounds_available', 'capture_started_at',
                        'capture_completed_at', 'raw_files', 'raw_combined_files', 'outputs', 'errors', 'software_version'):
                self.assertIn(key, m)
            self.assertTrue(m['backgrounds_available'])
            self.assertEqual(len(m['raw_combined_files']), 3)
            self.assertEqual(m['frames'], [])
            result = process_scan(scan)
            self.assertEqual(result['quick']['status'], 'unavailable')
            self.assertTrue(result['detailed']['requires_split'])
            self.assertEqual(result['detailed']['image_count'], 3)
            self.assertFalse((scan/'outputs/quick/preview.glb').exists())
            self.assertEqual(photo_catalog(scan)['total'], 3)
            self.assertTrue(thumbnail(scan, m['raw_combined_files'][0]).startswith(b'\xff\xd8'))
            imported = import_scan(scan, Path(temp)/'imported')
            self.assertTrue(load_manifest(imported)['retain'])
            bad = json.loads(json.dumps(m)); bad['files'][0]['file'] = 'raw_combined/../../escape.jpg'
            with self.assertRaises(NodeClientError):
                transfer_records(bad, sid)
            # Exercise the public CLI receive/stage path; no model should be invented.
            with patch('logging.basicConfig'), patch('logging.FileHandler', return_value=logging.NullHandler()), redirect_stdout(io.StringIO()):
                self.assertEqual(client_main(['--node', client.node, '--scan-id', sid, '--root', str(scan.parent), '--no-viewer']), 0)

    def test_deadlines_do_not_accumulate_capture_time(self):
        class Clock:
            value = 0
            stopped = False
            def monotonic(self): return self.value
            def wait(self, seconds): self.value += seconds; return self.stopped
            def is_set(self): return self.stopped
            def set(self): self.stopped = True
        clock = Clock()
        class TimedCamera(MockCapture):
            name = 'rpicam'
            mock_mode = False
            command = 'fake-rpicam'
            cost = .4
            def capture_file(self, path, camera, settings):
                path.parent.mkdir(exist_ok=True, parents=True)
                Image.new('RGB', (64,64), 'white').save(path)
                clock.value += self.cost
                return dict(output='fake capture')
        with tempfile.TemporaryDirectory() as temp:
            service = ScanService(Path(temp)/'scans', TimedCamera())
            params = validate_request(dict(steps=12, rotation_seconds=60, use_backgrounds=False), 'rpicam')
            params['operation'] = 'scan'
            sid = 'scan_20260101_000000'
            folder = service.root/sid; folder.mkdir()
            service.states[sid] = dict(scan_id=sid, state='pending', current_step=0, total_steps=12, files_captured=0, planned_seconds=60)
            service.stop = clock
            try:
                with patch('pi_node.scan_manager.time', clock):
                    service._capture(sid, params)
                manifest = load_manifest(folder)
                self.assertEqual([r['actual_start_seconds'] for r in manifest['capture_logs']], list(range(0,60,5)))
                self.assertAlmostEqual(service.status(sid)['elapsed_seconds'], 55.4)
                self.assertFalse(manifest['backgrounds_available'])
                service.backend.cost = 6
                late_sid = 'scan_20260101_000001'
                (service.root/late_sid).mkdir()
                service.states[late_sid] = {**service.states[sid], 'scan_id':late_sid, 'state':'pending'}
                with patch('pi_node.scan_manager.time', clock):
                    service._capture(late_sid, params)
                failed = json.loads((service.root/late_sid/'manifest.json').read_text())
                self.assertEqual(failed['status'], 'failed')
                self.assertIn('interval', failed['errors'][0]['message'])
                self.assertEqual(len(failed['raw_combined_files']), 1)
            finally:
                service.close()
