"""Read COLMAP PLY surfaces and export bounded actual triangles for WebGL."""
import math
import struct
from pathlib import Path
import numpy as np
from windows_client.scan import write_json

TYPES={'float':'f','float32':'f','double':'d','float64':'d','uchar':'B','uint8':'B',
       'char':'b','int8':'b','int':'i','int32':'i','uint':'I','uint32':'I','short':'h','ushort':'H'}


def simplify_surface(vertices, faces, limit, colors=None):
    """Cluster nearby vertices, retaining connectivity instead of dropping faces."""
    if len(faces)<=limit:return (vertices,faces) if colors is None else (vertices,faces,colors)
    lo=vertices.min(axis=0);extent=max(float(np.ptp(vertices,axis=0).max()),1e-12)
    resolution=max(2,int(math.sqrt(limit/2)))
    for _ in range(30):
        cells=np.floor((vertices-lo)/extent*resolution).astype(np.int32)
        _,mapping=np.unique(cells,axis=0,return_inverse=True)
        counts=np.bincount(mapping)
        reduced=np.column_stack([np.bincount(mapping,weights=vertices[:,axis])/counts for axis in range(3)])
        triangles=mapping[faces]
        valid=(triangles[:,0]!=triangles[:,1])&(triangles[:,1]!=triangles[:,2])&(triangles[:,0]!=triangles[:,2])
        triangles=triangles[valid]
        _,indices=np.unique(np.sort(triangles,axis=1),axis=0,return_index=True)
        triangles=triangles[np.sort(indices)]
        if len(triangles)<=limit:
            if colors is None:return reduced,triangles
            reduced_colors=np.column_stack([np.bincount(mapping,weights=colors[:,axis])/counts for axis in range(3)])
            return reduced,triangles,reduced_colors
        resolution=max(1,int(resolution*.8))
    raise ValueError('Could not bound the browser mesh')


def export_mesh(workspace, source, limit=100000):
    source=Path(source);vertices=[];faces=[];colors=[]
    with source.open('rb') as stream:
        if stream.readline().strip()!=b'ply':raise ValueError('Not a PLY file')
        elements=[];fmt=None
        for _ in range(200):
            line=stream.readline().decode('ascii').strip().split()
            if not line:continue
            if line[0]=='end_header':break
            if line[0]=='format':fmt=line[1]
            if line[0]=='element':elements.append([line[1],int(line[2]),[]])
            if line[0]=='property':elements[-1][2].append(line[1:])
        else:raise ValueError('Oversized PLY header')
        if fmt not in ('ascii','binary_little_endian','binary_big_endian'):raise ValueError('Unsupported PLY format')
        face_total=next((count for name,count,_ in elements if name=='face'),0)
        endian='>' if fmt=='binary_big_endian' else '<'
        def scalar(kind):
            if kind not in TYPES:raise ValueError('Unsupported PLY scalar '+kind)
            code=TYPES[kind];return struct.unpack(endian+code,stream.read(struct.calcsize(code)))[0]
        for element,count,properties in elements:
            if count<0 or count>10000000:raise ValueError('PLY element limit exceeded')
            # Fast path for the large binary vertex table.
            if element=='vertex' and fmt!='ascii' and all(p[0]!='list' for p in properties):
                dtype=np.dtype([(p[1],endian+TYPES[p[0]]) for p in properties])
                raw=stream.read(dtype.itemsize*count)
                if len(raw)!=dtype.itemsize*count:raise ValueError('Truncated PLY vertices')
                data=np.frombuffer(raw,dtype=dtype)
                vertices=np.column_stack([data[key] for key in ('x','y','z')])
                if all(k in data.dtype.names for k in ('red','green','blue')):colors=np.column_stack([data[k] for k in ('red','green','blue')])/255.
                continue
            for i in range(count):
                tokens=iter(stream.readline().decode('ascii').split()) if fmt=='ascii' else None
                values={}
                for p in properties:
                    if p[0]=='list':
                        length=int(next(tokens)) if tokens is not None else scalar(p[1])
                        if not 0<=length<=10000:raise ValueError('Invalid PLY list length')
                        values[p[3]]=[int(next(tokens)) if tokens is not None else scalar(p[2]) for _ in range(length)]
                    else:values[p[1]]=float(next(tokens)) if tokens is not None else scalar(p[0])
                if element=='vertex':
                    vertices.append([values[k] for k in ('x','y','z')])
                    if all(k in values for k in ('red','green','blue')):colors.append([values[k]/255. for k in ('red','green','blue')])
                if element=='face':
                    indices=values.get('vertex_indices',values.get('vertex_index',[]))
                    for j in range(1,len(indices)-1):
                        if len(faces)>=3000000:raise ValueError('Mesh too large for browser conversion; use the full PLY')
                        faces.append([indices[0],indices[j],indices[j+1]])
    if not faces:raise ValueError('Reconstruction produced no mesh faces')
    v=np.asarray(vertices,dtype=np.float64);f=np.asarray(faces,dtype=np.int64)
    if f.min()<0 or f.max()>=len(v):raise ValueError('Mesh references missing vertices')
    if not np.isfinite(v).all():raise ValueError('Mesh contains nonfinite coordinates')
    colored=len(colors)==len(v)
    if colored:v,f,colors=simplify_surface(v,f,limit,np.asarray(colors))
    else:v,f=simplify_surface(v,f,limit)
    triangles=v[f];normal=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    lengths=np.linalg.norm(normal,axis=1);valid=(lengths>1e-12)&np.isfinite(triangles).all(axis=(1,2))
    # Area-weighted shared-vertex normals avoid artificial facets in smooth surfaces.
    vertex_normals=np.zeros_like(v)
    for corner in range(3):np.add.at(vertex_normals,f[valid,corner],normal[valid])
    vertex_normals/=np.maximum(np.linalg.norm(vertex_normals,axis=1,keepdims=True),1e-12)
    triangles=triangles[valid];normal=vertex_normals[f[valid]]
    if not len(triangles):raise ValueError('Mesh contains no valid surface triangles')
    target=Path(workspace)/'reports/mesh_view.json'
    write_json(target,dict(kind='mesh',coordinate_system='colmap',positions=triangles.reshape(-1).tolist(),normals=normal.reshape(-1).tolist(),
                          triangles_total=face_total,triangles_displayed=len(triangles),
                          **({'colors':np.asarray(colors)[f][valid].reshape(-1).tolist()} if colored else {})))
    return dict(kind='mesh',file='reports/mesh_view.json',triangles_total=face_total,triangles_displayed=len(triangles),simplification='vertex clustering',
                mesh_file=source.relative_to(workspace).as_posix())
