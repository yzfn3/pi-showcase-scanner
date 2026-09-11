# Synthetic scans

Run from the repository root:

```powershell
python -m sample_data.generate --root scans --steps 12 --size 256
```

The generator renders a newly randomized union of overlapping ellipsoids from four fixed, approximately positioned cameras as an object rotates. One `object_seed` keeps all views coherent within the scan. It writes real JPEGs, matching backgrounds, and a complete manifest. The reconstruction reads these images, not the ellipsoid parameters. Generated folders live in gitignored `scans/`; the browser removes old generated scans on the next generation or Done.

`example_manifest.json` shows the complete format; its referenced images are illustrative. Generate a scan to obtain matching files. Images are created algorithmically, without downloads, external assets, or AI image services.
