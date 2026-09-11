# Architecture

```text
Synthetic generator / mock Pi capture over HTTP
             |
   complete manifest + images
             |
   import/watch folder or verified HTTP receive
             |
   validate + fingerprint + scan lock
             |
   quick: foreground masks -> voxel carving -> smoothed surface mesh
             |                            |
             |                     GLB + OBJ + viewer JSON
             |                            |
   detailed: copy all images       local WebGL viewer
             |
   preparation notes + job.json
             |
   outputs/result.json (completion marker)
```

`windows_client.scan` owns the input contract. `sample_data.generate` creates images with analytical ray/ellipsoid intersections. It shares only the camera convention with the preview; the preview never reads the synthetic object's source geometry. Changing the images changes the reconstruction.

`preview3d.pipeline` uses background differences at a maximum 640-pixel image dimension, one-pixel mask dilation, and a configurable 12–160³ voxel grid (default 96³). `preview3d.selection` samples a configurable number of views across the sorted full capture: default 0, meaning all. Results record `max_frames` and the exact `frame_files`; the cache fingerprint includes the limit. Surviving voxels project into every selected silhouette, and diagonal edge contacts are filled before mesh export. Exposed quads are extracted with array operations, welded, and smoothed with 8–32 pairs of positive/negative Laplacian passes, increasing with grid resolution. Quad edges avoid diagonal smoothing bias; area-weighted vertex normals provide smooth shading. No texture fitting or hidden-concavity inference is attempted.

`preview3d.export` writes the same geometry three ways. GLB uses glTF 2.0 positions, normals, and one matte material. OBJ includes the same smooth normals. `mesh.json` is a simple triangle stream for the dependency-free WebGL viewer. Coordinates in exported models remain in meters; the viewer alone centers and scales its display.

`windows_client.workflow` runs quick preview first, then detailed staging. If quick reconstruction fails it still attempts detailed staging, then reports the failure. The browser loads the completed combined result. A future separation into two concurrent jobs can display the quick model while copying very large detailed image sets.

`windows_client.server` binds only to `127.0.0.1`. It serves bundled assets and managed scan artifacts. A single executor keeps HTTP requests responsive during generation and reconstruction. Same-origin JSON POST requests and Host checks protect the local import endpoint from other websites. This development server is for one trusted local user; do not expose it on a LAN or public interface.

The CLI watcher polls instead of adding a filesystem-watcher dependency. Raw frames and manifests are immutable after completion. Content fingerprints skip successful repeats and catch input changes; a per-scan exclusive lock prevents concurrent writers. After a process crash the user removes the stale lock after confirming the old client has stopped. Output files are replaced individually, and the overall completion marker is written last.

## Local client HTTP API (implemented)

- `GET /api/scans` → `{root, scans: [{id, source, frames, thumbnail, result}]}`
- `POST /api/jobs` with `{"action":"generate"}` → HTTP 202 `{job_id}`
- `POST /api/jobs` with `{"action":"import","path":"C:\\captures\\scan_..."}` → HTTP 202 `{job_id}`
- `POST /api/jobs` with `{"action":"process","scan_id":"scan_..."}` → HTTP 202 `{job_id}`
- `GET /api/jobs/<id>` → `{id, action, status, scan_id?, result?, error?}`

Processing requests accept `max_frames` (integer 0–10000, default 0; 0 means all). Processing also accepts `grid` (integer 12–160, default 96). The local UI exposes an image-count field, Use all button, and Geometry detail selector. Listings include `default_grid` and `default_max_frames`. Results include `surface_version`, `grid`, and `voxel_size_mm`; the fingerprint versions the surface algorithm.

- `GET /api/scans/<id>/photos?filter=all|used|backgrounds&offset=0&limit=48` returns paginated photo metadata and flags indicating use in the last successful quick preview. Limit is 1–96. Older results without recorded filenames use the original sampler and set `selection_inferred: true`.
- `GET /api/scans/<id>/thumbnail?file=raw/<filename>&size=240` returns an in-memory JPEG thumbnail of a manifest-listed raw or background image. Size is restricted to 64–640 pixels; full-resolution source files remain unchanged. No thumbnail files are saved.

Job status is `queued`, `running`, `complete`, or `failed`. A second active job is rejected. Jobs are in memory, outputs persist on disk. POST requires `Content-Type: application/json` and an Origin matching the local URL. The Pi's future API is intentionally separate.

## Mock Pi node and HTTP receiver

`pi_node.camera_backends.base.CaptureBackend` separates camera image production from the API. `MockCapture` reuses the analytical synthetic renderer, adds a labeled footer, and declares `preview_crop` so the footer does not enter silhouette carving. Backgrounds have matching dimensions. `RpicamBackend` uses bounded subprocess calls, with no shell, to capture JPEGs from rpicam/libcamera. Its geometry remains estimated. `splitter.py` explicitly rejects unverified quad splitting.

`pi_node.scan_manager.ScanService` (also exported from `service.py`) runs one capture at a time on a worker thread. It persists request/idempotency metadata, progress, and the final image manifest with byte counts and SHA-256 digests. Scan folders are allocated exclusively. Restart recovery retains completed scans and marks interrupted capture failed.

`pi_node.app` exposes the [implemented API](pi_node_api.md) using Flask and a threaded Werkzeug server. It defaults to loopback; `--host 0.0.0.0` is available for the trusted demo LAN. It is unauthenticated HTTP, without a reloader, and should have one process per scan root.

`windows_client.node_api.NodeClient` uses requests with connect/read timeouts, bounded retries, and capture/transfer deadlines. Idempotency keys make start retries safe. Transfer receipts bind a local ID to one source URL and manifest. Verified files are reused, incomplete files use `.jpg.part`, conflicting existing data is rejected, and manifest validation precedes publication of `manifest.json`. Failed downloads can be resumed with `--scan-id`.

`windows_client.client` connects these operations to the original `process_scan()` pipeline. Received scans live under `scans/received/`. After processing, the CLI starts the existing viewer server with that library and an OS-assigned free port, prints the exact URL, and opens it. The original UI can also browse this library with `--root scans/received`.

## Extension seams

1. Implement a real camera adapter behind `CaptureBackend` and update timing/calibration metadata.
2. Extend the HTTP transfer adapter with authentication or range resume if deployment needs it.
3. Replace `foreground` for better masks, or `reconstruct` for calibrated perspective carving.
4. Extend `detailed.prepare` with an explicit reconstruction-engine adapter and separate job status.

Physical rpicam command capture is implemented but has not been tested on this hardware. Motor control, encoder synchronization, camera calibration, verified quad splitting and heavy reconstruction are not implemented. Mock capture, the Pi service, and HTTP image pulling are implemented.

Timed scans schedule step starts against monotonic deadlines, excluding setup/background copying. Each command records start time, approximate midpoint angle, stdout/stderr and elapsed time. Missed step deadlines fail without burst catch-up. Background capture uses the same single worker and progress endpoint. Immutable reference names plus an atomic index publish a complete current set; scan manifests copy references. Unsplit mosaics use `combined_frames`/`raw_combined_files`, transfer normally, and produce an explicit unavailable quick result with detailed staging. Imported and physical scans are retained.
