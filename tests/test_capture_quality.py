import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from photogrammetry.vggt_runner import brighten
from photogrammetry.mesh_view import export_mesh
from photogrammetry.learned_workspace import active_base
from windows_client.full_runner import DEFAULT_SETTINGS
from pi_node.config import validate_request

class CaptureQualityTests(unittest.TestCase):
    def test_capture_defaults_are_real_exposure_controls(self):
        settings=validate_request(DEFAULT_SETTINGS,'rpicam')
        self.assertEqual(settings['gain'],3)
        self.assertEqual(settings['exposure_time'],4000)
        self.assertEqual(settings['lens_position'],6.85)
        image=Image.fromarray(np.arange(256,dtype=np.uint8).reshape(16,16))
        np.testing.assert_array_equal(np.asarray(brighten(image,1)),np.asarray(image.convert('RGB')))

    def test_color_mesh_export_preserves_captured_colors(self):
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);(w/'reports').mkdir()
            (w/'mesh.ply').write_text('ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0 255 0 0\n1 0 0 0 255 0\n0 1 0 0 0 255\n3 0 1 2\n')
            export_mesh(w,w/'mesh.ply')
            result=json.loads((w/'reports/mesh_view.json').read_text())
            self.assertEqual(result['colors'],[1,0,0,0,1,0,0,0,1])

    def test_incomplete_new_engine_does_not_hide_completed_result(self):
        with tempfile.TemporaryDirectory() as temp:
            scan=Path(temp)
            a=scan/'outputs/vggt/reports';a.mkdir(parents=True);(a/'vggt.json').write_text('{}')
            b=scan/'outputs/worldmirror2';b.mkdir(parents=True);(b/'workspace.json').write_text('{}')
            self.assertEqual(active_base(scan),'outputs/vggt')
