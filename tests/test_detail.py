"""Geometry quality regressions, independent of the synthetic object generator."""
import unittest
import numpy as np

from preview3d.export import regularize_edges, surface
from preview3d.pipeline import validate_grid


class DetailTests(unittest.TestCase):
    def test_finer_smoothed_surface_reduces_sphere_error(self):
        errors = {}
        for grid in (32, 96, 160):
            axis = (np.arange(grid)+0.5)*0.24/grid-0.12
            xyz = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
            volume = regularize_edges(np.sum(xyz**2, axis=-1) < 0.06**2)
            smoothed = max(8, grid // 5)
            for iterations in (0, smoothed):
                vertices, normals = surface(volume, 0.24, iterations)
                errors[grid, bool(iterations)] = np.abs(np.linalg.norm(vertices, axis=1)-0.06).mean()
                self.assertTrue(np.isfinite(vertices).all())
                np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)
                self.assertTrue((np.sum(vertices*normals, axis=1) > 0).all())
                triangles = vertices.reshape(-1, 3, 3)
                winding = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
                self.assertTrue((np.sum(winding*normals[::3], axis=1) > 0).all())
            self.assertLess(errors[grid, True], errors[grid, 0]*0.6)
        self.assertLess(errors[96, True], errors[32, 0]*0.25)
        self.assertLess(errors[160, True], errors[96, True])

    def test_invalid_grid_is_rejected(self):
        for invalid in (True, "96", 96.5, 0, 11, 161):
            with self.subTest(grid=invalid), self.assertRaises(ValueError):
                validate_grid(invalid)
