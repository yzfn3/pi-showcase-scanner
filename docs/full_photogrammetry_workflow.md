# Full photogrammetry workflow

## Physical setup checklist

- Update this repository on both Windows and the Pi, install requirements, and restart the Pi API. The new POST `/api/v1/backgrounds/check` endpoint validates matching references before full capture.
- One Arducam device must already produce combined 2x2 JPEGs. Software does not enable hardware mosaic routing.
- Secure all cameras and use stable diffuse lighting, a matte background and a centered object. Remove moving clutter and minimize table texture/shadows.
- The turntable must run at the requested period. No encoder, automatic motor control, calibrated scale or verified camera poses are supplied.
- Combined 3840x2160 yields approximately 1920x1080 per view; 1920x1080 yields only 960x540. Actual dimensions are read from JPEGs and reported. Hardware may reject a size; the pipeline does not silently substitute it.

## Focus checklist

Inspect full-size split images before a long capture. Use autofocus-on-capture and a settling timeout. Auto focus may breathe across views; for repeatability, find a sharp manual lens position and lock it after testing. Background and scan must use the same focus/exposure/AWB/dimension settings. Auto exposure and white balance changes can impair subtraction. `--awbgains 1.0,1.0` is available as an advanced option; automatic AWB is the default.

Sharpness is measured using Laplacian variance on grayscale images resized to at most 1024 pixels. Scores below 40 trigger a heuristic warning; texture affects scores, so inspect the photos rather than treating the score as a guarantee.

## Background checklist

Remove only the object; leave the table and cameras in place. Capture the empty table with the same lighting and camera settings. Restore the object afterward. The node verifies saved reference checksums and settings. Full scan reuses matching references or asks you to remove the object before taking new ones, then to replace it. In a noninteractive terminal, capture references separately first. `--capture-backgrounds-first` forces replacement. `--skip-backgrounds` explicitly disables references and generates a turntable warning; no pretend masks are created.

## Object selection

Good starting objects are matte, rigid, opaque, richly textured, and stationary relative to the table. Poor candidates include glass, polished metal, shiny plastic, thin foliage, moving fabrics, repeating patterns, and plain untextured surfaces. Capture more overlapping views and additional elevations for undersides/occlusions. Masks cannot create missing texture or reveal unseen geometry.

## Setup and commands

From the repository root, activate the Windows virtual environment. The existing requirements are sufficient for preparation; COLMAP is a separate optional local installation.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m windows_client.client --node http://192.168.127.145:8000 --capture-backgrounds --capture-width 3840 --capture-height 2160 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 1000 --awb auto
python -m windows_client.client --node http://192.168.127.145:8000 --full-scan --no-run-colmap --steps 24 --rotation-seconds 60 --capture-width 3840 --capture-height 2160 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 1000 --awb auto
python -m windows_client.client --node http://192.168.127.145:8000 --full-scan --run-colmap --steps 24 --rotation-seconds 60 --capture-width 3840 --capture-height 2160 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 1000 --awb auto
python -m windows_client.client --node http://192.168.127.145:8000 --full-scan --steps 36 --rotation-seconds 90 --capture-width 4624 --capture-height 3472 --camera-timeout-ms 1500
```

`--full-scan` alone supplies 24 steps, 60 seconds, combined 3840x2160, autofocus auto/on-capture, 1000ms and AWB auto. It preserves originals and attempts sparse COLMAP only when available. Quad splitting is on Windows; the reserved Pi split flag remains false on the wire. Existing `--start-scan --quality fast` continues to use the quick viewer. Full mode opens its output folder; `--no-viewer` or `--no-browser` prints paths without opening it. `--full-workspace-root PATH` places each scan's workspace under PATH/scan_id.

For downloaded scans (replace `<scan_id>`):

```powershell
python -m photogrammetry.workspace --scan scans/received/<scan_id>
python -m photogrammetry.masks --scan scans/received/<scan_id> --threshold 25 --inspect
python -m photogrammetry.colmap_runner --workspace scans/received/<scan_id>/outputs/full_photogrammetry
python -m preview3d.quad_splitter --scan scans/received/<scan_id> --make-mapping-sheet
```

Preparation and mask CLI reruns archive the previous workspace to a sibling `full_photogrammetry_run_*` folder. The newest workspace stays at the standard path. Archives preserve previous outputs and manual edits; they consume disk space. Original captures are never deleted by full mode. COLMAP reruns use a separate attempt when a database/model exists, avoiding stale features after edits. Do not edit inputs during processing.

## Output folders

Under `scans/received/<scan_id>/`:

- `raw_combined/`: unchanged source JPEGs.
- `raw/`: individual camera JPEGs with stable step/camera names.
- `outputs/inspection/`: combined/split contact sheets, sharpness contact sheet, optional camera mapping sheet.
- `outputs/full_photogrammetry/images/`: reconstruction input copies.
- `outputs/full_photogrammetry/masks/`: full-size `image.jpg.png`, white foreground and black excluded background.
- `outputs/full_photogrammetry/colmap/`: database created only by COLMAP, sparse models, optional dense output and isolated retries.
- `outputs/full_photogrammetry/realitycapture/images/`: geometry images and `image.jpg.mask.png` mask layers; import instructions and CSV are one level up.
- `outputs/full_photogrammetry/reports/`: Markdown/JSON report, mask coverage JSON, mask sheet, before/after pairs, sharpness CSV, command plan.
- `outputs/full_photogrammetry/logs/`: stdout/stderr for each COLMAP stage.

The manifest records settings, actual dimensions, estimated angles, masks, sharpness, status and output paths. `manifest.pre_full.json` and integrity receipts support repeat verified downloads. The quick viewer remains available and displays the full workspace/report path; sparse PLY/bin models are not passed off as meshes. Open PLY meshes/clouds in COLMAP or another compatible local viewer.

## Masks and mapping

Masks use per-camera background subtraction with max RGB difference, Gaussian blur, binary opening and closing. CLI parameters: `--threshold 25 --blur 1 --opening 3 --closing 5`. Full client exposes `--mask-threshold` and `--mask-inspect` (inspection is already enabled by default). Missing references generate warnings; all-black/all-white masks block reconstruction. Coverage below 1% or above 95% warns. Coverage does not establish correctness: shadows, changed exposure or table edges can still be included.

Use the before/after images and mask sheet to verify the object is retained and the room/table suppressed. Adjust threshold conservatively. For difficult objects, edit the full-resolution PNG masks manually before running the standalone COLMAP command; do not rerun mask generation afterward.

Edit `config/quad_split.json` or use `--quad-config PATH` to change camera order, normalized crops and clockwise rotations. Mapping sheets label the first step's four views. The splitter defaults are assumptions; no rig calibration is claimed. Run the splitter again after mapping changes, then rebuild the workspace.

## COLMAP workflow

Install a local Windows distribution from https://colmap.github.io/install.html. Add COLMAP to PATH or set `$env:COLMAP_PATH = 'C:\COLMAP\COLMAP.bat'`. The standalone runner also accepts `--colmap PATH`. Detection checks PATH and C:/COLMAP and Program Files/COLMAP. No automatic download or installation occurs.

Sparse stages are feature_extractor, exhaustive_matcher (or `--matcher sequential`) and mapper. CPU feature extraction/matching is selected using options supported by the installed executable. Each image has independent intrinsics because autofocus/crops can vary; calibrated camera grouping is a future refinement. Exhaustive matching is preferable for interleaved quad-camera names; sequential matching can miss cross-camera links.

Masks use COLMAP's `ImageReader.mask_path` with `image.jpg.png` naming. If masks exist but the installed executable lacks this option, the runner stops with an error rather than dropping them. The report lists commands actually run, while COLMAP_COMMANDS.txt holds a command plan. Failure or no registered sparse points is reported; no fake database/model is generated. Missing COLMAP produces `prepared_only` and exact retry instructions.

Add `--dense` for image_undistorter, patch_match_stereo and stereo_fusion. Dense stereo generally requires a CUDA-capable COLMAP installation and can take substantial time/memory. Optional `--mesher poisson` or `--mesher delaunay` is available on the standalone runner with --dense. Each stage has a configurable timeout (default 7200 seconds). Sparse masks suppress background features; they do not guarantee background-free dense stereo. Inspect/clean the fused cloud before using a mesh. Multiple sparse components are retained; dense processing uses the largest by image-record file size, and may still need alignment review.

Official CLI and masks references: https://colmap.github.io/cli.html and https://colmap.github.io/faq.html

## RealityCapture / RealityScan

Open `realitycapture/README_IMPORT.txt`. Import the images folder, including named mask layers, and enable masks for alignment and meshing. Start with High feature detection quality; inspect aligned cameras and components before reconstruction. The image_index.csv maps every crop to its original file and estimated angle. Neither application is required for export. Layer reference: https://rshelp.capturingreality.com/en-US/tools/imglayers.htm

## Troubleshooting

| Symptom | Action |
|---|---|
| Blurry images | Inspect sharpness CSV/full-size images; increase settling timeout, improve light, shorten exposure or test manual focus. Reduce step count or slow rotation if capture overruns. |
| Split images too small | Increase combined resolution; each dimension is approximately halved. Inspect actual dimensions in the report. |
| Empty masks | Check empty-table references and matching settings; lower threshold. All-black masks block COLMAP. |
| Masks include background | Stabilize lighting/exposure, remove shadows/clutter, increase threshold carefully or manually edit masks. |
| Too few matches | Improve texture, focus and overlap; try exhaustive matching. Check masks did not remove the object. |
| Room reconstructed | Fixed background dominated; correct masks before rerunning with a fresh database. |
| Shiny/transparent/plain object | Use a more suitable test object; standard image matching may fail regardless of image count. |
| Turntable mismatch | Verify constant rotation and object stability; suppress background; timestamps are estimates, not motor feedback. |
| Wrong camera order | Inspect mapping sheet, edit quad configuration/rotation, re-split and prepare again. |
| Downloaded but no model | Read status/report: prepared_only means COLMAP is absent/disabled; sparse_complete is a cloud, not a mesh; failed means inspect logs. |
| Dense takes too long | Start with sparse; reduce dense settings externally, check CUDA support, or omit --dense. Increase --timeout only when appropriate. |

The full pipeline prepares usable local inputs and runs installed tools; it cannot guarantee successful reconstruction without adequate imagery, masks, overlap and calibration. No physical capture or actual COLMAP reconstruction is claimed by mocked subprocess tests.
