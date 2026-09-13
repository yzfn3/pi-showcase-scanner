import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageFilter
from photogrammetry.masks import foreground
from photogrammetry.validation import sharpness
from photogrammetry.workspace import prepare
from photogrammetry.colmap_runner import commands,run,detect
from sample_data.generate import generate
from windows_client.client import main
from tests.test_pi_node import running_node


class FullPhotogrammetryTests(unittest.TestCase):
    def test_windows_wrapper_and_fixed_focus_commands(self):
        with tempfile.TemporaryDirectory(prefix='colmap test ') as folder:
            root=Path(folder);(root/'bin').mkdir()
            (root/'COLMAP.bat').write_text('@echo off')
            native=root/'bin/colmap.exe';native.write_bytes(b'fixture')
            self.assertEqual(detect(str(root/'COLMAP.bat')),str(native.resolve()))
        plan=commands('colmap',Path('workspace with spaces'),Path('output'),grouped=True)
        self.assertIn('--ImageReader.single_camera_per_folder',plan[0])
        self.assertEqual(plan[0][plan[0].index('--image_path')+1],str(Path('output/inputs/images')))
        self.assertEqual(plan[0][plan[0].index('--ImageReader.mask_path')+1],str(Path('output/inputs/masks')))

    def test_masks_and_sharpness(self):
        bg=Image.new('RGB',(120,120),'white');im=bg.copy();im.paste('black',(30,30,90,90))
        mask=foreground(im,bg,25)
        coverage=(np.asarray(mask)>0).mean()
        self.assertGreater(coverage,.2);self.assertLess(coverage,.3)
        self.assertGreater(sharpness(im),sharpness(im.filter(ImageFilter.GaussianBlur(4))))
        self.assertEqual(np.asarray(foreground(bg,bg)).max(),0)
        with self.assertRaises(ValueError): foreground(im,bg,-1)

    def test_workspace_and_preservation(self):
        with tempfile.TemporaryDirectory() as folder, patch('photogrammetry.colmap_runner.detect',return_value=None):
            scan=generate(Path(folder),steps=3)
            result=prepare(scan,run_colmap=True)
            w=Path(result['full_workspace_path'])
            self.assertEqual(result['colmap_status'],'prepared_only')
            self.assertEqual(len(list((w/'images').glob('*.jpg'))),12)
            self.assertEqual(len(list((w/'masks').glob('*.png'))),12)
            self.assertTrue((w/'realitycapture/image_index.csv').is_file())
            self.assertTrue((w/'reports/mask_contact_sheet.jpg').is_file())
            self.assertTrue(any('resolution' in s for s in result['warnings']))
            m=json.loads((scan/'manifest.json').read_text())
            self.assertTrue(m['full_photogrammetry_mode']);self.assertTrue(m['retain'])
            self.assertIn('sharpness_stats',m)
            self.assertIn('--ImageReader.mask_path',(w/'reports/COLMAP_COMMANDS.txt').read_text())
            prepare(scan,threshold=255)
            self.assertTrue(any(w.parent.glob('full_photogrammetry_run_*')))
            self.assertTrue(any('Weak mask' in s for s in json.loads((w/'reports/run_report.json').read_text())['warnings']))

    def test_colmap_commands_and_failure(self):
        plan=commands('colmap',Path('workspace'),Path('out'),dense=True,mesher='poisson')
        self.assertEqual([c[1] for c in plan],['feature_extractor','exhaustive_matcher','mapper','image_undistorter','patch_match_stereo','stereo_fusion','poisson_mesher'])
        self.assertIn('--ImageReader.mask_path',plan[0])
        self.assertIn('--ImageReader.single_camera_per_folder',plan[0])
        with tempfile.TemporaryDirectory() as folder,patch('photogrammetry.colmap_runner.detect',return_value=None):
            scan=generate(Path(folder),steps=3);result=prepare(scan)
            with patch('photogrammetry.colmap_runner.detect',return_value='missing.exe'):
                failed=run(result['full_workspace_path'])
            self.assertEqual(failed['status'],'failed');self.assertTrue(failed['errors'])

    def test_full_scan_mock_http(self):
        from tests.vggt_fixture import Worker
        with tempfile.TemporaryDirectory() as folder,patch('photogrammetry.vggt_runner.available',return_value=True),patch('photogrammetry.vggt_runner.subprocess.Popen',Worker):
            root=Path(folder)
            with running_node(root/'node/scans') as (client,app):
                with redirect_stdout(io.StringIO()):
                    status=main(['--node',client.node,'--full-scan','--capture-profile','standard','--no-run-colmap','--steps','3',
                                 '--rotation-seconds','6','--capture-width','640','--capture-height','480',
                                 '--root',str(root/'received'),'--no-viewer'])
                self.assertEqual(status,0)
                scan=next((root/'received').glob('scan_*'))
                m=json.loads((scan/'manifest.json').read_text())
                self.assertTrue(m['quad_split_applied']);self.assertEqual(len(list((scan/'outputs/vggt/masks').glob('*.png'))),12)
                self.assertEqual(len(m['frames']),12)
                self.assertEqual(json.loads((scan/'outputs/vggt/reports/vggt.json').read_text())['engine'],'vggt')
                client.download_scan(scan.name,root/'received')
                self.assertEqual(json.loads((scan/'manifest.json').read_text()),m)

    def test_successful_sparse_runner_and_masks(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as folder, patch('photogrammetry.colmap_runner.detect',return_value=None):
            scan=generate(Path(folder),steps=3); prepared=prepare(scan)
            workspace=Path(prepared['full_workspace_path'])
            def execute(command, **kwargs):
                if command[1]=='mapper':
                    model=Path(command[command.index('--output_path')+1])/'0';model.mkdir()
                    (model/'images.bin').write_bytes(struct.pack('<Q',2)+b'x'*64)
                    (model/'points3D.bin').write_bytes(struct.pack('<Q',31)+b'x'*64)
                return CompletedProcess(command,0,stdout='COLMAP test fixture ImageReader.mask_path FeatureExtraction.use_gpu FeatureMatching.use_gpu',stderr='')
            with patch('photogrammetry.colmap_runner.detect',return_value='fixture-colmap'),patch('photogrammetry.colmap_runner.subprocess.run',side_effect=execute):
                result=run(workspace)
            self.assertEqual(result['status'],'sparse_partial')
            self.assertEqual(result['registered_images'],2)
            self.assertEqual(len(result['commands']),3)
            self.assertIn('--FeatureExtraction.use_gpu',result['commands'][0])
            self.assertTrue(Path(result['sparse_model']).exists())
            self.assertFalse((workspace/'colmap/dense/fused.ply').exists())

    def test_full_defaults_payload(self):
        from unittest.mock import MagicMock
        client=MagicMock();client.health.return_value=dict(mock_mode=True,backend='mock',ready=True)
        client.json.return_value={'matching':True}
        client.start.side_effect=lambda **kwargs:{'scan_id':kwargs['scan_id']}
        client.download_scan.return_value=Path('scan_20260101_000001')
        result=dict(full_workspace_path='test-workspace',report_path='test-report',status='vggt_complete',next_action='inspect',errors=[])
        with patch('windows_client.client.NodeClient',return_value=client),patch('photogrammetry.vggt_runner.run',return_value=result),redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--node','http://localhost:8000','--full-scan','--no-viewer']),0)
        payload=client.start.call_args.kwargs
        for key,value in dict(steps=8,rotation_seconds=60,capture_width=4624,capture_height=3472,camera_timeout_ms=6000,autofocus_on_capture=False,focus_mode='continuous',awb='auto',combined_quad_output=True,split_combined_output=False,zsl=True,autofocus_window='0.6,0.15,0.3,0.2').items():
            self.assertEqual(payload[key],value)

if __name__=='__main__': unittest.main()
