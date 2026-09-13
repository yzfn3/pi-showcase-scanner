"""SAM 2.1 foreground refinement in a separate GPU process."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from windows_client.scan import write_json

MODEL='facebook/sam2.1-hiera-small'
REVISION='ee5bba1d82bb8749febdf90f45e84b687142ba03'

def prompt_from_mask(mask, region):
    from scipy.ndimage import label, distance_transform_edt
    h,w=mask.shape
    labels,count=label(mask)
    if count:
        sizes=np.bincount(labels.ravel());sizes[0]=0
        component=labels==sizes.argmax()
        if component.sum()>=max(30,mask.size*.001):
            y,x=np.where(component);pad=max(4,round(max(x.ptp(),y.ptp())*.04))
            box=np.array([max(0,x.min()-pad),max(0,y.min()-pad),min(w-1,x.max()+pad),min(h-1,y.max()+pad)])
            cy,cx=np.unravel_index(np.argmax(distance_transform_edt(component)),component.shape)
            return box,np.array([[cx,cy]]),'background prompt'
    x,y,rw,rh=region
    return np.array([x*w,y*h,(x+rw)*w,(y+rh)*h]),None,'user object region'

def validate_region(region):
    if len(region)!=4 or not all(isinstance(v,(int,float)) and np.isfinite(v) for v in region):raise ValueError('Object region needs x,y,width,height')
    x,y,w,h=region
    if min(x,y)<0 or min(w,h)<=0 or x+w>1 or y+h>1:raise ValueError('Object region must fit normalized image coordinates')
    return region

def run(workspace, region):
    import torch
    from huggingface_hub import hf_hub_download
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    w=Path(workspace);validate_region(region)
    def progress(stage,**kw):print('PROGRESS '+json.dumps(dict(stage=stage,phase=0,**kw)),flush=True)
    progress('Loading SAM 2.1 foreground model')
    checkpoint=hf_hub_download(MODEL,'sam2.1_hiera_small.pt',revision=REVISION)
    predictor=SAM2ImagePredictor(build_sam2('configs/sam2.1/sam2.1_hiera_s.yaml',checkpoint,device='cuda',apply_postprocessing=False))
    images=sorted((w/'images').glob('*.jpg'));rows=[]
    (w/'masks_before_sam2').mkdir(exist_ok=True)
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
        for i,path in enumerate(images):
            rgb=np.asarray(Image.open(path).convert('RGB'));target=w/'masks'/(path.name+'.png')
            seed=np.asarray(Image.open(target))>127 if target.exists() else np.zeros(rgb.shape[:2],bool)
            Image.fromarray(seed.astype('uint8')*255).save(w/'masks_before_sam2'/target.name)
            box,points,prompt=prompt_from_mask(seed,region)
            predictor.set_image(rgb)
            masks,scores,_=predictor.predict(box=box,point_coords=points,point_labels=np.ones(len(points),dtype=int) if points is not None else None,multimask_output=True)
            masks=np.asarray(masks)>.5
            # Prefer a whole-object mask consistent with the prompt, not a tiny confident part.
            overlaps=np.array([(m&seed).sum()/max(1,(m|seed).sum()) for m in masks]) if seed.any() else np.zeros(len(scores))
            choice=int(np.argmax(scores+.5*overlaps));selected=masks[choice]
            fraction=float(selected.mean())
            if fraction<.001 or fraction>.9:raise RuntimeError(f'SAM 2 mask for {path.name} has implausible coverage {fraction:.1%}; adjust object region')
            Image.fromarray(selected.astype('uint8')*255).save(target)
            rows.append(dict(file=path.name,score=float(scores[choice]),coverage=fraction,prompt=prompt,box=box.tolist()))
            progress('SAM 2 object masks',done=i+1,total=len(images))
    write_json(w/'reports/sam2.json',dict(model=MODEL,revision=REVISION,images=len(rows),frames=rows,region=region))
    write_json(w/'reports/masks.json',dict(applied=True,complete=True,method='sam2',coverage=[dict(filename=r['file'],percent=round(r['coverage']*100,3)) for r in rows],warnings=[]))
    progress('SAM 2 masks complete',done=len(images),total=len(images))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--region',default='0.1,0.1,0.8,0.8')
    a=p.parse_args();run(a.workspace,[float(v) for v in a.region.split(',')])
