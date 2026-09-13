$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path (Get-Location) '.venv/Scripts/python.exe'
& $python -m pip install numpy==1.26.4 scipy==1.13.1 gsplat==1.5.3 omegaconf
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Close running scanner processes before retrying.' }
if (-not (Test-Path tools/hyworld2)) { git clone https://github.com/Tencent-Hunyuan/HY-World-2.0.git tools/hyworld2 }
git -C tools/hyworld2 checkout df9988efb87bfc0f4947eb3889411cf957478b06
if ($LASTEXITCODE -ne 0) { throw 'Could not select verified upstream revision' }
& $python -m photogrammetry.worldmirror --setup
if ($LASTEXITCODE -ne 0) { throw 'WorldMirror setup failed' }
