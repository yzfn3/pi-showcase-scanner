"""Bounded subprocess captures using the installed Raspberry Pi camera stack."""
import argparse
import json
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from pi_node.config import NodeError, validate_request
from sample_data.generate import camera_configuration


class RpicamBackend:
    name = "rpicam"
    mock_mode = False
    combined_quad_output = True

    def __init__(self, which=shutil.which, runner=subprocess.run, timeout=20):
        self.command = which("rpicam-still") or which("libcamera-still")
        self.runner, self.timeout = runner, timeout
        self.last_error = None
        self.diagnostic = None
        self.last_output = ""
        self.last_command = None

    def _run(self, args, timeout):
        if not self.command:
            raise NodeError("Neither rpicam-still nor libcamera-still was found on PATH. Install/configure the Pi camera stack.", 503, "camera_unavailable")
        try:
            self.last_command = [self.command, *args]
            self.last_output = ""
            result = self.runner([self.command, *args], capture_output=True, text=True,
                                 timeout=timeout, check=False, shell=False)
            output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[-8192:]
            self.last_output = output
            if result.returncode:
                raise RuntimeError(f"Camera command exited {result.returncode}: {output}")
            return output
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            self.last_error = str(exc)
            raise NodeError(f"Camera command failed: {exc}", 503, "camera_failed") from exc

    def diagnose(self):
        raw, cameras, error = "", [], None
        try:
            raw = self._run(["--list-cameras"], self.timeout)
            for index, description in re.findall(r"^\s*(\d+)\s*:\s*(.+)$", raw, re.MULTILINE):
                cameras.append(dict(index=int(index), description=description.strip()))
            if not cameras:
                error = "No cameras detected. Check cables, overlays, permissions and the raw command output."
        except NodeError as exc:
            error = str(exc)
            raw = self.last_output
        self.last_error = error
        self.diagnostic = dict(cameras=cameras, raw_output=raw, command_used=self.command,
                               camera_command_found=self.command is not None, error=error)
        return self.diagnostic

    def cameras(self):
        info = self.diagnostic or self.diagnose()
        if info["error"]:
            raise NodeError(info["error"], 503, "camera_unavailable")
        configured = camera_configuration()
        # These are initial geometry estimates, never claimed to be calibration.
        for camera, detected in zip(configured, info["cameras"]):
            camera["device_index"] = detected["index"]
            camera.pop("background", None)
        return configured[:len(info["cameras"])]

    def capture_file(self, path, camera, settings):
        """One command per image; save valid JPEG atomically and retain its log."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise NodeError(f"Refusing to overwrite captured image: {path}")
        temporary = path.with_name(path.name + ".part")
        camera_timeout = settings.get("camera_timeout_ms") or 1000
        autofocus = settings.get("autofocus_on_capture", False)
        use_lens = not autofocus or settings.get("focus_mode") == "manual"
        args = ["--nopreview", "--camera", str(camera.get("device_index", 0)),
                "--timeout", str(camera_timeout), "--encoding", "jpg", "--output", str(temporary)]
        flags = {"capture_width": "--width", "capture_height": "--height", "exposure_time": "--shutter",
                 "gain": "--gain", "awb": "--awb", "awbgains": "--awbgains", "focus_mode": "--autofocus-mode", "lens_position": "--lens-position"}
        for key, flag in flags.items():
            if key == "lens_position" and not use_lens:
                continue
            if settings.get(key) is not None:
                args.extend([flag, str(settings[key])])
        if autofocus and settings.get("focus_mode") != "continuous":
            args.append("--autofocus-on-capture")
        if use_lens and settings.get("lens_position") is not None and settings.get("focus_mode") is None:
            args.extend(["--autofocus-mode", "manual"])
        started = time.monotonic()
        try:
            output = self._run(args, self.timeout + camera_timeout/1000 + (settings.get("exposure_time") or 0)/1e6)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise NodeError("Camera command produced no image; inspect permissions and camera logs", 503, "empty_capture")
            with Image.open(temporary) as image:
                if image.format != "JPEG":
                    raise NodeError("Camera did not produce a JPEG")
                dimensions = image.size
                image.verify()
            temporary.replace(path)
            self.last_error = None
            return dict(command=[self.command, *args], output=output,
                        duration_seconds=round(time.monotonic()-started, 6), dimensions=list(dimensions))
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--diagnose", action="store_true")
    action.add_argument("--test-capture", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args(argv)
    backend = RpicamBackend()
    diagnostic = backend.diagnose()
    print(json.dumps(diagnostic, indent=2))
    if diagnostic["error"]:
        return 1
    if args.test_capture:
        path = args.output or Path(__file__).resolve().parents[1] / "test_captures" / (datetime.now(timezone.utc).strftime("test_%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:6]+".jpg")
        try:
            backend.capture_file(path, {"device_index": args.camera}, validate_request({}, "rpicam"))
            print(f"Saved test capture: {path.resolve()}")
        except (NodeError, OSError, ValueError) as exc:
            print(f"Test capture failed: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
