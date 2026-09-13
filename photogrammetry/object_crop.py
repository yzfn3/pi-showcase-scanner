"""Mask-driven, fixed-per-camera crops. Source captures remain immutable."""
import math
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np
from preview3d.quad_splitter import contact_sheet
from windows_client.scan import write_json


def padded_box(box, size, padding):
    # padding is the total enlargement: 15% adds 7.5% on each side.
    x0,y0,x1,y1=box; dx=(x1-x0)*padding/2;dy=(y1-y0)*padding/2
    return (max(0,math.floor(x0-dx)),max(0,math.floor(y0-dy)),
            min(size[0],math.ceil(x1+dx)),min(size[1],math.ceil(y1+dy)))


def object_mask(mask):
    """Remove disconnected background specks that would inflate the crop bounds.

    Find the dominant foreground cluster on a bounded thumbnail. Dilating only
    this search thumbnail bridges small gaps between object parts; the original
    full-resolution mask remains unchanged inside the selected region.
    """
    small=mask.copy();small.thumbnail((512,512),Image.Resampling.NEAREST)
    a=np.array(small.filter(ImageFilter.MaxFilter(3)))>0
    best=[];height,width=a.shape
    for y,x in zip(*np.where(a)):
        if not a[y,x]:continue
        pending=[(int(y),int(x))];a[y,x]=False;points=[]
        while pending:
            row,col=pending.pop();points.append((row,col))
            for dy,dx in ((-1,0),(1,0),(0,-1),(0,1)):
                yy,xx=row+dy,col+dx
                if 0<=yy<height and 0<=xx<width and a[yy,xx]:
                    a[yy,xx]=False;pending.append((yy,xx))
        if len(points)>len(best):best=points
    if not best:return mask
    ys,xs=zip(*best);sx=mask.width/width;sy=mask.height/height
    box=(max(0,math.floor(min(xs)*sx)),max(0,math.floor(min(ys)*sy)),
         min(mask.width,math.ceil((max(xs)+1)*sx)),min(mask.height,math.ceil((max(ys)+1)*sy)))
    cleaned=Image.new('L',mask.size);cleaned.paste(mask.crop(box),box[:2]);return cleaned


def validate_options(padding, rotation, enabled):
    if type(padding) not in (int,float) or not math.isfinite(padding) or not 0<=padding<=1:
        raise ValueError('Crop enlargement must be 0..100%')
    if type(rotation) is not int or rotation not in (0,90,180,270):raise ValueError('Rotation must be clockwise 0, 90, 180 or 270')
    if type(enabled) is not bool:raise ValueError('Object crop must be true or false')


def apply(workspace, manifest, padding=.15, rotation=90, enabled=True, progress=None):
    validate_options(padding,rotation,enabled)
    progress=progress or (lambda event:None)
    frames=manifest['frames'];groups={};warnings=[]
    prior=manifest.get('quad_split_config',{}).get('rotations',{})
    def angle(camera):return (rotation-prior.get(camera,0))%360
    for index,frame in enumerate(frames,1):
        name=Path(frame['file']).name;camera=frame['camera_id'];mask_file=workspace/'masks'/(name+'.png')
        with Image.open(workspace/'images'/name) as image:
            size=image.rotate(-angle(camera),expand=True).size
        group=groups.setdefault(camera,dict(size=size,boxes=[],missing=False))
        if group['size']!=size:raise ValueError('Mixed resolutions for '+camera)
        box=None
        if mask_file.exists():
            with Image.open(mask_file) as mask:
                mask=mask.convert('L')
            if enabled and mask.getextrema()!=(255,255):
                mask=object_mask(mask);mask.save(mask_file)
            mask=mask.rotate(-angle(camera),expand=True);box=mask.getbbox()
            if mask.getextrema()==(255,255):box=None
        if box:group['boxes'].append(box)
        else:group['missing']=True
        progress(dict(stage='Finding object crop bounds',done=index,total=len(frames)))
    transforms={}
    for camera,group in groups.items():
        size=group['size'];boxes=group['boxes']
        if enabled and boxes and not group['missing']:
            union=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
            box=padded_box(union,size,padding)
        else:
            box=(0,0,*size)
            if enabled:warnings.append(f'{camera}: missing/empty masks; retained full frame rather than guessing an object crop.')
        transforms[camera]=dict(rotation_clockwise=rotation,additional_rotation=angle(camera),
            rotated_size=list(size),crop_box=list(box),output_size=[box[2]-box[0],box[3]-box[1]])
    rows=[];mask_rows=[];coverage=[]
    for index,frame in enumerate(frames,1):
        name=Path(frame['file']).name;t=transforms[frame['camera_id']]
        for kind,suffix in [('images',''),('masks','.png')]:
            target=workspace/kind/(name+suffix)
            if not target.exists():continue
            with Image.open(target) as original:
                im=original.rotate(-t['additional_rotation'],expand=True).crop(tuple(t['crop_box']))
                if kind=='images':im.convert('RGB').save(target,quality=95,subsampling=0)
                else:
                    im.save(target);hist=im.convert('L').histogram()
                    coverage.append(dict(filename=name,percent=round(100*(1-hist[0]/sum(hist)),3)))
                    mask_rows.append((target,frame['step'],frame['camera_id'],'cropped object mask'))
        rows.append((workspace/'images'/name,frame['step'],frame['camera_id'],'cropped / oriented'))
        progress(dict(stage='Cropping and rotating reconstruction images',done=index,total=len(frames)))
    contact_sheet(rows,workspace/'reports/cropped_contact_sheet.jpg')
    if mask_rows:contact_sheet(mask_rows,workspace/'reports/mask_contact_sheet.jpg')
    result=dict(enabled=enabled,padding=padding,padding_definition='total bounding-box enlargement',
                shared_crop_per_camera=True,mask_cleanup='dominant foreground cluster',cameras=transforms,warnings=warnings)
    write_json(workspace/'reports/crops.json',result)
    return result,coverage
