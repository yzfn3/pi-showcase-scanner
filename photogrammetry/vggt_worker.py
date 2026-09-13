"""GPU-only VGGT inference and CPU Poisson surface reconstruction. No COLMAP dependency."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from PIL import Image
from windows_client.scan import write_json


def progress(stage, **fields):
    print('PROGRESS '+json.dumps(dict(stage=stage, **fields)), flush=True)


def choose_frames(frames, limit):
    if len(frames) <= limit: return sorted(frames, key=lambda f:(f['step'], f['camera_id']))
    cameras = sorted({f['camera_id'] for f in frames}); selected = []
    for i, camera in enumerate(cameras):
        group = sorted((f for f in frames if f['camera_id']==camera), key=lambda f:f['step'])
        n = min(len(group), limit//len(cameras)+(i < limit%len(cameras)))
        selected += [group[j] for j in np.linspace(0, len(group)-1, n, dtype=int)]
    return sorted(selected, key=lambda f:(f['step'], f['camera_id']))


def inputs(workspace, frames, size):
    images, masks = [], []
    for frame in frames:
        name = frame['name']
        with Image.open(workspace/'images'/name) as source: im = source.convert('RGB')
        with Image.open(workspace/'masks'/(name+'.png')) as source: mask = source.convert('L')
        if im.size != mask.size: raise ValueError('Image and mask dimensions differ: '+name)
        scale = size / max(im.size); dims = tuple(max(1, round(v*scale)) for v in im.size)
        offset = ((size-dims[0])//2, (size-dims[1])//2)
        im = im.resize(dims, Image.Resampling.LANCZOS)
        mask = mask.resize(dims, Image.Resampling.NEAREST)
        canvas = Image.new('RGB', (size,size), (240,240,240)); canvas.paste(im, offset, mask)
        valid = Image.new('L', (size,size)); valid.paste(mask, offset)
        canvas.save(workspace/'model_inputs'/(name+'.png'))
        images.append(np.asarray(canvas)); masks.append(np.asarray(valid)>127)
    return np.stack(images), np.stack(masks)


def main(workspace):
    started = time.monotonic(); workspace = Path(workspace).resolve()
    request = json.loads((workspace/'request.json').read_text())
    import torch
    import open3d as o3d
    from vggt.models.vggt import VGGT
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
    from vggt.utils.geometry import unproject_depth_map_to_point_map
    if not torch.cuda.is_available(): raise RuntimeError('VGGT requires CUDA for this runner; install the CUDA PyTorch build')
    torch.set_num_threads(8)
    frames = choose_frames(request['frames'], request['max_frames'])
    engine=request.get('engine','vggt')
    warnings=[]
    size=request['image_size']
    if engine=='worldmirror2':
        rgb,masks=inputs(workspace,frames,size)
        from photogrammetry.worldmirror import infer
        depth,confidence,extrinsic,intrinsic=infer(rgb,progress)
    else:
        progress('Loading VGGT-1B weights (first run downloads model)', phase=1)
        model = VGGT.from_pretrained('facebook/VGGT-1B', revision='860abec7937da0a4c03c41d3c269c366e82abdf9').eval().to('cuda')
        # Depth + camera prediction is the official recommended point reconstruction path.
        model.point_head = None; model.track_head = None
        dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0]>=8 else torch.float16
        warnings = []
        size = request['image_size']
        rgb, masks = inputs(workspace, frames, size)
        images = torch.from_numpy(rgb.copy()).permute(0,3,1,2).float().div_(255).unsqueeze(0).cuda()
        handles = []
        for i, block in enumerate(model.aggregator.global_blocks):
            handles.append(block.register_forward_hook(lambda module, args, output, i=i:
                progress('VGGT cross-view geometry', done=i+1, total=len(model.aggregator.global_blocks), phase=1)))
        try:
            with torch.inference_mode(), torch.autocast('cuda', dtype=dtype):
                aggregated, start = model.aggregator(images)
                progress('Predicting camera poses and depth', phase=1)
                # Heads expect FP32 computation, as in the upstream model forward.
                with torch.autocast('cuda', enabled=False):
                    pose = model.camera_head(aggregated)[-1]
                    depth, confidence = model.depth_head(aggregated, images, start, frames_chunk_size=2)
                extrinsic, intrinsic = pose_encoding_to_extri_intri(pose, images.shape[-2:])
            depth = depth[0].float().cpu().numpy()
            confidence = confidence[0].float().cpu().numpy()
            extrinsic = extrinsic[0].float().cpu().numpy(); intrinsic = intrinsic[0].float().cpu().numpy()
            del aggregated, images, pose
        except torch.cuda.OutOfMemoryError:
            raise RuntimeError('GPU memory exhausted. Retry with VGGT resolution 336 or fewer views; no COLMAP fallback.')
        finally:
            for handle in handles: handle.remove()
        del model; torch.cuda.empty_cache()
    progress('Filtering predicted object points', phase=1)
    world = unproject_depth_map_to_point_map(depth, extrinsic, intrinsic)
    valid = masks & np.isfinite(world).all(-1) & np.isfinite(confidence) & (depth[...,0]>0)
    for i in range(len(frames)):
        if valid[i].any(): valid[i] &= confidence[i] >= np.percentile(confidence[i][valid[i]], 20)
    points = world[valid]; colors = rgb[valid]/255.
    if len(points)<100: raise RuntimeError('VGGT produced too few valid object points')
    low, high = np.percentile(points, [1,99], axis=0)
    extent = float(np.max(high-low))
    if not np.isfinite(extent) or extent <= 1e-6: raise RuntimeError('Degenerate VGGT geometry')
    np.savez_compressed(workspace/'predictions.npz', depth=depth, confidence=confidence,
                        extrinsics=extrinsic, intrinsics=intrinsic, masks=masks)
    pcd = o3d.geometry.PointCloud(); pcd.points=o3d.utility.Vector3dVector(points); pcd.colors=o3d.utility.Vector3dVector(colors)
    pcd = pcd.voxel_down_sample(extent/500)
    o3d.io.write_point_cloud(str(workspace/'points.ply'),pcd)
    xyz=np.asarray(pcd.points); color=np.asarray(pcd.colors); sample=np.linspace(0,len(xyz)-1,min(len(xyz),150000),dtype=int)
    write_json(workspace/'reports/points_view.json',dict(kind='points',coordinate_system='colmap',
               positions=xyz[sample].reshape(-1).tolist(),colors=color[sample].reshape(-1).tolist()))
    result=dict(mask_method=request.get("mask_method","background"),engine=engine,status=engine+'_complete',scan_id=request['scan_id'],
                input_images=request['input_images'],processed_images=len(frames),registered_images=len(frames),
                points3D=len(xyz),camera_grouping='VGGT predicted cameras; no COLMAP optimization',
                camera_models={},view={'file':'reports/points_view.json'},warnings=warnings,errors=[],
                image_size=size,gamma=request['gamma'],selected_images=[f['name'] for f in frames],
                model='facebook/VGGT-1B',model_revision='860abec7937da0a4c03c41d3c269c366e82abdf9',mesh_status='not run',mesh_errors=[])
    if engine=='worldmirror2':
        from photogrammetry.worldmirror import MODEL_REVISION
        result.update(model='tencent/HY-World-2.0 / HY-WorldMirror-2.0',model_revision=MODEL_REVISION,camera_grouping='WorldMirror predicted cameras; no COLMAP optimization')
    write_json(workspace/'reports/vggt.json', result)
    if request['mesh']:
        progress('Filtering surface points', phase=2,phase_done=1,phase_total=4)
        surface_points = pcd.voxel_down_sample(float(np.ptp(np.percentile(xyz,[1,99],axis=0),axis=0).max())/180)
        surface_points, _ = surface_points.remove_statistical_outlier(nb_neighbors=20,std_ratio=1.5)
        progress('Estimating surface normals',phase=2,phase_done=2,phase_total=4)
        surface_extent=float(np.ptp(np.percentile(xyz,[1,99],axis=0),axis=0).max())
        surface_points.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=surface_extent/30,max_nn=40))
        surface_points.orient_normals_consistent_tangent_plane(15)
        progress('Building Poisson surface from predicted points',phase=2,phase_done=2,phase_total=4)
        surface, density = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(surface_points,depth=8,n_threads=8)
        density=np.asarray(density)
        surface.remove_vertices_by_mask(density<np.quantile(density,.05))
        surface.remove_degenerate_triangles();surface.remove_unreferenced_vertices();surface.compute_vertex_normals()
        if not len(surface.triangles): raise RuntimeError('VGGT points could not form a mesh; point cloud is available')
        result['mesh_method']='Open3D Poisson, depth 8, lowest 5% density trimmed'
        result['warnings'].append('Poisson interpolation can fill gaps and invent connecting surfaces; inspect against input photos.')
        o3d.io.write_triangle_mesh(str(workspace/'mesh.ply'),surface)
        progress('Exporting VGGT mesh viewer',phase=2,phase_done=3,phase_total=4)
        from photogrammetry.mesh_view import export_mesh
        result['mesh_view']=export_mesh(workspace,workspace/'mesh.ply')
        result['mesh_status']='complete'
        progress('Fusing depth maps into a camera-aware surface',phase=2,phase_done=2,phase_total=4)
        from photogrammetry.depth_surface import run as fuse_depth
        fusion=fuse_depth(workspace,resolution=request.get('surface_resolution',192),smoothing=request.get('surface_smoothing',6),truncation=8,progress=progress)
        fused_view=dict(fusion['view'])
        fused_view.update(file='surface_fusion/'+fused_view['file'],mesh_file='surface_fusion/'+fused_view['mesh_file'])
        result.update(fusion=fusion,fused_mesh_view=fused_view)
    result['elapsed_seconds']=round(time.monotonic()-started,2)
    result['warnings'] += ['Learned reconstruction predicts approximate, unscaled geometry; processed views are not an alignment-quality guarantee.']
    write_json(workspace/'reports/vggt.json',result)
    progress(engine+' finished',done=1,total=1,phase=2 if request['mesh'] else 1,phase_done=4 if request['mesh'] else 0,phase_total=4)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True)
    main(parser.parse_args().workspace)
