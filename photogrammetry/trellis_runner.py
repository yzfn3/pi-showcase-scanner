"""Optional single-photo TRELLIS.2 asset generation, explicitly separate from the scan."""
import json
import hashlib
import logging
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
import numpy as np
from PIL import Image
from windows_client.scan import write_json


def export_glb(source, folder):
    import trimesh
    mesh=trimesh.load(source,force='mesh',process=False)
    if not len(mesh.faces):raise ValueError('AI model contains no triangles')
    if len(mesh.faces)>1500000:raise ValueError('AI mesh exceeds browser triangle limit; GLB remains available')
    faces=np.asarray(mesh.faces);folder=Path(folder);(folder/'reports').mkdir(exist_ok=True)
    data=dict(kind='mesh',coordinate_system='gltf',positions=mesh.vertices[faces].reshape(-1).tolist(),
        normals=mesh.vertex_normals[faces].reshape(-1).tolist(),triangles_total=len(faces),triangles_displayed=len(faces))
    if mesh.visual.kind=='texture':
        material=mesh.visual.material
        texture=getattr(material,'baseColorTexture',None)
        if texture is None:texture=getattr(material,'image',None)
        if texture is not None:
            texture.save(folder/'reports/texture.png')
            data.update(uv=mesh.visual.uv[faces].reshape(-1).tolist(),texture='texture.png')
    write_json(folder/'reports/mesh_view.json',data)
    return dict(file='reports/mesh_view.json',mesh_file=Path(source).name,triangles_total=len(faces),triangles_displayed=len(faces))


def run(workspace, source=None, resolution=512, seed=42, progress=None):
    progress=progress or (lambda **kw:None);w=Path(workspace).resolve()
    if type(resolution) is not int or resolution not in (512,1024):raise ValueError('TRELLIS resolution must be 512 or 1024')
    if type(seed) is not int or not 0<=seed<2**31:raise ValueError('Invalid seed')
    report=json.loads((w/'reports/vggt.json').read_text(encoding='utf8'))
    names=report['selected_images'];source=source or names[len(names)//2]
    if source not in names or Path(source).name!=source:raise ValueError('Select a processed scan image')
    root=Path(__file__).resolve().parents[1];exe=root/'tools/trellis-runtime/trellis-cli.exe';models=root/'models/trellis2/q8'
    if not exe.is_file() or not (models/'tex_dec.gguf').is_file():raise ValueError('Install the optional local model: python -m scripts.setup_trellis')
    folder=w/('trellis_'+uuid.uuid4().hex[:8]);folder.mkdir()
    with Image.open(w/'images'/source) as image,Image.open(w/'masks'/(source+'.png')) as mask:
        rgba=image.convert('RGBA');rgba.putalpha(mask.convert('L'));rgba.save(folder/'input.png')
    command=[str(exe),'--image',str(folder/'input.png'),'--output',str(folder/'model.glb'),'--models',str(models),
        '--res',str(resolution),'--seed',str(seed),'--require-gpu','--webp','off','--max-tokens','32768','--threads','8']
    progress(stage='TRELLIS.2: generating an alternative from one photo',phase=4,done=None,total=None)
    started=time.monotonic();timed_out=threading.Event()
    with (folder/'inference.log').open('w',encoding='utf8') as log:
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace') as process:
            def timeout():
                timed_out.set();process.kill()
            timer=threading.Timer(1800,timeout);timer.start()
            try:
                for line in process.stdout:
                    log.write(line);log.flush()
                    text=line.strip()
                    if text:
                        logging.getLogger(__name__).info(text)
                        step=re.search(r'\]\s+(\d+)/(\d+)\s+',text)
                        progress(stage='TRELLIS.2: '+text[-200:],phase=4,
                            done=int(step[1]) if step else None,total=int(step[2]) if step else None)
                code=process.wait()
            finally:timer.cancel()
    if timed_out.is_set():raise RuntimeError('TRELLIS exceeded 30 minutes; inspect '+str(folder/'inference.log'))
    if code or not (folder/'model.glb').is_file():raise RuntimeError('TRELLIS failed; inspect '+str(folder/'inference.log'))
    view=export_glb(folder/'model.glb',folder)
    view.update(file=folder.name+'/'+view['file'],mesh_file=folder.name+'/'+view['mesh_file'])
    result=dict(method='TRELLIS.2 Q8 via trellis.cpp v0.6.0',source_image=source,resolution=resolution,seed=seed,
        model='microsoft/TRELLIS.2-4B',weights_repository='ilintar/trellis2-gguf',weights_revision='a57397bd3d351599d9729fc144b3f87c3f87d65b',
        input_sha256=hashlib.sha256((folder/'input.png').read_bytes()).hexdigest(),
        elapsed_seconds=round(time.monotonic()-started,2),view=view,
        warning='AI-generated from ONE image. Hidden surfaces and fine details may be invented. This is not a measured scan or a cleanup of the reconstructed mesh.')
    write_json(folder/'report.json',result)
    for name in ('vggt.json','run_report.json'):
        path=w/'reports'/name
        if path.is_file():
            r=json.loads(path.read_text(encoding='utf8'));r.update(trellis=result,generated_mesh_view=view);write_json(path,r)
    progress(stage='TRELLIS.2 alternative ready for comparison',phase=4,done=1,total=1)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--source');p.add_argument('--resolution',type=int,default=512);p.add_argument('--seed',type=int,default=42)
    print(json.dumps(run(**vars(p.parse_args()),progress=lambda **kw:print(kw['stage'],flush=True)),indent=2))
