# Physical demo runbook

## Setup checklist

- Secure the four cameras and cables clear of the rotating table. Power down before adjusting camera ribbons.
- Put the Pi and Windows laptop on the same trusted LAN. Record the Pi hostname/IP.
- Install this repository's Python environment on both machines. Configure the Pi OS/vendor camera stack separately; this project does not install drivers or overlays.
- Run the diagnostic and test capture below. Inspect whether the JPEG contains one view or a mosaic, its borders, orientation and camera mapping. Four lenses need not enumerate as four devices.

## Lighting checklist

- Use steady diffuse lighting; avoid flicker, moving shadows and reflective objects.
- Center a matte object with good background contrast, fully visible throughout rotation.
- Keep camera placement and lighting fixed between backgrounds and object capture.
- Lock supported exposure, white balance and focus after inspecting a test photo. Repeat those settings for background and scan commands.

## Start the Pi

From the repository root, in its activated Python environment:

```bash
python -m pi_node.camera_backends.rpicam_backend --diagnose
python -m pi_node.camera_backends.rpicam_backend --test-capture
python -m pi_node.app --host 0.0.0.0 --port 8000 --backend rpicam
```

Test images have unique names in `pi_node/test_captures/`. Optionally use `--output test.jpg --camera 0`; existing files are protected. Keep the node terminal running. It captures images only when requested. Use `--backend mock` for a rehearsal.

The physical default is combined output: one enumerated device produces one unchanged image per step. This setting cannot make the hardware produce a mosaic. If actual captures establish independent devices, use `--no-combined-quad-output` on both background and scan commands. Separate captures are sequential, not synchronized.

## Capture the empty table

Remove the object. From the Windows repository terminal:

```powershell
python -m windows_client.client --node http://raspberrypi.local:8000 --capture-backgrounds
```

Wait for completion. References are published under `pi_node/backgrounds/current/`; their index records matching settings. Background jobs retain their images and logs for diagnosis. Changing layout, resolution, exposure or focus requires matching new references. Missing references do not prevent scanning; the manifest records their absence.

## Place the object and start the scan

Place the object near the rotation axis. Start the table and let its speed settle near 1 RPM. The client request establishes the time origin; there is no home-angle trigger or motor control.

```powershell
python -m windows_client.client --node http://raspberrypi.local:8000 --start-scan --steps 12 --rotation-seconds 60
```

For 24 positions:

```powershell
python -m windows_client.client --node http://raspberrypi.local:8000 --start-scan --steps 24 --rotation-seconds 60 --quality fast
```

Intervals are 5 or 2.5 seconds. The last step begins at 55 or 57.5 seconds; there is no duplicate at 360 degrees. Each command includes a one-second settling period plus launch/exposure/storage time. Four separate commands may exceed 2.5 seconds. Start with 12 steps and reduce if necessary. Quality controls Windows reconstruction only.

Optional supported settings: `--capture-width 1280 --capture-height 720 --exposure-time 10000 --gain 1 --awb auto --focus-mode manual --lens-position 1`. Exposure is in microseconds and lens position in dioptres. Actual camera support varies. Use identical settings for backgrounds.

## Expected output

For the verified 1080p autofocus settings, run these separately from Windows, first with the table empty and then with the object placed:

```powershell
python -m windows_client.client --node http://192.168.127.145:8000 --capture-backgrounds --capture-width 1920 --capture-height 1080 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 5000 --awb auto
python -m windows_client.client --node http://192.168.127.145:8000 --start-scan --steps 12 --rotation-seconds 60 --capture-width 1920 --capture-height 1080 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 5000 --awb auto --quality fast
```

Update the code on both machines and restart the Pi node first. The 5-second camera settling timeout plus command overhead may exceed this scan's 5-second interval; the existing scheduler reports a missed-deadline error. Reduce steps if needed while keeping rotation_seconds equal to the measured table period. Advanced locked red/blue gains are available with `--awbgains 1.0,1.0`; recapture backgrounds with matching settings.

- Pi: `pi_node/scans/scan_.../`, containing manifest/status/request files, copied backgrounds, and either `raw/step_000_cam_01.jpg` or `raw_combined/step_000_quad.jpg`.
- Manifest: capture settings, file hashes, command logs, observed start times, estimated midpoint angles, errors and completion timestamps. These are not hardware exposure timestamps.
- Windows: `scans/received/scan_.../` and `outputs/detailed/` with original images and preparation notes.
- Separate views: the quick preview runs automatically, producing GLB/OBJ when masks and approximate geometry are usable.
- Unsplit combined views: source photos and detailed staging are available, with an explicit layout-verification message. No model is invented. Do not treat mosaics as single-camera photogrammetry inputs.
- The client prints an exact local viewer URL and opens it. Keep its terminal running. `--no-browser` prints the URL; `--no-viewer` finishes without a server.
- Physical/imported scans are retained. Only disposable mock/synthetic scans are automatically removed on next generation or Done. Runtime artifacts are gitignored.

## What to say during the demo

“The Pi captures views as the object rotates. The laptop verifies and downloads the images, makes a fast silhouette approximation, and prepares the originals for later detailed reconstruction. This preview prioritizes responsiveness, not calibrated measurement. If this kit produces a mosaic, the next hardware step is verifying and splitting those camera views.”

## Troubleshooting

| Symptom | Action |
|---|---|
| No cameras detected | Inspect diagnostic output, cable seating with power off, vendor overlays and routing. A quad device may enumerate only once. |
| rpicam command missing | Check `rpicam-still --list-cameras`, then `libcamera-still --list-cameras`. Configure camera tools for the Pi OS/vendor setup. Health stays reachable with `ready=false`. |
| Permission issue | Read the command error; check device access for the current account and write access to the scan directory. Avoid broad permission changes or running everything as root. |
| Output folder empty | Read status/manifest errors, test a single capture, and check free space, permissions and command timeout. Failed captures never publish a completed scan. |
| Backgrounds missing | Capture the empty table with identical settings/layout. Inspect `backgrounds/current/index.json`. Scanning continues with a warning if references are unavailable. |
| Combined quad image not split yet | Expected until physical verification. Inspect exact crops, camera mapping and orientation before implementing `pi_node/splitter.py`. The split option currently returns a clear error. |
| Windows cannot reach Pi | Check hostname/IP, same LAN, node binding on port 8000, firewall and guest-network isolation. Open `http://<Pi-IP>:8000/api/v1/health`. Keep this unauthenticated API off the public internet. |
| Scan starts but timing is wrong | Measure the table period; adjust rotation_seconds. Inspect scheduled/actual starts in capture_logs. Reduce steps or resolution if commands overrun; separate devices are sequential. Missed deadlines fail without catch-up bursts. |
| Quick preview fails | Check masks, object centering, shadows/table edges, backgrounds and estimated camera poses. Detailed images are still staged. |
| Client times out | Increase scan-timeout for long rotations. Resume with scan-id; a client timeout does not cancel the Pi capture. |
| Node restarts mid-scan | Interrupted scans are marked failed with retained files/errors; start a new ID. Completed transfers remain resumable. |

Camera options: [official Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html). Actual Pi/Arducam behavior must still be verified on this equipment.
