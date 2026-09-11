"""One capture at a time, durable scan states, and immutable completed folders."""
import hashlib
import json
import logging
import re
import time
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pi_node import __version__
from pi_node.config import NodeError, validate_request, capture_interval
from PIL import Image
from windows_client.scan import ID_PATTERN, load_manifest, write_json
from windows_client.cleanup import discard_generated, is_generated

LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ScanService:
    def __init__(self, root, backend):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")
        self.started = {}
        self.last_error = None
        self.background_root = self.root.parent / "backgrounds" / "current"
        self.states = {}
        self.idempotency = {}
        self._recover()

    def _recover(self):
        for folder in sorted(self.root.glob("scan_*")):
            try:
                state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
                request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
                if state["state"] in ("pending", "capturing"):
                    try:
                        manifest = load_manifest(folder)
                        state.update(state="complete", current_step=state["total_steps"],
                                     files_captured=len(manifest.get("frames", []))+len(manifest.get("combined_frames", [])), error=None)
                    except (OSError, ValueError):
                        state.update(state="failed", error="Node stopped before capture completed; start a new scan")
                        manifest_path = folder / "manifest.json"
                        if manifest_path.is_file():
                            interrupted = json.loads(manifest_path.read_text(encoding="utf-8"))
                            interrupted.update(status="failed", error=state["error"], capture_completed_at=utc_now())
                            interrupted.setdefault("errors", []).append(dict(at=utc_now(), message=state["error"]))
                            write_json(manifest_path, interrupted)
                    write_json(folder / "status.json", state)
                self.states[folder.name] = state
                if request.get("idempotency_key"):
                    self.idempotency[request["idempotency_key"]] = (request["parameters"], folder.name)
            except (OSError, ValueError, KeyError, TypeError):
                LOG.warning("Skipping incomplete node metadata in %s", folder)
        for tombstone in (self.root / ".deleted").glob("*.json"):
            record = json.loads(tombstone.read_text(encoding="utf-8"))
            sid = record["state"]["scan_id"]
            self.states[sid] = record["state"]
            saved = record["request"]
            if saved.get("idempotency_key"):
                self.idempotency[saved["idempotency_key"]] = (saved["parameters"], sid)

    def status(self, sid):
        with self.lock:
            if sid not in self.states:
                raise NodeError("Unknown scan", 404, "scan_not_found")
            state = dict(self.states[sid])
            if sid in self.started and state["state"] in ("pending", "capturing"):
                state["elapsed_seconds"] = round(time.monotonic()-self.started[sid], 3)
                state["estimated_remaining_seconds"] = round(max(0, state.get("planned_seconds", 0)-state["elapsed_seconds"]), 3)
            return state

    def listing(self):
        with self.lock:
            return [dict(self.states[sid]) for sid in sorted(self.states, reverse=True) if not self.states[sid].get("deleted") and self.states[sid].get("operation") != "backgrounds"]

    def _discard_locked(self, sid):
        state = self.states.get(sid)
        if not state:
            raise NodeError("Unknown scan", 404, "scan_not_found")
        if state.get("deleted"):
            return
        if state["state"] in ("pending", "capturing"):
            raise NodeError("Cannot delete an active capture", 409, "node_busy")
        folder = self.root / sid
        if not is_generated(folder):
            raise NodeError("Only disposable mock generations can be deleted; physical scans and references are retained", 409, "scan_retained")
        saved_request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        discard_generated(folder, self.root)
        state = {**state, "state": "failed", "deleted": True, "error": "Generation deleted after use"}
        write_json(self.root / ".deleted" / f"{sid}.json", dict(state=state, request=saved_request))
        self.states[sid] = state

    def discard(self, sid):
        with self.lock:
            self._discard_locked(sid)
        return dict(scan_id=sid, deleted=True)

    def start(self, body, idempotency_key=None, operation="scan"):
        params = validate_request(body, getattr(self.backend, "name", "mock"))
        params["operation"] = operation
        if idempotency_key and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
            raise NodeError("Idempotency-Key must be 1-128 ASCII letters, digits, hyphens, or underscores")
        with self.lock:
            if idempotency_key in self.idempotency:
                original, sid = self.idempotency[idempotency_key]
                if original != params:
                    raise NodeError("Idempotency key was used with different parameters", 409, "idempotency_conflict")
                return dict(self.states[sid])
            if any(state["state"] in ("pending", "capturing") for state in self.states.values()):
                raise NodeError("A scan is already in progress", 409, "node_busy")
            if self.stop.is_set():
                raise NodeError("Node is shutting down", 503, "node_stopping")
            if not self.backend.mock_mode:
                cameras = self.backend.cameras()
                if not params["combined_quad_output"] and len(cameras) < params["cameras"]:
                    raise NodeError("Fewer independent cameras detected than camera_count; use combined mode only if the device actually outputs a quad image", 409, "camera_count_mismatch")
            sid = params.get("scan_id") or "scan_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            if params.get("scan_id") and sid in self.states:
                raise NodeError("scan_id already used; choose a new ID", 409, "scan_exists")
            if sid in self.states:
                sid += "_" + uuid.uuid4().hex[:8]
            while True:
                folder = self.root / sid
                try:
                    folder.mkdir()
                    break
                except FileExistsError:
                    if params.get("scan_id"):
                        raise NodeError("scan_id already exists; choose a new ID", 409, "scan_exists")
                    sid = "scan_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            state = dict(scan_id=sid, state="pending", current_step=0, total_steps=1 if operation == "backgrounds" else params["steps"],
                         operation=operation, files_captured=0, error=None, elapsed_seconds=0,
                         estimated_remaining_seconds=params["rotation_seconds"] if params["timed"] and operation == "scan" else 0,
                         planned_seconds=params["rotation_seconds"] if params["timed"] and operation == "scan" else 0)
            write_json(folder / "request.json", dict(parameters=params, idempotency_key=idempotency_key))
            write_json(folder / "status.json", state)
            self.states[sid] = state
            if idempotency_key:
                self.idempotency[idempotency_key] = (params, sid)
            self.executor.submit(self._capture, sid, params)
            return dict(state)

    def _update(self, sid, **fields):
        with self.lock:
            if sid in self.started:
                fields["elapsed_seconds"] = round(time.monotonic()-self.started[sid], 3)
                fields["estimated_remaining_seconds"] = 0 if fields.get("state") in ("complete", "failed") else round(max(0, self.states[sid].get("planned_seconds", 0)-fields["elapsed_seconds"]), 3)
            state = {**self.states[sid], **fields}
            write_json(self.root / sid / "status.json", state)
            self.states[sid] = state

    @staticmethod
    def _save_image(image, folder, relative):
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".jpg.tmp")
        image.save(temporary, format="JPEG", quality=95)
        temporary.replace(path)
        payload = path.read_bytes()
        return dict(file=relative, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())

    def capture_backgrounds(self, body, key=None):
        return self.start(body, key, operation="backgrounds")

    @staticmethod
    def _record(folder, relative):
        data = (folder / relative).read_bytes()
        return dict(file=relative, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def _camera_set(self, params):
        cameras = [dict(c) for c in self.backend.cameras()[:params["cameras"]]]
        if params["combined_quad_output"]:
            cameras = [cameras[0]]
        for c in cameras:
            c.pop("background", None)
        return cameras

    def _take(self, folder, relative, camera, params, sid, step=0, angle=0, background=False):
        if self.stop.is_set():
            raise RuntimeError("Capture interrupted by node shutdown")
        if not self.backend.mock_mode:
            log = self.backend.capture_file(folder / relative, camera, params)
            return self._record(folder, relative), log
        if params["combined_quad_output"]:
            views = []
            for c in self.backend.cameras()[:params["cameras"]]:
                views.append(self.backend.background(c) if background else self.backend.capture(
                    c, scan_id=sid, step=step, angle_deg=angle, captured_at=utc_now()))
            image = Image.new("RGB", (views[0].width*2, views[0].height*2))
            for i, view in enumerate(views):
                image.paste(view, ((i % 2)*view.width, (i // 2)*view.height))
        else:
            image = self.backend.background(camera) if background else self.backend.capture(
                camera, scan_id=sid, step=step, angle_deg=angle, captured_at=utc_now())
        return self._save_image(image, folder, relative), dict(output="Mock capture", dimensions=list(image.size))

    def _background_settings(self, params):
        keys = ("cameras", "combined_quad_output", "capture_width", "capture_height",
                "exposure_time", "gain", "awb", "focus_mode", "lens_position")
        return {"backend": getattr(self.backend, "name", "mock"), **{k: params[k] for k in keys}}

    def _publish_backgrounds(self, folder, manifest, params):
        # Immutable image names + a final atomic index preserve the old set if copying fails.
        root = self.background_root
        root.mkdir(parents=True, exist_ok=True)
        entries = []
        for record in manifest["files"]:
            name = manifest["scan_id"] + "_" + Path(record["file"]).name
            shutil.copy2(folder / record["file"], root / name)
            entries.append(dict(name=name, relative=record["file"], sha256=record["sha256"]))
        write_json(root / "index.json", dict(settings=self._background_settings(params),
                   images=entries, captured_at=utc_now()))
        keep = {e["name"] for e in entries}
        for old in root.glob("scan_*.jpg"):
            if old.name not in keep and old.resolve().parent == root.resolve() and not old.is_symlink():
                old.unlink()

    def _copy_backgrounds(self, folder, manifest, params):
        if not params["use_backgrounds"]:
            return
        try:
            saved = json.loads((self.background_root / "index.json").read_text(encoding="utf-8"))
            if saved["settings"] != self._background_settings(params):
                raise ValueError("Background settings differ; recapture references")
            for item in saved["images"]:
                source = self.background_root / item["name"]
                if source.resolve().parent != self.background_root.resolve() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Background reference is missing or changed")
            for item in saved["images"]:
                relative = item["relative"]
                if not re.fullmatch(r"backgrounds/(?:cam_\d{2}|quad)\.jpg", relative):
                    raise ValueError("Invalid background path")
                (folder / "backgrounds").mkdir(exist_ok=True)
                shutil.copy2(self.background_root / item["name"], folder / relative)
                manifest["files"].append(self._record(folder, relative))
        except (OSError, ValueError, KeyError) as exc:
            manifest["warnings"].append(f"Backgrounds unavailable: {exc}")
            # Keep the established accelerated mock workflow working without setup.
            if self.backend.mock_mode and not params["timed"]:
                for camera in manifest["cameras"]:
                    relative = "backgrounds/quad.jpg" if params["combined_quad_output"] else f"backgrounds/{camera['id']}.jpg"
                    record, _ = self._take(folder, relative, camera, params, manifest["scan_id"], background=True)
                    manifest["files"].append(record)
        backgrounds = {f["file"] for f in manifest["files"] if f["file"].startswith("backgrounds/")}
        for camera in manifest["cameras"]:
            relative = "backgrounds/quad.jpg" if params["combined_quad_output"] else f"backgrounds/{camera['id']}.jpg"
            if relative in backgrounds:
                camera["background"] = relative
        manifest["backgrounds_available"] = len(backgrounds) == len(manifest["cameras"])

    def _capture(self, sid, params):
        folder = self.root / sid
        self.started[sid] = time.monotonic()
        manifest = None
        try:
            self._update(sid, state="capturing")
            combined = params["combined_quad_output"]
            backgrounds_only = params["operation"] == "backgrounds"
            interval = capture_interval(params["rotation_seconds"], params["steps"])
            manifest = dict(schema_version=1, scan_id=sid, status="capturing",
                source="pi_mock" if self.backend.mock_mode else "pi_rpicam", retain=not self.backend.mock_mode or backgrounds_only,
                created_at=utc_now(), projection="orthographic", units="meters", scene_extent_m=0.24,
                rotation_period_s=params["rotation_seconds"], rotation_direction="positive_y_right_hand",
                backend=getattr(self.backend, "name", "mock"), camera_command=getattr(self.backend, "command", None),
                camera_count=params["cameras"], steps=params["steps"], rotation_seconds=params["rotation_seconds"],
                capture_interval_seconds=interval, capture_width=params["capture_width"], capture_height=params["capture_height"],
                combined_quad_output=combined, split_combined_output=False, backgrounds_available=False,
                capture_started_at=None, capture_completed_at=None, cameras=self._camera_set(params),
                frames=[], combined_frames=[], raw_files=[], raw_combined_files=[], files=[],
                outputs={"quick": "outputs/quick", "detailed": "outputs/detailed"}, errors=[], warnings=[], capture_logs=[],
                settings=params, software_version=__version__, mock_mode=self.backend.mock_mode,
                timing="monotonic_deadlines" if params["timed"] else "simulated",
                angle_estimation="command midpoint; exposure timing is approximate" if not self.backend.mock_mode else "synthetic exact angles",
                preview_ready=not combined, geometry_calibrated=False)
            if self.backend.mock_mode:
                manifest["object_seed"] = hashlib.sha256(sid.encode()).hexdigest()[:32]
            write_json(folder / "manifest.json", manifest)
            if not backgrounds_only:
                self._copy_backgrounds(folder, manifest, params)
            schedule = time.monotonic()
            manifest["capture_started_at"] = utc_now()
            total = 1 if backgrounds_only else params["steps"]
            for step in range(total):
                target = schedule + step*interval
                if params["timed"] and not backgrounds_only and self.stop.wait(max(0, target-time.monotonic())):
                    raise RuntimeError("Capture interrupted by node shutdown")
                for camera in manifest["cameras"]:
                    if backgrounds_only:
                        relative = "backgrounds/quad.jpg" if combined else f"backgrounds/{camera['id']}.jpg"
                    else:
                        relative = f"raw_combined/step_{step:03d}_quad.jpg" if combined else f"raw/step_{step:03d}_{camera['id']}.jpg"
                    begin = time.monotonic()
                    captured_at = utc_now()
                    record, log = self._take(folder, relative, camera, params, sid, step, step*360/params["steps"], backgrounds_only)
                    end = time.monotonic()
                    stamp = step*interval if self.backend.mock_mode else (begin+end)/2-schedule
                    log.update(file=relative, step=step, started_at=captured_at,
                               scheduled_seconds=round(step*interval, 6), actual_start_seconds=round(begin-schedule, 6))
                    manifest["capture_logs"].append(log)
                    manifest["files"].append(record)
                    if not backgrounds_only:
                        frame = dict(file=relative, camera_id=camera["id"], step=step,
                            timestamp_s=stamp, angle_deg=stamp*360/params["rotation_seconds"], captured_at=captured_at)
                        manifest["combined_frames" if combined else "frames"].append(frame)
                        manifest["raw_combined_files" if combined else "raw_files"].append(relative)
                    self._update(sid, files_captured=sum(not f["file"].startswith("backgrounds/") for f in manifest["files"]) if not backgrounds_only else len(manifest["files"]))
                write_json(folder / "manifest.json", manifest)
                self._update(sid, current_step=step+1)
                LOG.info("%s step %d/%d", sid, step+1, total)
                if step+1 < total:
                    if params["timed"] and time.monotonic() > schedule+(step+1)*interval:
                        raise RuntimeError("Capture exceeded the step interval. Use fewer steps, lower resolution, or combined output; no burst catch-up was attempted.")
                    if not params["timed"] and self.stop.wait(params["delay_between_steps_ms"]/1000):
                        raise RuntimeError("Capture interrupted by node shutdown")
            if backgrounds_only:
                self._publish_backgrounds(folder, manifest, params)
                manifest["backgrounds_available"] = True
            manifest.update(status="complete", capture_completed_at=utc_now())
            write_json(folder / "manifest.json", manifest)
            self.last_error = None
            self._update(sid, state="complete")
            if not backgrounds_only:
                with self.lock:
                    for previous in list(self.states):
                        if previous != sid and not self.states[previous].get("deleted") and is_generated(self.root / previous):
                            try:
                                self._discard_locked(previous)
                            except (OSError, ValueError, NodeError):
                                LOG.exception("Could not clean up previous mock scan %s", previous)
        except Exception as exc:
            LOG.exception("Capture failed: %s", sid)
            self.last_error = str(exc)
            if manifest:
                manifest.update(status="failed", error=str(exc), capture_completed_at=utc_now())
                manifest["errors"].append(dict(at=utc_now(), message=str(exc),
                    command=getattr(self.backend, "last_command", None),
                    output=getattr(self.backend, "last_output", "")))
                try:
                    write_json(folder / "manifest.json", manifest)
                except OSError:
                    LOG.exception("Could not persist failed manifest")
            try:
                self._update(sid, state="failed", error=str(exc))
            except OSError:
                with self.lock:
                    self.states[sid].update(state="failed", error=str(exc), estimated_remaining_seconds=0)
                LOG.exception("Could not persist failed status")

    def manifest(self, sid):
        if self.status(sid).get("deleted"):
            raise NodeError("Generation has been deleted", 410, "scan_deleted")
        if self.status(sid)["state"] != "complete":
            raise NodeError("Scan is not complete", 409, "scan_not_complete")
        return load_manifest(self.root / sid)

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True)
