from __future__ import annotations

from itertools import product

import numpy as np
import pytest

from electrace.geometry import (
    deformation_aware_distance,
    minimum_image_displacement,
    periodic_voxel_overlap,
)


def test_triclinic_minimum_image_matches_brute_force() -> None:
    cell = np.array([[3.0, 0.0, 0.0], [1.2, 2.4, 0.0], [0.3, 0.2, 2.8]])
    rng = np.random.default_rng(20260808)
    for first, second in zip(rng.random((12, 3)), rng.random((12, 3)), strict=True):
        actual = np.linalg.norm(minimum_image_displacement(first, second, cell))
        delta = second - first
        expected = min(
            np.linalg.norm((delta - np.asarray(shift)) @ cell)
            for shift in product(range(-3, 4), repeat=3)
        )
        assert actual == pytest.approx(expected, abs=1.0e-12)


def test_deformation_aware_distance_uses_symmetric_metric() -> None:
    previous = np.diag([10.0, 10.0, 10.0])
    current = np.diag([12.0, 12.0, 12.0])
    distance = deformation_aware_distance([0.2, 0.2, 0.2], [0.3, 0.2, 0.2], previous, current)
    assert distance == pytest.approx(0.1 * np.sqrt((10.0**2 + 12.0**2) / 2.0))


def test_periodic_voxel_overlap_wraps_grid_indices() -> None:
    first = [(0, 0, 0), (9, 0, 0)]
    second = [(1, 0, 0), (0, 0, 0)]
    assert periodic_voxel_overlap(first, second, (10, 10, 10), shift_b=(-1, 0, 0)) == 1.0


def test_invalid_cell_is_rejected() -> None:
    with pytest.raises(ValueError, match="nonsingular"):
        minimum_image_displacement([0, 0, 0], [0, 0, 0], np.zeros((3, 3)))
