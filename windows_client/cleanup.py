"""Delete only identified mock scans within an explicitly supplied scan library."""
import json
import logging
import os
import shutil
import stat
import time
from pathlib import Path

from windows_client.scan import ID_PATTERN, write_json

LOG = logging.getLogger(__name__)


def is_generated(scan):
    scan = Path(scan)
    pending = scan.parent / ".cleanup" / f"{scan.name}.json"
    if pending.is_file():
        return True
    try:
        manifest = json.loads((Path(scan) / "manifest.json").read_text(encoding="utf-8"))
        return manifest.get("source") in ("synthetic", "pi_mock") and not manifest.get("retain")
    except (OSError, ValueError, AttributeError):
        return False


def discard_generated(scan, root):
    root, scan = Path(root).resolve(), Path(scan).absolute()
    if (scan.parent != root or scan.resolve().parent != root or not ID_PATTERN.fullmatch(scan.name)
            or scan.is_symlink() or getattr(scan, "is_junction", lambda: False)()):
        raise ValueError("Cleanup target must be a real scan directory directly inside its library")
    if not is_generated(scan):
        raise ValueError("Only generated mock scans can be discarded; imported scans are preserved")
    if any((scan / marker).exists() for marker in (".processing", ".receiving")):
        raise ValueError("Scan is still processing or transferring")
    # Reject links/junctions anywhere in the deletion tree before recursive removal.
    for current, dirs, files in os.walk(scan, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("Refusing cleanup of a scan containing filesystem links")
    # Keep a small receipt outside the tree, so interrupted deletion can be retried.
    pending = root / ".cleanup" / f"{scan.name}.json"
    write_json(pending, {"scan_id": scan.name})
    def remove_readonly(function, target, error):
        resolved = Path(target).resolve()
        if resolved != scan and not resolved.is_relative_to(scan):
            raise ValueError("Cleanup retry leaves the verified scan directory")
        if not isinstance(error[1], PermissionError):
            raise error[1]
        # OneDrive commonly marks copied image directories as read-only on Windows.
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        for attempt in range(4):
            try:
                function(target)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(.1*(attempt+1))
    shutil.rmtree(scan, onerror=remove_readonly)
    pending.unlink(missing_ok=True)
    LOG.info("Discarded generated scan: %s", scan)


def finish_demo(scan, root):
    """Delete the local generation and request removal of its remote mock copy."""
    scan = Path(scan)
    receipt_path = scan / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
    discard_generated(scan, root)
    if receipt:
        from windows_client.node_api import NodeClient, NodeClientError
        client = None
        try:
            client = NodeClient(receipt["node"], timeout=2, retries=0)
            client.json("DELETE", f"/scans/{scan.name}")
        except (NodeClientError, KeyError, OSError) as exc:
            LOG.warning("Local files deleted; node cleanup deferred: %s", exc)
            return "Local files deleted. The node was unavailable; its copy will be removed on its next scan."
        finally:
            if client:
                client.close()
    return "Generated images and models deleted."
