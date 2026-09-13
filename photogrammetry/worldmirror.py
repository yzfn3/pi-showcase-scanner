"""WorldMirror 2.0 depth/camera inference using the official weights; no generated views."""
import json
import sys
from pathlib import Path
import numpy as np

REPO_REVISION = 'df9988efb87bfc0f4947eb3889411cf957478b06'
MODEL_REVISION = 'd78a16c91c7a56488894a1c8de4f5c7cc28aa8b0'

def available():
    import importlib.util
    root=Path(__file__).resolve().parents[1]
    return (root/'tools/hyworld2/hyworld2/worldrecon').is_dir() and all(importlib.util.find_spec(n) is not None for n in ('torch','gsplat','safetensors'))


def infer(rgb, progress):
    import torch
    from huggingface_hub import snapshot_download
    from safetensors.torch import load_file
    source=Path(__file__).resolve().parents[1]/'tools/hyworld2'
    if not source.is_dir(): raise RuntimeError('Install WorldMirror with scripts/setup_worldmirror.ps1 first')
    sys.path.insert(0,str(source))
    from hyworld2.worldrecon.hyworldmirror.models.models.worldmirror import WorldMirror
    progress('Loading WorldMirror 2.0',phase=1)
    folder=source.parents[1]/'models/worldmirror2'
    if not (folder/'model.safetensors').exists():
        folder=Path(snapshot_download('tencent/HY-World-2.0',revision=MODEL_REVISION,
            allow_patterns=['HY-WorldMirror-2.0/config.json','HY-WorldMirror-2.0/model.safetensors']))/'HY-WorldMirror-2.0'
    config=json.loads((folder/'config.json').read_text());config['enable_bf16']=True
    model=WorldMirror(**config)
    state=load_file(str(folder/'model.safetensors'))
    # Upstream checkpoint omits deterministic rotary-frequency buffers; never permit missing learned parameters.
    for key,value in model.state_dict().items():
        if key.endswith('.rope.periods') and key not in state:state[key]=value
    model.load_state_dict(state,strict=True);del state
    # Camera + depth are the measured reconstruction path. No synthetic view generation or splat training.
    for key in ['gs','pts','norm']:
        setattr(model,'enable_'+key,False)
        setattr(model,key+'_head',None)
    model.gs_renderer=None
    model=model.eval().to('cuda',dtype=torch.bfloat16)
    images=torch.from_numpy(rgb.copy()).permute(0,3,1,2).float().div_(255).unsqueeze(0).cuda()
    progress('WorldMirror camera and depth inference',phase=1)
    blocks=model.visual_geometry_transformer.global_blocks
    handles=[block.register_forward_hook(lambda module,args,out,i=i:progress('WorldMirror cross-view geometry',phase=1,done=i+1,total=len(blocks))) for i,block in enumerate(blocks)]
    try:
        with torch.inference_mode():prediction=model({'img':images})
    finally:
        for handle in handles:handle.remove()
    depth=prediction['depth'][0].float().cpu().numpy()
    confidence=prediction['depth_conf'][0].float().cpu().numpy()
    poses=prediction['camera_poses'][0].float().cpu().numpy()
    intrinsic=prediction['camera_intrs'][0].float().cpu().numpy()
    extrinsic=np.linalg.inv(poses)[:,:3,:]
    del model,prediction,images;torch.cuda.empty_cache()
    return depth,confidence,extrinsic,intrinsic


def setup():
    """Add a mathematically equivalent PyTorch attention fallback for Windows."""
    root=Path(__file__).resolve().parents[1]
    file=root/'tools/hyworld2/hyworld2/worldrecon/hyworldmirror/models/layers/attention.py'
    source=file.read_text(encoding='utf8')
    old='    from flash_attn.flash_attn_interface import flash_attn_func as flash_attn_func_v2'
    replacement="""    try:
        from flash_attn.flash_attn_interface import flash_attn_func as flash_attn_func_v2
    except ImportError:
        def flash_attn_func_v2(q, k, v, dropout_p=0.0):
            return F.scaled_dot_product_attention(q.transpose(1,2),k.transpose(1,2),v.transpose(1,2),dropout_p=dropout_p).transpose(1,2)"""
    if 'def flash_attn_func_v2(' not in source:
        if old not in source:raise RuntimeError('Unexpected upstream attention implementation')
        file.write_text(source.replace(old,replacement),encoding='utf8')
    from huggingface_hub import snapshot_download
    snapshot_download('tencent/HY-World-2.0',revision=MODEL_REVISION,
        allow_patterns=['HY-WorldMirror-2.0/config.json','HY-WorldMirror-2.0/model.safetensors'])

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--setup',action='store_true',required=True)
    parser.parse_args();setup()
