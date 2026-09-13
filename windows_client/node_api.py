"""HTTP adapter with bounded retries, verified downloads, and resumable receipts."""
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from windows_client.scan import ID_PATTERN, validate_manifest, write_json, capture_frames

LOG = logging.getLogger(__name__)


class NodeClientError(RuntimeError):
    pass


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def transfer_records(manifest, scan_id):
    """Validate untrusted remote paths and require exactly the referenced images."""
    if (not isinstance(manifest, dict) or manifest.get("scan_id") != scan_id or
            manifest.get("status") != "complete" or manifest.get("schema_version") != 1):
        raise NodeClientError("Node returned an incompatible or incomplete manifest")
    try:
        frames, cameras, files = capture_frames(manifest), manifest["cameras"], manifest["files"]
        if not 1 <= len(frames) <= 10000 or not 1 <= len(cameras) <= 32:
            raise ValueError("Invalid image or camera count")
        paths = [f["file"] for f in frames]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate frame files")
        referenced = set(paths) | {c["background"] for c in cameras if c.get("background")}
        records = {}
        for item in files:
            path = item["file"]
            if not isinstance(path, str) or not re.fullmatch(r"(?:raw|raw_combined|backgrounds)/[A-Za-z0-9_-]+\.jpg", path):
                raise ValueError("Unsafe image path")
            if path in records:
                raise ValueError("Duplicate transfer records")
            if type(item["size_bytes"]) is not int or not 1 <= item["size_bytes"] <= 50*1024*1024:
                raise ValueError("Invalid image size (limit 50 MiB)")
            if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError("Missing or invalid SHA-256 digest")
            records[path] = item
        if set(records) != referenced:
            raise ValueError("Transfer list must exactly match all referenced images and backgrounds")
        if sum(item["size_bytes"] for item in records.values()) > 2*1024**3:
            raise ValueError("Scan exceeds the 2 GiB demo transfer limit")
        return records
    except (ValueError, KeyError, TypeError) as exc:
        raise NodeClientError(f"Invalid transfer manifest: {exc}") from exc


class NodeClient:
    def __init__(self, node, timeout=15.0, retries=2, session=None):
        parsed = urlsplit(node)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise NodeClientError("--node must be a base URL such as http://raspberrypi.local:8000")
        if timeout <= 0 or retries < 0:
            raise NodeClientError("Timeout must be positive and retries nonnegative")
        self.node = node.rstrip("/")
        self.timeout, self.retries = timeout, retries
        self.session = session or requests.Session()
        # Local/LAN calls should not be routed through a laptop's HTTP proxy.
        self.session.trust_env = False

    def close(self):
        self.session.close()

    def _timeout(self, deadline=None):
        remaining = self.timeout if deadline is None else min(self.timeout, deadline-time.monotonic())
        if remaining <= 0:
            raise NodeClientError("Operation deadline exceeded")
        return min(3.0, remaining), remaining

    @staticmethod
    def _check(response):
        if response.status_code >= 500:
            raise requests.HTTPError(f"Node returned HTTP {response.status_code}", response=response)
        if not 200 <= response.status_code < 300:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.reason
            raise NodeClientError(f"HTTP {response.status_code}: {message}")

    def _pause(self, attempt, deadline):
        delay = 0.2*(attempt+1)
        if deadline is not None:
            delay = min(delay, max(0, deadline-time.monotonic()))
        time.sleep(delay)

    def json(self, method, path, *, body=None, key=None, deadline=None):
        for attempt in range(self.retries+1):
            try:
                with self.session.request(method, self.node + "/api/v1" + path, json=body,
                                          headers={"Idempotency-Key": key} if key else {},
                                          timeout=self._timeout(deadline), allow_redirects=False) as response:
                    self._check(response)
                    return response.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == self.retries:
                    raise NodeClientError(f"{method} {path} failed after {attempt+1} attempts: {exc}") from exc
                LOG.warning("Retrying %s %s (%d/%d)", method, path, attempt+1, self.retries)
                self._pause(attempt, deadline)

    def health(self):
        health = self.json("GET", "/health")
        if not isinstance(health, dict) or health.get("status") != "ok" or health.get("api_version") != 1:
            raise NodeClientError("Node is unavailable or does not implement API v1")
        return health

    def start(self, *, scan_id=None, steps=12, cameras=4, delay_between_steps_ms=0, key=None, mode="mock", **settings):
        params = dict(steps=steps, cameras=cameras, mode=mode, output_format="jpg",
                      delay_between_steps_ms=delay_between_steps_ms)
        params.update(settings)
        if scan_id:
            params["scan_id"] = scan_id
        return self.json("POST", "/scan/start", body=params, key=key or uuid.uuid4().hex)

    def capture_backgrounds(self, *, key=None, **settings):
        return self.json("POST", "/backgrounds/capture", body=settings, key=key or uuid.uuid4().hex)

    def wait(self, scan_id, *, timeout=300.0, poll_interval=0.5, on_progress=None):
        if timeout <= 0 or poll_interval <= 0:
            raise NodeClientError("Scan timeout and poll interval must be positive")
        deadline = time.monotonic()+timeout
        last = None
        while time.monotonic() < deadline:
            state = self.json("GET", "/scan/status/" + quote(scan_id, safe=""), deadline=deadline)
            if not isinstance(state, dict) or state.get("scan_id") != scan_id:
                raise NodeClientError("Node returned invalid scan status")
            progress = (state.get("state"), state.get("current_step"), state.get("files_captured"))
            if progress != last:
                if on_progress:
                    on_progress(state)
                LOG.info("%s: %s | step %s/%s | %s images", scan_id, state["state"],
                         state.get("current_step"), state.get("total_steps"), state.get("files_captured"))
                last = progress
            if state["state"] == "complete":
                return state
            if state["state"] == "failed":
                raise NodeClientError(f"Capture failed: {state.get('error')}")
            if state["state"] not in ("pending", "capturing"):
                raise NodeClientError("Unknown capture state")
            time.sleep(min(poll_interval, max(0, deadline-time.monotonic())))
        raise NodeClientError(f"Scan timed out after {timeout:g}s; capture may still be running. Resume using --scan-id {scan_id}")

    def _download(self, scan_id, folder, record, deadline):
        path = folder / record["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never follow a local symlink or junction outside the reserved scan folder.
        if not path.resolve().is_relative_to(folder.resolve()):
            raise NodeClientError("Download path leaves the local scan folder")
        if path.exists():
            if path.stat().st_size == record["size_bytes"] and digest_file(path) == record["sha256"]:
                return
            raise NodeClientError(f"Existing image differs; refusing to overwrite {path}. Use a new --root.")
        partial = path.with_suffix(".jpg.part")
        if partial.is_symlink():
            raise NodeClientError("Refusing a linked partial download")
        for attempt in range(self.retries+1):
            try:
                digest, size = hashlib.sha256(), 0
                url = self.node + f"/api/v1/scans/{quote(scan_id, safe='')}/files/{quote(record['file'], safe='/')}"
                with self.session.get(url, stream=True, timeout=self._timeout(deadline), allow_redirects=False) as response:
                    self._check(response)
                    with partial.open("wb") as output:
                        for chunk in response.iter_content(65536):
                            self._timeout(deadline)
                            size += len(chunk)
                            if size > record["size_bytes"]:
                                raise requests.RequestException("Downloaded image exceeds its declared size")
                            output.write(chunk)
                            digest.update(chunk)
                if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
                    raise requests.RequestException("Image size or SHA-256 mismatch")
                partial.replace(path)
                return
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise NodeClientError(f"Transfer failed for {record['file']} after {attempt+1} attempts: {exc}") from exc
                LOG.warning("Retrying image %s (%d/%d): %s", record["file"], attempt+1, self.retries, exc)
                self._pause(attempt, deadline)

    def download_scan(self, scan_id, root, *, timeout=300.0, on_progress=None):
        if not isinstance(scan_id, str) or not ID_PATTERN.fullmatch(scan_id):
            raise NodeClientError("Invalid scan ID")
        if timeout <= 0:
            raise NodeClientError("Transfer timeout must be positive")
        deadline = time.monotonic()+timeout
        manifest = self.json("GET", f"/scans/{scan_id}/manifest", deadline=deadline)
        records = transfer_records(manifest, scan_id)
        manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        receipt = dict(node=self.node, scan_id=scan_id, manifest_sha256=manifest_hash)
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        folder = root / scan_id
        try:
            folder.mkdir()
            new = True
        except FileExistsError:
            new = False
        if folder.resolve().parent != root:
            raise NodeClientError("Received scan folder leaves the local library")
        lock = folder / ".receiving"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise NodeClientError(f"Transfer already active. After a crash, stop the old client and remove {lock}") from exc
        os.close(descriptor)
        try:
            if (folder / ".processing").exists():
                raise NodeClientError("Scan is processing or splitting; retry after it finishes")
            receipt_path = folder / "transfer.json"
            if new:
                write_json(receipt_path, receipt)
            elif not receipt_path.is_file() or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt:
                raise NodeClientError("A different scan already occupies this local ID; use a different --root. Nothing overwritten.")
            manifest_path = folder / "manifest.json"
            if manifest_path.exists():
                local = json.loads(manifest_path.read_text(encoding="utf-8"))
                if local != manifest:
                    # Splitting creates a local derived manifest. Its immutable source
                    # and content-checksum receipt must still match before resuming.
                    from preview3d.quad_splitter import json_digest
                    source_path = folder / "manifest.source.json"
                    split_receipt = folder / "quad_split_receipt.json"
                    valid_split = False
                    if local.get("quad_split_applied") and source_path.is_file() and split_receipt.is_file():
                        source = json.loads(source_path.read_text(encoding="utf-8"))
                        derived = json.loads(split_receipt.read_text(encoding="utf-8"))
                        valid_split = (source == manifest and derived.get("source_hash") == json_digest(source)
                                       and derived.get("manifest_hash") == json_digest(local))
                    if not valid_split and local.get("full_photogrammetry_mode"):
                        before = folder / "manifest.pre_full.json"
                        full_receipt = folder / "full_receipt.json"
                        if before.is_file() and full_receipt.is_file():
                            source = json.loads(before.read_text(encoding="utf-8"))
                            derived = json.loads(full_receipt.read_text(encoding="utf-8"))
                            valid_split = (source == manifest and derived.get("source_hash") == json_digest(source)
                                           and derived.get("manifest_hash") == json_digest(local))
                    if not valid_split:
                        raise NodeClientError("Local manifest was modified; refusing to overwrite it")
            for index, record in enumerate(records.values(), 1):
                self._download(scan_id, folder, record, deadline)
                if on_progress:on_progress(index,len(records))
                if index % 4 == 0 or index == len(records):
                    LOG.info("Verified images: %d/%d", index, len(records))
            validate_manifest(folder, manifest)
            if not manifest_path.exists():
                write_json(manifest_path, manifest)
            LOG.info("Received scan ready: %s", folder)
            return folder
        finally:
            lock.unlink(missing_ok=True)
