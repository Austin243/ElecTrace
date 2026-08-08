from __future__ import annotations

from itertools import product

import numpy as np
import pytest

from electrace.geometry import (
    cartesian_to_fractional,
    deformation_aware_distance,
    deformation_gradient,
    fractional_to_cartesian,
    minimum_image_fractional_displacement,
    pairwise_periodic_distances,
    periodic_distance,
    periodic_fractional_centroid,
    periodic_voxel_overlap,
    wrap_fractional,
)


def test_fractional_cartesian_round_trip() -> None:
    cell = np.array([[3.0, 0.0, 0.0], [0.7, 2.5, 0.0], [0.2, 0.3, 4.0]])
    fractional = np.array([[0.2, 0.4, 0.9], [1.1, -0.2, 0.0]])
    cartesian = fractional_to_cartesian(fractional, cell)
    np.testing.assert_allclose(cartesian_to_fractional(cartesian, cell), fractional)


def test_wrap_respects_nonperiodic_axes() -> None:
    wrapped = wrap_fractional([1.2, -0.1, 2.5], pbc=(True, True, False))
    np.testing.assert_allclose(wrapped, [0.2, 0.9, 2.5])


def test_minimum_image_across_boundary() -> None:
    cell = np.diag([10.0, 10.0, 10.0])
    displacement = minimum_image_fractional_displacement([0.98, 0.2, 0.3], [0.02, 0.2, 0.3], cell)
    np.testing.assert_allclose(displacement, [0.04, 0.0, 0.0], atol=1e-14)
    assert periodic_distance([0.98, 0.2, 0.3], [0.02, 0.2, 0.3], cell) == pytest.approx(0.4)


def test_triclinic_nearest_image_is_not_component_rounding() -> None:
    # For this skew lattice, subtracting one of the nearly parallel lattice
    # vectors is much shorter than leaving both components at +0.49.
    cell = np.array([[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 0.0, 1.0]])
    displacement = minimum_image_fractional_displacement([0.0, 0.0, 0.0], [0.49, 0.49, 0.0], cell)
    np.testing.assert_allclose(displacement, [0.49, -0.51, 0.0])
    assert periodic_distance([0, 0, 0], [0.49, 0.49, 0], cell) == pytest.approx(
        np.hypot(0.031, 0.051)
    )


def test_triclinic_nearest_image_matches_bounded_brute_force() -> None:
    cell = np.array([[3.0, 0.0, 0.0], [1.2, 2.4, 0.0], [0.3, 0.2, 2.8]])
    rng = np.random.default_rng(20260808)
    for first, second in zip(rng.random((32, 3)), rng.random((32, 3)), strict=True):
        displacement = minimum_image_fractional_displacement(first, second, cell)
        actual = np.linalg.norm(displacement @ cell)
        delta = second - first
        expected = min(
            np.linalg.norm((delta - np.asarray(shift)) @ cell)
            for shift in product(range(-3, 4), repeat=3)
        )
        assert actual == pytest.approx(expected, abs=1.0e-12)


def test_pairwise_distances_factorize_the_cell_once(monkeypatch: pytest.MonkeyPatch) -> None:
    cell = np.diag([10.0, 11.0, 12.0])
    first = np.linspace(0.0, 0.9, 24).reshape(8, 3)
    second = np.linspace(0.05, 0.95, 18).reshape(6, 3)
    real_svd = np.linalg.svd
    calls = 0

    def counted_svd(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real_svd(*args, **kwargs)

    monkeypatch.setattr(np.linalg, "svd", counted_svd)
    distances = pairwise_periodic_distances(first, second, cell)

    assert distances.shape == (8, 6)
    assert calls == 1


def test_pairwise_distances_validate_empty_inputs() -> None:
    with pytest.raises(ValueError, match="finite"):
        pairwise_periodic_distances(
            np.empty((0, 3)),
            np.array([[np.nan, 0.0, 0.0]]),
            np.eye(3),
        )


def test_deformation_aware_distance_uses_symmetric_metric() -> None:
    previous = np.diag([10.0, 10.0, 10.0])
    current = np.diag([12.0, 12.0, 12.0])
    distance = deformation_aware_distance([0.2, 0.2, 0.2], [0.3, 0.2, 0.2], previous, current)
    assert distance == pytest.approx(0.1 * np.sqrt((10.0**2 + 12.0**2) / 2.0))
    np.testing.assert_allclose(deformation_gradient(previous, current), np.eye(3) * 1.2)


def test_periodic_voxel_overlap_and_centroid() -> None:
    first = [(0, 0, 0), (9, 0, 0)]
    second = [(1, 0, 0), (0, 0, 0)]
    assert periodic_voxel_overlap(first, second, (10, 10, 10), shift_b=(-1, 0, 0)) == 1.0
    centroid = periodic_fractional_centroid([[0.98, 0.2, 0.3], [0.02, 0.2, 0.3]])
    assert min(abs(centroid[0]), abs(centroid[0] - 1.0)) < 1e-12
    np.testing.assert_allclose(centroid[1:], [0.2, 0.3])


def test_invalid_cells_and_coordinate_shapes_are_rejected() -> None:
    with pytest.raises(ValueError, match="nonsingular"):
        periodic_distance([0, 0, 0], [0, 0, 0], np.zeros((3, 3)))
    with pytest.raises(ValueError, match="axis of length 3"):
        wrap_fractional([0.0, 0.0])
