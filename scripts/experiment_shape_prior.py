"""Rejected experiment: alignment/deformation of a generated shape to scan colors.

Kept for reproducibility, not used by the production runner. See surface_research.md.
"""
from pathlib import Path
import itertools
import json
import numpy as np
from windows_client.scan import write_json


def run(workspace, generated_folder, fit_iterations=0):
    import open3d as o3d
    import trimesh
    from photogrammetry.depth_surface import load_inputs,project_colors
    from photogrammetry.mesh_view import export_mesh
    w=Path(workspace);folder=Path(generated_folder)
    asset=trimesh.load(folder/'model.glb',force='mesh',process=False)
    mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(asset.vertices),o3d.utility.Vector3iVector(asset.faces))
    target_mesh=o3d.io.read_triangle_mesh(str(w/'surface_fusion/mesh.ply'))
    o3d.utility.random.seed(42)
    source=mesh.sample_points_uniformly(25000);target=target_mesh.sample_points_uniformly(35000)
    a=np.asarray(source.points);b=np.asarray(target.points);ac=a.mean(0);bc=b.mean(0)
    _,ap=np.linalg.eigh(np.cov(a.T));_,bp=np.linalg.eigh(np.cov(b.T))
    scale=np.sqrt(np.sum(np.var(b,axis=0))/np.sum(np.var(a,axis=0)))
    extent=np.ptp(b,axis=0).max();best=None
    for signs in itertools.product([-1,1],repeat=3):
        r=bp@np.diag(signs)@ap.T
        if np.linalg.det(r)<0:continue
        transform=np.eye(4);transform[:3,:3]=scale*r;transform[:3,3]=bc-scale*r@ac
        for threshold in (.12,.05,.025):
            result=o3d.pipelines.registration.registration_icp(source,target,extent*threshold,transform,
                o3d.pipelines.registration.TransformationEstimationPointToPoint(with_scaling=False),
                o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=50))
            transform=result.transformation
        score=result.inlier_rmse+extent*.05*(1-result.fitness)
        if best is None or score<best[0]:best=(score,result)
    result=best[1];mesh.transform(result.transformation);mesh.compute_vertex_normals()
    if fit_iterations:
        from scipy.sparse import coo_matrix,diags
        faces=np.asarray(mesh.triangles)
        edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]);edges=np.concatenate([edges,edges[:,::-1]])
        adjacency=coo_matrix((np.ones(len(edges)),(edges[:,0],edges[:,1])),shape=(len(mesh.vertices),len(mesh.vertices))).tocsr()
        adjacency=diags(1/np.maximum(np.asarray(adjacency.sum(1)).ravel(),1))@adjacency
        scene=o3d.t.geometry.RaycastingScene();scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(target_mesh))
        vertices=np.asarray(mesh.vertices).copy()
        for _ in range(fit_iterations):
            closest=scene.compute_closest_points(o3d.core.Tensor(vertices.astype(np.float32)))['points'].numpy()
            delta=closest-vertices
            for _ in range(8):delta=.5*delta+.5*(adjacency@delta)
            lengths=np.linalg.norm(delta,axis=1,keepdims=True)
            delta*=np.minimum(1,extent*.006/np.maximum(lengths,1e-12))
            vertices+=.6*delta
        mesh.vertices=o3d.utility.Vector3dVector(vertices);mesh.compute_vertex_normals()
    mesh.vertex_colors=o3d.utility.Vector3dVector(np.full((len(mesh.vertices),3),.65))
    coverage=project_colors(mesh,load_inputs(w),extent/128)
    out=folder/('scan_colors_fitted' if fit_iterations else 'scan_colors');out.mkdir(exist_ok=True)
    o3d.io.write_triangle_mesh(str(out/'mesh.ply'),mesh)
    view=export_mesh(out,out/'mesh.ply',limit=300000)
    report=dict(method='TRELLIS generated shape aligned to scan; captured photo colors',
        generated_shape=True,fit_iterations=fit_iterations,icp_fitness=result.fitness,icp_rmse=result.inlier_rmse,
        transform=result.transformation.tolist(),photo_color_coverage=coverage,view=view,
        warning='Experimental generated shape prior. Registration and photo colors do not prove that the geometry matches the physical object.')
    write_json(out/'reports/shape_prior.json',report)
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--generated-folder',required=True);p.add_argument('--fit-iterations',type=int,default=0)
    print(json.dumps(run(**vars(p.parse_args())),indent=2))
