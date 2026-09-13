# Shoe capture verification — 12 September 2026

Brightness now comes from capture settings. Browser defaults: gain **3**, shutter **4000 microseconds**, manual focus **6.85**, settling **1500 ms**, AWB auto, JPEG quality 95, matching `4624:3472:10` sensor/viewfinder modes, and ZSL. These are measured settings for this rig at its current distance, not universal defaults.

The previous automatic exposure selected approximately 3880 us and gain 1. The final scan metadata records 3994 us, analogue gain 2.994152, digital gain 1.003404 and lens position 6.85 in all 16 exposures. All 64 split images were downloaded. Foreground-mask median JPEG luminance ranges from 127 to 157 (0–255); maximum clipped foreground fraction is 0.182%. This measures exposure, not geometric accuracy.

Stationary focus sweeps from 4 to 10 and finer comparisons around 7 showed slightly different optima across cameras. 6.85 balances them. Fabric, stitching and laces are visible at native pixel scale; rotating captures were inspected too. Fine features can remain soft/noisy. Current output is a 4624×3472 mosaic containing four 2312×1736 views, not four full 64MP images.

## Browser workflow

Launch `Launch Scanner.cmd`. Camera settings now expose sensor gain and shutter; empty means auto. After changing these settings, capture new backgrounds with the shoe removed. Replace it and start the usual one-minute rotation, then Capture object + reconstruct. Processing gamma defaults to **1.0** (neutral). Background capture keeps focus locked and does not require a foreground focus target.

## Repeat commands

From the repository root with the virtual environment activated, capture the empty table:

```powershell
python -m windows_client.client --node http://192.168.127.145:8000 --capture-backgrounds --capture-width 4624 --capture-height 3472 --sensor-mode 4624:3472:10 --viewfinder-mode 4624:3472:10 --zsl --focus-mode manual --lens-position 6.85 --camera-timeout-ms 1500 --exposure-time 4000 --gain 3 --awb auto --autofocus-window 0,0,1,1 --autofocus-range full --jpeg-quality 95 --combined-quad-output
```

Replace the shoe and start rotation:

```powershell
python -m windows_client.client --node http://192.168.127.145:8000 --start-scan --steps 16 --rotation-seconds 60 --capture-width 4624 --capture-height 3472 --sensor-mode 4624:3472:10 --viewfinder-mode 4624:3472:10 --zsl --focus-mode manual --lens-position 6.85 --camera-timeout-ms 1500 --exposure-time 4000 --gain 3 --awb auto --autofocus-window 0,0,1,1 --autofocus-range full --jpeg-quality 95 --combined-quad-output --full-scan --brightness-gamma 1 --reconstruction-engine worldmirror2 --vggt-frames 64 --vggt-size 518
```

Scan: `scans/received/scan_20260912_223440`. Original images and capture metadata are preserved. Exposure audit: `outputs/capture_audit.json`. Local test photographs: `logs/shoe_capture/` (ignored by Git).

## Limits

Neither VGGT nor WorldMirror guarantees calibrated dimensions or 100% accuracy. Poisson meshing interpolates surfaces and can connect points incorrectly. Triangle count and processed-image count do not measure accuracy. The underside on the table is unobserved and cannot be described as measured geometry.
