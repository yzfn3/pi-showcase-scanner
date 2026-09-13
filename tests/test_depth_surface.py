import importlib.util
import tempfile
import unittest
from pathlib import Path
import numpy as np
from photogrammetry.depth_surface import fuse,project_colors


@unittest.skipUnless(importlib.util.find_spec('open3d'),'optional reconstruction dependency')
class DepthSurfaceTests(unittest.TestCase):
    def data(self,size=32):
        mask=np.zeros((4,size,size),bool);mask[:,4:-4,4:-4]=True
        return dict(depth=np.ones((4,size,size,1),np.float32),masks=mask,confidence=np.ones((4,size,size),np.float32),
            extrinsics=np.repeat(np.eye(4,dtype=np.float32)[None,:3],4,axis=0),
            intrinsics=np.repeat(np.array([[[64.,0,size/2],[0,64.,size/2],[0,0,1]]]),4,axis=0),
            rgb=np.tile(np.array([255,0,0],np.uint8),(4,size,size,1)))

    def test_fusion_retains_plane_depth_and_color(self):
        mesh,report=fuse(self.data(),resolution=96,smoothing=0)
        self.assertGreater(len(mesh.triangles),100)
        np.testing.assert_allclose(np.asarray(mesh.vertices)[:,2],1,atol=.01)
        self.assertGreater(np.asarray(mesh.vertex_colors)[:,0].mean(),.9)
        self.assertLess(np.asarray(mesh.vertex_colors)[:,1].mean(),.1)
        self.assertEqual(report['frames'],4)

    def test_empty_masks_and_invalid_settings_fail(self):
        d=self.data();d['masks'][:]=False
        with self.assertRaises(ValueError):fuse(d)
        for kw in (dict(resolution=0),dict(smoothing=-1),dict(truncation=100),dict(resolution=True)):
            with self.assertRaises(ValueError):fuse(self.data(),**kw)

    def test_glb_view_preserves_texture_coordinates(self):
        if not importlib.util.find_spec('trimesh'):self.skipTest('optional TRELLIS dependency')
        import trimesh,json
        from PIL import Image
        from photogrammetry.trellis_runner import export_glb
        mesh=trimesh.Trimesh(vertices=[[0,0,0],[1,0,0],[0,1,0]],faces=[[0,1,2]],process=False)
        mesh.visual=trimesh.visual.TextureVisuals(uv=[[0,0],[1,0],[0,1]],image=Image.new('RGB',(8,8),'red'))
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);mesh.export(w/'model.glb');r=export_glb(w/'model.glb',w)
            data=json.loads((w/r['file']).read_text());self.assertEqual(len(data['uv']),6)
            self.assertTrue((w/'reports'/data['texture']).is_file())

    def test_trellis_rejects_non_scan_source(self):
        import json
        from photogrammetry.trellis_runner import run
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);(w/'reports').mkdir();(w/'reports/vggt.json').write_text(json.dumps({'selected_images':['frame.jpg']}))
            with self.assertRaises(ValueError):run(w,source='../secret.jpg')

    def test_saved_depth_rebuild_preserves_original_mesh_and_view(self):
        import json
        from PIL import Image
        from photogrammetry.depth_surface import run
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);(w/'reports').mkdir();(w/'model_inputs').mkdir();d=self.data()
            names=[f'frame_{i}.jpg' for i in range(4)]
            for name,rgb in zip(names,d.pop('rgb')):Image.fromarray(rgb).save(w/'model_inputs'/(name+'.png'))
            np.savez(w/'predictions.npz',**d)
            original={'file':'reports/original.json','mesh_file':'mesh.ply'}
            (w/'reports/vggt.json').write_text(json.dumps(dict(selected_images=names,mesh_view=original)))
            (w/'mesh.ply').write_bytes(b'original retained')
            run(w,resolution=96,smoothing=0,publish=True)
            self.assertEqual((w/'mesh.ply').read_bytes(),b'original retained')
            report=json.loads((w/'reports/vggt.json').read_text())
            self.assertEqual(report['mesh_view'],original)
            self.assertTrue((w/report['fused_mesh_view']['mesh_file']).is_file())
