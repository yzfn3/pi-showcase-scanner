import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import request
from PIL import Image
from pi_node.config import validate_request, NodeError
from pi_node.camera_backends.rpicam_backend import RpicamBackend
from windows_client.client import main
from tests.test_pi_node import running_node


class CaptureOptionTests(unittest.TestCase):
    def test_focus_command_modes_and_timeout(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            Image.new('RGB', (64,64)).save(args[args.index('--output')+1], format='JPEG')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        backend = RpicamBackend(which=lambda name: name, runner=run)
        with tempfile.TemporaryDirectory() as temp:
            for mode in ('auto', 'continuous', 'manual', None):
                settings = validate_request(dict(camera_timeout_ms=5000, autofocus_on_capture=True,
                    focus_mode=mode, lens_position=1, awb='auto', awbgains='1.0,1.0'), 'rpicam')
                backend.capture_file(Path(temp)/f'{mode}.jpg', {}, settings)
                args, kwargs = calls[-1]
                self.assertEqual(args[args.index('--timeout')+1], '5000')
                self.assertEqual('--autofocus-on-capture' in args, mode != 'continuous')
                self.assertEqual('--lens-position' in args, mode == 'manual')
                if mode:
                    self.assertEqual(args[args.index('--autofocus-mode')+1], mode)
                self.assertEqual(args[args.index('--awb')+1], 'auto')
                self.assertEqual(args[args.index('--awbgains')+1], '1.0,1.0')
                self.assertGreaterEqual(kwargs['timeout'], 25)

    def test_background_and_scan_cli_payload_and_manifests(self):
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        with tempfile.TemporaryDirectory() as temp, running_node(Path(temp)/'node/scans') as (client, app):
            payloads = []
            @app.before_request
            def record_request():
                if request.method == 'POST':
                    payloads.append(request.get_json())
            common = ['--node', client.node, '--root', str(Path(temp)/'received'),
                '--camera-timeout-ms', '5000', '--autofocus-on-capture', '--awbgains', '1.0,1.0',
                '--focus-mode', 'auto', '--capture-width', '1920', '--capture-height', '1080',
                '--combined-quad-output', '--poll-interval', '.05']
            with patch('logging.basicConfig'), patch('logging.FileHandler', return_value=logging.NullHandler()), redirect_stdout(io.StringIO()):
                self.assertEqual(main(common+['--capture-backgrounds']), 0)
                self.assertEqual(main(common+['--start-scan', '--steps', '3', '--no-viewer']), 0)
            self.assertEqual(len(payloads), 2)
            expected = dict(camera_timeout_ms=5000, autofocus_on_capture=True, awbgains='1.0,1.0')
            for payload in payloads:
                for key, value in expected.items(): self.assertEqual(payload[key], value)
                self.assertEqual(payload['awb'], 'auto')
            for path in (Path(temp)/'node/scans').glob('scan_*/manifest.json'):
                manifest = json.loads(path.read_text())
                for key, value in expected.items():
                    self.assertEqual(manifest[key], value)
                    self.assertEqual(manifest['settings'][key], value)
            index = json.loads((Path(temp)/'node/backgrounds/current/index.json').read_text())
            for key, value in expected.items(): self.assertEqual(index['settings'][key], value)

    def test_validation(self):
        self.assertEqual(validate_request({}, 'rpicam')['awb'], 'auto')
        for body in ({'camera_timeout_ms':0}, {'camera_timeout_ms':True}, {'camera_timeout_ms':2.5},
                     {'autofocus_on_capture':'true'}, {'awbgains':'1'}, {'awbgains':'nan,1'},
                     {'awbgains':'0,1'}, {'awb':'balanced'}, {'awb':'white'}):
            with self.subTest(body=body), self.assertRaises(NodeError): validate_request(body)
