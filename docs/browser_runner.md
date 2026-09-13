The active engine is now **VGGT**, including new captures and reruns of existing scans. Use the brightness, image-limit and resolution controls in Reconstruction settings. See [the VGGT workflow](vggt.md); COLMAP instructions below describe the retained legacy backend.

# Browser runner and repeatable physical pipeline

## Start the services

On the Pi, from its repository (once per node startup):

```bash
.venv/bin/python -m pi_node.app --host 0.0.0.0 --port 8000 --backend rpicam
```

On Windows, double-click `Launch Scanner.cmd` in the repository. The runner opens
http://127.0.0.1:49848/ with the library at `scans/received`. Alternatively:

```powershell
.\.venv\Scripts\python.exe -m windows_client.launcher
```

Connect, capture empty backgrounds when needed, replace the object, start the turntable,
and click **Capture object + reconstruct**. Leave the server window running.
No COLMAP GUI operation is required: the runner finds the local executable and runs it.
If detection fails, install COLMAP and set `COLMAP_PATH` before launching the server.
The detected status appears under Reconstruction settings.

## Equivalent end-to-end CLI commands

Run these in PowerShell from the repository. Remove the object for the first command.
Keep camera positions, lighting, focus and crop settings unchanged between backgrounds and scans.
Skip background capture when the existing references still match.

```powershell
.\.venv\Scripts\python.exe -m windows_client.client --node http://192.168.127.145:8000 --capture-backgrounds --capture-profile quad-sharp --focus-mode manual --lens-position 7.05 --camera-timeout-ms 1000 --awb auto
```

Replace the object and start the physical table at one rotation per minute:

```powershell
.\.venv\Scripts\python.exe -m windows_client.client --node http://192.168.127.145:8000 --full-scan --steps 16 --rotation-seconds 60 --focus-mode manual --lens-position 7.05 --camera-timeout-ms 1000 --awb auto --run-colmap --no-viewer
```

Open the runner and select the new scan. `--full-scan` applies the quad-sharp profile by default:
4624×3472 combined capture, matching `4624:3472:10` sensor and viewfinder modes,
ZSL, JPEG quality 95. Four 2312×1736 views are split from each combined frame.
Manual focus 7.05 was measured on this rig; it is not a universal setting.

Retry COLMAP on a prepared scan without recapturing (replace the example ID):

```powershell
.\.venv\Scripts\python.exe -m photogrammetry.colmap_runner --workspace "scans/received/scan_YYYYMMDD_HHMMSS_ID/outputs/full_photogrammetry"
```

## Outputs and camera identity

```text
scans/received/<scan_id>/
  raw_combined/                         preserved Pi images
  raw/                                  split camera images
  backgrounds/                          matching references
  outputs/inspection/                   combined/split contact sheets
  outputs/full_photogrammetry/
    images/ and masks/                  prepared reconstruction input
    colmap/inputs/images/cam_01/…       same physical camera across time
    colmap/inputs/masks/cam_01/…        matching mask folder structure
    colmap/database.db                  four shared camera identities
    colmap/sparse/<model>/              native COLMAP reconstruction
    colmap/dense_<attempt>/             dense/mesh PLY outputs
    reports/sparse_view.json            actual sparse points for browser
    reports/colmap.json                 status, camera audit, metrics
    reports/run_report.md              capture/quality/reconstruction report
    logs/                              per-stage COLMAP stdout/stderr
    realitycapture/                    import instructions and image index
```

Retries use isolated COLMAP attempt directories; rebuilding preparation archives the old
workspace. Physical inputs are never deleted automatically. The browser's Done action only
removes disposable synthetic/mock scans. Runtime images, databases and logs are Git-ignored.

Each physical camera gets its own shared intrinsics across all positions. Different cameras
are not forced to share identical numeric values. No measured calibration is invented, and
intrinsics are re-estimated per scan. Autofocus or crop changes can violate the fixed-intrinsics
assumption, so locked focus is the browser default.

The browser reports the largest selected sparse model, not the sum of disconnected models.
Incomplete alignment remains visible as partial; a dense result may still contain background
and needs inspection. Phase 1 displays up to 50,000 actual sparse points. Phase 2 displays a simplified surface with up to 100,000 triangles; the complete PLY stays on disk and is downloadable.

The browser server is loopback-only and runs one job at a time. Page reloads can reconnect to
that job; closing the server cannot. Downloads verify file hashes and can resume from the Pi
scan ID. There is no remote motor control, capture cancellation, or automatic texture fix.


## Mask crops, orientation, and two reconstruction phases

The full pipeline now defaults to cropping images to foreground masks, with a **15% total
bounding-box enlargement** (7.5% on each side). Adjust Crop enlargement in the browser;
10–15% is a useful starting range. Cropping does not upscale pixels or create extra detail.
The mask is generated by comparing each capture with its matching empty background, then
cleaned to remove disconnected specks outside the dominant foreground cluster. This is a
single-object foreground heuristic, not a semantic recognition model: inspect masks for shadows,
missing translucent parts, or disconnected object pieces. Missing/empty masks retain the full
frame with a warning; all-empty/all-full masks block reconstruction validation.

Each physical camera uses the union of its cleaned masks across every position. Every image
and mask from that camera receives the **same crop rectangle**, so image dimensions and
intrinsics remain shared. Some views consequently have more border than others. Reframing
every image independently would shift its principal point independently. The per-camera crop,
rotation and source/output dimensions are saved in `reports/crops.json` and `workspace.json`.
With no measured calibration, the initial focal estimate uses the original frame size, and
its approximate principal point is shifted by the crop offset; those guesses are not calibration.

New combined captures are split into four camera views and rotated **90 degrees clockwise**
using `config/quad_split.json`. Matching split backgrounds rotate identically. The original
combined JPEG remains untouched. Existing scans are oriented in their reconstruction workspace;
rotation metadata prevents applying 90 degrees twice. The browser orientation selector controls
the final reconstruction orientation. Use **Prepare + reconstruct** to apply changed crop,
threshold or rotation settings; **Retry COLMAP** reuses the existing prepared inputs.

- **Phase 1 — sparse:** split/orient, inspect sharpness, generate and clean masks, crop,
  extract features, match, solve the cameras. The sparse point cloud appears when this finishes.
- **Phase 2 — full mesh:** reuse the phase 1 solution, undistort images and matching masks,
  estimate dense depth maps, fuse foreground points, and run Poisson or Delaunay surface
  reconstruction. The actual mesh appears in its own orbit/zoom viewer, with a full PLY download.
  Poisson uses depth 10 and trim 5 for this demo; the stricter default trim removed all faces from the tested partial scan. Rebuilding phase 2 at the same resolution reuses completed fused points and reruns only meshing. Automatic continuation is checked by default; uncheck it to stop after phase 1, then click
  **Build phase 2 mesh** when ready. A failed mesh phase retains the sparse result for inspection.

COLMAP dense stereo needs a compatible CUDA build/GPU. The dense resolution setting controls
runtime and GPU memory. Phase 2 cannot recover object surfaces that phase 1 did not align;
partial sparse registration is reported even when a mesh is produced. Mask fusion prevents
background pixels from becoming fused points, but wrong masks or depth estimates can still
produce surface artifacts. Mesh output is geometry, not a UV-textured asset.

Progress bars show file counts for capture, transfer, masks, crop/rotation, export, and
supported COLMAP log counters. Stereo progress combines the photometric and geometric passes when the COLMAP log identifies the pass; otherwise it shows the current pass. The mesh phase also shows completed stages out of four. Operations without
reliable totals use an indeterminate bar; percentages are not estimated remaining time.
Mesh download into the browser has a byte-based loading percentage.

The photo filter **Cropped COLMAP inputs** displays thumbnails of the actual prepared images.
The mask/crop inspection panel and contact sheets show the transformations before reconstruction.
The full input images remain available under `outputs/full_photogrammetry/images/`.

To prepare an existing scan and run only phase 1 from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m photogrammetry.workspace --scan "scans/received/SCAN_ID" --run-colmap --crop-padding 0.15 --rotation 90
```

Then run phase 2 on that same prepared solution:

```powershell
.\.venv\Scripts\python.exe -m photogrammetry.dense_runner --workspace "scans/received/SCAN_ID/outputs/full_photogrammetry" --mesher poisson --max-image-size 1600
```

COLMAP references: [shared intrinsics](https://colmap.github.io/faq.html),
[dense reconstruction and meshing](https://colmap.github.io/tutorial.html),
[command-line pipeline](https://colmap.github.io/cli.html).

Browser mesh reduction uses vertex clustering to preserve surface connectivity, not random face sampling. The downloadable PLY retains every original face.
