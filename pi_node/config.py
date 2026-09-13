"""Validated capture parameters shared by the API, manager and camera CLI."""
import math
import re
from windows_client.scan import ID_PATTERN


class NodeError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def capture_interval(rotation_seconds, steps):
    return rotation_seconds / steps


def validate_request(body, backend="mock"):
    if not isinstance(body, dict):
        raise NodeError("Request must be a JSON object")
    defaults = dict(steps=12, cameras=4, mode=backend, delay_between_steps_ms=0,
                    rotation_seconds=60, output_format="jpg", capture_width=None,
                    capture_height=None, exposure_time=None, gain=None, awb="auto",
                    camera_timeout_ms=None, autofocus_on_capture=False, awbgains=None,
                    sensor_mode=None, viewfinder_mode=None, viewfinder_width=None, viewfinder_height=None,
                    zsl=False, autofocus_window=None, autofocus_range=None, jpeg_quality=None,
                    focus_mode=None, lens_position=None, use_backgrounds=True,
                    combined_quad_output=backend == "rpicam", split_combined_output=False)
    allowed = set(defaults) | {"scan_id", "camera_count"}
    if set(body) - allowed:
        raise NodeError("Unknown request fields: " + ", ".join(sorted(set(body)-allowed)))
    params = {**defaults, **body}
    if "camera_count" in body:
        if "cameras" in body and body["cameras"] != body["camera_count"]:
            raise NodeError("cameras and camera_count disagree")
        params["cameras"] = params.pop("camera_count")
    for key, low, high in (("steps", 3, 120), ("cameras", 1, 4), ("delay_between_steps_ms", 0, 10000)):
        if type(params[key]) is not int or not low <= params[key] <= high:
            raise NodeError(f"{key} must be an integer from {low} to {high}")
    if params["mode"] != backend or params["output_format"] != "jpg":
        raise NodeError(f"This node requires mode '{backend}' and output_format 'jpg'")
    for key, low, high in (("rotation_seconds", .01, 3600), ("exposure_time", 1, 10000000),
                           ("gain", 0.1, 64), ("lens_position", 0, 100)):
        value = params[key]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high):
            raise NodeError(f"{key} must be a number from {low} to {high}")
    if params["rotation_seconds"] is None:
        raise NodeError("rotation_seconds is required")
    for key in ("capture_width", "capture_height", "viewfinder_width", "viewfinder_height"):
        if params[key] is not None and (type(params[key]) is not int or not 64 <= params[key] <= 8192):
            raise NodeError(f"{key} must be an integer from 64 to 8192")
    if (params["capture_width"] is None) != (params["capture_height"] is None):
        raise NodeError("Set capture_width and capture_height together")
    if (params['viewfinder_width'] is None) != (params['viewfinder_height'] is None):
        raise NodeError('Set viewfinder_width and viewfinder_height together')
    for key in ('sensor_mode', 'viewfinder_mode'):
        value = params[key]
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[1-9]\d{1,4}:[1-9]\d{1,4}(?::(?:8|10|12|14|16)(?::[PU])?)?', value)):
            raise NodeError(f'{key} must be WIDTH:HEIGHT[:BITS[:P|U]]')
    if params['autofocus_range'] not in (None, 'normal', 'macro', 'full'):
        raise NodeError('autofocus_range must be normal, macro or full')
    if params['jpeg_quality'] is not None and (type(params['jpeg_quality']) is not int or not 1 <= params['jpeg_quality'] <= 100):
        raise NodeError('jpeg_quality must be 1..100')
    if params['autofocus_window'] is not None:
        try:
            x,y,w,h = map(float, params['autofocus_window'].split(','))
            if not all(math.isfinite(v) for v in (x,y,w,h)) or min(x,y)<0 or min(w,h)<=0 or x+w>1 or y+h>1:
                raise ValueError()
        except (ValueError, AttributeError):
            raise NodeError('autofocus_window must be normalized x,y,w,h within the sensor')
    for key in ("use_backgrounds", "combined_quad_output", "split_combined_output", "autofocus_on_capture", "zsl"):
        if type(params[key]) is not bool:
            raise NodeError(f"{key} must be true or false")
    if params["split_combined_output"]:
        raise NodeError("Quad layout is unverified; splitting is not implemented. Use split_combined_output=false.")
    timeout = params["camera_timeout_ms"]
    if timeout is not None and (type(timeout) is not int or not 1 <= timeout <= 120000):
        raise NodeError("camera_timeout_ms must be an integer from 1 to 120000")
    gains = params["awbgains"]
    if gains is not None:
        try:
            values = [float(v) for v in gains.split(",")]
            if len(values) != 2 or any(not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError()
        except (AttributeError, ValueError):
            raise NodeError("awbgains must be two positive finite gains, e.g. 1.0,1.0")
    if params["awb"] not in (None, "auto", "incandescent", "tungsten", "fluorescent", "indoor", "daylight", "cloudy", "custom"):
        raise NodeError("Unsupported awb mode")
    if params["focus_mode"] not in (None, "default", "manual", "auto", "continuous"):
        raise NodeError("Unsupported focus_mode")
    if (params["lens_position"] is not None and params["focus_mode"] not in (None, "manual")
            and not params["autofocus_on_capture"]):
        raise NodeError("lens_position requires manual focus")
    sid = params.get("scan_id")
    if sid is not None and (not isinstance(sid, str) or len(sid) > 80 or not ID_PATTERN.fullmatch(sid)):
        raise NodeError("scan_id must match scan_YYYYMMDD_HHMMSS with optional alphanumeric suffix")
    if backend == "rpicam" and params["delay_between_steps_ms"]:
        raise NodeError("Physical timing uses rotation_seconds / steps, not delay_between_steps_ms")
    # Legacy mock calls stay accelerated. An explicit rotation period requests real timing.
    params["timed"] = backend == "rpicam" or "rotation_seconds" in body
    return params
