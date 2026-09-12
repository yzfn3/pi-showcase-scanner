"""Capture Pi images, receive and process a scan, and serve its local viewer."""
import argparse
import logging
import sys
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from windows_client.node_api import NodeClient, NodeClientError
from windows_client.server import create_server
from windows_client.workflow import process_scan
from windows_client.cleanup import is_generated, finish_demo

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)


def show_result(scan, port=0, open_browser=True):
    # An OS-assigned port avoids clashing with the original UI or another demo.
    server = create_server(scan.parent, port=port)
    url = f"http://127.0.0.1:{server.server_port}/?scan={scan.name}"
    print(f"VIEWER: {url}", flush=True)
    LOG.info("Keep this terminal open for the viewer. Ctrl+C stops only the viewer.")
    if open_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            LOG.warning("Browser could not open automatically; use the VIEWER URL above")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        server.app.executor.shutdown(wait=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, help="http://raspberrypi.local:8000 or http://127.0.0.1:8000")
    parser.add_argument("--start-scan", action="store_true")
    parser.add_argument("--capture-backgrounds", action="store_true", help="Capture the empty table, then exit unless --start-scan is also supplied")
    parser.add_argument("--rotation-seconds", type=float, help="Physical rotation period (default 60); explicitly setting this also times mock scans")
    parser.add_argument("--quad-split-config", type=Path, help="Local quad crop/order configuration")
    parser.add_argument("--quality", choices=("fast", "detailed", "fine"), default="detailed")
    parser.add_argument("--combined-quad-output", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--split-combined-output", action="store_true", help="Reserved: rejected until the physical layout is verified")
    parser.add_argument("--use-backgrounds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--capture-width", type=int)
    parser.add_argument("--capture-height", type=int)
    parser.add_argument("--exposure-time", type=float, help="Shutter time in microseconds")
    parser.add_argument("--gain", type=float)
    parser.add_argument("--awb", default="auto")
    parser.add_argument("--camera-timeout-ms", type=int, default=None)
    parser.add_argument("--autofocus-on-capture", action="store_true")
    parser.add_argument("--awbgains", default=None, help="Advanced red,blue gains, e.g. 1.0,1.0")
    parser.add_argument("--focus-mode")
    parser.add_argument("--lens-position", type=float, help="Focus in dioptres; requires lens support")
    parser.add_argument("--scan-id", help="Existing scan to receive, or an explicit new ID with --start-scan")
    parser.add_argument("--steps", type=int, choices=range(3,121), metavar="3..120", default=12)
    parser.add_argument("--cameras", type=int, choices=range(1,5), default=4)
    parser.add_argument("--delay-between-steps-ms", type=int, choices=range(10001), metavar="0..10000", default=0)
    parser.add_argument("--root", type=Path, default=ROOT / "scans/received")
    parser.add_argument("--timeout", type=float, default=15, help="HTTP read timeout in seconds; connect timeout at most 3s")
    parser.add_argument("--scan-timeout", type=float, default=300, help="Capture polling deadline in seconds")
    parser.add_argument("--transfer-timeout", type=float, default=300, help="Whole-transfer deadline in seconds")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--grid", type=int, choices=range(12,161), metavar="12..160", help="Override the quality preset")
    parser.add_argument("--max-frames", type=int, choices=range(10001), metavar="0..10000", default=0,
                        help="Images used in the quick preview; 0 uses all images")
    parser.add_argument("--viewer-port", type=int, default=0, help="0 chooses a free port automatically")
    parser.add_argument("--no-browser", action="store_true", help="Serve the viewer and print its URL without opening a browser")
    parser.add_argument("--no-viewer", action="store_true", help="Finish after processing; print artifact paths and the reopen command")
    args = parser.parse_args(argv)
    args.grid = args.grid or {"fast": 32, "detailed": 96, "fine": 160}[args.quality]
    if not args.start_scan and not args.scan_id and not args.capture_backgrounds:
        parser.error("Pass --start-scan, --capture-backgrounds, or --scan-id")
    if min(args.timeout, args.scan_timeout, args.transfer_timeout, args.poll_interval) <= 0:
        parser.error("Timeouts and poll interval must be positive")
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs/client.log", encoding="utf-8")])
    client, sid = None, args.scan_id
    try:
        client = NodeClient(args.node, timeout=args.timeout)
        health = client.health()
        LOG.info("Connected: %s | version=%s | mock=%s", health.get("hostname"), health.get("software_version"), health.get("mock_mode"))
        mode = health.get("backend", "mock" if health.get("mock_mode") else "rpicam")
        if (args.start_scan or args.capture_backgrounds) and health.get("ready") is False:
            raise NodeClientError(health.get("last_error") or "Node camera backend is not ready; run diagnostics on the Pi")
        settings = {key: getattr(args, key) for key in (
            "rotation_seconds", "capture_width", "capture_height", "exposure_time", "gain", "awb", "focus_mode", "lens_position", "camera_timeout_ms", "awbgains")
            if getattr(args, key) is not None}
        settings.update(camera_timeout_ms=args.camera_timeout_ms, awbgains=args.awbgains,
                        autofocus_on_capture=args.autofocus_on_capture, use_backgrounds=args.use_backgrounds, split_combined_output=args.split_combined_output,
                        combined_quad_output=health.get("combined_quad_output", False) if args.combined_quad_output is None else args.combined_quad_output)
        if args.capture_backgrounds:
            LOG.info("Capturing empty-table background references")
            background = client.capture_backgrounds(mode=mode, camera_count=args.cameras, **settings)
            client.wait(background["scan_id"], timeout=args.scan_timeout, poll_interval=args.poll_interval)
            print("Background references saved on the node under backgrounds/current/", flush=True)
            if not args.start_scan and not args.scan_id:
                return 0
        if args.start_scan:
            sid = sid or "scan_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            LOG.info("Starting scan %s", sid)
            started = client.start(scan_id=sid, steps=args.steps, cameras=args.cameras,
                                   delay_between_steps_ms=args.delay_between_steps_ms, mode=mode, **settings)
            if started.get("scan_id") != sid:
                raise NodeClientError("Node returned a different scan ID")
        client.wait(sid, timeout=args.scan_timeout, poll_interval=args.poll_interval)
        scan = client.download_scan(sid, args.root, timeout=args.transfer_timeout)
        client.close()
        client = None
        result = process_scan(scan, grid=args.grid, max_frames=args.max_frames, quad_config=args.quad_split_config)
        for previous in scan.parent.glob("scan_*"):
            if previous != scan and is_generated(previous):
                try:
                    LOG.info("%s", finish_demo(previous, scan.parent))
                except (OSError, ValueError) as exc:
                    LOG.warning("Could not discard previous generation: %s", exc)
        if result["quick"].get("status") == "unavailable":
            print("QUICK PREVIEW: " + result["quick"]["reason"], flush=True)
        else:
            print(f"GLB: {scan / 'outputs/quick/preview.glb'}", flush=True)
            print(f"OBJ: {scan / 'outputs/quick/preview.obj'}", flush=True)
        print(f"DETAILED: {scan / 'outputs/detailed'}", flush=True)
        if args.no_viewer:
            print(f'To open the viewer: "{sys.executable}" -m windows_client --root "{scan.parent}" serve', flush=True)
        else:
            show_result(scan, port=args.viewer_port, open_browser=not args.no_browser)
        return 0
    except KeyboardInterrupt:
        LOG.info("Client stopped; completed scan files remain on disk")
        return 130
    except (NodeClientError, OSError, ValueError, KeyError, TypeError) as exc:
        LOG.error("%s", exc)
        if sid:
            LOG.error('Resume without starting another scan: python -m windows_client.client --node %s --scan-id %s --root "%s"', args.node, sid, args.root.resolve())
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
