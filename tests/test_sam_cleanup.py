import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from photogrammetry.sam2_masks import prompt_from_mask,validate_region
from photogrammetry.mesh_cleanup import silhouette_support,run

class MaskCleanupTests(unittest.TestCase):
    def test_invalid_prompt_regions(self):
        for region in ([0,0,2,1],[0,0,0,1],[0,float('nan'),1,1],[]):
            with self.assertRaises(ValueError):validate_region(region)

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'optional SAM dependency')
    def test_prompt_ignores_disconnected_background_noise(self):
        mask=np.zeros((100,100),bool);mask[40:70,30:60]=True;mask[1,1]=True
        box,point,method=prompt_from_mask(mask,[.1,.1,.8,.8])
        self.assertGreater(box[0],1);self.assertTrue(mask[point[0,1],point[0,0]])
        box,point,method=prompt_from_mask(np.zeros_like(mask),[.2,.3,.4,.5])
        np.testing.assert_allclose(box,[20,30,60,80]);self.assertIsNone(point)

    def test_projection_uses_correct_camera_coordinates(self):
        vertices=np.array([[0,0,2],[8,0,2],[0,0,-1]])
        e=np.array([np.eye(4)[:3]]);k=np.array([[[2,0,5],[0,2,5],[0,0,1]]])
        masks=np.zeros((1,10,10),bool);masks[0,5,5]=True
        hits,visible=silhouette_support(vertices,e,k,masks)
        np.testing.assert_array_equal(hits,[1,0,0]);np.testing.assert_array_equal(visible,[1,0,0])

    @unittest.skipUnless(importlib.util.find_spec('open3d'),'optional mesh dependency')
    def test_cleanup_preserves_source_and_exports_separate_mesh(self):
        import open3d as o
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);(w/'reports').mkdir()
            sphere=o.geometry.TriangleMesh.create_sphere(resolution=12)
            tiny=o.geometry.TriangleMesh.create_tetrahedron(radius=.01).translate([5,5,5])
            o.io.write_triangle_mesh(str(w/'mesh.ply'),sphere+tiny)
            original=(w/'mesh.ply').read_bytes()
            r=run(w,smoothing=0)
            self.assertEqual(original,(w/'mesh.ply').read_bytes())
            self.assertLess(r['after_faces'],r['before_faces'])
            self.assertTrue((w/'mesh_clean.ply').exists())
            self.assertTrue((w/r['view']['file']).exists())
            self.assertFalse(r['ai_masks'])

    def test_gallery_lists_only_actual_model_inputs(self):
        from PIL import Image
        from sample_data.generate import generate
        from windows_client.scan import load_manifest
        from windows_client.photos import photo_catalog,thumbnail
        with tempfile.TemporaryDirectory() as temp:
            scan=generate(Path(temp),steps=3,size=64,seed='5a12')
            frame=load_manifest(scan)['frames'][0]
            w=scan/'outputs/worldmirror2';(w/'reports').mkdir(parents=True);(w/'model_inputs').mkdir()
            (w/'reports/vggt.json').write_text('{}')
            name=Path(frame['file']).name+'.png'
            Image.new('RGB',(64,64),'white').save(w/'model_inputs'/name)
            items=photo_catalog(scan,kind='model_inputs')
            self.assertEqual(items['total'],1)
            self.assertTrue(items['items'][0]['model_input'])
            self.assertTrue(thumbnail(scan,items['items'][0]['file']))
            with self.assertRaises(ValueError):thumbnail(scan,'outputs/worldmirror2/model_inputs/not-a-frame.png')
