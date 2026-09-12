"""Prepare a clean image workspace; never launch reconstruction software."""
import shutil
from pathlib import Path
from windows_client.scan import write_json, processing_frames


def prepare(scan, manifest):
    out = scan / "outputs" / "detailed"
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    expected = {Path(f["file"]).name for f in processing_frames(manifest)}
    if len(expected) != len(processing_frames(manifest)):
        raise ValueError("Detailed export requires unique image basenames")
    # Remove only stale generated image files, never raw capture data.
    for old in images.iterdir():
        if old.is_file() and old.name not in expected:
            old.unlink()
    for frame in processing_frames(manifest):
        shutil.copy2(scan / frame["file"], images / Path(frame["file"]).name)
    write_json(out / "capture_manifest.json", manifest)
    combined = bool(manifest.get("combined_frames")) and not manifest.get("quad_split_applied", False)
    write_json(out / "job.json", dict(status="prepared_only", engine=None,
               image_count=len(expected), images="images", reconstruction_started=False, requires_split=combined,
               note="Approximate orthographic preview poses are not calibrated photogrammetry cameras."))
    (out / "README.md").write_text(
        "# Detailed reconstruction workspace\n\n"
        "Status: PREPARED ONLY. No reconstruction has run.\n\n"
        "`images/` contains copies of every raw frame. Backgrounds are excluded.\n"
        "`capture_manifest.json` preserves capture metadata. `job.json` records staging status.\n\n"
        "Later: import images/ into your chosen installed reconstruction tool (COLMAP, "
        "Meshroom, or RealityCapture/RealityScan). Configure real camera intrinsics and "
        "turntable foreground masks before reconstruction. A static background with a moving "
        "object violates the usual static-scene assumption; mask the background.\n\n"
        "Synthetic silhouette images have little texture and are intended to test the quick "
        "pipeline, not feature matching. No detailed model is promised for them.\n",
        encoding="utf-8")
    if combined:
        with (out / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("\nCOMBINED QUAD IMAGES: layout has not been verified. Do not import these mosaics as single camera photos. Verify camera crops/orientations and split before reconstruction.\n")
    return {"status": "prepared_only", "image_count": len(expected), "requires_split": combined}
