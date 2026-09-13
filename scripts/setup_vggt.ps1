$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Create .venv and install requirements.txt first." }
& $pythonPath -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "CUDA PyTorch installation failed" }
& $pythonPath -m pip install -r (Join-Path $projectRoot "requirements-vggt.txt")
if ($LASTEXITCODE -ne 0) { throw "VGGT installation failed" }
& $pythonPath -c "import torch, vggt, open3d; assert torch.cuda.is_available(), 'CUDA unavailable'; print('VGGT CUDA ready:', torch.cuda.get_device_name())"
if ($LASTEXITCODE -ne 0) { throw "VGGT verification failed" }
