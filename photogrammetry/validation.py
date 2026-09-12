"""Input validation and resolution-independent sharpness inspection."""
import csv
import numpy as np
from PIL import Image
from preview3d.quad_splitter import contact_sheet


def sharpness(image):
    gray = image.convert('L')
    gray.thumbnail((1024, 1024))
    a = np.asarray(gray, dtype=np.float32)
    lap = -4*a[1:-1, 1:-1]+a[:-2, 1:-1]+a[2:, 1:-1]+a[1:-1, :-2]+a[1:-1, 2:]
    return float(lap.var()) if lap.size else 0.0


def inspect(scan, manifest, workspace):
    warnings, errors, rows, dimensions = [], [], [], set()
    frames = manifest['frames']
    expected = manifest.get('steps', len({f['step'] for f in frames})) * len(manifest['cameras'])
    if len(frames) != expected:
        errors.append(f'Expected {expected} individual images, found {len(frames)}')
    if manifest.get('combined_quad_output'):
        if not (scan/'raw_combined').is_dir(): errors.append('Missing raw_combined directory')
        if not manifest.get('quad_split_config'): errors.append('Missing quad split configuration')
        for name in manifest.get('contact_sheet_files', []):
            if not (scan/name).is_file(): errors.append(f'Missing contact sheet: {name}')
    for f in frames:
        with Image.open(scan/f['file']) as im:
            im.load(); dimensions.add(im.size)
            score = sharpness(im)
            if max(im.convert('L').getextrema()) == min(im.convert('L').getextrema()):
                warnings.append(f"Uniform image: {f['file']}")
        rows.append(dict(filename=(scan/f['file']).name, camera=f['camera_id'], score=round(score, 3)))
        if 'angle_deg' not in f or 'step' not in f: errors.append(f"Missing angle/step: {f['file']}")
    if len(dimensions) != 1: warnings.append('Split dimensions differ across images; check camera crops')
    if any(min(w,h)<1080 or max(w,h)<1920 for w,h in dimensions):
        warnings.append('Split resolution is low for full photogrammetry: prefer at least 1920x1080 per camera (3840x2160 combined).')
    averages = {c:round(float(np.mean([r['score'] for r in rows if r['camera']==c])),3) for c in {r['camera'] for r in rows}}
    blurry = [r['filename'] for r in rows if r['score'] < 40]
    if blurry: warnings.append(f'{len(blurry)} images may be blurry (Laplacian variance below 40 at max 1024px; heuristic only).')
    with (workspace/'reports/sharpness_report.csv').open('w', newline='', encoding='utf-8') as out:
        writer=csv.DictWriter(out, fieldnames=['filename','camera','score']); writer.writeheader(); writer.writerows(rows)
    if frames:
        contact_sheet([(scan/f['file'],f['step'],f['camera_id'],f"sharpness {r['score']}") for f,r in zip(frames,rows)],scan/'outputs/inspection/sharpness_contact_sheet.jpg')
    combined=[]
    for f in manifest.get('combined_frames',[]):
        with Image.open(scan/f['file']) as im: combined.append(list(im.size))
    return dict(warnings=warnings, errors=errors, actual_split_dimensions=[list(d) for d in sorted(dimensions)],
                actual_combined_dimensions=sorted(map(list,{tuple(d) for d in combined})),
                sharpness_stats=dict(per_image=rows,per_camera_average=averages,likely_blurry=blurry))
