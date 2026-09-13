"""VGGT workspace preparation and isolated GPU worker; never invokes COLMAP."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from PIL import Image
import numpy as np
from windows_client.scan import load_manifest, write_json


def available():
    return all(importlib.util.find_spec(name) is not None for name in ('torch', 'vggt', 'open3d'))


def brighten(image, gamma=0.8):
    """Lift midtones/shadows gently, preserving black/white endpoints."""
    if not isinstance(gamma, (int, float)) or not math.isfinite(gamma) or not .4 <= gamma <= 1.5:
        raise ValueError('Brightness gamma must be 0.4..1.5 (below 1 brightens)')
    lut = np.round(255 * (np.arange(256) / 255.) ** gamma).astype(np.uint8)
    return Image.fromarray(lut[np.asarray(image.convert('RGB'))])


def run(scan, *, engine="vggt", progress=None, gamma=1.0, max_frames=64, image_size=392,
        threshold=25, crop_padding=.15, rotation=90, crop_objects=True, mesh=True, mask_method="background", sam_region=(.1,.1,.8,.8), cleanup=False, cleanup_smoothing=2, surface_resolution=192, surface_smoothing=6):
    if engine not in ('vggt','worldmirror2'): raise ValueError('Unknown reconstruction engine')
    if type(surface_resolution) is not int or not 96<=surface_resolution<=512:raise ValueError('Surface resolution must be 96..512')
    if type(surface_smoothing) is not int or not 0<=surface_smoothing<=20:raise ValueError('Surface smoothing must be 0..20')
    if mask_method not in ('background','sam2'):raise ValueError('Unknown mask method')
    if type(cleanup) is not bool or type(cleanup_smoothing) is not int or not 0<=cleanup_smoothing<=5:raise ValueError('Invalid cleanup options')
    from photogrammetry.sam2_masks import validate_region
    validate_region(sam_region)
    if mask_method=='sam2' and importlib.util.find_spec('sam2') is None:raise RuntimeError('Install SAM 2 with scripts/setup_sam2.ps1 first')
    progress = progress or (lambda event: None)
    if type(max_frames) is not int or not 4 <= max_frames <= 128:
        raise ValueError('VGGT image limit must be 4..128')
    if image_size not in (280, 336, 392, 448, 518):
        raise ValueError('Unsupported VGGT image size')
    brighten(Image.new('RGB', (1, 1)), gamma)
    if not available():
        raise RuntimeError('VGGT dependencies missing. Run scripts/setup_vggt.ps1 first.')
    scan = Path(scan).resolve()
    from preview3d.quad_splitter import needs_split, split_scan
    if needs_split(scan): split_scan(scan, progress=progress)
    manifest = load_manifest(scan)
    lock = scan / '.processing'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd)
    workspace = scan / 'outputs' / engine
    try:
        if workspace.exists():
            if workspace.is_symlink() or not (workspace/'workspace.json').is_file():
                raise ValueError('Refusing to replace an unrelated VGGT directory')
            workspace.rename(workspace.with_name(engine+'_run_' + uuid.uuid4().hex[:8]))
        for name in ('images', 'masks', 'reports', 'logs', 'model_inputs'):
            (workspace/name).mkdir(parents=True, exist_ok=True)
        write_json(workspace/'workspace.json', dict(engine=engine, scan_path=str(scan), gamma=gamma))
        from photogrammetry import masks
        from photogrammetry.object_crop import apply
        for frame in manifest['frames']:
            shutil.copy2(scan/frame['file'], workspace/'images'/Path(frame['file']).name)
        progress(dict(stage='Preparing object masks', phase=0))
        masks.generate(scan, workspace, threshold=threshold, progress=progress)
        if mask_method=='sam2':
            command=[sys.executable,'-u','-m','photogrammetry.sam2_masks','--workspace',str(workspace),'--region',','.join(map(str,sam_region))]
            with (workspace/'logs/sam2.log').open('w',encoding='utf8') as log:
                with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace') as process:
                    for line in process.stdout:
                        log.write(line);log.flush()
                        if line.startswith('PROGRESS '):progress(json.loads(line[9:]))
                    if process.wait():raise RuntimeError('SAM 2 failed; inspect '+str(workspace/'logs/sam2.log'))
        apply(workspace, manifest, crop_padding, rotation, crop_objects, progress)
        # Copies used for inference and inspection are brightened AFTER background subtraction.
        for i, frame in enumerate(manifest['frames'], 1):
            path = workspace/'images'/Path(frame['file']).name
            if gamma != 1.0:
                with Image.open(path) as source: adjusted = brighten(source, gamma)
                adjusted.save(path, quality=95)
            progress(dict(stage='Preparing processing images', done=i, total=len(manifest['frames'])))
        from preview3d.quad_splitter import contact_sheet
        contact_sheet([(workspace/'images'/Path(f['file']).name,f['step'],f['camera_id'],'brightened / cropped')
                       for f in manifest['frames']],workspace/'reports/cropped_contact_sheet.jpg')
        config = dict(surface_resolution=surface_resolution,surface_smoothing=surface_smoothing,mask_method=mask_method,engine=engine, scan_id=scan.name, max_frames=max_frames, image_size=image_size,
                      frames=[dict(name=Path(f['file']).name, camera_id=f['camera_id'], step=f['step']) for f in manifest['frames']],
                      gamma=gamma, mesh=mesh, input_images=len(manifest['frames']))
        write_json(workspace/'request.json', config)
        command = [sys.executable, '-u', '-m', 'photogrammetry.vggt_worker', '--workspace', str(workspace)]
        env = os.environ.copy(); env['PYTHONUTF8'] = '1'
        with (workspace/'logs/inference.log').open('w', encoding='utf8') as log:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding='utf8', errors='replace', env=env) as process:
                for line in process.stdout:
                    log.write(line); log.flush()
                    if line.startswith('PROGRESS '):
                        progress(json.loads(line[len('PROGRESS '):]))
                if process.wait():
                    raise RuntimeError('VGGT failed; inspect '+str(workspace/'logs/inference.log'))
        if cleanup and mesh:
            from photogrammetry.mesh_cleanup import run as clean_mesh
            clean_mesh(workspace,smoothing=cleanup_smoothing,progress=lambda **event:progress(event))
        result = json.loads((workspace/'reports/vggt.json').read_text())
        result['mask_method']=mask_method
        result.update(full_workspace_path=str(workspace), report_path=str(workspace/'reports/run_report.md'),
                      next_action='Inspect the reconstructed point cloud and mesh. Learned geometry is approximate, not calibrated.')
        write_json(workspace/'reports/run_report.json', result)
        (workspace/'reports/run_report.md').write_text('# VGGT reconstruction\n\n```json\n'+json.dumps(result, indent=2)+'\n```\n', encoding='utf8')
        progress(dict(stage='VGGT reconstruction complete', done=1, total=1, phase=3 if cleanup and mesh else 2 if mesh else 1, phase_done=4 if mesh else 0, phase_total=4))
        return result
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan', type=Path, required=True)
    p.add_argument('--engine', choices=['vggt','worldmirror2'], default='vggt')
    p.add_argument('--gamma', type=float, default=1.0)
    p.add_argument('--max-frames', type=int, default=64)
    p.add_argument('--image-size', type=int, default=392)
    a = p.parse_args()
    print(json.dumps(run(a.scan, engine=a.engine, gamma=a.gamma, max_frames=a.max_frames, image_size=a.image_size,
                         progress=lambda event: print(event, flush=True)), indent=2))
