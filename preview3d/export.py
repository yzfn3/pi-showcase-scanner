"""Build a smooth shared-vertex surface and export OBJ, GLB and viewer data."""
import json
import struct
from pathlib import Path
import numpy as np
from windows_client.scan import write_json


def regularize_edges(volume):
    """Fill diagonal-only voxel contacts so four faces do not share one edge."""
    volume = volume.copy()
    changed = True
    while changed:
        changed = False
        for axis in range(3):
            cells = np.moveaxis(volume, axis, 0)
            a, b = cells[:, :-1, :-1], cells[:, 1:, :-1]
            c, d = cells[:, :-1, 1:], cells[:, 1:, 1:]
            diagonal_ad = a & d & ~b & ~c
            diagonal_bc = b & c & ~a & ~d
            if diagonal_ad.any() or diagonal_bc.any():
                b[diagonal_ad] = True
                a[diagonal_bc] = True
                changed = True
    return volume


def surface(volume, extent, smooth_iterations=8):
    """Extract the hull in batches, then smooth geometry with little shrinkage.

    Alternating positive/negative Laplacian steps round voxel stairs without
    repeatedly shrinking the whole object. Shared vertices keep faces joined.
    """
    quads = []
    n = volume.shape[0]
    # Counterclockwise quads viewed from outside each face.
    faces = [((-1,0,0), ((0,0,0),(0,0,1),(0,1,1),(0,1,0))),
             ((1,0,0), ((1,0,0),(1,1,0),(1,1,1),(1,0,1))),
             ((0,-1,0), ((0,0,0),(1,0,0),(1,0,1),(0,0,1))),
             ((0,1,0), ((0,1,0),(0,1,1),(1,1,1),(1,1,0))),
             ((0,0,-1), ((0,0,0),(0,1,0),(1,1,0),(1,0,0))),
             ((0,0,1), ((0,0,1),(1,0,1),(1,1,1),(0,1,1)))]
    padded = np.pad(volume, 1)
    for normal, corners in faces:
        neighbor = padded[tuple(slice(1+d, 1+d+n) for d in normal)]
        cells = np.argwhere(volume & ~neighbor)
        quads.append(cells[:, None, :] + np.array(corners)[None, :, :])
    quads = np.concatenate(quads)
    vertices, inverse = np.unique(quads.reshape(-1, 3), axis=0, return_inverse=True)
    quads = inverse.reshape(-1, 4)
    triangles = quads[:, [0, 1, 2, 0, 2, 3]].reshape(-1, 3)
    vertices = vertices.astype(np.float64) * (extent/n) - extent/2
    # Quad edges avoid bias toward the triangulation diagonal during smoothing.
    edges = quads[:, [0, 1, 1, 2, 2, 3, 3, 0]].reshape(-1, 2)
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    source = edges.reshape(-1)
    target = edges[:, ::-1].reshape(-1)
    degree = np.bincount(source, minlength=len(vertices))
    for _ in range(smooth_iterations):
        for strength in (0.5, -0.53):
            average = np.column_stack([
                np.bincount(source, weights=vertices[target, k], minlength=len(vertices))
                for k in range(3)]) / degree[:, None]
            vertices += strength * (average - vertices)
    positions = vertices[triangles]
    face_normals = np.cross(positions[:, 1]-positions[:, 0], positions[:, 2]-positions[:, 0])
    normals = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(normals, triangles[:, corner], face_normals)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-20)
    return positions.reshape(-1, 3).astype('<f4'), normals[triangles].reshape(-1, 3).astype('<f4')


def atomic_bytes(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def export_mesh(folder, positions, normals):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    obj = ["# Pi Showcase Scanner; meters; Y up"]
    obj.extend("v %.6f %.6f %.6f" % tuple(p) for p in positions)
    obj.extend("vn %.6f %.6f %.6f" % tuple(p) for p in normals)
    obj.extend(f"f {i+1}//{i+1} {i+2}//{i+2} {i+3}//{i+3}" for i in range(0, len(positions), 3))
    atomic_bytes(folder / "preview.obj", ("\n".join(obj) + "\n").encode())
    blob = positions.tobytes() + normals.tobytes()
    length = positions.nbytes
    gltf = {"asset": {"version": "2.0", "generator": "Pi Showcase Scanner"},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.15,0.68,0.72,1], "metallicFactor": 0, "roughnessFactor": 0.8}}],
            "buffers": [{"byteLength": len(blob)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": length, "target": 34962},
                            {"buffer": 0, "byteOffset": length, "byteLength": length, "target": 34962}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
                           "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
                          {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"}]}
    encoded = json.dumps(gltf, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    total = 12 + 8 + len(encoded) + 8 + len(blob)
    glb = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(encoded), b"JSON") + encoded
    glb += struct.pack("<I4s", len(blob), b"BIN\0") + blob
    atomic_bytes(folder / "preview.glb", glb)
    write_json(folder / "mesh.json", {"positions": positions.reshape(-1).tolist(), "normals": normals.reshape(-1).tolist()})
