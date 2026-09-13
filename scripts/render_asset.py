"""Render a generated GLB from several fixed angles, including its actual input."""
import argparse
from pathlib import Path
import numpy as np
import open3d as o3d
import trimesh
from PIL import Image,ImageDraw


def main(folder,output):
    folder=Path(folder);m=trimesh.load(folder/'model.glb',force='mesh',process=False)
    v=np.asarray(m.vertices);v=(v-(v.max(0)+v.min(0))/2)/(np.ptp(v,axis=0).max()/2)
    size=640;sheet=Image.new('RGB',(size*4,size+40),'white');draw=ImageDraw.Draw(sheet)
    source=Image.open(folder/'input.png').convert('RGBA');source.thumbnail((size,size));background=Image.new('RGBA',(size,size),'#eeeeee');background.alpha_composite(source,((size-source.width)//2,(size-source.height)//2));sheet.paste(background.convert('RGB'),(0,40));draw.text((10,10),'Actual masked input',fill='black')
    texture=np.asarray(m.visual.material.baseColorTexture.convert('RGB'));uv=np.asarray(m.visual.uv)
    for col,yaw in enumerate([-.5,np.pi-.5,np.pi/2],1):
        pitch=.25;c,s=np.cos(yaw),np.sin(yaw);a,b=np.cos(pitch),np.sin(pitch)
        r=np.array([[1,0,0],[0,a,-b],[0,b,a]])@np.array([[c,0,s],[0,1,0],[-s,0,c]])
        mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(v@r.T),o3d.utility.Vector3iVector(m.faces))
        scene=o3d.t.geometry.RaycastingScene();scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
        xx,yy=np.meshgrid(np.linspace(-1.3,1.3,size),np.linspace(1.3,-1.3,size));rays=np.zeros((size,size,6),np.float32)
        rays[...,0]=xx;rays[...,1]=yy;rays[...,2]=3;rays[...,5]=-1
        hits=scene.cast_rays(o3d.core.Tensor(rays));valid=np.isfinite(hits['t_hit'].numpy());ids=hits['primitive_ids'].numpy()[valid];weights=hits['primitive_uvs'].numpy()[valid]
        weights=np.column_stack((1-weights.sum(1),weights));coord=(uv[m.faces[ids]]*weights[:,:,None]).sum(1)
        px=np.clip(np.rint(coord[:,0]*(texture.shape[1]-1)).astype(int),0,texture.shape[1]-1);py=np.clip(np.rint((1-coord[:,1])*(texture.shape[0]-1)).astype(int),0,texture.shape[0]-1)
        image=np.full((size,size,3),238,np.uint8);image[valid]=texture[py,px]
        sheet.paste(Image.fromarray(image),(col*size,40));draw.text((col*size+10,10),f'Generated view {col}',fill='black')
    sheet.save(output)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--output',required=True);main(**vars(p.parse_args()))
