"""Manifest loading, boundary checks, and immutable input fingerprints."""
import hashlib
import json
import math
import re
from pathlib import Path

ID_PATTERN = re.compile(r"scan_\d{8}_\d{6}(?:_[A-Za-z0-9]+)?")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def safe_file(root, relative, directory):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Manifest paths must be relative and use forward slashes")
    path = Path(relative)
    base = (Path(root) / directory).resolve()
    resolved = (Path(root) / path).resolve()
    if path.is_absolute() or not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError(f"Missing or unsafe {directory} file: {relative}")
    return resolved


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number between {low} and {high}")


def capture_frames(manifest):
    """All captured photos, including unsplit mosaics kept for later processing."""
    return manifest.get("frames", []) + manifest.get("combined_frames", [])


def load_manifest(scan):
    scan = Path(scan).resolve()
    m = json.loads((scan / "manifest.json").read_text(encoding="utf-8"))
    return validate_manifest(scan, m)


def validate_manifest(scan, m):
    """Validate metadata and files before a receiver publishes manifest.json."""
    scan = Path(scan).resolve()
    if not isinstance(m, dict):
        raise ValueError("Manifest must be a JSON object")
    if m.get("schema_version") != 1 or m.get("status") != "complete":
        raise ValueError("Only schema_version 1 scans with status 'complete' can run")
    if not ID_PATTERN.fullmatch(m.get("scan_id", "")):
        raise ValueError("Invalid scan_id")
    if m.get("projection") != "orthographic":
        raise ValueError("The MVP preview requires projection: orthographic")
    if m.get("units") != "meters":
        raise ValueError("The MVP preview requires units: meters")
    if m.get("rotation_direction", "positive_y_right_hand") != "positive_y_right_hand":
        raise ValueError("Convert frame angles to positive_y_right_hand rotation")
    number(m.get("scene_extent_m"), "scene_extent_m", 0.001, 100)
    number(m.get("rotation_period_s"), "rotation_period_s", 0.001, 86400)
    cameras = m.get("cameras", [])
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 32:
        raise ValueError("Expected 1-32 camera definitions")
    ids = set()
    for c in cameras:
        if not isinstance(c, dict):
            raise ValueError("Each camera must be an object")
        cid = c.get("id", "")
        if not re.fullmatch(r"cam_\d{2}", cid) or cid in ids:
            raise ValueError("Camera IDs must be unique cam_NN values")
        ids.add(cid)
        number(c.get("azimuth_deg"), "azimuth_deg", -360, 360)
        number(c.get("elevation_deg"), "elevation_deg", -89, 89)
        number(c.get("ortho_width_m"), "ortho_width_m", 0.001, 1000)
        if "preview_crop" in c:
            crop = c["preview_crop"]
            if (not isinstance(crop, list) or len(crop) != 4 or
                    any(type(v) is not int or v < 0 for v in crop) or
                    crop[2] <= crop[0] or crop[3] <= crop[1]):
                raise ValueError("preview_crop must be [left, top, right, bottom] pixel bounds")
        if c.get("background"):
            safe_file(scan, c["background"], "backgrounds")
    raw = m.get("frames", [])
    combined = m.get("combined_frames", [])
    if not isinstance(raw, list) or not isinstance(combined, list):
        raise ValueError("Frame collections must be lists")
    if combined and (not m.get("combined_quad_output") or m.get("split_combined_output") or raw):
        raise ValueError("Unsplit combined scans must declare combined_quad_output and contain no individual frames")
    frames = capture_frames(m)
    if not isinstance(frames, list) or not 1 <= len(frames) <= 10000:
        raise ValueError("Expected 1-10000 frames")
    paths = set()
    for f in frames:
        if not isinstance(f, dict):
            raise ValueError("Each frame must be an object")
        if f.get("camera_id") not in ids:
            raise ValueError("Frame refers to an undefined camera")
        number(f.get("angle_deg"), "angle_deg", -360000, 360000)
        number(f.get("timestamp_s"), "timestamp_s", 0, 86400)
        path = safe_file(scan, f.get("file"), "raw_combined" if combined else "raw")
        if path in paths:
            raise ValueError("Duplicate frame file")
        paths.add(path)
    for key, collection in (("raw_files", raw), ("raw_combined_files", combined)):
        if key in m and m[key] != [f["file"] for f in collection]:
            raise ValueError(f"{key} does not match frame records")
    return m


def fingerprint(scan, manifest, grid, max_frames=0):
    h = hashlib.sha256(json.dumps([manifest, grid, max_frames, "preview-v6-capture"], sort_keys=True).encode())
    paths = {f["file"] for f in capture_frames(manifest)}
    paths.update(c["background"] for c in manifest["cameras"] if c.get("background"))
    for name in sorted(paths):
        h.update(name.encode())
        h.update((Path(scan) / name).read_bytes())
    return h.hexdigest()
