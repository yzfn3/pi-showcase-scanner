"""Loopback-only standard-library HTTP server with one background worker."""
import json
import logging
import mimetypes
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from sample_data.generate import generate
from windows_client.scan import load_manifest, capture_frames, processing_frames
from windows_client.workflow import import_scan, process_scan
from windows_client.cleanup import is_generated, finish_demo
from windows_client.photos import photo_catalog, thumbnail
from preview3d.selection import validate_limit
from preview3d.pipeline import validate_grid

LOG = logging.getLogger(__name__)
VIEWER = Path(__file__).resolve().parents[1] / "viewer"


class App:
    def __init__(self, root, grid, max_frames=0):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.grid = validate_grid(grid)
        self.max_frames = validate_limit(max_frames)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.jobs = {}

    def scan_path(self, sid):
        if not isinstance(sid, str) or Path(sid).name != sid:
            raise ValueError("Invalid scan folder name")
        path = (self.root / sid).resolve()
        if path.parent != self.root or not path.is_dir():
            raise ValueError("Unknown scan")
        return path

    def submit(self, request):
        action = request.get("action")
        if action not in ("generate", "import", "process", "discard"):
            raise ValueError("Expected action: generate, import, process, or discard")
        if action in ("process", "discard"):
            self.scan_path(request.get("scan_id"))
        if action == "process":
            validate_limit(request.get("max_frames", self.max_frames))
            validate_grid(request.get("grid", self.grid))
        if action == "import" and not isinstance(request.get("path"), str):
            raise ValueError("Enter the scan folder's full Windows path")
        with self.lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                raise ValueError("A job is already running; wait for it to finish")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"id": job_id, "status": "queued", "action": action}
        self.executor.submit(self.run, job_id, request)
        return job_id

    def run(self, job_id, request):
        def update(**fields):
            with self.lock:
                self.jobs[job_id].update(fields)
        update(status="running")
        try:
            action = request["action"]
            if action == "generate":
                previous = [p for p in self.root.glob("scan_*") if is_generated(p)]
                scan = generate(self.root)
                warnings = []
                for old in previous:
                    try:
                        message = finish_demo(old, self.root)
                        if "unavailable" in message:
                            warnings.append(message)
                    except (OSError, ValueError) as exc:
                        warnings.append(f"Could not clean up {old.name}: {exc}")
                update(status="complete", scan_id=scan.name, message=" ".join(warnings))
            elif action == "discard":
                message = finish_demo(self.scan_path(request["scan_id"]), self.root)
                update(status="complete", scan_id=None, message=message)
            elif action == "import":
                scan = import_scan(request["path"], self.root)
                update(status="complete", scan_id=scan.name)
            else:
                scan = self.scan_path(request["scan_id"])
                result = process_scan(scan, request.get("grid", self.grid), force=True, max_frames=request.get("max_frames", self.max_frames))
                update(status="complete", scan_id=scan.name, result=result)
        except Exception as exc:
            LOG.exception("Job failed")
            update(status="failed", error=str(exc))

    def listing(self):
        result = []
        for scan in sorted(self.root.glob("scan_*"), reverse=True):
            try:
                m = load_manifest(scan)
                report = scan / "outputs" / "result.json"
                result.append(dict(id=scan.name, source=m.get("source", "imported"), frames=len(processing_frames(m)), photo_count=len(capture_frames(m)), inspection=m.get("contact_sheet_files", []),
                                   disposable=is_generated(scan), full_workspace=m.get("full_workspace_path"), full_status=m.get("colmap_status"),
                                   result=json.loads(report.read_text(encoding="utf-8")) if report.exists() else None,
                                   thumbnail=f"/scans/{scan.name}/{capture_frames(m)[0]['file']}"))
            except (ValueError, OSError, TypeError, KeyError):
                continue
        return result


def create_server(root, grid=96, port=8765, max_frames=0):
    app = App(root, grid, max_frames)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            LOG.debug(format, *args)

        def send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def trusted_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.trusted_host():
                return self.send(403, {"error": "Use the localhost URL shown in the terminal"})
            path = unquote(urlparse(self.path).path)
            try:
                if path == "/api/scans":
                    return self.send(200, {"scans": app.listing(), "root": str(app.root), "default_max_frames": app.max_frames, "default_grid": app.grid})
                if path.startswith("/api/scans/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise ValueError("Unknown scan endpoint")
                    scan = app.scan_path(parts[2])
                    query = parse_qs(urlparse(self.path).query)
                    if parts[3] == "photos":
                        return self.send(200, photo_catalog(scan, query.get("filter", ["all"])[0],
                                         int(query.get("offset", ["0"])[0]), int(query.get("limit", ["48"])[0])))
                    if parts[3] == "thumbnail":
                        return self.send(200, thumbnail(scan, query.get("file", [""])[0],
                                         int(query.get("size", ["240"])[0])), "image/jpeg")
                    raise ValueError("Unknown scan endpoint")
                if path.startswith("/api/jobs/"):
                    with app.lock:
                        job = dict(app.jobs.get(path.rsplit("/", 1)[-1], {}))
                    return self.send(200 if job else 404, job or {"error": "Unknown job"})
                if path.startswith("/scans/"):
                    parts = path.split("/")
                    scan = app.scan_path(parts[2])
                    relative = "/".join(parts[3:])
                    allowed = ("raw/", "raw_combined/", "outputs/inspection/", "outputs/full_photogrammetry/", "outputs/quick/", "outputs/detailed/")
                    if not relative.startswith(allowed):
                        raise ValueError("File is not a displayable scan artifact")
                    base, name = scan, relative
                else:
                    base, name = VIEWER, "index.html" if path == "/" else path.lstrip("/")
                file = (base / name).resolve()
                if not file.is_relative_to(base) or not file.is_file():
                    return self.send(404, {"error": "File not found"})
                mime = {".glb": "model/gltf-binary", ".js": "text/javascript", ".obj": "text/plain"}.get(file.suffix)
                mime = mime or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.send(200, file.read_bytes(), mime)
            except (OSError, ValueError, IndexError, TypeError) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            # JSON plus a same-origin header prevents unrelated websites from driving local imports.
            origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.trusted_host() or self.headers.get("Origin", "") not in origins:
                return self.send(403, {"error": "Same-origin requests required"})
            if self.path != "/api/jobs":
                return self.send(404, {"error": "Unknown endpoint"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected a small application/json request")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("Expected a JSON object")
                self.send(202, {"job_id": app.submit(request)})
            except (ValueError, TypeError, OSError) as exc:
                self.send(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    return server
