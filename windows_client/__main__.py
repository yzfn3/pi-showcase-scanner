"""Run with python -m windows_client (web UI) or choose a CLI subcommand."""
import argparse
import logging
import threading
import webbrowser
from pathlib import Path

from sample_data.generate import generate
from windows_client.server import create_server
from windows_client.scan import load_manifest, fingerprint
from windows_client.workflow import import_scan, process_scan

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)


def watch(root, grid, stop, callback=None, max_frames=0):
    attempted = {}
    LOG.info("Watching %s; only complete manifests are eligible", root)
    while not stop.is_set():
        for scan in sorted(Path(root).glob("scan_*")):
            signature = None
            try:
                m = load_manifest(scan)
                signature = fingerprint(scan, m, grid, max_frames)
                if attempted.get(scan) == signature:
                    continue
                process_scan(scan, grid, max_frames=max_frames)
                attempted[scan] = signature
                if callback:
                    callback(scan)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Partial transfers are retried; malformed scans don't terminate watching.
                if signature is not None and not (scan / ".processing").exists():
                    attempted[scan] = signature
                message = str(exc)
                if attempted.get((scan, "error")) != message:
                    LOG.warning("%s: %s", scan.name, message)
                    attempted[(scan, "error")] = message
        stop.wait(2)


def main():
    parser = argparse.ArgumentParser(description="Pi Showcase Scanner: local synthetic-to-3D demo")
    parser.add_argument("--root", type=Path, default=ROOT / "scans", help="Managed scan library")
    parser.add_argument("--grid", type=int, choices=range(12, 161), metavar="12..160", default=96)
    parser.add_argument("--max-frames", type=int, choices=range(10001), metavar="0..10000", default=0,
                        help="Images used in the quick preview; 0 uses all images")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Local web UI (default)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--watch", action="store_true", help="Automatically process new complete scans in --root")
    gen = sub.add_parser("generate", help="Generate a synthetic scan")
    gen.add_argument("--steps", type=int, default=12)
    imp = sub.add_parser("import", help="Copy a scan into the local library")
    imp.add_argument("path", type=Path)
    run = sub.add_parser("process", help="Run quick preview and prepare detailed workspace")
    run.add_argument("path", type=Path)
    run.add_argument("--force", action="store_true")
    sub.add_parser("watch", help="Watch scan library without opening a UI")
    sub.add_parser("demo", help="Generate, process, and open the result in the browser")
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs" / "scanner.log", encoding="utf-8")])
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "generate":
            print(generate(args.root, args.steps))
            return
        if args.command == "import":
            print(import_scan(args.path, args.root))
            return
        if args.command == "process":
            process_scan(args.path, args.grid, args.force, max_frames=args.max_frames)
            return
        if args.command == "watch":
            watch(args.root, args.grid, threading.Event(), max_frames=args.max_frames)
            return
        selected = None
        if args.command == "demo":
            scan = generate(args.root)
            process_scan(scan, args.grid, max_frames=args.max_frames)
            selected = scan.name
        server = create_server(args.root, args.grid, getattr(args, "port", 8765), max_frames=args.max_frames)
        url = f"http://127.0.0.1:{server.server_port}/"
        if selected:
            url += "?scan=" + selected
        LOG.info("Open %s | scan library: %s | Ctrl+C stops the client", url, args.root)
        stop = threading.Event()
        watcher = None
        if getattr(args, "watch", False):
            watcher = threading.Thread(target=watch, args=(args.root, args.grid, stop),
                                       kwargs={"max_frames":args.max_frames}, daemon=True)
            watcher.start()
        if not getattr(args, "no_browser", False):
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            stop.set()
            if watcher:
                watcher.join()
            server.server_close()
            server.app.executor.shutdown(wait=True)
    except KeyboardInterrupt:
        LOG.info("Client stopped")
    except Exception as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
