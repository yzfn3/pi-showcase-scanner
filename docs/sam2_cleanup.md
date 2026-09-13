# SAM 2 masks and mesh cleanup

The local web runner now supports four phases: object masks, learned geometry, surface reconstruction, and cleanup. This has been run on all 64 images in `scan_20260912_223440` with WorldMirror 2.0.

## Use from the browser

1. Select a saved scan, or use the existing capture/import controls.
2. Set **Object masks → SAM 2.1 (AI)**. Background subtraction remains available.
3. Enable **Automatically clean the completed mesh** and choose smoothing: off, light, or stronger.
4. Click **Run reconstruction again**, or **Capture object + reconstruct** for a new physical scan.
5. Expand **Inspect masks used for reconstruction**. In the photo gallery select **Masked inputs actually processed** to inspect the exact masked/resized inputs, rather than unprocessed photographs.
6. After completion, use **Show in mesh viewer** to switch between original and cleaned geometry. The download link follows the selection. **Clean existing mesh** reruns cleanup without recapturing, segmenting, or reconstructing.

All phases report progress. The original capture files, original mesh, and previous reconstruction folders are retained.

## How segmentation works

SAM 2.1 Hiera Small runs on CUDA in an isolated process before cropping. Matching background subtraction provides the dominant object region and an interior positive point. SAM refines the object mask; candidate selection combines its prediction score with overlap with the background seed. The final masks drive cropping and are applied to the model inputs.

For imported scans without backgrounds, set **Object region if no background** to normalized `x,y,width,height` in the original split image coordinates. It is a box prompt, not a text/object detector. The same fallback box applies to all images, so keep the subject within it across the sequence. Inspect the masks: automatic segmentation can miss thin laces or select the wrong subject. Implausibly empty or full-image masks fail explicitly.

The model is `facebook/sam2.1-hiera-small`, revision `ee5bba1d82bb8749febdf90f45e84b687142ba03`. Source: https://github.com/facebookresearch/sam2 at `2b90b9f5ceec907a1c18123530e92e794ad901a4`. Weights download once; photographs are not uploaded. On another Windows installation, install the existing CUDA/VGGT dependencies and run `scripts/setup_sam2.ps1`. The optional custom CUDA extension is disabled; image prediction uses the official PyTorch path.

## What cleanup does

This is **AI-mask-guided geometric cleanup**, not a generative mesh model. It projects mesh vertices into the predicted cameras, checks their support in the object masks, removes unsupported faces, removes small disconnected fragments, repairs duplicate/degenerate primitives, and optionally applies light Taubin smoothing. With background masks instead of SAM, the report identifies the operation as silhouette-guided geometric cleanup.

Cleanup never overwrites `mesh.ply`. It writes `mesh_clean.ply` and a separate viewer artifact. A safety check rejects cleanup if it would remove more than half the original faces. Unobserved geometry is not invented. Wrong masks/poses can still remove real details, so compare original and cleaned results. Smoothing is not evidence of increased geometric accuracy.

## Output files

Inside the chosen engine workspace (`outputs/worldmirror2` or `outputs/vggt`):

- `masks/`: masks used for cropping/reconstruction.
- `masks_before_sam2/`: background seed masks before SAM refinement.
- `model_inputs/`: exact masked and resized images submitted to the model.
- `reports/sam2.json`: model revision, per-image prompt, scores and coverage.
- `reports/mask_contact_sheet.jpg`: final mask previews.
- `mesh.ply`: original surface.
- `mesh_clean.ply`: cleaned surface.
- `cleanup/reports/mesh_view.json`: separate cleaned viewer geometry.
- `reports/cleanup.json`: operations, face counts, settings and limitations.
- `logs/sam2.log`: segmentation progress and errors.

The verified shoe run produced 64 SAM masks. Cleanup reduced the original 390,132-face mesh to 382,032 faces: 6,393 mask-inconsistent faces and 1,707 fragment faces removed. These counts describe operations, not an accuracy score.
