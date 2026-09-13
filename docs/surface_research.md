# Improving the shoe surface

## What was wrong

The 1.68-million-point display is a dense colored point cloud, not a sparse SfM model. It directly displays the photographic colors. The old mesh path pooled noisy depth points, estimated normals without their observing cameras, ran Poisson reconstruction, and displayed face normals with strong extra shading. The photographs already contain lighting, so the viewer effectively shaded them twice. SAM-guided trimming removed unsupported fragments but did not solve this surface-fitting problem.

## Methods reviewed (September 12, 2026)

There is no verified universal "best cleanup model" that guarantees a faithful scan from these inputs. These methods solve different tasks:

| Method | Relevance and decision |
|---|---|
| [NKSR](https://github.com/nv-tlabs/NKSR) | Neural surface reconstruction from noisy oriented points; a direct candidate. Its [build](https://github.com/nv-tlabs/NKSR/blob/public/package/setup.py) requires a CUDA compiler and supports x86-64 Linux. This Windows host has no WSL or CUDA compiler. Not installed or claimed as tested. |
| [NoKSR](https://theialab.github.io/noksr/) | 2025 transformer-based point-cloud surface reconstruction, evaluated on large outdoor/indoor datasets. Its benchmark does not establish superiority for this shoe. Reviewed, not run. |
| [2D Gaussian Splatting](https://github.com/hbb1/2d-gaussian-splatting) | Optimizes oriented surface elements using posed photographs, with mesh extraction. More appropriate for photo consistency than arbitrary mesh smoothing; requires custom CUDA extensions and reliable poses. Reviewed, not run. |
| [2D-SuGaR](https://github.com/Divyam10/2D-SuGaR) | Eurographics 2026 method combining depth/normal priors, surface splats and refinement. Published results make it a promising future reconstruction backend, not a drop-in pretrained cleanup filter. Requires compatible C++/CUDA build tooling. Reviewed, not run. |
| [2D Triangle Splatting](https://github.com/GaodeRender/triangle-splatting) | Direct differentiable triangle training from images. Another reconstruction approach rather than a guaranteed repair model. Reviewed, not run. |
| [SAM 3D Objects](https://github.com/facebookresearch/sam-3d-objects) | Predicts geometry and appearance from a masked single image. Can infer hidden content; that is not evidence that hidden details match the real shoe. Reviewed, not run. |
| [Hunyuan3D 2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) | Image-conditioned generative geometry and PBR materials. Supports Windows but its full painting pipeline needs additional compiled components. Reviewed, not run. |
| [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) | Recent image-to-3D generative model with textured output. Official implementation is tested on Linux. The separately maintained [trellis.cpp v0.6.0](https://github.com/pwilkin/trellis.cpp/releases/tag/v0.6.0) provides a portable Windows CUDA runtime, allowing a local comparison without changing the working Python/Torch environment. Exposed as an explicitly generative alternative. |
| [TSDF depth fusion](https://open3d.org/docs/release/tutorial/t_reconstruction_system/integration.html) | Fuses noisy depths using camera poses into a shared surface. Implemented and tested here using the existing WorldMirror predictions. This is geometric fusion of AI predictions, not a new pretrained AI cleanup model. |

## Changes actually implemented

- Fuse the original learned depth maps using their camera matrices, masks and confidence filtering, instead of relying exclusively on globally estimated normals.
- Filter each depth map with an edge-preserving bilateral filter before fusion. Mesh fragment removal and adjustable Taubin smoothing follow fusion.
- Project color from front-facing, depth-supported photographs; avoid averaging every view into a blurry texture.
- Export area-weighted vertex normals. The browser offers photo colors, soft lighting, and neutral clay so texture cannot conceal geometric defects.
- Rebuild phase 2 from `predictions.npz` without recapturing or rerunning the neural model.
- Retain the original Poisson surface, conservative cleanup, fused surface and optional generated alternative as separately selectable results.

Three configurations were visually compared through four of the actual predicted cameras. Baseline Poisson silhouette IoUs were 0.875, 0.785, 0.827, 0.807; fusion at resolution 192 / truncation 8 / smoothing 6 gave 0.894, 0.806, 0.828, 0.876. This is input-mask agreement under predicted poses, **not ground-truth 3D accuracy**. Higher resolution 256 did not restore missing lace geometry. The 192 result is the default because it is cleaner and smaller.

A fourth fusion experiment shared median intrinsics across each physical camera sequence; it did not improve the four-view comparison overall. It was not promoted to the production path.

## Actual TRELLIS tests and remaining limitations

The following runs used the real masked `step_008_cam_03.jpg`, entirely locally:

| Resolution / seed | Total generation and viewer export | Mesh faces | Visual finding |
|---|---:|---:|---|
| 512 / 42 | 24.89 s | 146,536 | Clean recognizable shoe; the best overall generative comparison in this test. Some sole, lace and stripe details differ. |
| 1024 / 42 | 81.14 s | 283,940 | Cleaner detailed surface but invented curved stripe motifs. Higher resolution did not improve faithfulness. |
| 512 / 7 | 25.91 s | 142,460 | Closer side stripes but an invented pink tongue detail. Not selected as the preferred comparison. |

A fourth completed browser run used `step_008_cam_01.jpg` at 512 / seed 42 (22.66 seconds). It was also inspected from three angles; its stripe geometry and sole edge were less faithful. Each source is included in the result selector to distinguish otherwise identical settings.

These times include loading and browser conversion, but exclude the one-time weight download. The original scan, every generated GLB, its source cutout, and logs remain available. The web selector lists each resolution/seed separately.

`scripts/experiment_shape_prior.py` records another rejected experiment: rigidly align the generated shape to the fused scan and project real photo colors, optionally fitting it with bounded, smoothed nearest-surface displacements. The rigid variant disagreed more with the source silhouettes; deformation recovered silhouette overlap but introduced texture instability and surface distortion. It is deliberately not wired into the production runner.

**The exact-replica target was not achieved.** Depth fusion is the more faithful choice for the existing multi-view capture; TRELLIS is the cleaner-looking generated alternative. Neither matching silhouettes under predicted poses nor a polished generative mesh establishes measured accuracy. The original cleanup was conservative geometry filtering, not a pretrained neural cleanup model.

Reproduce the image comparison with `python -m scripts.compare_surfaces --workspace <engine-workspace> --meshes <original-mesh.ply> <candidate-mesh.ply> --output comparison.jpg`. Each row shows the actual model input, then photo-colored and clay renderings from the same predicted camera.

## Web use

Open the saved scan. In phase 2, choose resolution and smoothing and click **Rebuild surface from saved depth**. Use **Show in mesh viewer** to compare surfaces and **Appearance → Clay** to inspect shape. The download follows the selected result.

The optional **TRELLIS.2 image to 3D** section selects one processed image, resolution and seed, and generates a separate textured GLB. It is labeled as a generated alternative, not a multi-view scan. Source photos and the reconstructed point cloud are retained.

For another Windows installation, after the normal scanner dependencies:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-trellis.txt
.venv\Scripts\python.exe -m scripts.setup_trellis
```

The installer downloads pinned Q8 GGUF weights and the checksum-verified portable runtime into ignored `models/trellis2` and `tools/trellis-runtime` directories. No photos are uploaded. TRELLIS jobs have a 30-minute timeout and save their input, log, GLB, viewer texture and report in a new `trellis_<id>` folder each time.
