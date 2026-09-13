"""Export geometry images and RealityScan image-layer masks."""
import csv
import shutil
from pathlib import Path


def export(scan, manifest, workspace, progress=None):
    root = workspace / 'realitycapture'
    images = root / 'images'
    images.mkdir(parents=True, exist_ok=True)
    rows = []
    for index,frame in enumerate(manifest['frames'],1):
        name = Path(frame['file']).name
        shutil.copy2(workspace / 'images' / name, images / name)
        mask = workspace / 'masks' / (name + '.png')
        target = images / (name + '.mask.png')
        if mask.exists():
            shutil.copy2(mask, target)
        rows.append(dict(filename=name, step=frame['step'], camera=frame['camera_id'],
                         estimated_turntable_angle_degrees=frame.get('angle_deg'),
                         original_file=str(scan / frame['file']), mask_file=str(target) if mask.exists() else ''))
        if progress:progress(dict(stage='Preparing RealityScan image export',done=index,total=len(manifest['frames'])))
    with (root / 'image_index.csv').open('w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / 'README_IMPORT.txt').write_text(
        'Import images/ into RealityCapture or RealityScan. Masks are alongside images as image.jpg.mask.png layers.\n'
        'Enable masks for alignment and meshing. Start with High feature detection quality and inspect components.\n'
        'Turntable warning: the object moves against a fixed background. Missing or weak masks can reconstruct the room.\n'
        'Inspect every mask; coverage alone does not prove correctness. Angles in the CSV are estimates, not camera poses.\n'
        'Documentation: https://rshelp.capturingreality.com/en-US/tools/imglayers.htm\n', encoding='utf-8')
    return str(root)
