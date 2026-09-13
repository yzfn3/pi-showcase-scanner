"""Capture Pi images, receive and process a scan, and serve its local viewer."""
import argparse
import os
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
    parser.add_argument("--full-scan", action="store_true", help="Run VGGT reconstruction")
    parser.add_argument("--reconstruction-engine", choices=["vggt","worldmirror2"], default="vggt")
    parser.add_argument("--brightness-gamma", type=float, default=1.0)
    parser.add_argument("--vggt-frames", type=int, default=64)
    parser.add_argument("--vggt-size", type=int, default=392)
    parser.add_argument("--capture-profile", choices=("quad-sharp", "standard"),
                        help="quad-sharp: tested 4:3 sensor mode and targeted focus; default for full scans")
    parser.add_argument("--capture-backgrounds-first", action="store_true")
    parser.add_argument("--skip-backgrounds", action="store_true")
    colmap_group = parser.add_mutually_exclusive_group()
    colmap_group.add_argument("--run-colmap", dest="run_colmap", action="store_true")
    colmap_group.add_argument("--no-run-colmap", dest="run_colmap", action="store_false")
    parser.set_defaults(run_colmap=True)
    parser.add_argument("--dense", action="store_true")
    parser.add_argument("--matcher", choices=("exhaustive", "sequential"), default="exhaustive")
    parser.add_argument("--mask-threshold", type=int, default=25)
    parser.add_argument("--mask-inspect", action="store_true")
    parser.add_argument("--full-workspace-root", type=Path)
    parser.add_argument("--preserve-originals", action="store_true")
    parser.add_argument("--crop-mode", choices=("quadrants",), default="quadrants")
    parser.add_argument("--capture-backgrounds", action="store_true", help="Capture the empty table, then exit unless --start-scan is also supplied")
    parser.add_argument("--rotation-seconds", type=float, help="Physical rotation period (default 60); explicitly setting this also times mock scans")
    parser.add_argument("--quad-split-config", "--quad-config", type=Path, help="Local quad crop/order configuration")
    parser.add_argument("--quality", choices=("fast", "detailed", "fine", "full"), default=None)
    parser.add_argument("--combined-quad-output", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--split-combined-output", action="store_true", help="Reserved: rejected until the physical layout is verified")
    parser.add_argument("--use-backgrounds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--capture-width", type=int)
    parser.add_argument("--capture-height", type=int)
    parser.add_argument("--exposure-time", type=float, help="Shutter time in microseconds")
    parser.add_argument("--gain", type=float)
    parser.add_argument("--awb", default="auto")
    parser.add_argument("--camera-timeout-ms", type=int, default=None)
    parser.add_argument("--sensor-mode", help="rpicam sensor mode WIDTH:HEIGHT:BITS")
    parser.add_argument("--viewfinder-mode", help="Use the same sensor mode while focusing")
    parser.add_argument("--viewfinder-width", type=int)
    parser.add_argument("--viewfinder-height", type=int)
    parser.add_argument("--zsl", action="store_true", help="Keep still and preview streams configured together")
    parser.add_argument("--autofocus-window", help="Normalized x,y,w,h focus region")
    parser.add_argument("--autofocus-range", choices=("normal", "macro", "full"))
    parser.add_argument("--jpeg-quality", type=int)
    parser.add_argument("--autofocus-on-capture", action="store_true")
    parser.add_argument("--awbgains", default=None, help="Advanced red,blue gains, e.g. 1.0,1.0")
    parser.add_argument("--focus-mode")
    parser.add_argument("--lens-position", type=float, help="Focus in dioptres; requires lens support")
    parser.add_argument("--scan-id", help="Existing scan to receive, or an explicit new ID with --start-scan")
    parser.add_argument("--steps", type=int, choices=range(3,121), metavar="3..120", default=None)
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
    if args.full_scan and args.capture_profile is None:
        args.capture_profile = "quad-sharp"
    if args.capture_profile == "quad-sharp":
        # Verified on this quad kit. Explicit CLI values remain authoritative.
        profile = dict(steps=8, capture_width=4624, capture_height=3472, sensor_mode="4624:3472:10",
                       viewfinder_mode="4624:3472:10", camera_timeout_ms=6000, focus_mode="continuous",
                       autofocus_window="0.6,0.15,0.3,0.2", autofocus_range="full", jpeg_quality=95,
                       combined_quad_output=True)
        for key, value in profile.items():
            if getattr(args, key) is None: setattr(args, key, value)
        args.zsl = True
    if args.full_scan:
        args.start_scan = True
        for key, value in dict(steps=24, rotation_seconds=60, capture_width=3840, capture_height=2160,
                               focus_mode="auto", camera_timeout_ms=1000, quality="full", combined_quad_output=True).items():
            if getattr(args, key) is None: setattr(args, key, value)
        if args.focus_mode == 'auto':
            args.autofocus_on_capture = True
        args.preserve_originals = True
        args.split_combined_output = False  # Windows splits locally; the Pi flag is reserved.
    args.steps = args.steps or 12
    args.quality = args.quality or "detailed"
    args.grid = args.grid or {"fast": 32, "detailed": 96, "fine": 160, "full": 96}[args.quality]
    if args.skip_backgrounds:
        args.use_backgrounds = False
        if args.capture_backgrounds_first or args.capture_backgrounds:
            parser.error("--skip-backgrounds conflicts with background capture")
    if not 0 <= args.mask_threshold <= 255:
        parser.error("--mask-threshold must be 0..255")
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
            "rotation_seconds", "capture_width", "capture_height", "exposure_time", "gain", "awb", "focus_mode", "lens_position", "camera_timeout_ms", "awbgains",
            "sensor_mode", "viewfinder_mode", "viewfinder_width", "viewfinder_height", "autofocus_window", "autofocus_range", "jpeg_quality")
            if getattr(args, key) is not None}
        settings.update(camera_timeout_ms=args.camera_timeout_ms, awbgains=args.awbgains,
                        autofocus_on_capture=args.autofocus_on_capture, use_backgrounds=args.use_backgrounds, split_combined_output=args.split_combined_output,
                        combined_quad_output=health.get("combined_quad_output", False) if args.combined_quad_output is None else args.combined_quad_output)
        if args.zsl:
            settings['zsl'] = True
        background_needed = args.capture_backgrounds or args.capture_backgrounds_first
        if args.full_scan and args.use_backgrounds and not background_needed:
            availability = client.json("POST", "/backgrounds/check", body=dict(mode=mode, cameras=args.cameras, **settings))
            background_needed = not availability["matching"]
            if background_needed:
                LOG.warning("Matching backgrounds unavailable: %s", availability.get("reason"))
        if background_needed:
            if args.full_scan and not health.get("mock_mode"):
                if not sys.stdin.isatty():
                    raise ValueError("Background capture needs an empty table. Capture backgrounds separately in an interactive terminal, or use --skip-backgrounds.")
                input("Remove the object, leave the empty table in place, then press Enter to capture backgrounds: ")
            LOG.info("Capturing empty-table background references")
            background = client.capture_backgrounds(mode=mode, camera_count=args.cameras, **settings)
            client.wait(background["scan_id"], timeout=args.scan_timeout, poll_interval=args.poll_interval)
            print("Background references saved on the node under backgrounds/current/", flush=True)
            if args.full_scan and not health.get("mock_mode"):
                input("Replace the object and start the turntable, then press Enter to start the scan: ")
            if not args.start_scan and not args.scan_id:
                return 0
        if args.start_scan:
            capture_budget = (args.camera_timeout_ms or 1000)/1000 + 0.75
            if args.capture_profile == 'quad-sharp' and args.focus_mode == 'manual':
                capture_budget = max(capture_budget, 3.0)
            if mode == "rpicam" and (args.rotation_seconds or 60)/args.steps < capture_budget:
                raise ValueError("Capture startup/settling cannot reliably fit the step interval. Use --steps 16 for the tested locked-focus setup, --steps 8 for six-second autofocus, or slow the physical table and set its actual rotation period.")
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
        if args.full_scan:
            from photogrammetry.vggt_runner import run
            result = run(scan, engine=args.reconstruction_engine, gamma=args.brightness_gamma, max_frames=args.vggt_frames,
                         image_size=args.vggt_size, threshold=args.mask_threshold, mesh=True,
                         progress=lambda event: LOG.info("%s", event))
            print("RECONSTRUCTION WORKSPACE: " + result["full_workspace_path"], flush=True)
            print("STATUS: " + result["status"], flush=True)
            print(result["next_action"], flush=True)
            if not args.no_viewer:
                show_result(scan,open_browser=not args.no_browser)
            return 1 if result["errors"] else 0
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
