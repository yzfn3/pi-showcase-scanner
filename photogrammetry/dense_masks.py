"""Transform object masks into COLMAP's undistorted pixel coordinates."""
import struct
from pathlib import Path
import numpy as np
from PIL import Image

COUNTS={0:3,1:4,2:4,3:5,4:8}


def cameras(path):
    result={}
    with Path(path).open('rb') as stream:
        count=struct.unpack('<Q',stream.read(8))[0]
        if count>10000:raise ValueError('Invalid camera count')
        for _ in range(count):
            cid,model,width,height=struct.unpack('<iiQQ',stream.read(24))
            if model not in COUNTS:raise ValueError('Mask undistortion does not support camera model '+str(model))
            params=struct.unpack('<'+'d'*COUNTS[model],stream.read(8*COUNTS[model]))
            result[cid]=(model,width,height,params)
    return result


def intrinsics(camera):
    model,_,_,p=camera
    return (p[0],p[0],p[1],p[2]) if model in (0,2,3) else p[:4]


def mapping(original, undistorted):
    model,w,h,p=original;_,uw,uh,_=undistorted
    if uw*uh>20000000:raise ValueError('Mask resolution too large')
    fx,fy,cx,cy=intrinsics(undistorted)
    x,y=np.meshgrid((np.arange(uw,dtype=np.float32)+.5-cx)/fx,(np.arange(uh,dtype=np.float32)+.5-cy)/fy)
    r=x*x+y*y
    if model==2:x,y=x*(1+p[3]*r),y*(1+p[3]*r)
    elif model==3:x,y=x*(1+p[3]*r+p[4]*r*r),y*(1+p[3]*r+p[4]*r*r)
    elif model==4:
        k1,k2,p1,p2=p[4:];radial=1+k1*r+k2*r*r
        x,y=x*radial+2*p1*x*y+p2*(r+2*x*x),y*radial+p1*(r+2*y*y)+2*p2*x*y
    fx,fy,cx,cy=intrinsics(original)
    u=np.floor(fx*x+cx).astype(np.int32);v=np.floor(fy*y+cy).astype(np.int32)
    valid=(u>=0)&(u<w)&(v>=0)&(v<h)
    return u,v,valid


def prepare(workspace, sparse, dense, camera_models, progress=None):
    original=cameras(Path(sparse)/'cameras.bin');undistorted=cameras(Path(dense)/'sparse/cameras.bin')
    files=sorted((Path(dense)/'images').glob('*/*.jpg'));cache={}
    target=Path(dense)/'masks'
    for index,file in enumerate(files,1):
        name=file.name;cam=file.parent.name;cid=camera_models[cam]['camera_id']
        source=Path(workspace)/'masks'/(name+'.png')
        if not source.is_file():raise ValueError('Dense object mask missing: '+name)
        if cid not in cache:cache[cid]=mapping(original[cid],undistorted[cid])
        u,v,valid=cache[cid]
        with Image.open(source) as mask:
            if mask.size!=original[cid][1:3]:raise ValueError('Mask and calibrated camera dimensions disagree')
            a=np.asarray(mask.convert('L'));out=np.zeros(u.shape,dtype=np.uint8);out[valid]=a[v[valid],u[valid]]
        folder=target/cam;folder.mkdir(parents=True,exist_ok=True)
        Image.fromarray(out).save(folder/(name+'.png'))
        if progress:progress(dict(stage='Undistorting object masks for dense fusion',done=index,total=len(files)))
    if not files:raise ValueError('No undistorted images for mask projection')
    return target
