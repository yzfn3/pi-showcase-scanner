"""Camera-aware surface fusion from saved learned depth. No inference or image edits."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from PIL import Image
from windows_client.scan import write_json


def load_inputs(workspace):
    w=Path(workspace)
    with np.load(w/'predictions.npz') as p:
        data={k:p[k].copy() for k in p.files}
    report=json.loads((w/'reports/vggt.json').read_text(encoding='utf8'))
    data['rgb']=np.stack([np.asarray(Image.open(w/'model_inputs'/(n+'.png')).convert('RGB')) for n in report['selected_images']])
    return data


def project_colors(mesh,data,voxel):
    """Choose a front-facing, depth-supported photo, avoiding all-view color blur."""
    import cv2
    vertices=np.asarray(mesh.vertices)
    normal_mesh=mesh.filter_smooth_taubin(number_of_iterations=15)
    normal_mesh.compute_vertex_normals();normals=np.asarray(normal_mesh.vertex_normals)
    best=np.zeros(len(vertices));colors=np.asarray(mesh.vertex_colors).copy()
    for e,k,mask,depth,color in zip(data['extrinsics'],data['intrinsics'],data['masks'],data['depth'][...,0],data['rgb']):
        camera=vertices@e[:,:3].T+e[:,3];uv=camera@k.T
        uv=uv[:,:2]/np.maximum(uv[:,2,None],1e-8)
        inside=(camera[:,2]>0)&(uv[:,0]>=1)&(uv[:,0]<mask.shape[1]-1)&(uv[:,1]>=1)&(uv[:,1]<mask.shape[0]-1)
        ids=np.where(inside)[0];xy=uv[ids].astype(int)
        gap=np.abs(depth[xy[:,1],xy[:,0]]-camera[ids,2])
        center=-e[:,:3].T@e[:,3];direction=center-vertices[ids]
        direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-8)
        cosine=np.maximum(0,(normals[ids]*direction).sum(1))
        score=cosine**4*np.exp(-.5*(gap/(voxel*6))**2)*mask[xy[:,1],xy[:,0]]
        chosen=score>best[ids];ids=ids[chosen]
        if not len(ids):continue
        for chunk in np.array_split(ids,max(1,(len(ids)+15999)//16000)):
            sample=cv2.remap(color,uv[chunk,0].astype(np.float32).reshape(-1,1),uv[chunk,1].astype(np.float32).reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3)
            colors[chunk]=sample/255.
        best[ids]=score[chosen]
    import open3d as o3d
    mesh.vertex_colors=o3d.utility.Vector3dVector(colors)
    return float((best>0).mean())


def fuse(data, resolution=256, truncation=6, smoothing=3, progress=None):
    import cv2
    import open3d as o3d
    progress=progress or (lambda **kw:None)
    depth=data['depth'][...,0]; masks=data['masks'];confidence=data['confidence']
    es=data['extrinsics'];ks=data['intrinsics'];rgb=data['rgb']
    if type(resolution) is not int or not 96<=resolution<=512:raise ValueError('Surface resolution must be 96..512')
    if type(truncation) is not int or not 2<=truncation<=12:raise ValueError('Truncation must be 2..12 voxels')
    if type(smoothing) is not int or not 0<=smoothing<=20:raise ValueError('Smoothing must be 0..20')
    # Determine scale from the same object pixels, rather than assuming metric predictions.
    cloud=[]
    for d,m,e,k in zip(depth,masks,es,ks):
        v,u=np.where(m & np.isfinite(d) & (d>0)); u=u[::16];v=v[::16]
        camera=np.column_stack((u,v,np.ones(len(u))))@np.linalg.inv(k).T*d[v,u,None]
        cloud.append((camera-e[:,3])@e[:,:3])
    points=np.concatenate(cloud)
    if len(points)<100:raise ValueError('Too few valid depth pixels')
    extent=float(np.ptp(np.percentile(points,[1,99],axis=0),axis=0).max())
    voxel=extent/resolution
    volume=o3d.pipelines.integration.ScalableTSDFVolume(voxel_length=voxel,sdf_trunc=voxel*truncation,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,depth_sampling_stride=2)
    used=[]
    for i,(d,m,c,e,k,color) in enumerate(zip(depth,masks,confidence,es,ks,rgb)):
        valid=m & np.isfinite(d) & (d>0) & np.isfinite(c)
        if valid.any():valid &= c>=np.percentile(c[valid],20)
        # Edge-preserving filtering within each depth map, before fusion across cameras.
        clean=cv2.bilateralFilter(np.nan_to_num(d,nan=0,posinf=0,neginf=0).astype(np.float32),5,voxel*2,2)
        clean[~valid | ~np.isfinite(clean)]=0
        used.append(int(valid.sum()))
        rgbd=o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(np.ascontiguousarray(color)),
            o3d.geometry.Image(np.ascontiguousarray(clean)),depth_scale=1.,depth_trunc=float(depth[np.isfinite(depth)].max()*1.1),convert_rgb_to_intensity=False)
        intrinsic=o3d.camera.PinholeCameraIntrinsic(d.shape[1],d.shape[0],k[0,0],k[1,1],k[0,2],k[1,2])
        pose=np.eye(4);pose[:3]=e
        volume.integrate(rgbd,intrinsic,pose)
        progress(stage='Fusing camera depth maps',phase=2,done=i+1,total=len(depth),phase_done=2,phase_total=4)
    mesh=volume.extract_triangle_mesh()
    mesh.remove_duplicated_vertices();mesh.remove_degenerate_triangles();mesh.remove_unreferenced_vertices()
    if not len(mesh.triangles):raise ValueError('Depth fusion produced an empty mesh')
    labels,counts,_=mesh.cluster_connected_triangles();labels=np.asarray(labels);counts=np.asarray(counts)
    remove=(counts[labels]<max(30,counts.max()*.005))&(labels!=counts.argmax())
    removed=int(remove.sum());mesh.remove_triangles_by_mask(remove);mesh.remove_unreferenced_vertices()
    if smoothing:mesh=mesh.filter_smooth_taubin(number_of_iterations=smoothing)
    mesh.compute_vertex_normals()
    color_coverage=project_colors(mesh,data,voxel)
    return mesh,dict(method='Camera-aware TSDF fusion',color_method='Best supported photo projection',color_coverage=color_coverage,resolution=resolution,voxel_size=voxel,
        truncation_voxels=truncation,smoothing_iterations=smoothing,frames=len(depth),pixels_per_frame=used,
        removed_fragment_faces=removed,faces=len(mesh.triangles),vertices=len(mesh.vertices))


def run(workspace, output=None, resolution=192, truncation=8, smoothing=6, progress=None, publish=False):
    import open3d as o3d
    from photogrammetry.mesh_view import export_mesh
    started=time.monotonic();w=Path(workspace);out=Path(output) if output else w/'surface_fusion'
    out.mkdir(parents=True,exist_ok=True)
    mesh,report=fuse(load_inputs(w),resolution,truncation,smoothing,progress)
    if not o3d.io.write_triangle_mesh(str(out/'mesh.ply'),mesh):raise RuntimeError('Cannot save fused mesh')
    report['view']=export_mesh(out,out/'mesh.ply',limit=200000)
    report['elapsed_seconds']=round(time.monotonic()-started,2)
    write_json(out/'reports/fusion.json',report)
    if publish:
        view=dict(report['view']);view['file']=(out.relative_to(w)/view['file']).as_posix();view['mesh_file']=(out.relative_to(w)/view['mesh_file']).as_posix()
        for name in ('vggt.json','run_report.json'):
            path=w/'reports'/name
            if path.exists():
                result=json.loads(path.read_text(encoding='utf8'))
                result.update(fused_mesh_view=view,fusion=report,mesh_status='complete')
                if not result.get('mesh_view'):result['mesh_view']=view
                write_json(path,result)
        report['published_view']=view
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--output');p.add_argument('--resolution',type=int,default=256)
    p.add_argument('--truncation',type=int,default=6);p.add_argument('--smoothing',type=int,default=3)
    args=vars(p.parse_args());print(json.dumps(run(**args),indent=2))
