"""HTTP capture node with mock and rpicam/libcamera backends."""
import argparse
import logging
import socket
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from pi_node import __version__
from pi_node.capture import MockCapture
from pi_node.scan_manager import ScanService
from pi_node.config import NodeError
from pi_node.camera_backends.rpicam_backend import RpicamBackend

ROOT = Path(__file__).resolve().parent


def create_app(root=None, backend=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8192
    service = ScanService(root or ROOT / "scans", backend or MockCapture())
    app.extensions["scan_service"] = service

    @app.errorhandler(NodeError)
    def node_error(error):
        return jsonify(error=dict(code=error.code, message=str(error))), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=dict(code="http_error", message=error.description)), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Node request failed")
        return jsonify(error=dict(code="internal_error", message="Node request failed; inspect node logs")), 500

    @app.get("/api/v1/health")
    def health():
        adapter = service.backend
        diagnostic = {} if adapter.mock_mode else (adapter.diagnostic or adapter.diagnose())
        return jsonify(status="ok", hostname=socket.gethostname(), software_version=__version__,
                       api_version=1, mock_mode=adapter.mock_mode, backend=getattr(adapter, "name", "mock"),
                       ready=adapter.mock_mode or not diagnostic.get("error"),
                       camera_command_found=diagnostic.get("camera_command_found", False),
                       camera_command_used=diagnostic.get("command_used"), camera_count=4,
                       detected_camera_count=4 if adapter.mock_mode else len(diagnostic.get("cameras", [])),
                       combined_quad_output=getattr(adapter, "combined_quad_output", False),
                       last_error=service.last_error or diagnostic.get("error") or getattr(adapter, "last_error", None))

    @app.get("/api/v1/cameras")
    def cameras():
        if not service.backend.mock_mode:
            return jsonify(service.backend.diagnose())
        return jsonify(cameras=[dict(id=c["id"], status="configured", mock=True) for c in service.backend.cameras()],
                       raw_output="Mock backend: four configured cameras", command_used=None, error=None)

    @app.post("/api/v1/backgrounds/capture")
    def backgrounds():
        state = service.capture_backgrounds(request.get_json(), request.headers.get("Idempotency-Key"))
        return jsonify(**state, status_url=f"/api/v1/scan/status/{state['scan_id']}"), 202

    @app.post("/api/v1/backgrounds/check")
    def check_backgrounds():
        import hashlib
        import json
        from pi_node.config import validate_request
        params = validate_request(request.get_json(), getattr(service.backend, "name", "mock"))
        try:
            saved = json.loads((service.background_root / "index.json").read_text())
            if saved["settings"] != service._background_settings(params):
                raise ValueError("Background capture settings differ")
            if not saved["images"]:
                raise ValueError("No background images")
            for entry in saved["images"]:
                source = service.background_root / entry["name"]
                if source.resolve().parent != service.background_root.resolve():
                    raise ValueError("Invalid background path")
                if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("Background image changed")
            return jsonify(matching=True, captured_at=saved["captured_at"])
        except (OSError, ValueError, KeyError) as exc:
            return jsonify(matching=False, reason=str(exc))

    @app.post("/api/v1/scan/start")
    def start():
        state = service.start(request.get_json(), request.headers.get("Idempotency-Key"))
        return jsonify(**state, status_url=f"/api/v1/scan/status/{state['scan_id']}"), 202

    @app.get("/api/v1/scan/status/<sid>")
    def status(sid):
        return jsonify(service.status(sid))

    @app.get("/api/v1/scans")
    def scans():
        return jsonify(scans=service.listing())

    @app.get("/api/v1/scans/<sid>/manifest")
    def manifest(sid):
        return jsonify(service.manifest(sid))

    @app.delete("/api/v1/scans/<sid>")
    def discard(sid):
        return jsonify(service.discard(sid))

    @app.get("/api/v1/scans/<sid>/files/<path:filename>")
    def image(sid, filename):
        manifest = service.manifest(sid)
        # Bare filenames refer to raw/. Qualified paths also serve backgrounds/.
        relative = filename if "/" in filename else "raw/" + filename
        record = next((f for f in manifest["files"] if f["file"] == relative), None)
        if record is None:
            raise NodeError("File is not listed in this scan's manifest", 404, "file_not_found")
        path = (service.root / sid / relative).resolve()
        if not path.is_relative_to(service.root / sid):
            raise NodeError("Invalid file path", 400, "invalid_path")
        response = send_file(path, mimetype="image/jpeg", conditional=True,
                             etag=record["sha256"], download_name=path.name)
        response.headers["X-Content-SHA256"] = record["sha256"]
        return response

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mock", action="store_true", help="Compatibility alias for --backend mock")
    parser.add_argument("--backend", choices=("mock", "rpicam"))
    parser.add_argument("--root", type=Path, default=ROOT / "scans")
    args = parser.parse_args()
    if args.mock and args.backend == "rpicam":
        parser.error("--mock conflicts with --backend rpicam")
    backend = RpicamBackend() if args.backend == "rpicam" else MockCapture()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs/node.log", encoding="utf-8")])
    app = create_app(args.root, backend)
    service = app.extensions["scan_service"]
    server = None
    try:
        server = make_server(args.host, args.port, app, threaded=True)
        logging.info("Pi node %s | backend=%s | http://%s:%d | scans=%s", __version__, backend.name, args.host, server.server_port, service.root)
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping node")
    finally:
        service.close()
        if server:
            server.server_close()


if __name__ == "__main__":
    main()
