"""Select the latest learned reconstruction report without changing source captures."""
def active_base(scan):
    candidates=[scan/'outputs'/engine for engine in ('vggt','worldmirror2')]
    candidates=[p for p in candidates if (p/'reports/vggt.json').is_file()]
    if candidates:return max(candidates,key=lambda p:(p/'reports/vggt.json').stat().st_mtime).relative_to(scan).as_posix()
    return 'outputs/full_photogrammetry'
