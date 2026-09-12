"""Reusable workflow for both web and command-line clients."""
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from preview3d.pipeline import reconstruct, validate_grid
from preview3d.selection import validate_limit
from windows_client.detailed import prepare
from windows_client.scan import load_manifest, fingerprint, write_json, capture_frames, input_files, processing_frames

LOG = logging.getLogger(__name__)


def import_scan(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()
    manifest = load_manifest(source)
    if source.parent == root:
        return source
    root.mkdir(parents=True, exist_ok=True)
    destination = root / manifest["scan_id"]
    if destination.exists():
        destination = root / (manifest["scan_id"] + "_" + uuid.uuid4().hex[:6])
    # A partial import has no complete manifest, so the watcher cannot race it.
    destination.mkdir()
    for name in sorted(input_files(manifest)):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    if fingerprint(source, manifest, 32) != fingerprint(destination, manifest, 32):
        raise ValueError("Source changed while importing; retry when capture is complete")
    for name in ("manifest.source.json", "quad_split_receipt.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    for name in ("combined_contact_sheet.jpg", "split_contact_sheet.jpg"):
        relative = Path("outputs/inspection") / name
        if (source / relative).is_file():
            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination / relative)
    manifest["scan_id"] = destination.name
    manifest["retain"] = True  # Imported files are never part of automatic demo cleanup.
    write_json(destination / "manifest.json", manifest)
    LOG.info("Imported %s", destination)
    return destination


def process_scan(scan, grid=96, force=False, max_frames=0, *, auto_split=True, quad_config=None):
    validate_limit(max_frames)
    validate_grid(grid)
    scan = Path(scan).resolve()
    if auto_split:
        from preview3d.quad_splitter import needs_split, split_scan
        if needs_split(scan):
            LOG.info("Combined frames detected; splitting before reconstruction")
            split_scan(scan, quad_config)
    m = load_manifest(scan)
    signature = fingerprint(scan, m, grid, max_frames)
    report_path = scan / "outputs" / "result.json"
    if report_path.exists() and not force:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        required = ["quick/preview.glb", "quick/preview.obj", "quick/mesh.json", "quick/stats.json",
                    "detailed/job.json", "detailed/capture_manifest.json", "detailed/README.md"]
        if m.get("combined_frames") and not m.get("frames"):
            required = [p for p in required if not p.startswith("quick/")]
        required += ["detailed/images/" + Path(f["file"]).name for f in processing_frames(m)]
        if report.get("fingerprint") == signature and all((scan / "outputs" / p).is_file() for p in required):
            LOG.info("Reusing completed preview for %s", scan.name)
            return report
    lock = scan / ".processing"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"Scan is already processing. If the previous client crashed, remove {lock} after stopping it.") from exc
    os.close(descriptor)
    started = time.perf_counter()
    try:
        LOG.info("Processing %s (%d camera frames)", scan.name, len(processing_frames(m)))
        # Invalidate an earlier success marker before replacing any outputs.
        report_path.unlink(missing_ok=True)
        try:
            if m.get("combined_frames") and not m.get("frames"):
                reason = "Combined quad layout is unverified. Photos are saved; split them into verified camera views before reconstruction."
                quick = dict(status="unavailable", reason=reason, frames_used=0, frames_total=len(capture_frames(m)),
                             frame_files=[], max_frames=max_frames, grid=grid, seconds=0, triangles=0)
                LOG.warning(reason)
                write_json(scan / "outputs/quick/stats.json", quick)
            else:
                quick = reconstruct(scan, m, grid, max_frames)
        except Exception:
            prepare(scan, m)
            LOG.info("Detailed workspace prepared despite quick-preview failure")
            raise
        detailed = prepare(scan, m)
        if fingerprint(scan, m, grid, max_frames) != signature or load_manifest(scan) != m:
            raise ValueError("Scan inputs changed during processing; rerun after transfer finishes")
        result = dict(scan_id=m["scan_id"], fingerprint=signature, quick=quick, detailed=detailed,
                      total_seconds=round(time.perf_counter()-started, 3))
        write_json(report_path, result)
        LOG.info("Complete: %s | detailed images prepared: %d", scan.name, detailed["image_count"])
        return result
    finally:
        lock.unlink(missing_ok=True)
