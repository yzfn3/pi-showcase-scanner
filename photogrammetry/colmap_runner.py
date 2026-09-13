"""Optional COLMAP execution with isolated attempts and persistent logs."""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import re
import struct
import sqlite3
from contextlib import closing
from PIL import Image
from windows_client.scan import write_json

LOG = logging.getLogger(__name__)


def detect(explicit=None):
    candidates = [explicit, os.environ.get('COLMAP_PATH'), shutil.which('colmap'), shutil.which('COLMAP.bat')]
    for base in (Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'COLMAP', Path('C:/COLMAP')):
        candidates.extend([str(base/'COLMAP.bat'), str(base/'bin/colmap.exe')])
    # Official portable Windows archives are commonly extracted in Downloads.
    for base in sorted((Path.home()/'Downloads').glob('colmap*')):
        if base.is_dir():
            candidates.extend([str(base/'COLMAP.bat'), str(base/'bin/colmap.exe')])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            native = Path(candidate).parent/'bin/colmap.exe'
            if Path(candidate).suffix.lower() in ('.bat', '.cmd') and native.is_file():
                # The bundled batch wrapper breaks on quoted workspace paths.
                # Run the executable directly and recreate its library environment.
                return str(native.resolve())
            return str(Path(candidate).resolve())
    return None


def commands(executable, workspace, output, matcher='exhaustive', masks=True, dense=False, mesher=None,
             grouped=True):
    if matcher not in ('exhaustive', 'sequential'):
        raise ValueError('Invalid matcher')
    db = str(output/'database.db')
    images = str(output/'inputs/images' if grouped else workspace/'images')
    extract = [executable, 'feature_extractor', '--database_path', db, '--image_path', images,
               '--ImageReader.single_camera_per_folder' if grouped else '--ImageReader.single_camera_per_image', '1']
    if masks:
        extract += ['--ImageReader.mask_path', str(output/'inputs/masks' if grouped else workspace/'masks')]
    result = [extract, [executable, matcher+'_matcher', '--database_path', db],
              [executable, 'mapper', '--database_path', db, '--image_path', images, '--output_path', str(output/'sparse')]]
    if dense:
        d = str(output/'dense')
        result.extend([
            [executable, 'image_undistorter', '--image_path', images, '--input_path', str(output/'sparse/0'), '--output_path', d, '--output_type', 'COLMAP'],
            [executable, 'patch_match_stereo', '--workspace_path', d, '--workspace_format', 'COLMAP', '--PatchMatchStereo.geom_consistency', 'true'],
            [executable, 'stereo_fusion', '--workspace_path', d, '--workspace_format', 'COLMAP', '--input_type', 'geometric', '--output_path', str(output/'dense/fused.ply')]])
        if mesher:
            result.append([executable, mesher+'_mesher', '--input_path', str(output/'dense/fused.ply') if mesher == 'poisson' else d,
                           '--output_path', str(output/f'dense/meshed-{mesher}.ply')])
    return result


def _run(workspace, enabled=True, dense=False, matcher='exhaustive', executable=None, timeout=7200, mesher=None, progress=None):
    workspace = Path(workspace).resolve()
    if not (workspace/'workspace.json').is_file():
        raise ValueError('Not a prepared photogrammetry workspace')
    found = detect(executable)
    environment = os.environ.copy()
    if found:
        binary_dir = Path(found).parent
        environment['PATH'] = str(binary_dir)+os.pathsep+environment.get('PATH', '')
        plugins = binary_dir.parent/'plugins'
        if plugins.is_dir():
            environment['QT_PLUGIN_PATH'] = str(plugins)
    result = dict(detected=bool(found), version=None, status='prepared_only', commands=[], warnings=[], errors=[])
    output = workspace/'colmap'
    if (output/'database.db').exists() or ((output/'sparse').exists() and any((output/'sparse').iterdir())):
        output = output/('attempt_'+uuid.uuid4().hex[:8])
    (output/'sparse').mkdir(parents=True, exist_ok=True)
    (output/'dense').mkdir(exist_ok=True)
    result['output_path'] = str(output)
    mask_report = json.loads((workspace/'reports/masks.json').read_text())
    # Recheck PNGs so manual edits are respected and stale coverage cannot hide
    # invalid masks. COLMAP expects image.jpg.png, with white included pixels.
    coverage, mask_errors = [], []
    image_paths = sorted((workspace/'images').glob('*.jpg'))
    for image_path in image_paths:
        mask_path = workspace/'masks'/(image_path.name+'.png')
        if not mask_path.exists():
            continue
        try:
            with Image.open(mask_path) as mask, Image.open(image_path) as original:
                if mask.size != original.size:
                    raise ValueError('mask/image dimensions differ')
                histogram = mask.convert('L').histogram()
                percent = 100*(1-histogram[0]/sum(histogram))
                coverage.append(dict(filename=image_path.name, percent=percent))
        except (OSError, ValueError) as exc:
            mask_errors.append(f'{mask_path.name}: {exc}')
    mask_report.update(applied=bool(coverage), complete=len(coverage)==len(image_paths) and bool(coverage), coverage=coverage)
    result['errors'].extend(mask_errors)
    metadata = json.loads((workspace/'workspace.json').read_text())
    if not metadata.get('camera_groups'):
        metadata['camera_groups'] = {p.name: re.search(r'(cam_\d{2})\.jpg$', p.name)[1]
                                    for p in image_paths if re.search(r'(cam_\d{2})\.jpg$', p.name)}
    grouped = len(metadata['camera_groups']) == len(image_paths) and bool(image_paths)
    if not grouped:
        raise ValueError('Every image needs a physical camera ID; prepare the scan workspace again')
    if grouped:
        for source in image_paths:
            camera = metadata['camera_groups'].get(source.name)
            if not isinstance(camera, str) or not re.fullmatch(r'cam_\d{2}', camera):
                raise ValueError('Invalid fixed-focus camera grouping')
            for kind, original in [('images', source), ('masks', workspace/'masks'/(source.name+'.png'))]:
                if original.exists():
                    target = output/'inputs'/kind/camera/original.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        try:
                            os.link(original, target)
                        except OSError:
                            shutil.copy2(original, target)
    result['camera_grouping'] = 'shared_intrinsics_per_physical_camera'
    if not metadata.get('fixed_focus'):
        result['warnings'].append('Intrinsics are shared per physical camera. Lock focus and keep crop/resolution constant for this assumption to hold.')
    plan = commands(found or 'colmap', workspace, output, matcher, mask_report['applied'], dense, mesher, grouped)
    (workspace/'reports/COLMAP_COMMANDS.txt').write_text('\n'.join(subprocess.list2cmdline(c) for c in plan)+'\n', encoding='utf-8')
    instruction = f'Install COLMAP from https://colmap.github.io/install.html; set COLMAP_PATH to its executable or COLMAP.bat. Run: python -m photogrammetry.colmap_runner --workspace "{workspace}"'
    result['next_action'] = instruction
    if not found:
        result['warnings'].append('COLMAP not found. '+instruction)
    if not mask_report['complete'] or mask_report['warnings']:
        result['warnings'].append('Masks are missing or weak. Inspect masks before trusting turntable alignment.')
    if any(c['percent'] in (0, 100) for c in mask_report['coverage']):
        result['errors'].append('All-black/all-white masks must be corrected before reconstruction')
        result['status'] = 'validation_failed'
        enabled = False
    if mask_errors:
        result['status'] = 'validation_failed'
        enabled = False
    if dense:
        result['warnings'].append('Feature masks suppress background keypoints; dense stereo may still include background. Inspect and clean the fused cloud before meshing.')
    if enabled and found:
        try:
            probe = subprocess.run([found, '-h'], capture_output=True, text=True, timeout=20, errors='replace', env=environment)
            result['version'] = (probe.stdout+probe.stderr).strip()[:500]
            for i, command in enumerate(plan):
                if progress:
                    progress('COLMAP: '+command[1])
                if command[1] in ('feature_extractor', matcher+'_matcher'):
                    help_result = subprocess.run([found, command[1], '-h'], capture_output=True, text=True, timeout=20, errors='replace', env=environment)
                    help_text = help_result.stdout+help_result.stderr
                    options = ['FeatureExtraction.use_gpu', 'SiftExtraction.use_gpu'] if i == 0 else ['FeatureMatching.use_gpu', 'SiftMatching.use_gpu']
                    for option in options:
                        if option in help_text:
                            command += ['--'+option, '0']
                            break
                    if i == 0 and mask_report['applied'] and 'ImageReader.mask_path' not in help_text:
                        raise RuntimeError('Installed COLMAP does not expose ImageReader.mask_path; refusing to silently drop masks')
                log_path = workspace/f'logs/{output.name}_{i:02d}_{command[1]}.log'
                LOG.info('COLMAP %s; log: %s', command[1], log_path)
                result['commands'].append(command)
                with log_path.open('w', encoding='utf-8') as log:
                    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True, env=environment)
                if command[1] == 'feature_extractor' and (output/'database.db').exists():
                    result['camera_models'] = audit_cameras(output/'database.db')
                    result['crop_intrinsics_initialization']=initialize_crop_intrinsics(output/'database.db',metadata,result['camera_models'])
                if command[1] == 'mapper':
                    models = [p for p in (output/'sparse').iterdir() if (p/'images.bin').is_file()
                              and (p/'points3D.bin').is_file() and (p/'points3D.bin').stat().st_size > 8]
                    if not models:
                        raise RuntimeError('COLMAP produced no nonempty sparse model; inspect matches and masks')
                    chosen = max(models, key=lambda p: (p/'images.bin').stat().st_size)
                    result['sparse_model'] = str(chosen)
                    result['registered_images'] = struct.unpack('<Q', (chosen/'images.bin').read_bytes()[:8])[0]
                    result['points3D'] = struct.unpack('<Q', (chosen/'points3D.bin').read_bytes()[:8])[0]
                    result['input_images'] = len(image_paths)
                    partial = result['registered_images'] < max(3, len(image_paths)//2)
                    if partial:
                        result['warnings'].append(f"Weak alignment: only {result['registered_images']} of {len(image_paths)} images registered; this is not a complete reconstruction.")
                    for remaining in plan[i+1:]:
                        if remaining[1] == 'image_undistorter':
                            remaining[remaining.index('--input_path')+1] = str(chosen)
                    result['status'] = 'sparse_partial' if partial else 'sparse_complete'
            if dense:
                if not (output/'dense/fused.ply').is_file():
                    raise RuntimeError('No fused cloud produced')
                result['status'] = 'dense_complete'
            result['next_action'] = 'Inspect registered cameras and object points in COLMAP GUI. Sparse output is a point cloud, not a mesh.'
            if result['status'] == 'sparse_partial':
                result['next_action'] = 'Only part of the scan aligned. Inspect masks and overlap; add real surface texture to plain/shiny objects before recapturing.'
        except (OSError, subprocess.SubprocessError, RuntimeError, KeyboardInterrupt) as exc:
            result['status'] = 'failed'
            result['errors'].append(str(exc))
            result['next_action'] = 'Inspect logs, masks, sharpness and image overlap, then retry.'
    write_json(workspace/'reports/colmap.json', result)
    if result.get('sparse_model'):
        try:
            from photogrammetry.result_view import export_view
            result['view'] = export_view(workspace, result)
        except (OSError, ValueError, struct.error) as exc:
            result['warnings'].append('Could not create browser point preview: '+str(exc))
        write_json(workspace/'reports/colmap.json', result)
    return result


def audit_cameras(database):
    """Verify COLMAP assigned one distinct camera model per physical camera."""
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute('SELECT name,camera_id FROM images').fetchall()
    groups = {}
    for name, camera_id in rows:
        camera = name.replace('\\', '/').split('/')[0]
        if not re.fullmatch(r'cam_\d{2}', camera):
            raise RuntimeError('COLMAP image lacks its physical camera folder: '+name)
        groups.setdefault(camera, set()).add(camera_id)
    if not groups:
        raise RuntimeError('COLMAP database contains no camera assignments')
    if any(len(ids) != 1 for ids in groups.values()) or len({next(iter(ids)) for ids in groups.values()}) != len(groups):
        raise RuntimeError('COLMAP did not preserve distinct shared camera models')
    return {camera:dict(camera_id=next(iter(ids)),images=sum(name.startswith(camera+'/') for name,_ in rows))
            for camera,ids in sorted(groups.items())}


def initialize_crop_intrinsics(database, metadata, groups):
    """Shift the approximate optical center after cropping, without resizing focal length.

    With no measured calibration, use COLMAP's usual 1.2*max-size focal guess
    on the original frame, not the tight object crop. Bundle adjustment still
    estimates focal length and radial distortion per physical camera.
    """
    transforms=metadata.get('image_preprocessing',{}).get('cameras',{})
    updates={}
    with closing(sqlite3.connect(database)) as connection:
        for camera,group in groups.items():
            if camera not in transforms:continue
            t=transforms[camera];w,h=t['rotated_size'];x,y,_,_=t['crop_box']
            model=connection.execute('SELECT model FROM cameras WHERE camera_id=?',(group['camera_id'],)).fetchone()[0]
            if model!=2:raise RuntimeError('Crop initialization expects SIMPLE_RADIAL cameras')
            params=(1.2*max(w,h),w/2-x,h/2-y,0.)
            connection.execute('UPDATE cameras SET params=?,prior_focal_length=0 WHERE camera_id=?',
                               (struct.pack('<4d',*params),group['camera_id']))
            updates[camera]=dict(model='SIMPLE_RADIAL',initial_parameters=list(params),calibrated=False)
        connection.commit()
    return updates


def run(workspace, **options):
    workspace = Path(workspace).resolve()
    if not (workspace/'workspace.json').is_file():
        raise ValueError('Not a prepared photogrammetry workspace')
    lock = workspace/'.reconstructing'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        return _run(workspace, **options)
    finally:
        lock.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', required=True, type=Path)
    p.add_argument('--dense', action='store_true')
    p.add_argument('--matcher', choices=['exhaustive', 'sequential'], default='exhaustive')
    p.add_argument('--colmap', help='Path to colmap.exe or COLMAP.bat')
    p.add_argument('--timeout', type=float, default=7200)
    p.add_argument('--mesher', choices=['poisson', 'delaunay'])
    a = p.parse_args()
    if a.mesher and not a.dense:
        p.error('--mesher requires --dense')
    result = run(a.workspace, dense=a.dense, matcher=a.matcher, executable=a.colmap, timeout=a.timeout, mesher=a.mesher)
    from photogrammetry.report import update_reconstruction
    update_reconstruction(a.workspace, result)
    print(json.dumps(result, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
