"""Split quad captures on the laptop while retaining immutable source images."""
import argparse
import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
from sample_data.generate import camera_configuration
from windows_client.scan import ID_PATTERN, safe_file, validate_manifest, write_json

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config/quad_split.json"
SLOTS = [f"cam_{n:02d}" for n in range(1, 5)]
SHEETS = ["outputs/inspection/combined_contact_sheet.jpg", "outputs/inspection/split_contact_sheet.jpg"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def crop_box(crop, size):
    """Round shared normalized edges identically, including odd-sized images."""
    if not isinstance(crop, dict) or set(crop) != {"x", "y", "w", "h"}:
        raise ValueError("Each crop needs exactly x, y, w, h")
    x, y, w, h = [crop[k] for k in ("x", "y", "w", "h")]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x,y,w,h)):
        raise ValueError("Crop coordinates must be finite numbers")
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1.000000001 or y+h > 1.000000001:
        raise ValueError("Normalized crop must be nonempty and inside the image")
    width, height = size
    box = tuple(round(v*s) for v,s in zip((x,y,min(1,x+w),min(1,y+h)), (width,height,width,height)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Crop is empty at this image resolution")
    return box


def load_config(path=None):
    config = json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    if config.get("mode") != "quadrants":
        raise ValueError("Only mode quadrants is supported (crop rectangles are configurable)")
    order = config.get("camera_order")
    if not isinstance(order, list) or len(order) != 4 or set(order) != set(SLOTS):
        raise ValueError("camera_order must contain cam_01 through cam_04 exactly once")
    if not isinstance(config.get("crops"), dict) or set(config["crops"]) != set(SLOTS):
        raise ValueError("crops must define all four canonical quadrant slots")
    for slot in SLOTS:
        crop_box(config["crops"][slot], (100000,100000))
    rotations = config.get("rotations", {})
    if not isinstance(rotations, dict) or set(rotations)-set(SLOTS) or any(type(v) is not int or v not in (0,90,180,270) for v in rotations.values()):
        raise ValueError("rotations maps camera IDs to 0, 90, 180 or 270 clockwise degrees")
    return config


def split_views(image, config):
    for slot, camera in zip(SLOTS, config["camera_order"]):
        view = image.crop(crop_box(config["crops"][slot], image.size))
        rotation = config.get("rotations", {}).get(camera, 0)
        if rotation:
            view = view.rotate(-rotation, expand=True)
        yield camera, view


def combined_paths(scan):
    root = Path(scan).resolve()
    paths = sorted((root / "raw_combined").glob("step_*_quad.jpg"))
    for path in paths:
        if not re.fullmatch(r"step_\d+_quad\.jpg", path.name):
            raise ValueError(f"Unexpected combined filename: {path.name}")
        safe_file(root, path.relative_to(root).as_posix(), "raw_combined")
    return paths


def needs_split(scan):
    scan = Path(scan)
    if not combined_paths(scan):
        return False
    if (scan / "quad_split.pending.json").exists():
        return True
    if (scan / "manifest.json").is_file():
        manifest = json.loads((scan / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("quad_split_applied"):
            return any(not (scan / name).is_file() for name in manifest.get("split_files", []))
    raw = scan / "raw"
    return not raw.exists() or not any(raw.iterdir())


def source_manifest(scan, paths):
    backup = scan / "manifest.source.json"
    current_path = scan / "manifest.json"
    current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else None
    if backup.exists():
        source = json.loads(backup.read_text(encoding="utf-8"))
        if current and not current.get("quad_split_applied") and current != source:
            raise ValueError("Source manifest differs from its saved copy; use a fresh scan copy")
    elif current:
        source = copy.deepcopy(current)
    else:
        if not ID_PATTERN.fullmatch(scan.name):
            raise ValueError("A manifest-free scan folder must be named scan_YYYYMMDD_HHMMSS (optional suffix)")
        source = dict(schema_version=1, scan_id=scan.name, status="complete", source="imported_quad", retain=True,
                      created_at=datetime.now(timezone.utc).isoformat(), projection="orthographic", units="meters",
                      scene_extent_m=.24, rotation_period_s=60, cameras=camera_configuration()[:1], frames=[])
        source["cameras"][0].pop("background", None)
        if (scan / "backgrounds/quad.jpg").is_file():
            source["cameras"][0]["background"] = "backgrounds/quad.jpg"
        source["combined_quad_output"] = True
        source["combined_frames"] = [dict(file=p.relative_to(scan).as_posix(), camera_id="cam_01",
            step=int(re.search(r"step_(\d+)", p.name)[1]), timestamp_s=i*60/len(paths), angle_deg=i*360/len(paths))
            for i,p in enumerate(paths)]
        source["raw_combined_files"] = [f["file"] for f in source["combined_frames"]]
    if source.get("frames"):
        raise ValueError("Refusing to replace existing individual captures with quadrant crops")
    validate_manifest(scan, source)
    if {f["file"] for f in source["combined_frames"]} != {p.relative_to(scan).as_posix() for p in paths}:
        raise ValueError("Combined folder and manifest disagree; finish the transfer first")
    return source, current


def contact_sheet(rows, path):
    # Bounded sheets even for large scans: 24 evenly spaced thumbnails maximum.
    if len(rows) > 24:
        rows = [rows[round(i*(len(rows)-1)/23)] for i in range(24)]
    sheet = Image.new("RGB", (4*200, math.ceil(len(rows)/4)*150), "#eef1ef")
    draw = ImageDraw.Draw(sheet)
    for i,(file, step, camera, kind) in enumerate(rows):
        x,y = (i%4)*200, (i//4)*150
        with Image.open(file) as original:
            view = original.convert("RGB"); view.thumbnail((190,110))
        sheet.paste(view, (x+(200-view.width)//2, y+4))
        draw.text((x+5,y+116), f"step {step:03d} | {camera}", fill="black")
        draw.text((x+5,y+132), kind, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=80)


def _split(scan, config, dry_run, inspection):
    started = time.perf_counter()
    paths = combined_paths(scan)
    if not paths:
        raise ValueError("No raw_combined/step_NNN_quad.jpg images found")
    source, current = source_manifest(scan, paths)
    originals = {p.relative_to(scan).as_posix(): digest(p) for p in paths}
    backgrounds = {c["background"] for c in source["cameras"] if c.get("background")}
    if len(backgrounds) > 1:
        raise ValueError("Expected one combined background reference")
    background = next(iter(backgrounds), None)
    if background:
        originals[background] = digest(safe_file(scan, background, "backgrounds"))
    old_outputs = {}
    for name in ("quad_split_receipt.json", "quad_split.pending.json"):
        if (scan / name).exists():
            saved = json.loads((scan / name).read_text(encoding="utf-8"))
            if saved["source_hash"] != json_digest(source):
                raise ValueError("Saved source manifest was modified; refusing to resplit")
            old_outputs.update(saved["output_hashes"])
    # All crops are prepared before any original manifest or raw output changes.
    with tempfile.TemporaryDirectory(prefix=".quad_stage_", dir=scan) as staging:
        stage = Path(staging).resolve()
        assert stage.parent == scan  # Bound automatic temporary-directory cleanup.
        frames, combined_rows, split_rows = [], [], []
        cameras = camera_configuration()
        for camera in cameras:
            camera.pop("background", None)
            # Mock mosaics contain the known label footer in every quadrant.
            if source.get("mock_mode") and source["cameras"][0].get("preview_crop"):
                camera["preview_crop"] = source["cameras"][0]["preview_crop"]
        for frame in source["combined_frames"]:
            file = safe_file(scan, frame["file"], "raw_combined")
            step = frame["step"]
            combined_rows.append((file, step, "quad", "raw_combined"))
            with Image.open(file) as image:
                for cid, view in split_views(image.convert("RGB"), config):
                    relative = f"raw/step_{step:03d}_{cid}.jpg"
                    output = stage / relative; output.parent.mkdir(exist_ok=True)
                    if output.exists():
                        raise ValueError("Duplicate step numbers in combined manifest")
                    view.save(output, quality=95)
                    frames.append({**frame, "file":relative, "camera_id":cid, "source_file":frame["file"]})
                    split_rows.append((output, step, cid, "split raw"))
        if background:
            with Image.open(scan / background) as image:
                for cid, view in split_views(image.convert("RGB"), config):
                    # Unique derived names never overwrite an original per-camera reference.
                    relative = f"backgrounds/split_{cid}.jpg"
                    output = stage / relative; output.parent.mkdir(exist_ok=True)
                    view.save(output, quality=95)
                    next(c for c in cameras if c["id"] == cid)["background"] = relative
        if inspection:
            contact_sheet(combined_rows, stage / SHEETS[0])
            contact_sheet(split_rows, stage / SHEETS[1])
        updates = {p.relative_to(stage).as_posix(): digest(p) for p in stage.rglob("*.jpg")}
        for relative, checksum in updates.items():
            target = scan / relative
            if target.is_symlink() or not target.resolve().is_relative_to(scan):
                raise ValueError(f"Unsafe output path: {relative}")
            if target.exists() and digest(target) not in (checksum, old_outputs.get(relative)):
                raise ValueError(f"Refusing to overwrite unowned or modified output: {relative}")
        if dry_run:
            return dict(dry_run=True, combined_images=len(paths), split_images=len(frames),
                        camera_order=config["camera_order"], config=config,
                        outputs=list(updates), note="No scan files changed; contact sheets require a non-dry run")
        manifest = copy.deepcopy(source)
        manifest.update(scan_id=(current or source)["scan_id"], retain=(current or source).get("retain", False),
            cameras=cameras, frames=frames, combined_quad_output=True, split_combined_output=True,
            quad_split_applied=True, quad_split_mode=config["mode"], quad_split_config=config,
            raw_files=[f["file"] for f in frames], split_files=[f["file"] for f in frames],
            raw_combined_files=[f["file"] for f in source["combined_frames"]],
            contact_sheet_files=SHEETS if inspection else [], combined_background=background,
            preview_ready=True, geometry_calibrated=False, backgrounds_available=bool(background))
        manifest.setdefault("timing_summary", {})["split_seconds"] = round(time.perf_counter()-started, 6)
        # Preserve transfer records for originals and add explicit records for derived photos.
        files = {name:dict(file=name, sha256=checksum, size_bytes=(scan/name).stat().st_size) for name,checksum in originals.items()}
        for name,checksum in updates.items():
            if not name.startswith("outputs/"):
                files[name] = dict(file=name, sha256=checksum, size_bytes=(stage/name).stat().st_size)
        manifest["files"] = list(files.values())
        if any(digest(scan / name) != checksum for name,checksum in originals.items()):
            raise ValueError("Original images changed during splitting")
        receipt = dict(source_hash=json_digest(source), input_hashes=originals, output_hashes=updates,
                       manifest_hash=json_digest(manifest))
        write_json(scan / "quad_split.pending.json", receipt)
        if not (scan / "manifest.source.json").exists():
            write_json(scan / "manifest.source.json", source)
        (scan / "outputs/result.json").unlink(missing_ok=True)
        for relative in updates:
            target = scan / relative; target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or digest(target) != updates[relative]:
                (stage / relative).replace(target)
        validate_manifest(scan, manifest)
        write_json(scan / "manifest.json", manifest)
        write_json(scan / "quad_split_receipt.json", receipt)
        (scan / "quad_split.pending.json").unlink()
        LOG.info("Split %d combined frames into %d camera images in %.3fs", len(paths), len(frames), manifest["timing_summary"]["split_seconds"])
        return manifest


def split_scan(scan, config=None, *, dry_run=False, contact_sheets=True):
    scan = Path(scan).resolve()
    layout = load_config(config)
    if (scan / ".receiving").exists():
        raise ValueError("Transfer is active; wait before splitting")
    lock = scan / ".processing"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("Scan is already processing or splitting") from exc
    os.close(fd)
    try:
        if (scan / ".receiving").exists():
            raise ValueError("Transfer is active; wait before splitting")
        return _split(scan, layout, dry_run, contact_sheets)
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--contact-sheet", action="store_true", help="Generate inspection sheets (also generated by default)")
    parser.add_argument("--dry-run", action="store_true", help="Validate crops/outputs without changing scan files")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = split_scan(args.scan, args.config, dry_run=args.dry_run)
        print(json.dumps(result if args.dry_run else dict(scan=str(args.scan.resolve()), split_files=len(result["split_files"]), contact_sheet_files=result["contact_sheet_files"]), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Split failed: {exc}\n")


if __name__ == "__main__":
    main()
