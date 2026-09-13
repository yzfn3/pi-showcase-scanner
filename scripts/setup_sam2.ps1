$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path tools/sam2)) { git clone https://github.com/facebookresearch/sam2.git tools/sam2 }
git -C tools/sam2 checkout 2b90b9f5ceec907a1c18123530e92e794ad901a4
if ($LASTEXITCODE -ne 0) { throw 'SAM 2 source checkout failed' }
$env:SAM2_BUILD_CUDA = '0'
& .venv/Scripts/python.exe -m pip install --no-build-isolation --no-deps -e tools/sam2
if ($LASTEXITCODE -ne 0) { throw 'SAM 2 installation failed' }
& .venv/Scripts/python.exe -m pip install hydra-core==1.3.2 iopath==0.1.10 scipy==1.13.1
if ($LASTEXITCODE -ne 0) { throw 'SAM 2 dependency installation failed' }
Write-Output 'SAM 2 ready. The model downloads once on the first segmentation run.'
