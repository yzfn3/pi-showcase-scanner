"""Render a union of ellipsoids into actual camera images (no canned model)."""
import argparse
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from preview3d.geometry import basis, dot
from windows_client.scan import write_json

LOG = logging.getLogger(__name__)

def random_object(seed):
    """One connected, asymmetric sculpture per seed; identical across its views."""
    rng = np.random.default_rng(int(seed, 16))
    core = rng.uniform([0.025, 0.028, 0.025], [0.055, 0.055, 0.05])
    parts = [(np.zeros(3), core)]
    for _ in range(int(rng.integers(3, 8))):
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        # Overlapping lobes remain connected to the center rather than floating.
        radius = rng.uniform(0.016, 0.04, size=3)
        center = direction * rng.uniform(0.03, 0.063)
        bridge = (core+radius)/2
        parts.extend([(center/2, bridge*.8), (center, radius)])
    return parts


def render(camera, angle, size, background, parts):
    right, up, direction = basis(camera, angle)
    pixels = ((np.arange(size) + 0.5) / size - 0.5) * camera["ortho_width_m"]
    xx, yy = np.meshgrid(pixels, -pixels)
    origin = xx[..., None]*right + yy[..., None]*up
    depth = np.full((size, size), -np.inf)
    color = background.copy()
    for center, radii in parts:
        radius = np.array(radii)
        q = (origin - center) / radius
        ray = direction / radius
        a = np.sum(ray*ray)
        b = 2*dot(q, ray)
        c = np.sum(q*q, axis=-1) - 1
        disc = b*b - 4*a*c
        t = (-b + np.sqrt(np.maximum(0, disc))) / (2*a)
        hit = (disc >= 0) & (t > depth)
        depth[hit] = t[hit]
        normal = (origin + t[..., None]*direction - center) / radius**2
        normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-8)
        shade = 0.55 + 0.45*np.maximum(0, dot(normal, direction))
        color[hit] = (shade[..., None]*np.array([38, 151, 166]))[hit].astype(np.uint8)
    return Image.fromarray(color)


def camera_configuration():
    """Approximate fixed cameras shared by local and node mock captures."""
    return [dict(id=f"cam_{i+1:02d}", azimuth_deg=az, elevation_deg=el,
                 distance_m=0.5, height_m=round(0.5*np.sin(np.radians(el)), 4),
                 ortho_width_m=0.30, background=f"backgrounds/cam_{i+1:02d}.jpg")
            for i, (az, el) in enumerate(zip((-35, -12, 12, 35), (5, 15, 25, 35)))]


def generate(root, steps=12, size=256, seed=None):
    if not 3 <= steps <= 120 or not 64 <= size <= 1024:
        raise ValueError("Use 3-120 steps and image size 64-1024")
    now = datetime.now(timezone.utc)
    seed = seed or uuid.uuid4().hex
    parts = random_object(seed)
    sid = "scan_" + now.strftime("%Y%m%d_%H%M%S")
    scan = Path(root).resolve() / sid
    if scan.exists():
        scan = scan.with_name(sid + "_" + uuid.uuid4().hex[:6])
    (scan / "raw").mkdir(parents=True)
    (scan / "backgrounds").mkdir()
    for kind in ("quick", "detailed"):
        (scan / "outputs" / kind).mkdir(parents=True)
    cameras = camera_configuration()
    manifest = dict(schema_version=1, scan_id=scan.name, status="capturing", source="synthetic",
                    created_at=now.isoformat(), projection="orthographic", units="meters",
                    rotation_period_s=60.0, rotation_direction="positive_y_right_hand",
                    scene_extent_m=0.24, cameras=cameras, frames=[], object_seed=seed)
    write_json(scan / "manifest.json", manifest)
    background = np.full((size, size, 3), [229, 233, 232], dtype=np.uint8)
    for camera in cameras:
        Image.fromarray(background).save(scan / camera["background"], quality=95)
    for step in range(steps):
        angle = step*360/steps
        for camera in cameras:
            name = f"raw/step_{step:03d}_{camera['id']}.jpg"
            render(camera, angle, size, background, parts).save(scan / name, quality=95)
            manifest["frames"].append(dict(file=name, camera_id=camera["id"], step=step,
                                            timestamp_s=step*60/steps, angle_deg=angle))
    manifest["status"] = "complete"
    write_json(scan / "manifest.json", manifest)
    LOG.info("Generated %s: %d frames, 4 cameras, %.1f s between steps", scan.name, steps*4, 60/steps)
    return scan


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("scans"))
    p.add_argument("--steps", type=int, default=12)
    p.add_argument("--size", type=int, default=256)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(generate(args.root, args.steps, args.size))
