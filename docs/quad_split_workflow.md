# Combined quad capture workflow

The Pi captures and serves unchanged combined JPEGs. After a completed download, Windows detects `raw_combined/step_NNN_quad.jpg` with no individual raw images, splits locally, runs silhouette reconstruction and prepares detailed images. This also works with the mock combined backend. No extra dependency or Pi-side splitter is needed.

## Files

Before: `raw_combined/step_000_quad.jpg`, subsequent steps, `manifest.json`, and optionally `backgrounds/quad.jpg`.

After splitting:

```text
scan_.../
  raw_combined/step_000_quad.jpg       # unchanged original
  raw/step_000_cam_01.jpg              # four crops per step
  raw/step_000_cam_02.jpg
  raw/step_000_cam_03.jpg
  raw/step_000_cam_04.jpg
  backgrounds/quad.jpg                # original, if supplied
  backgrounds/split_cam_01.jpg        # same crop rules, if supplied
  manifest.source.json               # immutable original metadata
  manifest.json                      # original + derived records
  quad_split_receipt.json             # ownership and integrity checks
  outputs/inspection/combined_contact_sheet.jpg
  outputs/inspection/split_contact_sheet.jpg
  outputs/quick/preview.glb            # produced by processing
  outputs/quick/preview.obj
  outputs/detailed/images/            # individual images for later engines
```

A scan folder containing only `raw_combined/` can be bootstrapped: use a folder name `scan_YYYYMMDD_HHMMSS` (an alphanumeric suffix is allowed) and the filenames above. Missing metadata is estimated using one 60-second rotation; an existing Pi manifest's timing is preserved.

## Commands

Run from the repository root with the virtual environment activated:

```powershell
python -m windows_client.client --node http://192.168.127.145:8000 --start-scan --steps 8 --rotation-seconds 60 --capture-width 1920 --capture-height 1080 --focus-mode auto --autofocus-on-capture --camera-timeout-ms 1000 --awb auto --quality fast
python -m preview3d.quad_splitter --scan scans/received/scan_20260912_173957_874840f4 --config config/quad_split.json --dry-run
python -m preview3d.quad_splitter --scan scans/received/scan_20260912_173957_874840f4 --config config/quad_split.json --contact-sheet
python -m windows_client --grid 32 process scans/received/scan_20260912_173957_874840f4
python -m windows_client --root scans/received serve
```

The scan client opens its viewer. The standalone splitter only splits and invalidates the previous processing report; run `process` afterward to rebuild GLB/OBJ and detailed staging. The final command opens the library viewer. Use `--quad-split-config PATH` on the node client for another layout. Do not pass the reserved Pi-side `--split-combined-output` flag.

## Adjusting configuration

`config/quad_split.json` uses normalized x/y/w/h values between zero and one. The crop keys cam_01, cam_02, cam_03, cam_04 describe four source slots, defaulting respectively to top-left, top-right, bottom-left, bottom-right. `camera_order` assigns the destination camera ID for each slot in that order. For example `["cam_02", "cam_01", "cam_03", "cam_04"]` swaps the top camera assignments. All four IDs must appear exactly once.

Edit the slot's crop rectangle to remove borders or change boundaries. Shared edges round consistently even at odd resolutions. Invalid, out-of-bounds or empty crops fail before saving. Optional `"rotations": {"cam_01": 90, "cam_02": 90, "cam_03": 90, "cam_04": 90}` rotates each destination view clockwise after cropping. Supported rotations are 0, 90, 180, 270. The default performs no rotation. Backgrounds follow the same mapping and rotation.

Rerun the standalone splitter after changing configuration. Automatic processing does not silently replace an already complete split with new crop settings.

## Validation and recovery

For eight steps expect 8 unchanged originals and 32 raw images. With the default layout, a 1920x1080 mosaic becomes four 960x540 JPEGs. Inspect both contact sheets for mapping, upright orientation and borders. Sheets use at most 24 evenly spaced labeled thumbnails each; the viewer's photo gallery provides all images and identifies those used by the last preview. GLB/OBJ and detailed staging use the split frames, not the mosaics.

The manifest records the complete crop configuration, split flags, original/derived file lists, contact sheet paths and `timing_summary.split_seconds`. Original timestamps and angles remain associated with each derived view. Checksums protect source photos and identify splitter-owned outputs. Repeated downloads verify against `manifest.source.json` and the derived-manifest receipt. A manual rerun can replace its own unchanged outputs; it refuses unrelated or externally edited images. Physical/imported scans remain retained under the existing cleanup policy.

An interrupted commit leaves `quad_split.pending.json`; rerun the splitter to resume. If a process was forcibly killed and left `.processing`, stop that process before removing only that stale lock. Do not delete the source manifest or receipts to bypass ownership checks. Dry runs validate and stage temporary crops but leave no persistent changes.

## Limitations

The default layout is an assumption confirmed only by inspection, not calibrated camera identity or pose. Camera geometry remains approximate, and rotation, table/background clutter, shadows, occlusions or perspective can distort a silhouette model. This does not implement feature matching or heavy photogrammetry. JPEG crops are re-encoded; lossless source mosaics remain unchanged. Contact sheets deliberately sample large scans to keep inspection small. Manual splitting does not itself regenerate reconstruction outputs.
