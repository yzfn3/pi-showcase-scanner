"""Full-size foreground masks: white includes object, black excludes background."""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter
from preview3d.quad_splitter import contact_sheet
from windows_client.scan import load_manifest, write_json


def foreground(image, background, threshold=25, blur=1, opening=3, closing=5):
    if not 0 <= threshold <= 255 or not 0 <= blur <= 20:
        raise ValueError('Threshold must be 0..255; blur must be 0..20')
    if any(v not in (0,1,3,5,7,9,11,13,15) for v in (opening,closing)):
        raise ValueError('Morphology sizes must be zero or an odd number up to 15')
    if image.size != background.size: raise ValueError('Background dimensions do not match image')
    a=np.asarray(image.convert('RGB'),dtype=np.int16); b=np.asarray(background.convert('RGB'),dtype=np.int16)
    difference=Image.fromarray(np.abs(a-b).max(axis=2).astype('uint8'))
    if blur: difference=difference.filter(ImageFilter.GaussianBlur(blur))
    mask=difference.point(lambda p:255 if p>threshold else 0)
    if opening>1: mask=mask.filter(ImageFilter.MinFilter(opening)).filter(ImageFilter.MaxFilter(opening))
    if closing>1: mask=mask.filter(ImageFilter.MaxFilter(closing)).filter(ImageFilter.MinFilter(closing))
    return mask


def generate(scan, workspace, threshold=25, blur=1, opening=3, closing=5, inspect=False, progress=None):
    manifest=load_manifest(scan); cameras={c['id']:c for c in manifest['cameras']}
    settings=dict(threshold=threshold,blur=blur,opening=opening,closing=closing)
    (workspace/'masks').mkdir(parents=True,exist_ok=True)
    rows, warnings, coverage = [], [], []
    for index,frame in enumerate(manifest['frames'],1):
        if progress:progress(dict(stage='Generating foreground masks',done=index-1,total=len(manifest['frames'])))
        name=Path(frame['file']).name; camera=cameras[frame['camera_id']]
        reference=camera.get('background')
        if not reference:
            warnings.append(f'Missing matching background for {name}'); continue
        with Image.open(scan/frame['file']) as original, Image.open(scan/reference) as bg:
            try: mask=foreground(original,bg,**settings)
            except ValueError as exc: warnings.append(f'{name}: {exc}'); continue
            # Exclude synthetic labels from photogrammetry as well as the quick viewer.
            crop=camera.get('preview_crop')
            if crop:
                from PIL import ImageDraw
                bounded=Image.new('L',mask.size); bounded.paste(mask.crop(tuple(crop)),tuple(crop[:2])); mask=bounded
            percent=round(float((np.asarray(mask)>0).mean()*100),3)
            coverage.append(dict(filename=name,percent=percent))
            if percent<1 or percent>95: warnings.append(f'Weak mask {name}: {percent}% foreground')
            target=workspace/'masks'/(name+'.png'); mask.save(target)
            rows.append((target,frame['step'],frame['camera_id'],f'{percent}% foreground'))
            if inspect:
                before=original.convert('RGB'); before.thumbnail((480,360))
                after=Image.composite(original.convert('RGB'),Image.new('RGB',original.size),mask); after.thumbnail((480,360))
                pair=Image.new('RGB',(before.width*2,before.height));pair.paste(before);pair.paste(after,(before.width,0))
                folder=workspace/'reports/mask_inspection';folder.mkdir(exist_ok=True,parents=True);pair.save(folder/(name+'.jpg'))
    if rows: contact_sheet(rows,workspace/'reports/mask_contact_sheet.jpg')
    result=dict(applied=bool(rows),complete=len(rows)==len(manifest['frames']) and bool(rows),settings=settings,coverage=coverage,warnings=warnings)
    if not result['complete']: warnings.append('Turntable masks are incomplete: fixed background can dominate reconstruction.')
    write_json(workspace/'reports/masks.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scan',required=True,type=Path)
    p.add_argument('--threshold',type=int,default=25);p.add_argument('--blur',type=float,default=1)
    p.add_argument('--opening',type=int,default=3);p.add_argument('--closing',type=int,default=5);p.add_argument('--inspect',action='store_true')
    a=p.parse_args()
    from photogrammetry.workspace import prepare
    result=prepare(a.scan,threshold=a.threshold,blur=a.blur,opening=a.opening,closing=a.closing,inspect=a.inspect,run_colmap=False)
    print(result['report_path'])

if __name__=='__main__': main()
