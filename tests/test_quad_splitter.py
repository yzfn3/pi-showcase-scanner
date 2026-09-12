import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from preview3d.quad_splitter import crop_box, load_config, split_scan, needs_split, digest
from windows_client.scan import load_manifest, write_json
from windows_client.workflow import process_scan, import_scan
from windows_client.client import main as client_main
from tests.test_pi_node import running_node


class QuadSplitterTests(unittest.TestCase):
    def make_tiles(self, root):
        scan = Path(root)/'scan_20260912_120000'
        (scan/'raw_combined').mkdir(parents=True)
        image = Image.new('RGB', (200,120))
        for color, box in zip(('red','lime','blue','yellow'), ((0,0,100,60),(100,0,200,60),(0,60,100,120),(100,60,200,120))):
            image.paste(color, box)
        image.save(scan/'raw_combined/step_000_quad.jpg', quality=100, subsampling=0)
        return scan

    def test_normalized_crop_math_and_rejection(self):
        config = load_config()
        self.assertEqual(crop_box(config['crops']['cam_04'], (1920,1080)), (960,540,1920,1080))
        left = crop_box(config['crops']['cam_01'], (101,99))
        right = crop_box(config['crops']['cam_02'], (101,99))
        self.assertEqual(left[2], right[0])
        for crop in (dict(x=-.1,y=0,w=.5,h=.5), dict(x=.8,y=0,w=.5,h=.5),
                     dict(x=0,y=0,w=0,h=.5),dict(x=0,y=0,w=float('nan'),h=.5)):
            with self.subTest(crop=crop), self.assertRaises(ValueError): crop_box(crop,(100,100))

    def test_tiles_filenames_contact_sheets_dry_run_and_order(self):
        with tempfile.TemporaryDirectory() as root:
            scan = self.make_tiles(root)
            original = digest(scan/'raw_combined/step_000_quad.jpg')
            before = sorted(str(p.relative_to(scan)) for p in scan.rglob('*') if p.is_file())
            self.assertTrue(needs_split(scan))
            plan = split_scan(scan, dry_run=True)
            self.assertEqual(plan['split_images'], 4)
            self.assertEqual(before, sorted(str(p.relative_to(scan)) for p in scan.rglob('*') if p.is_file()))
            result = split_scan(scan)
            self.assertFalse(needs_split(scan))
            self.assertTrue(result['quad_split_applied'])
            self.assertEqual(len(result['raw_combined_files']), 1)
            self.assertEqual(result['raw_files'], result['split_files'])
            self.assertIn('split_seconds', result['timing_summary'])
            self.assertEqual(len(result['contact_sheet_files']), 2)
            for i,color in enumerate(((255,0,0),(0,255,0),(0,0,255),(255,255,0)),1):
                with Image.open(scan/f'raw/step_000_cam_{i:02d}.jpg') as image:
                    self.assertEqual(image.size,(100,60))
                    self.assertLess(np.abs(np.array(image.getpixel((50,30)))-color).max(),5)
            for name in result['contact_sheet_files']:
                with Image.open(scan/name) as image: self.assertEqual(image.width,800)
            mtimes = {f:(scan/f).stat().st_mtime_ns for f in result['split_files']}
            split_scan(scan)
            self.assertEqual(mtimes,{f:(scan/f).stat().st_mtime_ns for f in mtimes})
            config=load_config();config['camera_order']=['cam_02','cam_01','cam_03','cam_04']
            path=Path(root)/'layout.json';write_json(path,config)
            split_scan(scan,path)
            with Image.open(scan/'raw/step_000_cam_02.jpg') as image:
                self.assertGreater(image.getpixel((50,30))[0],250)
            self.assertEqual(original,digest(scan/'raw_combined/step_000_quad.jpg'))
            imported=import_scan(scan,Path(root)/'imported')
            split_scan(imported,path)
            self.assertTrue(load_manifest(imported)['retain'])

    def test_no_overwrite_of_unowned_or_modified_files(self):
        with tempfile.TemporaryDirectory() as root:
            scan=self.make_tiles(root)
            (scan/'raw').mkdir();protected=scan/'raw/step_000_cam_01.jpg'
            protected.write_bytes(b'precious')
            self.assertFalse(needs_split(scan))
            with self.assertRaisesRegex(ValueError,'overwrite'): split_scan(scan)
            self.assertEqual(protected.read_bytes(),b'precious')
            self.assertFalse((scan/'manifest.json').exists())
            protected.unlink()  # Test-owned placeholder only.
            split_scan(scan)
            protected.write_bytes(b'edited by user')
            with self.assertRaisesRegex(ValueError,'overwrite'): split_scan(scan)
            self.assertEqual(protected.read_bytes(),b'edited by user')

    def test_interrupted_commit_can_resume(self):
        with tempfile.TemporaryDirectory() as root:
            scan=self.make_tiles(root)
            replace=Path.replace
            def fail_one(path,target):
                if str(target).endswith('step_000_cam_02.jpg'): raise OSError('simulated disk interruption')
                return replace(path,target)
            with patch.object(Path,'replace',fail_one), self.assertRaises(OSError): split_scan(scan)
            self.assertTrue(needs_split(scan))
            self.assertTrue((scan/'quad_split.pending.json').exists())
            result=split_scan(scan)
            self.assertEqual(len(result['frames']),4)
            self.assertFalse((scan/'quad_split.pending.json').exists())

    def test_client_auto_split_export_backgrounds_and_resume(self):
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        with tempfile.TemporaryDirectory() as root, running_node(Path(root)/'node/scans') as (client,app):
            received=Path(root)/'received'
            args=['--node',client.node,'--root',str(received),'--start-scan','--steps','6',
                  '--combined-quad-output','--quality','fast','--poll-interval','.05']
            with patch('logging.basicConfig'), patch('logging.FileHandler',return_value=logging.NullHandler()), redirect_stdout(io.StringIO()), patch('windows_client.client.show_result') as viewer:
                self.assertEqual(client_main(args),0)
                viewer.assert_called_once()
            scan=next(received.glob('scan_*'));m=load_manifest(scan)
            self.assertEqual(len(m['frames']),24)
            self.assertEqual(len(m['combined_frames']),6)
            self.assertTrue(m['backgrounds_available'])
            for camera in m['cameras']:
                with Image.open(scan/camera['background']) as bg, Image.open(scan/m['frames'][0]['file']) as raw:
                    self.assertEqual(bg.size,raw.size)
            self.assertTrue((scan/'outputs/quick/preview.glb').read_bytes().startswith(b'glTF'))
            self.assertTrue((scan/'outputs/quick/preview.obj').is_file())
            report=json.loads((scan/'outputs/result.json').read_text())
            self.assertEqual(report['quick']['frames_total'],24)
            self.assertEqual(report['detailed']['image_count'],24)
            before=(scan/'manifest.json').read_bytes()
            self.assertEqual(client.download_scan(scan.name,received),scan)
            self.assertEqual(before,(scan/'manifest.json').read_bytes())
            self.assertEqual(process_scan(scan,grid=32),report)
