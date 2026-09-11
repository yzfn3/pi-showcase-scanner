"""Background subtraction and coarse orthographic silhouette carving."""
import logging
import time
import numpy as np
from PIL import Image, ImageFilter

from preview3d.geometry import basis, dot
from preview3d.export import surface, export_mesh, regularize_edges
from preview3d.selection import select_frames
from windows_client.scan import write_json

LOG = logging.getLogger(__name__)


def validate_grid(grid):
    if type(grid) is not int or not 12 <= grid <= 160:
        raise ValueError("Grid must be an integer between 12 and 160")
    return grid


def foreground(scan, frame, camera):
    with Image.open(scan / frame["file"]) as original:
        original_size = original.size
        rgb = original.convert("RGB")
        crop = camera.get("preview_crop")
        if crop:
            if crop[2] > original.width or crop[3] > original.height:
                raise ValueError(f"preview_crop exceeds image dimensions: {frame['file']}")
            rgb = rgb.crop(crop)
        rgb.thumbnail((640, 640))
        pixels = np.asarray(rgb, dtype=np.int16)
    if camera.get("background"):
        with Image.open(scan / camera["background"]) as bg:
            if bg.size != original_size:
                raise ValueError(f"Background dimensions do not match {frame['file']}")
            reference_image = bg.convert("RGB")
            if crop:
                reference_image = reference_image.crop(crop)
            reference = np.asarray(reference_image.resize(rgb.size), dtype=np.int16)
    else:
        border = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
        reference = np.median(border, axis=0)
    mask = np.max(np.abs(pixels-reference), axis=2) > 30
    # A one-pixel dilation softens JPEG edges and approximate camera estimates.
    mask = np.asarray(Image.fromarray(mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3))) > 0
    fraction = float(mask.mean())
    if not 0.002 < fraction < 0.95:
        raise ValueError(f"Unusable silhouette in {frame['file']} ({fraction:.1%} foreground)")
    return mask


def reconstruct(scan, manifest, grid=96, max_frames=0):
    started = time.perf_counter()
    validate_grid(grid)
    extent = manifest["scene_extent_m"]
    axis = (np.arange(grid, dtype=np.float32)+0.5)*(extent/grid)-extent/2
    points = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    alive = np.ones(len(points), dtype=bool)
    cameras = {c["id"]: c for c in manifest["cameras"]}
    frames = manifest["frames"]
    selected = select_frames(manifest, max_frames)
    out = scan / "outputs" / "quick"
    out.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(selected):
        camera = cameras[frame["camera_id"]]
        mask = foreground(scan, frame, camera)
        if i == 0:
            Image.fromarray(mask.astype(np.uint8)*255).save(out / "silhouette.png")
        right, up, _ = basis(camera, frame["angle_deg"])
        current = points[alive]
        height, width = mask.shape
        scale = width/camera["ortho_width_m"]
        u = np.floor(dot(current, right)*scale + width/2).astype(int)
        v = np.floor(-dot(current, up)*scale + height/2).astype(int)
        valid = (u >= 0) & (u < width) & (v >= 0) & (v < height)
        keep = np.zeros(len(current), dtype=bool)
        keep[valid] = mask[v[valid], u[valid]]
        alive[np.flatnonzero(alive)] = keep
        if not alive.any():
            raise ValueError("Carving produced no voxels. Check masks, camera angles, field width, and object centering.")
    volume = regularize_edges(alive.reshape(grid, grid, grid))
    # Finer grids expose pixel-sized stairs; allow more passes at smaller cells.
    smooth_iterations = max(8, grid // 5)
    positions, normals = surface(volume, extent, smooth_iterations)
    export_mesh(out, positions, normals)
    stats = dict(grid=grid, voxels=int(volume.sum()), triangles=len(positions)//3,
                 frames_used=len(selected), frames_total=len(frames),
                 max_frames=max_frames, frame_files=[f["file"] for f in selected],
                 seconds=round(time.perf_counter()-started, 3), method="smoothed orthographic visual hull",
                 surface_version=2, voxel_size_mm=round(extent/grid*1000, 3),
                 smooth_iterations=smooth_iterations,
                 units="meters", approximate_scale=True)
    write_json(out / "stats.json", stats)
    LOG.info("Quick preview ready in %.3f s: %d voxels, %d triangles", stats["seconds"], stats["voxels"], stats["triangles"])
    return stats
