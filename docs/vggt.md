# VGGT reconstruction (active engine)

The browser runner and `windows_client.client --full-scan` use Meta's VGGT-1B. COLMAP source files and existing scan outputs are retained, but these entry points do not execute COLMAP or fall back to it. The original COLMAP modules remain available for explicit backend development.

## Windows setup

Create the normal `.venv` and install `requirements.txt`, then run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_vggt.ps1
```

This installs CUDA PyTorch 2.8, torchvision, the pinned official VGGT package, and Open3D. It needs an NVIDIA GPU and several GB for packages/model weights. The first inference downloads `facebook/VGGT-1B` to the Hugging Face cache. Photos are processed locally and are never uploaded. After the model has been cached, `HF_HUB_OFFLINE=1` enables offline loading.

Upstream source: https://github.com/facebookresearch/vggt
Model and license: https://huggingface.co/facebook/VGGT-1B
Source commit: a288dd0f14786c93483e45524328726ab7b1b4ce
Model revision: 860abec7937da0a4c03c41d3c269c366e82abdf9

## Run the existing scan

```powershell
.venv\Scripts\python.exe -m photogrammetry.vggt_runner --scan scans/received/scan_20260912_212542 --max-frames 64 --image-size 392 --gamma 1
```

Or launch `Launch Scanner.cmd`, select the scan and click **Run VGGT again**. New captures automatically use VGGT with the same Pi/background procedure. **Automatically continue to phase 2** enables a mesh; otherwise the predicted point cloud is exported. **Build phase 2 mesh** currently reruns VGGT preparation and inference before surface reconstruction, using the displayed settings.

## Brightness and image preparation

Masks are computed from unchanged captures and matching backgrounds first. Crops/orientation are applied, then a gamma curve (default 1.0) gently lifts shadows and midtones in reconstruction copies. Gamma 1.0 leaves brightness unchanged; lower values brighten more. Originals and Pi exposure settings are unchanged. Inspect **Brightened reconstruction inputs** in the photo gallery. The model receives masked, square-padded RGB inputs to suppress stationary background features in the rotating-object sequence.

All 64 images of the latest scan were used at 392x392. The image limit and resolution are adjustable in the UI. Smaller limits sample across physical cameras and the rotation, rather than choosing only the first camera. If CUDA memory runs out, use 336 pixels or fewer views; the runner reports failure rather than substituting COLMAP.

## Outputs

`scans/received/{scan_id}/outputs/vggt/` contains:

- `images/`: brightened, rotated/cropped processing images.
- `masks/`: corresponding object masks.
- `model_inputs/`: exact padded RGB inputs sent to VGGT.
- `predictions.npz`: depth, confidence, camera extrinsics/intrinsics and masks.
- `points.ply`: filtered colored VGGT point cloud.
- `mesh.ply`: Open3D Poisson surface from filtered VGGT object points (depth 8, lowest 5% density trimmed).
- `reports/vggt.json`, `run_report.json`, `run_report.md`: settings, selected images, counts and timing.
- `reports/points_view.json`, `mesh_view.json`: browser geometry.
- `logs/inference.log`: inference and surface progress.

Earlier VGGT workspaces are archived with a unique suffix on rerun; original COLMAP outputs are preserved. Model weights and generated output are ignored by Git.

## Practical limits

VGGT predicts per-image camera parameters; it does not enforce a shared calibrated intrinsic matrix for each physical camera. The processed-view count is not COLMAP registration or an accuracy score. Geometry is unscaled, can contain duplicates, holes and learned errors, and Poisson interpolation can invent connecting surfaces and is not an accuracy guarantee. Masks and a rotating subject remain challenging. Browser mesh simplification preserves the full downloadable PLY. Gamma correction improves visibility but cannot recover clipped detail or undo motion blur.

Capture brightness now uses sensor gain and shutter; processing gamma defaults to 1.0. See [capture quality](capture_quality.md).
