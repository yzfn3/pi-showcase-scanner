"""Phase 2: reuse sparse cameras, reconstruct dense depth and an actual surface."""
import json
import logging
import os
from pathlib import Path
import subprocess
import struct
import uuid
from photogrammetry.colmap_runner import detect
from photogrammetry.mesh_view import export_mesh
from windows_client.scan import write_json

LOG=logging.getLogger(__name__)


def run(workspace, mesher='poisson', max_image_size=1600, progress=None, timeout=7200):
    if mesher not in ('poisson','delaunay'):raise ValueError('Choose Poisson or Delaunay meshing')
    if type(max_image_size) is not int or not 320<=max_image_size<=4000:raise ValueError('Dense image size must be 320..4000')
    workspace=Path(workspace).resolve();progress=progress or (lambda event:None)
    lock=workspace/'.reconstructing'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
    try:
        result=json.loads((workspace/'reports/colmap.json').read_text())
        if not result.get('sparse_model'):raise ValueError('Complete phase 1 before building a mesh')
        sparse=Path(result['sparse_model']).resolve();output=Path(result['output_path']).resolve()
        if not sparse.is_relative_to(workspace) or not output.is_relative_to(workspace):raise ValueError('Reconstruction paths leave workspace')
        if not (sparse/'images.bin').is_file():raise ValueError('Sparse model is missing; rerun phase 1')
        executable=detect()
        if not executable:raise ValueError('COLMAP executable not found')
        environment=os.environ.copy();binary=Path(executable).parent
        environment['PATH']=str(binary)+os.pathsep+environment.get('PATH','')
        environment['QT_PLUGIN_PATH']=str(binary.parent/'plugins')
        previous_size=result.get('dense_max_image_size')
        for old_command in result.get('mesh_commands',[]):
            if len(old_command)>1 and old_command[1]=='image_undistorter' and '--max_image_size' in old_command:
                previous_size=int(old_command[old_command.index('--max_image_size')+1])
        previous=Path(result.get('dense_path',workspace/'missing')).resolve()
        reuse=(previous.is_relative_to(output) and previous_size==max_image_size and
               (previous/'fused.ply').is_file() and (previous/'fused.ply').stat().st_size>512)
        dense=previous if reuse else output/('dense_'+uuid.uuid4().hex[:8])
        dense.mkdir(exist_ok=True)
        images=output/'inputs/images';mesh=dense/(f'meshed-{mesher}_{uuid.uuid4().hex[:8]}.ply' if reuse else f'meshed-{mesher}.ply')
        plan=[
            [executable,'image_undistorter','--image_path',str(images),'--input_path',str(sparse),'--output_path',str(dense),'--output_type','COLMAP','--max_image_size',str(max_image_size)],
            [executable,'patch_match_stereo','--workspace_path',str(dense),'--workspace_format','COLMAP','--PatchMatchStereo.geom_consistency','true','--PatchMatchStereo.max_image_size',str(max_image_size)],
            [executable,'stereo_fusion','--workspace_path',str(dense),'--workspace_format','COLMAP','--input_type','geometric','--output_path',str(dense/'fused.ply')],
            [executable,mesher+'_mesher','--input_path',str(dense/'fused.ply') if mesher=='poisson' else str(dense),'--output_path',str(mesh)]]
        if mesher=='poisson':plan[-1]+=['--PoissonMeshing.depth','10','--PoissonMeshing.trim','5']
        result.pop('mesh_view',None);result['mesh_status']='running';result['mesh_errors']=[]
        result['dense_path']=str(dense);result['dense_max_image_size']=max_image_size;result['dense_reused']=reuse;result['mesh_commands']=[]
        write_json(workspace/'reports/colmap.json',result)
        try:
            for i,command in enumerate(plan):
                if reuse and i<3:continue
                progress(dict(stage='Phase 2: '+command[1],done=None,total=None,phase=2,phase_done=i,phase_total=4))
                log_path=workspace/'logs'/f'{dense.name}_{i:02d}_{command[1]}.log'
                LOG.info('COLMAP %s; log: %s',command[1],log_path)
                result['mesh_commands'].append(command)
                with log_path.open('w',encoding='utf-8') as log:
                    subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout,env=environment)
                if i==0 and result.get('camera_models'):
                    from photogrammetry.dense_masks import prepare as prepare_masks
                    masks=prepare_masks(workspace,sparse,dense,result['camera_models'],progress)
                    plan[2]+=['--StereoFusion.mask_path',str(masks)]
                    result['dense_object_masks']=True
            progress('Exporting mesh for browser')
            result['mesh_view']=export_mesh(workspace,mesh)
            result['mesh_status']='complete'
            result['next_action']='Mesh ready. Inspect the surface and download the full PLY; missing views can still leave incomplete geometry.'
            progress(dict(stage='Mesh complete',done=4,total=4,phase=2,phase_done=4,phase_total=4))
        except (OSError,ValueError,struct.error,subprocess.SubprocessError) as exc:
            result['mesh_status']='failed';result['mesh_errors']=[str(exc)]
            result['next_action']='Mesh phase failed; inspect its logs. The phase 1 sparse result remains available.'
        write_json(workspace/'reports/colmap.json',result)
        return result
    finally:lock.unlink(missing_ok=True)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--mesher',choices=('poisson','delaunay'),default='poisson')
    parser.add_argument('--max-image-size',type=int,default=1600)
    args=parser.parse_args();logging.basicConfig(level=logging.INFO)
    result=run(args.workspace,mesher=args.mesher,max_image_size=args.max_image_size)
    from photogrammetry.report import update_reconstruction
    update_reconstruction(args.workspace,result)
    print(json.dumps(dict(mesh_status=result['mesh_status'],mesh_view=result.get('mesh_view'),errors=result['mesh_errors']),indent=2))
    return 0 if result['mesh_status']=='complete' else 1


if __name__=='__main__':raise SystemExit(main())
