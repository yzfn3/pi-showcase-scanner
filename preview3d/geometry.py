"""Shared orthographic camera convention; coordinates are object-local, Y up."""
import math
import numpy as np


def basis(camera, table_angle_deg):
    az = math.radians(camera["azimuth_deg"] - table_angle_deg)
    el = math.radians(camera["elevation_deg"])
    right = np.array([math.cos(az), 0, -math.sin(az)], dtype=np.float32)
    up = np.array([-math.sin(el)*math.sin(az), math.cos(el), -math.sin(el)*math.cos(az)], dtype=np.float32)
    outward = np.cross(right, up)
    return right, up, outward


def dot(points, vector):
    # Elementwise form avoids thread-pool overhead on tiny matrices.
    return points[..., 0]*vector[0] + points[..., 1]*vector[1] + points[..., 2]*vector[2]
