# Scan format, version 1

```text
scans/
  scan_YYYYMMDD_HHMMSS/
    raw/
      step_000_cam_01.jpg
      step_000_cam_02.jpg
      step_000_cam_03.jpg
      step_000_cam_04.jpg
      step_001_cam_01.jpg
      ...
    backgrounds/
      cam_01.jpg
      cam_02.jpg
      cam_03.jpg
      cam_04.jpg
    manifest.json
    outputs/
      quick/
        preview.glb
        preview.obj
        mesh.json
        silhouette.png
        stats.json
      detailed/
        images/                  # Copies of all raw frames; no backgrounds
        capture_manifest.json
        job.json
        README.md
      result.json                # Written last after BOTH paths succeed
```

Scan IDs use UTC and may have an alphanumeric collision suffix. The sample generator uses `scan_YYYYMMDD_HHMMSS`; folder names and manifest IDs should match. Forward-slash image paths are relative to the scan folder and must resolve inside `raw/` or `backgrounds/`. Do not use absolute paths. Image basenames must be unique for the detailed export.

See [manifest.schema.json](manifest.schema.json) for the machine-readable contract and [example_manifest.json](../sample_data/example_manifest.json) for a complete generated example. The Python loader validates the required subset without needing a JSON Schema package.

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `scan_id` | Scan directory name |
| `status` | `capturing`, `complete`, or `failed`; only complete scans process |
| `source` | `synthetic` or `pi` (informational) |
| `created_at` | UTC ISO-8601 timestamp |
| `units` | `meters` |
| `projection` | `orthographic` for this preview |
| `rotation_period_s` | `60.0` nominal period |
| `rotation_direction` | `positive_y_right_hand` for generated data |
| `scene_extent_m` | Full side length of the carving cube, centered at the origin |
| `cameras` | Unique `cam_NN` definitions |
| `frames` | Explicit frame file, camera ID, step, elapsed timestamp, rotation angle |

Camera definitions include `azimuth_deg`, `elevation_deg`, and `ortho_width_m` (horizontal field width at the object). Optional `background` references an empty-scene JPEG of exactly the same dimensions as its camera's object images. Without it, the preview estimates a uniform background from image-border colors. `distance_m` and `height_m` are informative estimates; the orthographic preview uses field width and angles directly.

Coordinate system: meters, **Y up**, origin at the object's approximate center. At azimuth 0° a camera is on +Z, looking toward the origin, with image-right along +X. Positive azimuth moves toward +X. Positive elevation looks down toward the origin. The object rotates right-handed about +Y; therefore the effective object-local camera azimuth is `camera.azimuth_deg - frame.angle_deg`. All cameras aim at the same origin; off-center principal points and lens distortion are not supported yet.

For rectangular images the vertical field is `ortho_width_m * image_height / image_width`. Optional camera `preview_crop: [left, top, right, bottom]` crops both object and background images first; use the cropped dimensions for that formula. Right/bottom bounds are exclusive. Mock node images use `[0, 0, 320, 320]` to exclude their labeled footer. Frame angles are authoritative; the preview does not infer them from filenames or assume all four cameras fired at exactly the same instant. At nominal speed: `angle_deg = 360 * timestamp_s / rotation_period_s`.

Node captures additionally include `mock_mode`, `timing: simulated`, `delay_between_steps_ms`, per-frame `captured_at` (actual UTC time), and a `files` list with `{file, size_bytes, sha256}` for every raw/background image. These extensions remain optional for standalone synthetic scans. The node receiver requires the complete transfer list and validates bytes/hashes before publishing its local manifest. `source: pi_mock` distinguishes node-rendered data. Node scans live in `pi_node/scans/`; received scans live in `scans/received/`. See [Pi node API](pi_node_api.md).

## Publishing a complete scan

1. Create the scan directory with `status: capturing` (or omit the manifest while transferring).
2. Write each frame/background to a temporary name, close it, and rename it to its final name.
3. Write the final complete manifest to `manifest.json.tmp` and atomically replace `manifest.json` **last**.
4. Treat raw inputs and the manifest as immutable after completion. Publish a new scan for a new capture.

The watcher retries incomplete or missing files and checks every two seconds. Atomic completion is required: a valid-looking JPEG can still be partially transferred. Both import and processing fingerprint inputs to detect changes. `outputs/result.json` marks a successful full run; older model files can remain after a later failed attempt, so their mere existence does not indicate success.

`outputs/quick/stats.json` reports actual quick runtime, voxel count, triangle count, and selected frame count. `outputs/detailed/job.json` says `prepared_only`; it must never be interpreted as a finished detailed reconstruction.


## Physical capture extension

Real scans use source pi_rpicam and retain=true. Existing synthetic/individual frame manifests remain valid. Unsplit combined scans use combined_quad_output=true, split_combined_output=false, frames=[], combined_frames containing the usual frame metadata with raw_combined/step_NNN_quad.jpg paths, and raw_combined_files listing those paths. They are transferable and inspectable but cannot run the silhouette pipeline until camera crops and orientations are verified. Separate views use raw_files/frames as before.

All images including references appear in files with byte counts and SHA-256. settings saves the capture request. capture_logs saves command output, dimensions and planned/actual timings; errors and warnings persist problems. Capture timestamps, rotation period, requested dimensions, backend/command and software version are recorded. See pi_node_api.md for every field. Background-job manifests are internal audit records, not input scan manifests.
