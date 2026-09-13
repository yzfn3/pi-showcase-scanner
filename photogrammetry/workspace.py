"""Build an inspectable full photogrammetry workspace; originals stay untouched."""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import uuid
from preview3d.quad_splitter import needs_split, split_scan
from windows_client.scan import load_manifest, write_json
from photogrammetry import masks, validation, colmap_runner, realitycapture_export, report

LOG=logging.getLogger(__name__)


def prepare(scan, root=None, threshold=25, blur=1, opening=3, closing=5, inspect=True,
            run_colmap=False, dense=False, matcher='exhaustive', quad_config=None, progress=None, mesher=None,
            crop_objects=True, crop_padding=.15, rotation=90):
    scan=Path(scan).resolve()
    from photogrammetry.object_crop import validate_options
    validate_options(crop_padding,rotation,crop_objects)
    progress = progress or (lambda message: None)
    progress('Splitting and validating camera images')
    if needs_split(scan): split_scan(scan,quad_config,progress=progress)
    manifest=load_manifest(scan)
    if not manifest['frames']: raise ValueError('No individual camera images; split combined captures first')
    if (scan/'.receiving').exists(): raise ValueError('Transfer is still active')
    lock=scan/'.processing'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
    try:
        base=Path(root).resolve()/scan.name if root else scan/'outputs/full_photogrammetry'
        # Preserve every earlier run, including any hand-edited masks and reconstruction.
        if base.exists():
            if (base/'.reconstructing').exists():
                raise ValueError('COLMAP is running; stop it before preparing another workspace')
            if base.is_symlink() or not (base/'workspace.json').is_file():
                raise ValueError('Refusing to replace an unrelated workspace directory')
            archive=base.with_name(base.name+'_run_'+uuid.uuid4().hex[:8])
            if archive.resolve().parent != base.resolve().parent:
                raise ValueError('Workspace archive must stay in the same parent directory')
            base.rename(archive)
        workspace=base
        for name in ('images','masks','colmap/sparse','colmap/dense','realitycapture','reports','logs'):
            (workspace/name).mkdir(parents=True,exist_ok=True)
        write_json(workspace/'workspace.json',dict(scan_path=str(scan),scan_id=manifest['scan_id'],
                   fixed_focus=manifest.get('settings',{}).get('focus_mode')=='manual',
                   camera_groups={Path(f['file']).name:f['camera_id'] for f in manifest['frames']}))
        for frame in manifest['frames']:
            shutil.copy2(scan/frame['file'],workspace/'images'/Path(frame['file']).name)
        progress('Measuring sharpness')
        checks=validation.inspect(scan,manifest,workspace,progress=progress)
        progress('Generating foreground masks')
        mask_result=masks.generate(scan,workspace,threshold,blur,opening,closing,inspect,progress=progress)
        for mask in mask_result['coverage']:
            if mask['percent'] in (0,100):
                checks['errors'].append('Invalid all-black/all-white mask: '+mask['filename'])
        from photogrammetry.object_crop import apply as crop_images
        crop_result,coverage=crop_images(workspace,manifest,crop_padding,rotation,crop_objects,progress)
        mask_result['coverage']=coverage
        write_json(workspace/'reports/masks.json',mask_result)
        metadata=json.loads((workspace/'workspace.json').read_text())
        metadata['image_preprocessing']=crop_result
        write_json(workspace/'workspace.json',metadata)
        from windows_client.detailed import prepare as prepare_detailed
        prepare_detailed(scan,manifest)
        rc=realitycapture_export.export(scan,manifest,workspace,progress=progress)
        progress('Preparing reconstruction and RealityScan export')
        reconstruction=colmap_runner.run(workspace,enabled=run_colmap and not checks['errors'],dense=dense,matcher=matcher,progress=progress,mesher=mesher)
        settings=manifest.get('settings',{})
        data=dict(scan_id=manifest['scan_id'],full_photogrammetry_mode=True,capture_settings=settings,
                  capture_width=manifest.get('capture_width',settings.get('capture_width')),
                  capture_height=manifest.get('capture_height',settings.get('capture_height')),
                  steps=manifest.get('steps',len({f['step'] for f in manifest['frames']})),
                  rotation_seconds=manifest.get('rotation_seconds',settings.get('rotation_seconds',60)),
                  estimated_angles=[dict(file=f['file'],step=f['step'],angle_deg=f.get('angle_deg')) for f in manifest['frames']],
                  combined_count=len(manifest.get('combined_frames',[])),split_count=len(manifest['frames']),
                  mask_generation_applied=mask_result['applied'],mask_settings=mask_result['settings'],mask_coverage_stats=mask_result['coverage'],
                  colmap_detected=reconstruction['detected'],colmap_version=reconstruction['version'],colmap_status=reconstruction['status'],
                  colmap_commands=reconstruction['commands'],colmap_output_path=reconstruction['output_path'],
                  realitycapture_export_path=rc,full_workspace_path=str(workspace),next_action=reconstruction['next_action'],**checks)
        data['image_preprocessing']=crop_result
        original_manifest=manifest
        if manifest.get('full_photogrammetry_mode') and (scan/'manifest.pre_full.json').is_file():
            original_manifest=json.loads((scan/'manifest.pre_full.json').read_text())
        data['pre_reconstruction_warnings']=list(original_manifest.get('warnings',[]))+checks['warnings']+mask_result['warnings']+crop_result['warnings']
        data['warnings']=list(dict.fromkeys(data['pre_reconstruction_warnings']+reconstruction['warnings']))
        data['validation_errors']=list(checks['errors'])
        data['reconstruction_summary']={k:reconstruction.get(k) for k in ('registered_images','input_images','points3D','camera_grouping')}
        data['errors']=list(checks['errors'])+reconstruction['errors']
        if checks['errors']: data['colmap_status']='validation_failed'
        for warning in data['warnings']: LOG.warning(warning)
        return report.publish(scan,workspace,data)
    finally:
        lock.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scan',required=True,type=Path)
    p.add_argument('--root',type=Path);p.add_argument('--run-colmap',action='store_true');p.add_argument('--dense',action='store_true')
    p.add_argument('--matcher',choices=['exhaustive','sequential'],default='exhaustive');p.add_argument('--threshold',type=int,default=25)
    p.add_argument('--quad-config',type=Path)
    p.add_argument('--crop-padding',type=float,default=.15)
    p.add_argument('--no-object-crop',action='store_true')
    p.add_argument('--rotation',type=int,choices=(0,90,180,270),default=90)
    a=p.parse_args();logging.basicConfig(level=logging.INFO)
    result=prepare(a.scan,root=a.root,threshold=a.threshold,run_colmap=a.run_colmap,dense=a.dense,matcher=a.matcher,quad_config=a.quad_config,
                   crop_objects=not a.no_object_crop,crop_padding=a.crop_padding,rotation=a.rotation)
    print(json.dumps(dict(workspace=result['full_workspace_path'],report=result['report_path'],status=result['colmap_status']),indent=2))

if __name__=='__main__': main()
