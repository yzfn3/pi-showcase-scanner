"""On-demand low-resolution photo previews; no extra image files stored on disk."""
import io
import json
from pathlib import Path
from PIL import Image

from preview3d.selection import select_frames
from windows_client.scan import load_manifest, safe_file, capture_frames, processing_frames


def photo_catalog(scan, kind="all", offset=0, limit=48):
    if kind not in ("all", "used", "backgrounds") or offset < 0 or not 1 <= limit <= 96:
        raise ValueError("Invalid photo filter or page (limit 1-96, offset >= 0)")
    scan = Path(scan)
    manifest = load_manifest(scan)
    report_path = scan / "outputs/result.json"
    selected, inferred = None, False
    if report_path.exists():
        result = json.loads(report_path.read_text(encoding="utf-8"))["quick"]
        if "frame_files" in result:
            selected = set(result["frame_files"])
        else:
            # Older previews used this same evenly spaced selection, but did not save filenames.
            selected = {f["file"] for f in select_frames(manifest, result["frames_used"])}
            inferred = True
    items = [dict(file=f["file"], camera_id=f["camera_id"], angle_deg=f["angle_deg"],
                  step=f.get("step"), used=None if selected is None else f["file"] in selected)
             for f in sorted(capture_frames(manifest), key=lambda f: (f["angle_deg"], f["camera_id"]))]
    backgrounds = [dict(file=c["background"], camera_id=c["id"], used=None, background=True)
                   for c in manifest["cameras"] if c.get("background")]
    if kind == "used":
        items = [f for f in items if f["used"]]
    elif kind == "backgrounds":
        items = backgrounds
    return dict(items=items[offset:offset+limit], total=len(items), offset=offset, limit=limit,
                frames_total=len(processing_frames(manifest)), frames_used=None if selected is None else len(selected),
                backgrounds_total=len(backgrounds), selection_inferred=inferred)


def thumbnail(scan, filename, size=240):
    if not 64 <= size <= 640:
        raise ValueError("Thumbnail size must be between 64 and 640")
    scan = Path(scan)
    manifest = load_manifest(scan)
    allowed = {f["file"] for f in capture_frames(manifest)}
    allowed.update(c["background"] for c in manifest["cameras"] if c.get("background"))
    if filename not in allowed:
        raise ValueError("Photo is not listed in this scan's manifest")
    directory = filename.split("/", 1)[0]
    path = safe_file(scan, filename, directory)
    with Image.open(path) as original:
        image = original.convert("RGB")
        image.thumbnail((size, size))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=65)
    return output.getvalue()
