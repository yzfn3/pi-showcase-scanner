# Pi Showcase Scanner

A local engineering demo: **Pi capture (mock or rpicam) over HTTP → verified image transfer → coarse 3D model → browser viewer**, plus a separate image workspace for future detailed reconstruction. The original standalone synthetic flow is also available.

Windows 11, Python 3.11+, no camera hardware or cloud services required. Four direct Python dependencies: NumPy, Pillow, Flask, and requests (plus their supporting packages). The UI and WebGL viewer are bundled JavaScript/CSS; no CDN, npm, Node, or internet is needed at runtime.

## Two-terminal HTTP demo on Windows

Open both PowerShell terminals in `pi-showcase-scanner`. Install or update the dependencies once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Terminal 1 — mock Pi node:**

```powershell
.\.venv\Scripts\python.exe -m pi_node.app --host 127.0.0.1 --port 8000 --mock
```

**Terminal 2 — capture, receive, reconstruct, and view:**

```powershell
.\.venv\Scripts\python.exe -m windows_client.client --node http://127.0.0.1:8000 --start-scan --steps 12
```

If your Python environment already has the requirements installed, the exact short commands are:

```powershell
# Terminal 1
python -m pi_node.app --host 127.0.0.1 --port 8000 --mock
# Terminal 2
python -m windows_client.client --node http://127.0.0.1:8000 --start-scan --steps 12
```

The client checks health, starts capture, polls progress, verifies 48 raw JPEGs and four backgrounds, runs both existing pipelines, and opens the model. Its viewer uses a free local port and prints the exact **`VIEWER: http://127.0.0.1:<port>/?scan=...`** URL, so it can coexist with the original UI on port 8765. Keep Terminal 2 open while viewing. Ctrl+C stops its viewer; completed files remain. Run the client again for a new scan with a unique ID.

The mock delay defaults to zero: a full 60-second rotation is simulated without waiting a minute. Add `--delay-between-steps-ms 250` to slow progress for a presentation. `timestamp_s` and `angle_deg` represent simulated table time; labels and `captured_at` record actual UTC wall time. No camera or motor is accessed.

```text
pi_node/scans/<scan_id>/
  raw/                     # 48 labeled JPEGs by default
  backgrounds/             # 4 matching backgrounds
  manifest.json            # Geometry, frames, sizes, SHA-256 digests
  request.json             # Request and idempotency metadata
  status.json              # Persisted progress

scans/received/<scan_id>/
  raw/
  backgrounds/
  transfer.json            # Source URL and manifest fingerprint
  manifest.json            # Published only after all images are verified
  outputs/
    quick/                 # preview.glb, preview.obj, mesh.json, stats.json
    detailed/              # images/, capture_manifest.json, job.json, README.md
    result.json
```

Node logs: `pi_node/logs/node.log`. Receiving-client logs: `logs/client.log`. Runtime data and logs are Git-ignored.

## Real Pi on the LAN (still mock capture)

Copy or clone the **whole repository** to the Pi; the mock backend reuses the shared synthetic renderer. From `pi-showcase-scanner`, using Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pi_node.app --host 0.0.0.0 --port 8000 --mock
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m windows_client.client --node http://raspberrypi.local:8000 --start-scan --steps 12
```

If mDNS does not resolve `raspberrypi.local`, substitute the Pi's actual LAN IP, such as `http://192.168.1.50:8000`. Both machines must be reachable on the same network and the node firewall must permit inbound TCP 8000. `0.0.0.0` is a listen address, not a client destination. This unauthenticated HTTP demo service is intended for a trusted LAN; production authentication and HTTPS are not implemented.

## Retry or receive an existing node scan

The client prints its scan ID before starting. After an interruption, receive that ID without starting another capture:

```powershell
.\.venv\Scripts\python.exe -m windows_client.client --node http://127.0.0.1:8000 --scan-id scan_20260910_120000
```

Replace the example with the actual ID. The receiver reuses already verified images, retries each failed HTTP operation at most twice, and detects truncated or checksum-mismatched downloads. Images are first saved as `.jpg.part`; the complete manifest is published last. A receipt binds the folder to the node URL and manifest fingerprint. Repeated receipt of the same scan is safe. Different scans, modified local files, or different source URLs are rejected rather than overwritten. For ID collisions between nodes, use another `--root`. After switching from hostname to IP, use the original URL or a separate receive root.

Scan-start retries use persisted idempotency keys. Only one capture runs per node; another start or explicit duplicate ID returns HTTP 409. Run one node process per scan root. Restarting marks unfinished captures failed; completed scans stay available. There is no capture cancellation endpoint.

```powershell
# Finish after processing; print paths and a command to reopen the viewer.
.\.venv\Scripts\python.exe -m windows_client.client --node http://127.0.0.1:8000 --start-scan --steps 12 --no-viewer

# Serve the viewer and print its URL without launching a browser.
.\.venv\Scripts\python.exe -m windows_client.client --node http://127.0.0.1:8000 --scan-id scan_20260910_120000 --no-browser

# Browse or watch the received library later using the original local UI.
.\.venv\Scripts\python.exe -m windows_client --root scans/received serve --watch
```

Options include `--timeout 15` (read timeout; connection timeout at most three seconds), `--scan-timeout 300`, `--transfer-timeout 300`, `--poll-interval 0.5`, `--viewer-port 0` (automatic), `--grid 32`, and `--root <receive-folder>`. Increase the scan timeout for intentionally long capture delays. A client timeout does not cancel capture on the node. After an abrupt client crash, confirm it has stopped, remove that scan's stale `.receiving` lock, and resume. Ordinary failures release the lock automatically.

See [the implemented Pi API](docs/pi_node_api.md) for endpoint examples.

## Run on Windows

### Physical Pi camera capture

Run these from the repository root on the Pi, with the Python environment installed/activated as above. This path uses the installed system `rpicam-still`, falling back to `libcamera-still`; it does not install an Arducam driver or configure overlays. Camera options follow the [official Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html).

```bash
python -m pi_node.camera_backends.rpicam_backend --diagnose
python -m pi_node.camera_backends.rpicam_backend --test-capture
python -m pi_node.app --host 0.0.0.0 --port 8000 --backend rpicam
```

Diagnostics print parsed camera entries, raw output, selected command and errors. Test capture saves a uniquely named JPEG in `pi_node/test_captures/`; `--output test.jpg --camera 0` can select the path/device. Existing images are never overwritten. If tools are missing, diagnostics exit with a clear error; the HTTP server still starts and reports `ready=false` until detection succeeds. `GET /api/v1/cameras` retries detection. There is no image capture at server startup: the POST start request is the start signal.

The physical default is **combined output**, because the quad kit's camera routing/layout has not been verified. Inspect the diagnostic JPEG first. Combined mode captures one rpicam device and stores its image unchanged as `raw_combined/step_000_quad.jpg`. This flag describes the expected layout; it cannot make hardware produce a quad mosaic. If diagnostics and actual images establish four independent devices, use `--no-combined-quad-output`; those captures are sequential, not simultaneous.

From the Windows repository terminal (activated environment), first remove the object and capture the empty table:

```powershell
python -m windows_client.client --node http://raspberrypi.local:8000 --capture-backgrounds
```

Wait for completion, place the object, start the table and let its speed settle. Then run either:

```powershell
python -m windows_client.client --node http://raspberrypi.local:8000 --start-scan --steps 12 --rotation-seconds 60
python -m windows_client.client --node http://raspberrypi.local:8000 --start-scan --steps 24 --rotation-seconds 60 --quality fast
```

The intervals are 5 and 2.5 seconds. The first command starts at time zero; the last step starts at 55 or 57.5 seconds, without a duplicated 360-degree view. Each rpicam invocation includes a 1-second settling period plus launch/exposure/storage time. Monotonic deadlines prevent accumulated sleep drift. If a step overruns the next deadline, the scan fails clearly instead of catching up in a burst. Four sequential commands may exceed a 2.5-second interval; use fewer steps or a verified combined device. Exposure time is estimated from the command midpoint, not a hardware timestamp; per-image times/angles and scheduling logs are saved. There is no encoder synchronization.

Additional options: `--capture-width 1280 --capture-height 720`, `--exposure-time 10000` (microseconds), `--gain 1`, `--awb daylight`, `--focus-mode manual --lens-position 1` (dioptres), `--no-use-backgrounds`, `--cameras 4`. Hardware must support each control. Capture backgrounds with the same resolution, layout, exposure and focus settings as the scan. Missing or incompatible references do not block scanning; the manifest records `backgrounds_available=false`. Current references live in `pi_node/backgrounds/current/` and are copied into each scan. They are reusable; physical scans are retained rather than automatically deleted.

The client detects the backend through health, starts/polls/downloads with the existing timeout and checksum protections, prepares detailed images and opens its local viewer. **Separate views** automatically attempt quick reconstruction using approximate camera geometry. **Unsplit combined frames** appear in the photo gallery and detailed staging, with a clear “layout needs verification” message; no GLB/OBJ is invented. `--split-combined-output` is reserved and rejected until we physically verify crops/orientations and implement `pi_node/splitter.py`.

For a timed hardware-free rehearsal, run the node with `--backend mock` (or `--mock`) and explicitly pass `--rotation-seconds`; omit that option for the original fast simulated workflow. `--combined-quad-output` on mock mode creates a synthetic mosaic to test transport/staging, not to establish the real kit's layout.

See the [physical demo runbook](docs/physical_demo_runbook.md) and [API contract](docs/pi_node_api.md).

## Inspect photos and choose how many images to use

Open **View source photos** below the model's image/timing counters. It shows low-resolution thumbnails for generated, imported, and received scans. Filter between **All captured photos**, **Used in last quick preview**, and **Background references**. Each captured photo indicates whether it was used. Click a thumbnail for a larger preview (maximum 640 pixels). Browsing generates thumbnails in memory only; it does not duplicate images on disk or modify the originals.

The old **24 / 48** display reflected a speed cap: the quick pipeline sampled 24 evenly spaced views from 48 available images. In **Images for quick preview**, enter any count up to the available total, or click **Use all**, then **Run preview**. This reruns the same scan without generating a different object. More images add viewpoints and processing time; detailed preparation always receives every raw image. New results save the exact used filenames in `outputs/quick/stats.json` as `frame_files`; old results without that list show an explicitly inferred selection in the gallery.

CLI equivalents (the original CLI's global options go before its subcommand):

```powershell
# All images from a local scan. Zero means all.
.\.venv\Scripts\python.exe -m windows_client --max-frames 0 process scans/received/scan_20260911_120000

# Make all images the default for a library's UI/watcher.
.\.venv\Scripts\python.exe -m windows_client --root scans/received --max-frames 0 serve --watch

# All images for a newly received Pi scan.
.\.venv\Scripts\python.exe -m windows_client.client --node http://127.0.0.1:8000 --start-scan --steps 12 --max-frames 0
```

Replace example scan IDs with real folders. The default is now all available images; `--max-frames 48` sets an explicit cap, and `--max-frames 0` uses all available images. Changing the count invalidates the processing cache automatically.

### Random generations and automatic cleanup

Each **Generate test scan** creates a new random object. A single shape seed is used across all views of that scan so reconstruction stays coherent. The Pi mock backend also changes its shape for every new scan ID.

Generated demos now last until the next generation succeeds or you click **Done — delete this generation**. Cleanup deletes the entire generated folder, including raw/background images, GLB/OBJ files, and the detailed image copies. Download any model you want to keep first. Imports are marked for retention and are not automatically deleted. Re-running a preview uses the selected scan's existing images, so it reconstructs the same object.

The Pi node removes old mock images after the next successful capture. Done also asks the original node to delete its copy; if it is offline, the local files are still deleted and the node cleans its old copy on its next scan. Small `.deleted/` records retain only request/idempotency metadata, not images or models. Deleted scans cannot be resumed. This cleanup policy supersedes the original indefinite-retention behavior described below.

If a browser was already open before updating, restart its Python server and refresh the page to load the new behavior.

Open PowerShell in this repository's `pi-showcase-scanner` directory. Confirm `python --version` reports 3.11 or newer (3.12 is the tested baseline).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m windows_client
```

This opens **http://127.0.0.1:8765** in your default browser. Keep the terminal open; Ctrl+C stops the client. No PowerShell activation or execution-policy change is required.

For the exact dependency versions verified on Python 3.12, install `-r requirements-lock.txt` instead. `requirements.txt` permits compatible releases for other supported Python versions.

1. Click **Generate test scan**. It renders 48 JPEGs: four cameras at each of 12 table positions.
2. Click **Run preview**. The coarse model appears automatically in the viewport.
3. Drag to orbit and scroll to zoom. Keyboard: arrow keys, `+` / `-`, `R` to reset.
4. Download the **GLB** or **OBJ**, or find the files under `scans/<scan_id>/outputs/quick/`.
5. Find the prepared detailed workspace under `scans/<scan_id>/outputs/detailed/`.

For a single-command demo that generates, processes, and opens the result:

```powershell
.\.venv\Scripts\python.exe -m windows_client demo
```

If port 8765 is busy:

```powershell
.\.venv\Scripts\python.exe -m windows_client serve --port 8766
```

## Import, watch, and CLI use

Paste an existing scan folder's absolute path into **Import a scan folder**. The folder must contain a complete `manifest.json` using the contract below. Import copies only referenced raw images and backgrounds into the local library; it does not modify the source. Name collisions get a short suffix. Select the imported scan and run its preview.

```powershell
# Generate only; prints the generated scan path.
.\.venv\Scripts\python.exe -m windows_client generate --steps 12

# Import only; then select it in the web UI or process its printed destination.
.\.venv\Scripts\python.exe -m windows_client import "C:\captures\scan_20260910_120000"

# Process an existing folder. Replace the example with your actual scan name.
.\.venv\Scripts\python.exe -m windows_client process "scans\scan_20260910_120000"

# Run the UI and automatically process new complete scans in the library.
.\.venv\Scripts\python.exe -m windows_client serve --watch

# Watch a different library without a UI. Global options come BEFORE the subcommand.
.\.venv\Scripts\python.exe -m windows_client --root "C:\captures" --grid 24 watch

# Display that same library in a browser, with automatic processing.
.\.venv\Scripts\python.exe -m windows_client --root "C:\captures" serve --watch
```

Watch polls every two seconds and waits for `status: complete` and all referenced files. Scans must be published atomically as described in [the folder contract](docs/scan-format.md). `--root` sets the actual managed library; this is not a second inbox that automatically copies elsewhere. The browser refreshes its scan list and results automatically. A content fingerprint skips unchanged successful runs. `process --force` forces a rebuild; the UI's Run preview also rebuilds.

A complete scan that fails reconstruction is retried by the watcher when its inputs change or the watcher restarts, avoiding a continuous failure loop. You can also rerun it manually.

Run one client against a given library. A per-scan lock prevents concurrent writes. After a crash, stop the old process and remove that scan's `.processing` file if the next run reports a stale lock.

## Preview geometry detail

Choose **Geometry detail**, then **Run preview** to rebuild the selected scan:

- **Fast**: 32³ grid, for the quickest coarse result.
- **Detailed** (default): 96³ grid, finer features and smoother curves.
- **Fine**: 160³ grid, more geometry at a higher processing and memory cost.

At the default 240 mm scene extent, these correspond to 7.5, 2.5, and 1.5 mm cells. These are sampling sizes, not guaranteed measurement accuracy. The viewer reports the grid used for the loaded result. Existing coarse results remain viewable; rerunning upgrades them. Image count remains independently adjustable.

The pipeline uses masks up to 640 pixels, rounds voxel stair steps with alternating smoothing passes, and exports smooth normals in both GLB and OBJ. Mesh extraction is batched with NumPy; no extra dependencies were added. It reconstructs from the photos, never from the synthetic object's stored seed or geometry.

This is still a silhouette visual hull: more detail improves visible outlines, but cannot recover recesses hidden in every silhouette, textures, or features absent from the images. Approximate camera calibration also limits real-photo accuracy. Full photogrammetry remains a later pipeline.

For CLI use, set `--grid 96` (default) or `--grid 160`. The accepted range is 12–160. Use `--max-frames 0` (default) for all photos. Both options invalidate the cached result when changed.

## What is implemented

- Randomized synthetic sculptures, consistent across each scan's camera views, rendered from approximate orthographic geometry.
- Background subtraction (or a border-color fallback when no background is supplied).
- A configurable 12–160³ voxel grid (96³ by default), carved using all images by default.
- A shared-vertex, smoothed surface with smooth normals exported as **glTF 2.0 GLB**, **OBJ**, and the identical triangles in viewer JSON.
- A browser viewer with orbit, zoom, reset, and model download links.
- Detailed image staging, metadata, and preparation notes. **No heavy reconstruction runs.**
- Local folder import, polling watcher, terminal logging, and `logs/scanner.log`.
- Scan validation, content-based caching, background jobs, atomic completion markers, and useful failure messages.

A measured 96³ preview on this laptop took about **0.65 seconds**, using all 48 input images and producing 18,540 triangles. This excludes synthetic generation, detailed image copying, and browser loading; your timing will vary. The UI reports its actual pipeline timing. `outputs/result.json` also records total processing time.

## Layout

```text
pi-showcase-scanner/
  pi_node/                 # Flask API, timed manager, mock and rpicam backends
  windows_client/          # HTTP receiver, CLI/UI, import/watch, detailed staging
  preview3d/               # Geometry, segmentation, voxel carving, mesh export
  viewer/                  # Fully local browser UI and WebGL renderer
  docs/                    # Scan format, JSON schema, architecture, Pi API
  sample_data/             # Synthetic scan generator and example manifest
  tests/                   # Pipeline and HTTP integration checks
  scans/                   # Generated/imported scans (gitignored)
  logs/                    # Runtime logs (gitignored)
  requirements.txt
```

See [scan format](docs/scan-format.md), [architecture](docs/architecture.md), and [Pi API](docs/pi_node_api.md).

## Fidelity and physical capture limitations

This is a **visual hull**, not feature-based photogrammetry. It cannot recover hidden concavities or textures. Scale and pose are approximate. A background-free, centered, opaque object with contrast against its background works best. The MVP assumes orthographic images aimed at the scene origin; perspective distortion, the real table, shadows, inaccurate angles, or a drifting object can cause poor or empty models.

The nominal table speed is one revolution per 60 seconds: `angle_deg = timestamp_s × 6` for ideal constant-speed motion. Twelve positions are five seconds / 30° apart. There is no duplicate 360° frame. Real captures should store measured or timestamp-derived angles per image, including offsets if cameras fire sequentially. A motor encoder and calibrated intrinsics/extrinsics are future upgrades.

Detailed reconstruction gets all original images, but moving-object/static-background scenes need masks and real camera calibration. The synthetic images have little texture and are meant for flow testing, not COLMAP feature matching.

The node uses `MockCapture` or `RpicamBackend` in `pi_node/camera_backends/`; `capture.py` and `service.py` remain compatibility imports. Real subprocess capture is implemented, but Arducam routing/layout, hardware synchronization, motor control, and calibration are not verified or implemented. Mock images include a labeled footer excluded from silhouette carving by `preview_crop`. A future detailed engine should crop that footer too; this step stages original images. Transfers are sequential, with whole-file retries, a 50 MiB per-image limit and a 2 GiB per-scan limit. The HTTP acceptance flow is tested on Windows localhost; physical Pi hardware and LAN operation still need testing on your equipment. Pi connection/capture is currently CLI-driven; the existing web UI displays the received library when started with `--root scans/received`.

## Checks and troubleshooting

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- **Python is not found:** install Python 3.11+ and enable its PATH option; reopen PowerShell. You can also use `py -3.12 -m venv .venv` if the Python launcher is installed.
- **Preview is empty:** inspect `outputs/quick/silhouette.png`, then check camera elevation/azimuth, field width, rotation sign, and object centering. Detailed images are still staged when quick reconstruction fails.
- **Watcher sees no scan:** publish a complete manifest after copying all images; press Refresh to inspect a manually managed library.
- **WebGL unavailable:** enable hardware acceleration in your browser or open the exported GLB/OBJ in another local 3D viewer. The files still export without WebGL.
- **No internet on the demo laptop:** install dependencies before disconnecting. To provision another offline Windows laptop with the same Python version and architecture, download wheels on a connected matching machine using `python -m pip download -r requirements.txt -d wheelhouse`, copy the folder, then install using `python -m pip install --no-index --find-links wheelhouse -r requirements.txt`.
- **Repeated local port conflict:** stop the existing client or use `serve --port 8766`.

## GitHub

Generated scans, models, logs, bytecode, and virtual environments are ignored. The source has no credentials, camera SDK, external service configuration, or vendored runtime assets. Synthetic images are generated from code, so no dataset download or image license is required.

If this directory is not already inside your intended Git repository:

```powershell
git init
git add .
git commit -m "Build local Pi scanner MVP"
```

If it already belongs to a repository, use that repository's normal add/commit workflow. Create an empty GitHub repository, add its URL as your remote, and push your branch. No remote is created and nothing is published automatically. Review `git status --short` before committing.

The included `.github/workflows/checks.yml` runs the integration tests when `pi-showcase-scanner` is the GitHub repository root. If keeping it inside a parent repository, move that workflow to the parent's `.github/workflows/` (it detects either layout).
