"""Bounded browser previews of real COLMAP sparse points."""
import math
import struct
from pathlib import Path
from windows_client.scan import write_json


def export_view(workspace, result, limit=50000):
    source=Path(result['sparse_model'])/'points3D.bin'
    positions, colors = [], []
    with source.open('rb') as stream:
        count=struct.unpack('<Q',stream.read(8))[0]
        if count>50000000: raise ValueError('Invalid sparse point count')
        stride=max(1,math.ceil(count/limit))
        for i in range(count):
            _,x,y,z,r,g,b,error=struct.unpack('<QdddBBBd',stream.read(43))
            length=struct.unpack('<Q',stream.read(8))[0]
            if length>1000000: raise ValueError('Invalid point track length')
            stream.seek(length*8,1)
            if i%stride==0 and all(math.isfinite(v) for v in (x,y,z)):
                positions.extend((x,y,z));colors.extend((r/255,g/255,b/255))
    target=Path(workspace)/'reports/sparse_view.json'
    write_json(target,dict(kind='sparse_points',coordinate_system='colmap',positions=positions,colors=colors,
                          points_total=count,points_displayed=len(positions)//3,units='unscaled'))
    return dict(file='reports/sparse_view.json',kind='sparse_points',points_displayed=len(positions)//3)
