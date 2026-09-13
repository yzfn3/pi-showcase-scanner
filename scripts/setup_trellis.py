"""Download the pinned portable TRELLIS.2 runtime and Q8 weights; no system install."""
import hashlib
import zipfile
from pathlib import Path
import requests
from huggingface_hub import hf_hub_download

REVISION='a57397bd3d351599d9729fc144b3f87c3f87d65b'
FILES=['dinov3','ss_flow','ss_dec','shape_flow_512','shape_flow_1024','shape_dec','tex_flow_512','tex_flow_1024','tex_dec']

def main():
    root=Path(__file__).resolve().parents[1];runtime=root/'tools/trellis-runtime';runtime.mkdir(parents=True,exist_ok=True)
    if not (runtime/'trellis-cli.exe').exists():
        archive=runtime/'runtime.zip'
        url='https://github.com/pwilkin/trellis.cpp/releases/download/v0.6.0/trellis-cuda-windows-x64.zip'
        with requests.get(url,stream=True,timeout=(15,120)) as r:
            r.raise_for_status()
            with archive.open('wb') as f:
                for chunk in r.iter_content(4*1024*1024):f.write(chunk)
        with archive.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!='4d08ab27e83094035fd8349aaf34d3460738df0466ef9c4991ddd958c0344bc2':raise RuntimeError('Runtime checksum mismatch')
        with zipfile.ZipFile(archive) as z:
            if any(not (runtime/n).resolve().is_relative_to(runtime.resolve()) for n in z.namelist()):raise RuntimeError('Unsafe archive path')
            z.extractall(runtime)
        archive.unlink()
    for i,name in enumerate(FILES):
        print(f'Downloading weights {i+1}/{len(FILES)}: {name}',flush=True)
        hf_hub_download('ilintar/trellis2-gguf','q8/'+name+'.gguf',revision=REVISION,local_dir=root/'models/trellis2')
    print('Portable TRELLIS.2 installed',flush=True)

if __name__=='__main__':main()
