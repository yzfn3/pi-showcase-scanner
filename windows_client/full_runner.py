"""Browser-driven node capture and local full reconstruction jobs."""
import logging
from pathlib import Path
from pi_node.config import validate_request
from windows_client.node_api import NodeClient

LOG=logging.getLogger(__name__)
DEFAULT_SETTINGS=dict(steps=16,rotation_seconds=60,capture_width=4624,capture_height=3472,
    exposure_time=4000,gain=3,focus_mode='manual',lens_position=6.85,camera_timeout_ms=1500,awb='auto',
    sensor_mode='4624:3472:10',viewfinder_mode='4624:3472:10',zsl=True,
    autofocus_window='0,0,1,1',autofocus_range='full',jpeg_quality=95,
    combined_quad_output=True,use_backgrounds=True,autofocus_on_capture=False)
ACTIONS={'capture_only','connect','backgrounds','capture','receive','full_process','colmap','mesh','cleanup','trellis'}


def execute(app, request, update):
    action=request['action']
    engine=request.get('engine','vggt')
    if engine not in ('vggt','worldmirror2'):raise ValueError('Unknown reconstruction engine')
    def progress(message):
        fields=message if isinstance(message,dict) else dict(stage=message)
        update(**{'done':None,'total':None,**fields})
    if action=='trellis':
        from photogrammetry.learned_workspace import active_base
        from photogrammetry.trellis_runner import run as generate_asset
        import os
        scan=app.scan_path(request.get('scan_id'));workspace=scan/active_base(scan)
        if not workspace.resolve().is_relative_to(scan.resolve()):raise ValueError('Workspace leaves scan')
        lock=scan/'.processing';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
        try:
            update(scan_id=scan.name,engine=workspace.name)
            result=generate_asset(workspace,source=request.get('trellis_source'),resolution=request.get('trellis_resolution',512),seed=request.get('trellis_seed',42),progress=update)
            return dict(scan_id=scan.name,trellis=result,message='AI-generated alternative complete; compare with the actual scan')
        finally:lock.unlink(missing_ok=True)
    if action=='mesh':
        from photogrammetry.learned_workspace import active_base
        from photogrammetry.depth_surface import run as fuse_depth
        import os
        scan=app.scan_path(request.get('scan_id'));workspace=scan/active_base(scan)
        if not workspace.resolve().is_relative_to(scan.resolve()):raise ValueError('Workspace leaves scan')
        if not (workspace/'predictions.npz').is_file():raise ValueError('Run phase 1 first to save camera poses and depth maps')
        lock=scan/'.processing';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
        try:
            update(scan_id=scan.name,engine=workspace.name)
            result=fuse_depth(workspace,resolution=request.get('surface_resolution',192),smoothing=request.get('surface_smoothing',6),truncation=8,publish=True,progress=update)
            update(stage='Depth fusion complete',phase=2,phase_done=4,phase_total=4,done=1,total=1)
            return dict(scan_id=scan.name,fusion=result,message='Depth fusion complete; original mesh retained')
        finally:lock.unlink(missing_ok=True)
    if action=='cleanup':
        from photogrammetry.learned_workspace import active_base
        from photogrammetry.mesh_cleanup import run as clean_mesh
        import os
        scan=app.scan_path(request.get('scan_id'));workspace=scan/active_base(scan)
        if not workspace.resolve().is_relative_to(scan.resolve()):raise ValueError('Workspace leaves scan')
        lock=scan/'.processing';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
        try:
            update(scan_id=scan.name,engine=workspace.name)
            result=clean_mesh(workspace,smoothing=request.get('cleanup_smoothing',2),progress=update)
            return dict(scan_id=scan.name,cleanup=result,message='Cleanup complete; original mesh retained')
        finally:lock.unlink(missing_ok=True)
    options=dict(threshold=request.get('mask_threshold',25),run_colmap=request.get('run_colmap',True),
                 dense=False,matcher=request.get('matcher','exhaustive'),
                 mesher=None,progress=progress,crop_objects=request.get('crop_objects',True),
                 crop_padding=request.get('crop_padding',.15),rotation=request.get('rotation',90))
    if type(options['threshold']) is not int or not 0<=options['threshold']<=255: raise ValueError('Mask threshold must be 0..255')
    from photogrammetry.object_crop import validate_options
    validate_options(options['crop_padding'],options['rotation'],options['crop_objects'])
    if request.get('dense') or action=='mesh':
        if (request.get('mesher') or 'poisson') not in ('poisson','delaunay'):raise ValueError('Invalid mesher')
        size=request.get('dense_size',1600)
        if type(size) is not int or not 320<=size<=4000:raise ValueError('Dense image size must be 320..4000')
    if options['matcher'] not in ('exhaustive','sequential'): raise ValueError('Invalid matcher')
    if options['mesher'] not in (None,'poisson','delaunay'): raise ValueError('Invalid mesher')
    if action in ('full_process','colmap','mesh'):
        scan=app.scan_path(request.get('scan_id'))
    else:
        settings={**DEFAULT_SETTINGS,**request.get('settings',{})}
        if set(settings)-set(DEFAULT_SETTINGS): raise ValueError('Unknown capture setting')
        client=NodeClient(request.get('node','http://192.168.127.145:8000'),timeout=15)
        try:
            progress('Connecting to Pi')
            health=client.health();mode=health.get('backend','mock')
            validate_request(dict(mode=mode,cameras=4,**settings),mode)
            update(node_health=health)
            background=client.json('POST','/backgrounds/check',body=dict(mode=mode,cameras=4,**settings))
            if action=='connect':
                return dict(message=f"Connected to {health.get('hostname')}. Backgrounds: {'matching' if background['matching'] else 'capture required'}",health=health,backgrounds=background)
            if not health.get('ready',True): raise ValueError(health.get('last_error') or 'Camera is unavailable')
            if action=='backgrounds':
                if request.get('empty_table') is not True: raise ValueError('Confirm that the object is removed before capturing backgrounds')
                progress('Capturing empty-table references')
                state=client.capture_backgrounds(mode=mode,cameras=4,**settings)
            elif action=='receive':
                state={'scan_id':request.get('remote_scan_id')}
                if not isinstance(state['scan_id'],str):raise ValueError('Enter the remote scan ID')
            else:
                if settings['use_backgrounds'] and not background['matching']:
                    raise ValueError('Matching backgrounds are missing. Remove the object, capture backgrounds, then replace it before scanning.')
                if mode=='rpicam' and settings['rotation_seconds']/settings['steps']<max(3,settings['camera_timeout_ms']/1000+.75):
                    raise ValueError('Step interval is too short for reliable capture. Use 16 steps/60 seconds at 1000ms settling, or fewer steps for autofocus.')
                progress('Starting physical scan')
                state=client.start(mode=mode,cameras=4,**settings)
            sid=state['scan_id'];update(remote_scan_id=sid)
            client.wait(sid,timeout=max(300,settings['rotation_seconds']+120),
                        on_progress=lambda state:update(stage='Capturing backgrounds' if action=='backgrounds' else 'Capturing images',capture_progress=state,done=state['current_step'],total=state['total_steps']))
            if action=='backgrounds':return dict(message='Backgrounds saved. Replace the object and start the turntable before capturing the object.')
            progress('Downloading and verifying images')
            scan=client.download_scan(sid,app.root,timeout=600,on_progress=lambda done,total:progress(dict(stage='Downloading and verifying images',done=done,total=total)))
        finally:
            client.close()
    if action=='capture_only':
        update(scan_id=scan.name)
        return dict(scan_id=scan.name, message='Photos captured and downloaded. Inspect source photos, then choose Prepare + reconstruct when ready.')
    update(scan_id=scan.name,workspace=str(scan/'outputs'/engine),engine=engine)
    from photogrammetry.vggt_runner import run as run_vggt
    result=run_vggt(scan,mask_method=request.get("mask_method","background"),sam_region=request.get("sam_region",[.1,.1,.8,.8]),cleanup=request.get("cleanup",False),cleanup_smoothing=request.get("cleanup_smoothing",2),engine=engine,progress=progress,gamma=request.get('brightness_gamma',1.0),
                    max_frames=request.get('vggt_frames',64),image_size=request.get('vggt_size',392),surface_resolution=request.get('surface_resolution',192),surface_smoothing=request.get('surface_smoothing',6),
                    threshold=options['threshold'],crop_padding=options['crop_padding'],
                    rotation=options['rotation'],crop_objects=options['crop_objects'],mesh=request.get('dense',True) or action=='mesh')
    return dict(scan_id=scan.name,full_report=result,message=engine+' complete: '+str(result['processed_images'])+' images; mesh '+result['mesh_status'])
