# Pi node HTTP API v1

Implemented by `pi_node/app.py` (Flask), software version **0.2.0**. Base URL: `http://127.0.0.1:8000/api/v1` locally, or `http://raspberrypi.local:8000/api/v1` on the LAN. Paths below include the full API prefix. All responses are JSON except image downloads.

Start: `python -m pi_node.app --host 127.0.0.1 --port 8000 --mock`. For physical capture use `--backend rpicam`. Install the root requirements first. Missing camera tools leave the service reachable with `ready=false`. No motor control is implemented. Physical extensions below add to the original mock examples.

## GET /api/v1/health

Request: no body.

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Response: HTTP 200.

```json
{"status":"ok","hostname":"raspberrypi","software_version":"0.2.0","api_version":1,"mock_mode":true}
```

The hostname comes from the machine running the node. Health reports service availability; scan failures appear in scan status.

## GET /api/v1/cameras

Request: no body.

```bash
curl http://127.0.0.1:8000/api/v1/cameras
```

Response: HTTP 200.

```json
{"cameras":[
  {"id":"cam_01","status":"configured","mock":true},
  {"id":"cam_02","status":"configured","mock":true},
  {"id":"cam_03","status":"configured","mock":true},
  {"id":"cam_04","status":"configured","mock":true}
]}
```

These are configured virtual views, not detected physical devices.

## POST /api/v1/scan/start

Requires `Content-Type: application/json`. `{}` starts a default scan. Example body:

```json
{"scan_id":"scan_20260910_120000","steps":12,"cameras":4,"mode":"mock","delay_between_steps_ms":0,"output_format":"jpg"}
```

PowerShell request:

```powershell
$captureRequest = @{steps=12; cameras=4; mode="mock"; delay_between_steps_ms=0; output_format="jpg"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/scan/start -ContentType application/json -Body $captureRequest
```

| Parameter | Default | Allowed values |
| --- | --- | --- |
| `scan_id` | Generated UTC ID | `scan_YYYYMMDD_HHMMSS`, optional `_alphanumeric` suffix, max 80 characters |
| `steps` | 12 | Integer 3–120 |
| `cameras` | 4 | Integer 1–4; uses the first N cameras |
| `mode` | Node backend | `mock` or `rpicam`; must match the node |
| `delay_between_steps_ms` | 0 | Integer 0–10000; pause between steps, added to rendering time |
| `output_format` | `jpg` | Only `jpg` |

Unknown fields, invalid types (including booleans for integer counts), and unsupported modes/formats return 400. The delay is not a precise camera interval. Mock frames simulate a full rotation even at zero delay.

Response: HTTP 202 immediately.

```json
{"scan_id":"scan_20260910_120000","state":"pending","current_step":0,"total_steps":12,"files_captured":0,"error":null,"status_url":"/api/v1/scan/status/scan_20260910_120000"}
```

States: `pending → capturing → complete` or `failed`. Another capture while busy returns 409. Explicit duplicate IDs return 409; generated IDs get a random suffix on collision. Previous scans are never overwritten.

Optional header `Idempotency-Key`: 1–128 ASCII letters, digits, underscores, or hyphens. Same key and normalized request return the original scan/current state with HTTP 202. Same key with different parameters returns 409. Keys persist in `request.json` across restarts for the scan's lifetime. The Windows client sends this header to make automatic POST retries safe.

## GET /api/v1/scan/status/{scan_id}

Request: no body.

```bash
curl http://127.0.0.1:8000/api/v1/scan/status/scan_20260910_120000
```

Response: HTTP 200.

```json
{"scan_id":"scan_20260910_120000","state":"capturing","current_step":3,"total_steps":12,"files_captured":14,"error":null}
```

`current_step` counts fully completed steps, from zero to `total_steps`. Frame filenames use zero-based step numbers. `files_captured` counts saved raw images, excluding backgrounds. Four cameras and 14 files therefore mean three complete steps and two images of the next step.

Completed default scan: `state: complete`, `current_step: 12`, `files_captured: 48`. Failure example:

```json
{"scan_id":"scan_20260910_120000","state":"failed","current_step":3,"total_steps":12,"files_captured":14,"error":"Simulated camera failure"}
```

Unknown scans return 404. Progress is persisted in `status.json`. Restarting marks unfinished captures failed unless a complete, validated manifest was already published. Failed captures require a new scan; partial rotations are not resumed.

## GET /api/v1/scans

Request: no body.

```bash
curl http://127.0.0.1:8000/api/v1/scans
```

Response: HTTP 200, retained scans in descending ID order (including pending/failed scans).

```json
{"scans":[{"scan_id":"scan_20260910_120000","state":"complete","current_step":12,"total_steps":12,"files_captured":48,"error":null}]}
```

An empty node returns `{"scans":[]}`. Deleted generations are omitted; see the DELETE endpoint below.

## GET /api/v1/scans/{scan_id}/manifest

Request: no body.

```bash
curl http://127.0.0.1:8000/api/v1/scans/scan_20260910_120000/manifest
```

Response: HTTP 200 with complete `manifest.json`. HTTP 409 for pending, capturing, or failed scans; 404 for unknown IDs.

It follows the [v1 scan format](scan-format.md) and [JSON schema](manifest.schema.json), with transfer/crop/timing extensions. This response example is **abbreviated** to one camera/frame/file; the real default has four cameras, 48 frames and 52 file records. Its sample digest is illustrative.

```json
{
  "schema_version":1,"scan_id":"scan_20260910_120000","status":"complete",
  "source":"pi_mock","created_at":"2026-09-10T12:00:00+00:00",
  "mock_mode":true,"timing":"simulated","delay_between_steps_ms":0,
  "projection":"orthographic","units":"meters","rotation_period_s":60.0,
  "rotation_direction":"positive_y_right_hand","scene_extent_m":0.24,
  "cameras":[{
    "id":"cam_01","azimuth_deg":-35,"elevation_deg":5,
    "distance_m":0.5,"height_m":0.0436,"ortho_width_m":0.30,
    "background":"backgrounds/cam_01.jpg","preview_crop":[0,0,320,320]
  }],
  "frames":[{
    "file":"raw/step_000_cam_01.jpg","camera_id":"cam_01","step":0,
    "timestamp_s":0.0,"angle_deg":0.0,"captured_at":"2026-09-10T12:00:00.123456+00:00"
  }],
  "files":[{
    "file":"raw/step_000_cam_01.jpg","size_bytes":11234,
    "sha256":"0000000000000000000000000000000000000000000000000000000000000000"
  }]
}
```

`files` lists **every** referenced raw image and background, with relative path, byte count and lowercase SHA-256 digest. Metadata files themselves are not in that list. `timestamp_s`/`angle_deg` simulate one revolution per minute; `captured_at` is actual UTC time. The 320×376 JPEG has a 320×320 object region plus a 56-pixel label footer. `preview_crop` defines exclusive right/bottom pixel bounds; preview calibration applies to that crop. The original labeled images are retained in detailed staging.

## GET /api/v1/scans/{scan_id}/files/{filename}

Request: no body. Bare names address raw images; qualified paths address raw images or backgrounds.

```bash
curl -o frame.jpg http://127.0.0.1:8000/api/v1/scans/scan_20260910_120000/files/step_000_cam_01.jpg
curl -o frame.jpg http://127.0.0.1:8000/api/v1/scans/scan_20260910_120000/files/raw/step_000_cam_01.jpg
curl -o background.jpg http://127.0.0.1:8000/api/v1/scans/scan_20260910_120000/files/backgrounds/cam_01.jpg
```

Response: HTTP 200, JPEG bytes, with these headers:

```text
Content-Type: image/jpeg
Content-Length: <image byte count>
ETag: "<SHA-256>"
X-Content-SHA256: <SHA-256>
```

`If-None-Match` is supported (304 when unchanged). Only exact manifest-listed files can be downloaded. Unknown files/private metadata return 404, incomplete/failed scans return 409. Traversal cannot access arbitrary node files. The Windows client independently checks manifest byte counts and hashes.

## Errors, reliability, and limitations

### DELETE /api/v1/scans/{scan_id}

Delete a completed/failed mock generation and its image files. No request body. Returns `{"scan_id":"scan_20260910_120000","deleted":true}` with HTTP 200. Repeated deletion is safe; active captures return 409, unknown IDs return 404. The node also performs this cleanup for older mock generations after a new capture succeeds.

Only a small request/status tombstone remains under `.deleted/` to prevent ID reuse or accidental new captures on an idempotent retry. Deleted scans are omitted from listing, their status reports `state: failed`, `deleted: true`, and their manifest returns HTTP 410 (`scan_deleted`). This replaces the earlier indefinite scan-retention behavior. Mock objects now vary per scan; one stable `object_seed` identifies the geometry used across all views.

Example JSON error:

```json
{"error":{"code":"node_busy","message":"A scan is already in progress"}}
```

Codes: `invalid_request` (400), `scan_not_found` / `file_not_found` (404), `node_busy` / `scan_exists` / `idempotency_conflict` / `scan_not_complete` (409), `node_stopping` (503), `internal_error` (500). Flask protocol errors use `http_error`; examples include missing JSON content type (415) and body larger than 8 KiB (413).

The client saves under `scans/received/{scan_id}`, downloads to `.jpg.part`, verifies every image, and publishes the complete local manifest last. It reuses verified files when resuming, retries incomplete images in full, and refuses to overwrite conflicting files/scans. `transfer.json` binds the local folder to the original node URL and manifest. There is no byte-range resume in the client. Connect/read timeouts, bounded retries, and capture/transfer deadlines prevent unbounded ordinary waits.

Run one node process per storage root. Real rpicam/libcamera detection and subprocess capture are implemented. No authentication, HTTPS, live streaming, motor control, verified quad splitting, explicit capture cancellation or heavy reconstruction is implemented. Use localhost or a trusted LAN. Node metadata tracks its own jobs; arbitrary copied folders are not adopted. Capture is CLI-driven on Windows; the web UI displays `scans/received` using its `--root` option.

## Physical extensions (software 0.3.0)

### Health and camera detection

GET /api/v1/health adds the following fields; the original service fields remain:

```json
{"backend":"mock","ready":true,"camera_command_found":false,"camera_command_used":null,"camera_count":4,"detected_camera_count":4,"combined_quad_output":false,"last_error":null}
```

status=ok means the HTTP service is alive. ready reports camera detection. camera_count is the configured four-camera setup; detected_camera_count counts enumerated devices. A mosaic device may enumerate once. combined_quad_output describes the backend default, not proof of hardware layout. GET /cameras retries detection after configuration changes; health reuses the last detection.

Physical GET /api/v1/cameras response example:

```json
{"cameras":[{"index":0,"description":"imx219 [3280x2464]"}],"raw_output":"Available cameras\n0 : imx219 [3280x2464]","command_used":"/usr/bin/rpicam-still","camera_command_found":true,"error":null}
```

Mock camera entries remain cam_01–cam_04 and add raw_output, command_used=null and error=null. Failure returns an empty camera list, available raw output and a clear error. The backend prefers rpicam-still, falling back to libcamera-still.

### POST /api/v1/backgrounds/capture

Remove the object first. Send JSON, with the same camera settings as scan/start; `{}` uses defaults. Idempotency-Key has the same semantics as scans.

```bash
curl -X POST http://raspberrypi.local:8000/api/v1/backgrounds/capture -H 'Content-Type: application/json' -H 'Idempotency-Key: background-001' -d '{"camera_count":4,"combined_quad_output":true}'
```

HTTP 202 example (abbreviated status):

```json
{"scan_id":"scan_20260911_120000","operation":"backgrounds","state":"pending","current_step":0,"total_steps":1,"files_captured":0,"elapsed_seconds":0,"estimated_remaining_seconds":0,"error":null,"status_url":"/api/v1/scan/status/scan_20260911_120000"}
```

Poll the returned status URL until complete/failed. Background jobs share the ID/status namespace but are excluded from scan listings. Images and logs remain in the job folder. A complete set is published under pi_node/backgrounds/current/ using immutable names and an atomic index. Scans copy references only when backend/layout/resolution/control settings match. Missing or changed references set backgrounds_available=false and a warning; capture still runs. The accelerated legacy mock flow can generate synthetic references automatically.

### Additional scan/start parameters

```bash
curl -X POST http://raspberrypi.local:8000/api/v1/scan/start -H 'Content-Type: application/json' -H 'Idempotency-Key: physical-001' -d '{"steps":12,"rotation_seconds":60,"camera_count":4,"combined_quad_output":true,"use_backgrounds":true}'
```

| Field | Default | Meaning |
|---|---|---|
| rotation_seconds | 60 | Finite number 0.01–3600 |
| camera_count | 4 | Alias for cameras, integer 1–4; aliases must agree |
| combined_quad_output | true for rpicam, false for mock | Capture one device image per step unchanged under raw_combined/; cannot configure device routing |
| split_combined_output | false | true is rejected until physical crop/orientation verification |
| capture_width / capture_height | null | Set both or neither; integers 64–8192 |
| exposure_time | null | Shutter microseconds, 1–10000000 |
| gain | null | Number 0.1–64 |
| awb | null | auto, incandescent, tungsten, fluorescent, indoor, daylight, cloudy, custom |
| focus_mode | null | default, manual, auto, continuous |
| lens_position | null | 0–100 dioptres; manual focus selected automatically if omitted |
| use_backgrounds | true | Copy matching references when available |

Camera-control support depends on hardware. Mock controls are recorded for contract testing; the renderer retains its fixed test size/appearance. Output remains JPG only. Nonzero delay_between_steps_ms is rejected on rpicam. Explicit rotation_seconds enables timed mock scans; otherwise mock stays accelerated.

The POST is the start signal. Start the table before sending it. Interval = rotation_seconds / steps: 5 seconds for 12/60 and 2.5 for 24/60. Steps start at zero and end at 55/57.5 seconds; completion follows the last capture, without a duplicate at 360 degrees. Monotonic deadlines avoid accumulated sleeps. Each command includes one-second settling plus launch/exposure/storage time. Independent devices capture sequentially; too few detected devices causes a clear error. A step that overruns the next deadline fails instead of catching up in a burst.

### Progress extensions

Status responses add operation (scan/backgrounds), elapsed_seconds, estimated_remaining_seconds and planned_seconds. Example:

```json
{"scan_id":"scan_20260911_120000","operation":"scan","state":"capturing","current_step":3,"total_steps":12,"files_captured":3,"elapsed_seconds":11.2,"estimated_remaining_seconds":48.8,"planned_seconds":60,"error":null}
```

Remaining time estimates rotation duration and becomes zero on completion/failure. Background jobs count their reference images instead of raw images. Failed captures preserve files/logs/errors. Restart marks unfinished work failed. Only complete scan manifests, not background-job audit manifests, are downloadable.

### Manifest and transfer extensions

Each scan includes backend, camera_command, camera_count, steps, rotation_seconds, capture_interval_seconds, capture_width/height, combined_quad_output, split_combined_output, backgrounds_available, capture_started_at, capture_completed_at, raw_files, raw_combined_files, outputs, errors, software_version, settings, warnings and capture_logs. Requested dimensions are separate from observed dimensions in each capture log.

Combined scans have frames=[] and combined_frames with file/camera_id/step/timestamp_s/angle_deg/captured_at records. raw_combined_files lists those same paths; raw_files lists normal frame paths. The single combined camera record identifies the device, not four verified sub-image poses. preview_ready=false indicates layout verification is required. Physical timestamps and angles estimate command midpoints, not hardware exposure timestamps. geometry_calibrated=false records the use of rough geometry defaults.

GET /scans/{id}/files/raw_combined/step_000_quad.jpg returns JPEG bytes with the same headers and checks as raw/. The files list must exactly match all raw/combined/background references. Windows downloads and displays mosaics, prepares detailed staging and reports an unavailable quick model instead of inventing geometry. Physical scans and reference captures are retained; DELETE remains limited to disposable mock generations.

See the [physical demo runbook](physical_demo_runbook.md). Actual hardware operation, quad layout and camera geometry still require verification on this equipment.
