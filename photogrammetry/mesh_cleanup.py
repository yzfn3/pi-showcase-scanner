"""Conservative, mask-guided mesh cleanup; original geometry is always retained."""
import json
from pathlib import Path
import numpy as np
from windows_client.scan import write_json

def silhouette_support(vertices, extrinsics, intrinsics, masks):
    hits=np.zeros(len(vertices),np.int32);visible=np.zeros(len(vertices),np.int32)
    h,w=masks.shape[1:]
    for e,k,mask in zip(extrinsics,intrinsics,masks):
        camera=vertices@e[:3,:3].T+e[:3,3]
        positive=camera[:,2]>1e-8
        projection=camera@k.T
        uv=projection[:,:2]/np.maximum(projection[:,2,None],1e-8)
        inside=positive & (uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)&np.isfinite(uv).all(1)
        ids=np.where(inside)[0];pixels=uv[ids].astype(int)
        visible[ids]+=1;hits[ids]+=mask[pixels[:,1],pixels[:,0]]
    return hits,visible

def run(workspace, *, smoothing=2, progress=None):
    import open3d as o3d
    from photogrammetry.mesh_view import export_mesh
    progress=progress or (lambda **kw:None)
    if type(smoothing) is not int or not 0<=smoothing<=5:raise ValueError('Cleanup smoothing must be 0..5')
    w=Path(workspace).resolve();source=w/'mesh.ply'
    if not source.is_file():raise ValueError('Build the original mesh before cleanup')
    progress(stage='Checking reconstructed mesh',phase=3,done=0,total=4)
    mesh=o3d.io.read_triangle_mesh(str(source));before=len(mesh.triangles)
    if not before:raise ValueError('Original mesh is empty')
    mesh.remove_duplicated_vertices();mesh.remove_duplicated_triangles();mesh.remove_degenerate_triangles();mesh.remove_unreferenced_vertices()
    guided=False;removed_support=0
    if (w/'predictions.npz').exists():
        with np.load(w/'predictions.npz') as predictions:
            vertices=np.asarray(mesh.vertices);hits,visible=silhouette_support(vertices,predictions['extrinsics'],predictions['intrinsics'],predictions['masks'])
        supported=(visible<3)|(hits/np.maximum(visible,1)>=.45)
        remove=supported[np.asarray(mesh.triangles)].sum(1)<2
        removed_support=int(remove.sum())
        if removed_support>before*.5:raise ValueError('Cleanup rejected: masks disagree with over half the mesh. Inspect poses/masks; original mesh retained.')
        mesh.remove_triangles_by_mask(remove);mesh.remove_unreferenced_vertices();guided=True
    progress(stage='Removing mask-inconsistent geometry',phase=3,done=1,total=4)
    labels,counts,_=mesh.cluster_connected_triangles();counts=np.asarray(counts)
    if not len(counts):raise ValueError('Cleanup would empty the mesh; original retained')
    labels=np.asarray(labels)
    remove=(counts[labels]<max(20,counts.max()*.002)) & (labels!=counts.argmax())
    removed_fragments=int(remove.sum());mesh.remove_triangles_by_mask(remove);mesh.remove_unreferenced_vertices()
    progress(stage='Removing small disconnected fragments',phase=3,done=2,total=4)
    if smoothing:mesh=mesh.filter_smooth_taubin(number_of_iterations=smoothing)
    mesh.compute_vertex_normals()
    if len(mesh.triangles)<before*.5:raise ValueError('Cleanup removed too much geometry; original retained')
    # Export through a separate report directory so the original browser mesh is untouched.
    target=w/'mesh_clean.ply';o3d.io.write_triangle_mesh(str(target),mesh)
    folder=w/'cleanup';(folder/'reports').mkdir(parents=True,exist_ok=True)
    import shutil
    shutil.copy2(target,folder/'mesh.ply')
    view=export_mesh(folder,folder/'mesh.ply')
    view.update(file='cleanup/reports/mesh_view.json',mesh_file='mesh_clean.ply')
    (folder/'mesh.ply').unlink()
    report=dict(method='SAM 2 guided filtering + geometric repair' if guided and (w/'reports/sam2.json').exists() else 'Silhouette-guided geometric repair' if guided else 'Geometric repair',
        ai_masks=(w/'reports/sam2.json').exists(),before_faces=before,after_faces=len(mesh.triangles),
        removed_support_faces=removed_support,removed_fragment_faces=removed_fragments,smoothing_iterations=smoothing,
        view=view,warnings=['Cleanup may remove thin details; compare with the original. No missing geometry is generated.'])
    write_json(w/'reports/cleanup.json',report)
    for name in ['vggt.json','run_report.json']:
        path=w/'reports'/name
        if path.exists():
            result=json.loads(path.read_text());result.update(cleanup=report,cleaned_mesh_view=view);write_json(path,result)
            if name=='run_report.json':(w/'reports/run_report.md').write_text('# Reconstruction and cleanup\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n',encoding='utf8')
    progress(stage='Mesh cleanup complete',phase=3,done=4,total=4)
    return report
