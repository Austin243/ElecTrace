"""Periodic and variable-cell geometry for feature correspondence.

Scientific assumptions
----------------------
Cells are 3x3 matrices whose *rows* are lattice vectors and fractional
coordinates are row vectors, so ``r_cart = r_frac @ cell``.  Feature motion
between two variable-cell frames is measured in a symmetric mean lattice
metric by default.  This removes homogeneous cell strain from the apparent
motion while avoiding an arbitrary preference for either endpoint.

The nearest periodic image is selected in Cartesian space.  Component-wise
rounding of fractional coordinates is only exact for orthogonal/reduced cells
and is therefore insufficient for general triclinic simulations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import product
from typing import Literal, cast

import numpy as np
import numpy.typing as npt

ArrayLike = npt.ArrayLike
FloatArray = npt.NDArray[np.float64]
PBC = tuple[bool, bool, bool]


def _validated_cell(cell: ArrayLike) -> tuple[FloatArray, float]:
    """Return a validated cell and a conservative lower singular-value bound."""

    result = np.asarray(cell, dtype=float)
    if result.shape != (3, 3):
        raise ValueError(f"cell must have shape (3, 3), got {result.shape}")
    if not np.all(np.isfinite(result)):
        raise ValueError("cell must contain only finite values")
    singular_values = np.linalg.svd(result, compute_uv=False)
    if singular_values[-1] <= max(1.0, singular_values[0]) * 1.0e-12:
        raise ValueError("cell must be nonsingular and reasonably conditioned")
    # The nearest-image radius must not be underestimated when the computed
    # smallest singular value is rounded upward.  Standard SVD backward error
    # is O(eps * ||cell||); this deliberately generous lower bound remains
    # positive under the conditioning gate above.
    roundoff = 32.0 * np.finfo(float).eps * max(1.0, float(singular_values[0]))
    return result, float(singular_values[-1] - roundoff)


def as_cell(cell: ArrayLike) -> FloatArray:
    """Return a validated 3x3 cell array using the row-vector convention."""

    return _validated_cell(cell)[0]


def as_pbc(pbc: Sequence[bool] | bool = (True, True, True)) -> PBC:
    """Normalize a scalar or three-component periodic-boundary flag."""

    if isinstance(pbc, (bool, np.bool_)):
        return (bool(pbc),) * 3
    result = tuple(bool(value) for value in pbc)
    if len(result) != 3:
        raise ValueError("pbc must be a bool or a sequence of three bools")
    return result


def cell_volume(cell: ArrayLike) -> float:
    """Return the positive cell volume."""

    return float(abs(np.linalg.det(as_cell(cell))))


def metric_average_cell(cell_a: ArrayLike, cell_b: ArrayLike) -> FloatArray:
    """Construct a cell realizing the arithmetic mean lattice metric.

    For row-vector cells the fractional metric is ``G = cell @ cell.T``.
    The returned Cholesky factor need not resemble either cell orientation;
    only distances in its metric are used.
    """

    first = as_cell(cell_a)
    second = as_cell(cell_b)
    metric = 0.5 * (first @ first.T + second @ second.T)
    result = np.asarray(np.linalg.cholesky(metric), dtype=np.float64)
    return result


def _nearest_image_single(
    delta: FloatArray,
    cell: FloatArray,
    pbc: PBC,
    sigma_min: float,
    *,
    max_candidates: int = 1_000_000,
) -> FloatArray:
    """Find the exact nearest lattice image for a single displacement.

    A finite search bound follows from the smallest singular value of the
    lattice and the norm of the component-rounded candidate.  This is exact
    for a validated, finite cell rather than assuming a reduced lattice.
    """

    if not any(pbc):
        return delta.copy()

    rounded = np.zeros(3, dtype=int)
    for axis, enabled in enumerate(pbc):
        if enabled:
            rounded[axis] = int(np.rint(delta[axis]))
    initial = delta - rounded
    upper = float(np.linalg.norm(initial @ cell))
    # A candidate improving on `upper` must have fractional norm <= upper/smin.
    bound = upper / sigma_min + 8.0 * np.finfo(float).eps

    ranges: list[range] = []
    candidate_count = 1
    for axis, enabled in enumerate(pbc):
        if not enabled:
            axis_range = range(0, 1)
        else:
            # If ||(delta - shift) @ cell|| <= upper, then every component of
            # ``delta - shift`` is bounded by ``upper / sigma_min``.  The
            # integer interval below is therefore complete without padding it
            # by extra lattice images, which matters for every pairwise call.
            lower = math.ceil(delta[axis] - bound)
            higher = math.floor(delta[axis] + bound)
            axis_range = range(lower, higher + 1)
        ranges.append(axis_range)
        candidate_count *= len(axis_range)
    if candidate_count > max_candidates:
        raise ValueError(
            "nearest-image search is too large; the cell is pathologically "
            "skewed or ill-conditioned"
        )

    best: FloatArray = initial
    best_norm_sq = float(np.dot(initial @ cell, initial @ cell))
    best_shift = tuple(int(value) for value in rounded)
    for shift_tuple in product(*ranges):
        shift = np.asarray(shift_tuple, dtype=float)
        candidate = delta - shift
        cartesian = candidate @ cell
        norm_sq = float(np.dot(cartesian, cartesian))
        tolerance = 32.0 * np.finfo(float).eps * max(1.0, best_norm_sq)
        if norm_sq < best_norm_sq - tolerance or (
            abs(norm_sq - best_norm_sq) <= tolerance and tuple(shift_tuple) < best_shift
        ):
            best = candidate
            best_norm_sq = norm_sq
            best_shift = tuple(int(value) for value in shift_tuple)
    return best


def _fractional_coordinate_pair(
    fractional_a: ArrayLike,
    fractional_b: ArrayLike,
) -> tuple[FloatArray, FloatArray]:
    """Validate and broadcast two arrays of fractional row vectors."""

    first = np.asarray(fractional_a, dtype=float)
    second = np.asarray(fractional_b, dtype=float)
    if first.shape == () or second.shape == () or first.shape[-1] != 3 or second.shape[-1] != 3:
        raise ValueError("fractional coordinates must end in an axis of length 3")
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        raise ValueError("coordinates must contain only finite values")
    broadcast_first, broadcast_second = np.broadcast_arrays(first, second)
    return cast(FloatArray, broadcast_first), cast(FloatArray, broadcast_second)


def _minimum_image_fractional_displacement(
    first: FloatArray,
    second: FloatArray,
    lattice: FloatArray,
    periodic: PBC,
    sigma_min: float,
) -> FloatArray:
    """Nearest-image implementation for already validated, broadcast inputs.

    Any image other than the component-rounded one differs from it by a nonzero
    lattice vector, so it is at least ``sigma_min - |rounded|`` long.  A rounded image
    shorter than ``sigma_min / 2`` is therefore the nearest image, and it is exactly
    what :func:`_nearest_image_single` returns; a small margin keeps rounding error and
    that search's tie tolerance from mattering.  Only longer rounded images are searched.
    """

    flat = (second - first).reshape(-1, 3)
    # Integer shifts, as in _nearest_image_single, so even signed zeros agree.
    result = flat - np.where(periodic, np.rint(flat), 0.0).astype(np.int64)
    lengths = np.linalg.norm(result @ lattice, axis=-1)
    for index in np.flatnonzero(lengths >= 0.5 * sigma_min * (1.0 - 1.0e-6)):
        result[index] = _nearest_image_single(flat[index], lattice, periodic, sigma_min)
    return cast(FloatArray, result.reshape(first.shape))


def minimum_image_displacement(
    fractional_a: ArrayLike,
    fractional_b: ArrayLike,
    cell: ArrayLike,
    pbc: Sequence[bool] | bool = (True, True, True),
) -> FloatArray:
    """Return nearest-image Cartesian displacement ``b - a``."""

    first, second = _fractional_coordinate_pair(fractional_a, fractional_b)
    lattice, sigma_min = _validated_cell(cell)
    periodic = as_pbc(pbc)
    displacement = _minimum_image_fractional_displacement(
        first, second, lattice, periodic, sigma_min
    )
    return displacement @ lattice


def deformation_aware_displacement(
    fractional_a: ArrayLike,
    fractional_b: ArrayLike,
    cell_a: ArrayLike,
    cell_b: ArrayLike,
    pbc: Sequence[bool] | bool = (True, True, True),
    reference: Literal["mean_metric", "previous", "current"] = "mean_metric",
) -> FloatArray:
    """Return internal feature motion with homogeneous cell strain factored out.

    Fractional positions are material coordinates.  Their minimum-image
    difference is evaluated in the previous, current, or (default) symmetric
    mean metric.  This is suitable for nearby variable-cell NEB/pressure
    frames that share lattice-vector correspondence.
    """

    if reference == "mean_metric":
        metric_cell = metric_average_cell(cell_a, cell_b)
    elif reference == "previous":
        metric_cell = as_cell(cell_a)
    elif reference == "current":
        metric_cell = as_cell(cell_b)
    else:
        raise ValueError(f"unknown reference metric {reference!r}")
    return minimum_image_displacement(fractional_a, fractional_b, metric_cell, pbc)


def deformation_aware_distance(
    fractional_a: ArrayLike,
    fractional_b: ArrayLike,
    cell_a: ArrayLike,
    cell_b: ArrayLike,
    pbc: Sequence[bool] | bool = (True, True, True),
    reference: Literal["mean_metric", "previous", "current"] = "mean_metric",
) -> float | FloatArray:
    """Norm of :func:`deformation_aware_displacement`."""

    result = np.linalg.norm(
        deformation_aware_displacement(fractional_a, fractional_b, cell_a, cell_b, pbc, reference),
        axis=-1,
    )
    return float(result) if result.ndim == 0 else result


def deformation_aware_distance_lower_bound(
    fractional_a: ArrayLike,
    fractional_b: ArrayLike,
    cell_a: ArrayLike,
    cell_b: ArrayLike,
    pbc: Sequence[bool] | bool = (True, True, True),
) -> FloatArray:
    """Return a lower bound on the mean-metric distance for every pair of rows.

    Entry ``[i, j]`` bounds :func:`deformation_aware_distance` between row ``i`` of
    *fractional_a* and row ``j`` of *fractional_b*.  A Cartesian vector ``f @ cell``
    is at least ``|f_k| * h_k`` long for each axis ``k``, where ``h_k`` is the spacing
    of the lattice planes spanned by the other two cell vectors.  Lattice translations
    change ``f_k`` only by integers on periodic axes, so every image is at least
    ``max_k(distance from f_k to the nearest integer) * h_k`` long.  The bound is
    vectorized and never exceeds the exact distance, which makes it a safe cutoff
    filter before the exact nearest-image search.
    """

    metric_cell = metric_average_cell(cell_a, cell_b)
    spacing = 1.0 / np.linalg.norm(np.linalg.inv(metric_cell), axis=0)
    first = np.asarray(fractional_a, dtype=float).reshape(-1, 3)
    second = np.asarray(fractional_b, dtype=float).reshape(-1, 3)
    periodic = as_pbc(pbc)
    bound = np.zeros((len(first), len(second)), dtype=float)
    for axis in range(3):
        delta = second[None, :, axis] - first[:, None, axis]
        if periodic[axis]:
            delta -= np.rint(delta)
        np.maximum(bound, np.abs(delta) * spacing[axis], out=bound)
    return bound


def periodic_voxel_overlap(
    voxels_a: Iterable[Sequence[int]],
    voxels_b: Iterable[Sequence[int]],
    grid_shape: Sequence[int],
    *,
    shift_b: Sequence[int] = (0, 0, 0),
    metric: Literal["jaccard", "dice", "intersection_over_minimum"] = "jaccard",
) -> float:
    """Return overlap of voxel supports on a common periodic grid.

    This routine deliberately requires a common grid.  Comparing supports
    from different grids without resampling can create convincing but
    scientifically meaningless overlaps; adapters should first resample into
    a common fractional grid.
    """

    shape = tuple(int(size) for size in grid_shape)
    shift = tuple(int(value) for value in shift_b)
    if len(shape) != 3 or any(size <= 0 for size in shape):
        raise ValueError("grid_shape must contain three positive integers")
    if len(shift) != 3:
        raise ValueError("shift_b must contain three integers")

    def normalize(
        voxels: Iterable[Sequence[int]], offset: tuple[int, int, int]
    ) -> set[tuple[int, int, int]]:
        result: set[tuple[int, int, int]] = set()
        for voxel in voxels:
            item = tuple(int(value) for value in voxel)
            if len(item) != 3:
                raise ValueError("voxel indices must contain three integers")
            result.add(
                tuple((item[axis] + offset[axis]) % shape[axis] for axis in range(3))  # type: ignore[arg-type]
            )
        return result

    first = normalize(voxels_a, (0, 0, 0))
    second = normalize(voxels_b, shift)
    intersection = len(first & second)
    if metric == "jaccard":
        denominator = len(first | second)
    elif metric == "dice":
        denominator = len(first) + len(second)
        intersection *= 2
    elif metric == "intersection_over_minimum":
        denominator = min(len(first), len(second))
    else:
        raise ValueError(f"unknown overlap metric {metric!r}")
    if denominator == 0:
        return 1.0 if not first and not second else 0.0
    return float(intersection / denominator)
