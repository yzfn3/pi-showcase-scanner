"""Render candidate meshes through the predicted cameras for honest input comparisons."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import open3d as o3d
from photogrammetry.depth_surface import load_inputs


def render(mesh,e,k,size):
    scene=o3d.t.geometry.RaycastingScene();scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    pose=np.eye(4);pose[:3]=e
    rays=scene.create_rays_pinhole(k.astype(np.float64),pose,size,size)
    hit=scene.cast_rays(rays);valid=np.isfinite(hit['t_hit'].numpy())
    ids=hit['primitive_ids'].numpy()[valid];uv=hit['primitive_uvs'].numpy()[valid]
    weights=np.column_stack((1-uv.sum(1),uv))
    faces=np.asarray(mesh.triangles)[ids]
    colors=np.asarray(mesh.vertex_colors)
    rgb=np.ones((size,size,3))*.94
    if len(colors):rgb[valid]=(colors[faces]*weights[:,:,None]).sum(1)
    else:rgb[valid]=.65
    mesh.compute_vertex_normals()
    normals=(np.asarray(mesh.vertex_normals)[faces]*weights[:,:,None]).sum(1)
    normals=normals@e[:,:3].T;normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-8)
    light=.35+.65*np.abs(normals@np.array([-.4,-.5,-.768]))
    clay=np.ones_like(rgb)*.94;clay[valid]=.7*light[:,None]
    return Image.fromarray(np.uint8(np.clip(rgb,0,1)*255)),Image.fromarray(np.uint8(np.clip(clay,0,1)*255)),valid


def main(workspace,meshes,output):
    w=Path(workspace);d=load_inputs(w);size=d['rgb'].shape[1];indices=[2,18,34,50]
    sheet=Image.new('RGB',(size*(1+2*len(meshes)),(size+30)*len(indices)),'white');draw=ImageDraw.Draw(sheet)
    results={}
    loaded=[o3d.io.read_triangle_mesh(p) for p in meshes]
    for row,i in enumerate(indices):
        y=row*(size+30);sheet.paste(Image.fromarray(d['rgb'][i]),(0,y+30));draw.text((10,y+5),f'Input camera {i}',fill='black')
        for j,(path,mesh) in enumerate(zip(meshes,loaded)):
            rgb,clay,valid=render(mesh,d['extrinsics'][i],d['intrinsics'][i],size)
            mask=d['masks'][i];iou=float((mask&valid).sum()/max(1,(mask|valid).sum()))
            name=Path(path).parent.name;results.setdefault(name,[]).append(iou)
            x=size*(1+2*j);sheet.paste(rgb,(x,y+30));sheet.paste(clay,(x+size,y+30));draw.text((x+10,y+5),f'{name} | silhouette IoU {iou:.3f} | photo colors / clay',fill='black')
    sheet.save(output)
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--meshes',nargs='+',required=True);p.add_argument('--output',required=True)
    main(**vars(p.parse_args()))
