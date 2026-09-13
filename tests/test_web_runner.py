import json
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import requests
from photogrammetry.colmap_runner import audit_cameras
from photogrammetry.result_view import export_view
from windows_client.server import create_server
from tests.test_pi_node import running_node


class RunnerTests(unittest.TestCase):
    def test_shared_models_reject_split_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'database.db'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE images(name TEXT,camera_id INTEGER)')
                connection.executemany('INSERT INTO images VALUES(?,?)',
                    [(f'cam_{c:02d}/step_{s:03d}_cam_{c:02d}.jpg',c) for c in range(1,5) for s in range(12)])
                connection.commit()
            groups=audit_cameras(db)
            self.assertEqual(len(groups),4)
            self.assertTrue(all(c['images']==12 for c in groups.values()))
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('UPDATE images SET camera_id=99 WHERE name=?',('cam_01/step_000_cam_01.jpg',))
                connection.commit()
            with self.assertRaises(RuntimeError):audit_cameras(db)

    def test_real_binary_points_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'reports').mkdir();model=root/'model';model.mkdir()
            with (model/'points3D.bin').open('wb') as stream:
                stream.write(struct.pack('<Q',3))
                for i in range(3):
                    stream.write(struct.pack('<QdddBBBdQii',i,float(i),2.,3.,255,128,0,.1,1,1,2))
            view=export_view(root,{'sparse_model':str(model)},limit=2)
            data=json.loads((root/view['file']).read_text())
            self.assertEqual(data['positions'],[0.,2.,3.,2.,2.,3.])
            self.assertEqual(data['colors'][:3],[1.,128/255,0.])
            self.assertEqual(data['points_total'],3)

    def test_browser_mock_capture_transfer_and_prepare(self):
        from tests.vggt_fixture import Worker
        with tempfile.TemporaryDirectory() as folder,patch('photogrammetry.colmap_runner.detect',return_value=None), patch('photogrammetry.vggt_runner.available',return_value=True), patch('photogrammetry.vggt_runner.subprocess.Popen',Worker), patch('photogrammetry.colmap_runner.run',side_effect=AssertionError('COLMAP must not run')):
            root=Path(folder)
            with running_node(root/'node') as (node,_):
                server=create_server(root/'received',port=0)
                thread=threading.Thread(target=server.serve_forever);thread.start()
                url=f'http://127.0.0.1:{server.server_port}'
                def job(action,**extra):
                    response=requests.post(url+'/api/jobs',headers={'Origin':url},json=dict(action=action,node=node.node,
                        settings=dict(steps=3,rotation_seconds=6,capture_width=640,capture_height=480),run_colmap=False,**extra),timeout=5)
                    response.raise_for_status();jid=response.json()['job_id']
                    end=time.monotonic()+90
                    while time.monotonic()<end:
                        result=requests.get(url+'/api/jobs/'+jid,timeout=5).json()
                        if result['status'] not in ('queued','running'):break
                        time.sleep(.1)
                    self.assertEqual(result['status'],'complete',result)
                    return result
                try:
                    self.assertIn('settings',requests.get(url+'/api/runner',timeout=5).json())
                    self.assertEqual(requests.post(url+'/api/jobs',json={'action':'capture'},timeout=5).status_code,403)
                    self.assertIn('Connected',job('connect')['message'])
                    job('backgrounds',empty_table=True)
                    with patch('photogrammetry.vggt_runner.run', side_effect=AssertionError('Capture only must not reconstruct')):
                        captured=job('capture_only')
                    captured_scan=root/'received'/captured['scan_id']
                    self.assertTrue((captured_scan/'manifest.json').is_file())
                    self.assertFalse((captured_scan/'outputs/vggt').exists())
                    capture_photos=requests.get(url+f"/api/scans/{captured['scan_id']}/photos?filter=all",timeout=5).json()
                    self.assertGreater(capture_photos['total'],0)
                    completed=job('capture');sid=completed['scan_id']
                    report=requests.get(url+f'/api/scans/{sid}/full',timeout=5).json()
                    self.assertEqual(report['report']['processed_images'],12)
                    self.assertEqual(report['reconstruction']['engine'],'vggt')
                    self.assertTrue(report['artifacts'])
                    artifact=requests.get(url+'/scans/'+sid+'/outputs/vggt/reports/run_report.md',timeout=5)
                    self.assertEqual(artifact.status_code,200)
                    self.assertTrue((root/'received'/sid/'raw_combined').is_dir())
                    photos=requests.get(url+f'/api/scans/{sid}/photos?filter=reconstruction',timeout=5).json()
                    self.assertEqual(photos['total'],12)
                    self.assertTrue(photos['items'][0]['reconstruction'])
                    image=requests.get(url+f'/api/scans/{sid}/thumbnail',params={'file':photos['items'][0]['file']},timeout=5)
                    self.assertEqual(image.status_code,200);self.assertTrue(image.content.startswith(b'\xff\xd8'))
                finally:
                    server.shutdown();thread.join();server.server_close();server.app.executor.shutdown()


if __name__=='__main__':unittest.main()
