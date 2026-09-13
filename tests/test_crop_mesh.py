import json
import struct
import sqlite3
from contextlib import closing
import numpy as np
import tempfile
import unittest
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch
from PIL import Image
from photogrammetry.object_crop import apply, padded_box
from photogrammetry.mesh_view import export_mesh
from photogrammetry.mesh_view import simplify_surface
from photogrammetry.dense_runner import run
from photogrammetry.dense_masks import mapping
from photogrammetry.colmap_runner import initialize_crop_intrinsics
from windows_client.scan import write_json

PLY='''ply
format ascii 1.0
element vertex 3
property float x
property float y
property float z
element face 1
property list uchar int vertex_indices
end_header
0 0 0
1 0 0
0 1 0
3 0 1 2
'''


class CropMeshTests(unittest.TestCase):
    def test_surface_simplification_retains_connected_faces(self):
        vertices=np.array([[x,y,0.] for y in range(10) for x in range(10)])
        faces=[]
        for y in range(9):
            for x in range(9):
                i=y*10+x;faces.extend([[i,i+1,i+10],[i+1,i+11,i+10]])
        v,f=simplify_surface(vertices,np.array(faces),30)
        self.assertGreater(len(f),0);self.assertLessEqual(len(f),30)
        # A solid triangulated disk has Euler characteristic 1. Random face
        # sampling would leave holes or disconnected triangles instead.
        edges={tuple(sorted(pair)) for t in f for pair in ((t[0],t[1]),(t[1],t[2]),(t[2],t[0]))}
        self.assertEqual(len(set(f.flat))-len(edges)+len(f),1)
    def test_crop_intrinsics_and_dense_mask_pixel_coordinates(self):
        original=(2,100,80,(120.,50.,40.,0.))
        u,v,valid=mapping(original,(1,100,80,(120.,120.,50.,40.)))
        self.assertTrue(valid.all());self.assertTrue(np.array_equal(u[0],np.arange(100)))
        self.assertTrue(np.array_equal(v[:,0],np.arange(80)))
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'db'
            with closing(sqlite3.connect(db)) as c:
                c.execute('CREATE TABLE cameras(camera_id INTEGER,model INTEGER,params BLOB,prior_focal_length INTEGER)')
                c.execute('INSERT INTO cameras VALUES(1,2,NULL,1)');c.commit()
            initialize_crop_intrinsics(db,{'image_preprocessing':{'cameras':{'cam_01':{'rotated_size':[100,80],'crop_box':[20,10,90,70]}}}},
                                       {'cam_01':{'camera_id':1}})
            with closing(sqlite3.connect(db)) as c:params=c.execute('SELECT params FROM cameras').fetchone()[0]
            self.assertEqual(struct.unpack('<4d',params),(120.,30.,30.,0.))

    def test_distant_mask_speck_does_not_expand_crop(self):
        from photogrammetry.object_crop import object_mask
        mask=Image.new('L',(200,200));mask.paste(255,(60,50,140,160));mask.paste(255,(190,190,194,194))
        self.assertEqual(object_mask(mask).getbbox(),(60,50,140,160))

    def test_same_crop_for_moving_object_and_aligned_masks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('images','masks','reports'):(root/name).mkdir()
            frames=[]
            for i,box in enumerate(((10,20,30,60),(20,20,40,60))):
                name=f'step_{i:03d}_cam_01.jpg';im=Image.new('RGB',(100,80),'white');im.paste('red',box);im.save(root/'images'/name)
                mask=Image.new('L',(100,80));mask.paste(255,box);mask.save(root/'masks'/(name+'.png'))
                frames.append(dict(file='raw/'+name,camera_id='cam_01',step=i))
            events=[];result,_=apply(root,dict(frames=frames),.15,90,True,events.append)
            t=result['cameras']['cam_01'];self.assertEqual(t['crop_box'],[17,7,63,43])
            for f in frames:
                name=Path(f['file']).name
                with Image.open(root/'images'/name) as im,Image.open(root/'masks'/(name+'.png')) as mask:
                    self.assertEqual(im.size,(46,36));self.assertEqual(im.size,mask.size)
                    self.assertEqual(sum(mask.histogram()[1:]),800)
            self.assertEqual(events[-1]['done'],2)
            self.assertEqual(padded_box((0,0,10,10),(10,10),.15),(0,0,10,10))

    def test_no_double_rotation_and_empty_mask_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('images','masks','reports'):(root/name).mkdir()
            name='step_000_cam_01.jpg';Image.new('RGB',(20,40),'red').save(root/'images'/name)
            Image.new('L',(20,40)).save(root/'masks'/(name+'.png'))
            result,_=apply(root,dict(frames=[dict(file=name,camera_id='cam_01',step=0)],quad_split_config={'rotations':{'cam_01':90}}))
            self.assertEqual(result['cameras']['cam_01']['output_size'],[20,40]);self.assertTrue(result['warnings'])

    def test_actual_ascii_and_binary_mesh_triangles(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'reports').mkdir();source=root/'surface.ply';source.write_text(PLY)
            view=export_mesh(root,source);data=json.loads((root/view['file']).read_text())
            self.assertEqual(data['triangles_displayed'],1);self.assertEqual(data['normals'],[0.,0.,1.]*3)
            source.write_bytes(PLY.split('end_header')[0].replace('format ascii','format binary_little_endian').encode()+b'end_header\n'+
                struct.pack('<9fB3i',0,0,0,1,0,0,0,1,0,3,0,1,2))
            self.assertEqual(export_mesh(root,source)['triangles_total'],1)

    def test_mesh_phase_reuses_sparse_and_preserves_it_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('reports','logs','colmap/inputs/images','colmap/sparse/0'):(root/name).mkdir(parents=True,exist_ok=True)
            (root/'colmap/sparse/0/images.bin').write_bytes(struct.pack('<Q',4))
            initial=dict(status='sparse_complete',sparse_model=str(root/'colmap/sparse/0'),output_path=str(root/'colmap'),warnings=[],errors=[])
            write_json(root/'reports/colmap.json',initial)
            def command(args,**kwargs):
                if args[1]=='poisson_mesher':Path(args[args.index('--output_path')+1]).write_text(PLY)
            with patch('photogrammetry.dense_runner.detect',return_value='colmap.exe'),patch('photogrammetry.dense_runner.subprocess.run',side_effect=command) as executed:
                result=run(root,max_image_size=1000)
            self.assertEqual([c.args[0][1] for c in executed.call_args_list],['image_undistorter','patch_match_stereo','stereo_fusion','poisson_mesher'])
            self.assertEqual(result['mesh_status'],'complete');self.assertEqual(result['status'],'sparse_complete')
            (Path(result['dense_path'])/'fused.ply').write_text(PLY+'\n'*600)
            with patch('photogrammetry.dense_runner.detect',return_value='colmap.exe'),patch('photogrammetry.dense_runner.subprocess.run',side_effect=command) as executed:
                reused=run(root,max_image_size=1000)
            self.assertTrue(reused['dense_reused'])
            self.assertEqual([c.args[0][1] for c in executed.call_args_list],['poisson_mesher'])
            with patch('photogrammetry.dense_runner.detect',return_value='colmap.exe'),patch('photogrammetry.dense_runner.subprocess.run',side_effect=CalledProcessError(1,['colmap'])):
                failed=run(root)
            self.assertEqual(failed['mesh_status'],'failed');self.assertNotIn('mesh_view',failed)
            self.assertEqual(failed['sparse_model'],initial['sparse_model']);self.assertFalse((root/'.reconstructing').exists())


if __name__=='__main__':unittest.main()
