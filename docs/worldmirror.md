# WorldMirror 2.0 comparison backend

Uses the official Tencent HY-WorldMirror-2.0 camera/depth model locally. No synthesized photographs or COLMAP reconstruction. VGGT and the older COLMAP backend remain intact.

Source: https://github.com/Tencent-Hunyuan/HY-World-2.0 at `df9988efb87bfc0f4947eb3889411cf957478b06`.
Weights: `tencent/HY-World-2.0`, subfolder `HY-WorldMirror-2.0`, revision `d78a16c91c7a56488894a1c8de4f5c7cc28aa8b0`.
Weight SHA256: `9fff06539d3d9e85338d7de1ffb5afffb7739fa5bf4d62b4b7c319b5ecdde54f`.

## Setup

Install the existing VGGT/CUDA dependencies first. Close running local scanner processes before package installation, then run `scripts/setup_worldmirror.ps1` in PowerShell. Approximately 5 GB of weights download once. Images stay local; source and weights are ignored by Git. Upstream licensing and attribution remain in the downloaded repository.

On Windows, setup adds a PyTorch scaled-dot-product attention fallback when the separate FlashAttention extension is absent. The learned weights are unchanged. The checkpoint loads strictly before unused Gaussian-splat, point and normal heads are disabled; camera/depth inference remains enabled.

## Run

Select WorldMirror 2.0 in the browser's Reconstruction engine menu, or:

```powershell
python -m photogrammetry.vggt_runner --engine worldmirror2 --scan scans/received/scan_20260912_223440 --gamma 1 --max-frames 64 --image-size 518
```

For new capture, add `--reconstruction-engine worldmirror2 --full-scan` to the capture command. Output is `outputs/worldmirror2/`; VGGT and COLMAP outputs remain. The viewer selects the newest learned result. The internal report filename `reports/vggt.json` remains for compatibility; its engine/model/revision fields identify the actual backend.

Both backends use the same masks, crops, rotations, confidence filtering and Poisson meshing. Download and preprocessing time are separate from inference time. A newer model is not guaranteed to be more accurate on this rig.

Verified shoe result: 64/64 views at 518 resolution, 1,842,789 filtered points and 346,940 mesh faces. Model inference plus export took 75.23 seconds, excluding capture, transfer, preprocessing and first-time model download. The geometry still has errors around the upper and laces.
