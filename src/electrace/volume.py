"""Strict, dependency-light reading of plain VASP volumetric grids.

The reader preserves raw values exactly as written by VASP.  In particular, it
does not assign physical units or normalize CHGCAR values: those semantics
depend on the source file and calculation.  Array indices follow
``data[ix, iy, iz]`` and VASP's x-fastest on-disk ordering.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

import numpy as np
import numpy.typing as npt

Vector3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vector3, Vector3, Vector3]
GridShape: TypeAlias = tuple[int, int, int]
SelectiveFlags: TypeAlias = tuple[bool, bool, bool]
FloatGrid: TypeAlias = npt.NDArray[np.float64]

DEFAULT_MAX_POINTS = 100_000_000
_INTEGER = re.compile(r"[+-]?\d+")


class VaspGridError(ValueError):
    """Raised when a VASP volumetric file is malformed or unsupported."""


def _determinant(matrix: Matrix3) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _inverse(matrix: Matrix3) -> Matrix3:
    a, b, c = matrix
    determinant = _determinant(matrix)
    if abs(determinant) < 1.0e-14:
        raise VaspGridError("VASP lattice is singular")
    return (
        (
            (b[1] * c[2] - b[2] * c[1]) / determinant,
            (a[2] * c[1] - a[1] * c[2]) / determinant,
            (a[1] * b[2] - a[2] * b[1]) / determinant,
        ),
        (
            (b[2] * c[0] - b[0] * c[2]) / determinant,
            (a[0] * c[2] - a[2] * c[0]) / determinant,
            (a[2] * b[0] - a[0] * b[2]) / determinant,
        ),
        (
            (b[0] * c[1] - b[1] * c[0]) / determinant,
            (a[1] * c[0] - a[0] * c[1]) / determinant,
            (a[0] * b[1] - a[1] * b[0]) / determinant,
        ),
    )


def _row_vector_times_matrix(vector: Vector3, matrix: Matrix3) -> Vector3:
    return (
        sum(vector[row] * matrix[row][0] for row in range(3)),
        sum(vector[row] * matrix[row][1] for row in range(3)),
        sum(vector[row] * matrix[row][2] for row in range(3)),
    )


@dataclass(frozen=True, slots=True)
class VaspGridMetadata:
    """Structure and dataset metadata attached to one VASP scalar grid."""

    source: Path
    comment: str
    cell: Matrix3
    species: tuple[str, ...]
    atom_counts: tuple[int, ...]
    fractional_positions: tuple[Vector3, ...]
    selective_dynamics: tuple[SelectiveFlags | None, ...]
    coordinate_mode: Literal["direct", "cartesian"]
    symbols_inferred: bool
    shape: GridShape
    dataset_index: int
    dataset_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        if len(self.cell) != 3 or any(len(vector) != 3 for vector in self.cell):
            raise ValueError("VASP grid cell must be a 3x3 row-vector matrix")
        if not all(math.isfinite(component) for vector in self.cell for component in vector):
            raise ValueError("VASP grid cell must contain only finite values")
        if abs(_determinant(self.cell)) < 1.0e-14:
            raise ValueError("VASP grid cell must be nonsingular")
        if not self.species or len(self.species) != len(self.atom_counts):
            raise ValueError("VASP grid species and atom-count lengths must match")
        if any(not species for species in self.species):
            raise ValueError("VASP grid species names must not be empty")
        if any(count < 0 for count in self.atom_counts) or sum(self.atom_counts) == 0:
            raise ValueError("VASP grid atom counts must be non-negative with a positive total")
        atom_count = sum(self.atom_counts)
        if len(self.fractional_positions) != atom_count:
            raise ValueError("VASP grid position count does not match its atom counts")
        if len(self.selective_dynamics) != atom_count:
            raise ValueError("VASP grid selective-dynamics count does not match its atom counts")
        if any(
            not math.isfinite(component)
            for position in self.fractional_positions
            for component in position
        ):
            raise ValueError("VASP grid atom positions must contain only finite values")
        if self.coordinate_mode not in {"direct", "cartesian"}:
            raise ValueError("VASP grid coordinate mode must be direct or cartesian")
        if len(self.shape) != 3 or any(size <= 0 for size in self.shape):
            raise ValueError("VASP grid shape must contain three positive integers")
        if self.dataset_count < 1:
            raise ValueError("VASP grid dataset count must be positive")
        if not 0 <= self.dataset_index < self.dataset_count:
            raise ValueError("VASP grid dataset index is outside the available range")

    @property
    def elements(self) -> tuple[str, ...]:
        """Return one element label per atom, in POSCAR order."""

        return tuple(
            species
            for species, count in zip(self.species, self.atom_counts, strict=True)
            for _ in range(count)
        )

    @property
    def point_count(self) -> int:
        """Return the number of scalar samples in the selected dataset."""

        return math.prod(self.shape)

    @property
    def cell_volume(self) -> float:
        """Return the positive cell volume in the header's length units cubed."""

        return abs(_determinant(self.cell))


@dataclass(frozen=True, slots=True)
class VaspGrid:
    """One immutable scalar dataset from a VASP ELFCAR/CHGCAR-like file."""

    metadata: VaspGridMetadata
    data: FloatGrid

    def __post_init__(self) -> None:
        array = np.array(self.data, dtype=np.float64, copy=True, order="C")
        if array.shape != self.metadata.shape:
            raise ValueError(
                f"VASP grid data shape {array.shape!r} does not match {self.metadata.shape!r}"
            )
        if not bool(np.isfinite(array).all()):
            raise ValueError("VASP grid data must contain only finite values")
        array.setflags(write=False)
        object.__setattr__(self, "data", array)

    @property
    def shape(self) -> GridShape:
        """Return the grid dimensions as ``(nx, ny, nz)``."""

        return self.metadata.shape

    def summary(self) -> dict[str, object]:
        """Return a deterministic, JSON-compatible summary of this dataset."""

        return {
            "source": str(self.metadata.source),
            "dataset_index": self.metadata.dataset_index,
            "dataset_count": self.metadata.dataset_count,
            "shape": list(self.metadata.shape),
            "point_count": self.metadata.point_count,
            "cell_volume": self.metadata.cell_volume,
            "minimum": float(np.min(self.data)),
            "maximum": float(np.max(self.data)),
            "mean": float(np.mean(self.data)),
        }

    def sample(self, fractional: Sequence[float]) -> float:
        """Periodically trilinearly interpolate at a fractional coordinate.

        VASP samples are located at ``(ix/nx, iy/ny, iz/nz)`` and do not repeat
        the periodic endpoint.  Coordinates outside ``[0, 1)`` are wrapped.
        """

        if isinstance(fractional, (str, bytes)):
            raise ValueError("fractional coordinate must contain three finite numbers")
        try:
            coordinates = tuple(fractional)
        except TypeError as exc:
            raise ValueError("fractional coordinate must contain three finite numbers") from exc
        if len(coordinates) != 3:
            raise ValueError("fractional coordinate must contain three finite numbers")

        axes: list[tuple[int, int, float]] = []
        for coordinate, size in zip(coordinates, self.shape, strict=True):
            if isinstance(coordinate, bool):
                raise ValueError("fractional coordinate must contain three finite numbers")
            try:
                value = float(coordinate)
            except (TypeError, ValueError) as exc:
                raise ValueError("fractional coordinate must contain three finite numbers") from exc
            if not math.isfinite(value):
                raise ValueError("fractional coordinate must contain three finite numbers")
            scaled = (value % 1.0) * size
            floor = math.floor(scaled)
            lower = floor % size
            axes.append((lower, (lower + 1) % size, scaled - floor))

        result = 0.0
        for x_choice in range(2):
            ix = axes[0][1] if x_choice else axes[0][0]
            wx = axes[0][2] if x_choice else 1.0 - axes[0][2]
            for y_choice in range(2):
                iy = axes[1][1] if y_choice else axes[1][0]
                wy = axes[1][2] if y_choice else 1.0 - axes[1][2]
                for z_choice in range(2):
                    iz = axes[2][1] if z_choice else axes[2][0]
                    wz = axes[2][2] if z_choice else 1.0 - axes[2][2]
                    result += wx * wy * wz * float(self.data[ix, iy, iz])
        return result


@dataclass(slots=True)
class _LineCursor:
    lines: Iterator[str]
    line_number: int = 0

    def next_nonblank(self, context: str, *, required: bool = True) -> tuple[str, int] | None:
        for raw_line in self.lines:
            self.line_number += 1
            line = raw_line.rstrip("\r\n")
            if line.strip():
                return line, self.line_number
        if required:
            raise VaspGridError(f"VASP volumetric file is missing {context}")
        return None


def _float(token: str, context: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise VaspGridError(f"invalid number {token!r} in {context}") from exc
    if not math.isfinite(value):
        raise VaspGridError(f"non-finite number {token!r} in {context}")
    return value


def _vector(line: str, context: str) -> Vector3:
    fields = line.split()
    if len(fields) < 3:
        raise VaspGridError(f"expected three numbers in {context}, got {line!r}")
    return (
        _float(fields[0], context),
        _float(fields[1], context),
        _float(fields[2], context),
    )


def _integer_tokens(line: str, context: str) -> tuple[int, ...]:
    fields = line.split()
    if not fields or any(_INTEGER.fullmatch(token) is None for token in fields):
        raise VaspGridError(f"{context} must contain integers")
    return tuple(int(token) for token in fields)


def _shape(line: str, context: str) -> GridShape:
    dimensions = _integer_tokens(line, context)
    if len(dimensions) != 3 or any(size <= 0 for size in dimensions):
        raise VaspGridError(f"{context} must contain exactly three positive integers")
    return dimensions


def _augmentation_line(line: str) -> bool:
    return line.lstrip().casefold().startswith("augmentation")


def _read_values(
    cursor: _LineCursor,
    *,
    point_count: int,
    dataset_index: int,
    keep: bool,
) -> FloatGrid | None:
    values = np.empty(point_count, dtype=np.float64) if keep else None
    consumed = 0
    while consumed < point_count:
        item = cursor.next_nonblank(f"values for dataset {dataset_index}")
        assert item is not None
        line, line_number = item
        if _augmentation_line(line):
            raise VaspGridError(
                "VASP augmentation blocks are not supported; provide a plain volumetric file"
            )
        fields = line.split()
        remaining = point_count - consumed
        if len(fields) > remaining:
            raise VaspGridError(
                f"dataset {dataset_index} has extra values on line {line_number}; "
                "dataset boundaries must occur between lines"
            )
        for offset, token in enumerate(fields):
            parsed = _float(token, f"dataset {dataset_index} line {line_number}")
            if values is not None:
                values[consumed + offset] = parsed
        consumed += len(fields)
    return values


def _validate_request(dataset: int, max_points: int) -> None:
    if isinstance(dataset, bool) or not isinstance(dataset, int) or dataset < 0:
        raise ValueError("dataset must be a non-negative integer")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 1:
        raise ValueError("max_points must be a positive integer")


def _parse_vasp_grid(
    *,
    source: Path,
    comment: str,
    cursor: _LineCursor,
    dataset: int,
    max_points: int,
) -> VaspGrid:
    scale_item = cursor.next_nonblank("scale factor")
    assert scale_item is not None
    scale_fields = scale_item[0].split()
    if len(scale_fields) not in {1, 3}:
        raise VaspGridError("VASP scale line must contain one or three numbers")
    scales = tuple(_float(token, "VASP scale") for token in scale_fields)

    raw_cell_rows: list[Vector3] = []
    for vector_index in range(3):
        item = cursor.next_nonblank(f"lattice vector {vector_index + 1}")
        assert item is not None
        raw_cell_rows.append(_vector(item[0], f"lattice vector {vector_index + 1}"))
    raw_cell: Matrix3 = tuple(raw_cell_rows)  # type: ignore[assignment]
    raw_volume = abs(_determinant(raw_cell))
    if raw_volume < 1.0e-14:
        raise VaspGridError("VASP lattice is singular")

    coordinate_scale: Vector3
    if len(scales) == 1:
        scale = scales[0]
        if scale == 0.0:
            raise VaspGridError("VASP scale factor cannot be zero")
        if scale < 0.0:
            scale = (-scale / raw_volume) ** (1.0 / 3.0)
        cell = tuple(tuple(component * scale for component in vector) for vector in raw_cell)
        coordinate_scale = (scale, scale, scale)
    else:
        if any(scale <= 0.0 for scale in scales):
            raise VaspGridError("three VASP scale factors must all be positive")
        cell = tuple(
            tuple(raw_cell[row][axis] * scales[axis] for axis in range(3)) for row in range(3)
        )
        coordinate_scale = scales  # type: ignore[assignment]
    typed_cell: Matrix3 = cell  # type: ignore[assignment]

    symbols_item = cursor.next_nonblank("element symbols or atom counts")
    assert symbols_item is not None
    symbol_tokens = symbols_item[0].split()
    symbols_inferred = all(_INTEGER.fullmatch(token) is not None for token in symbol_tokens)
    if symbols_inferred:
        atom_counts = _integer_tokens(symbols_item[0], "VASP atom counts")
        species = tuple(f"X{index + 1}" for index in range(len(atom_counts)))
    else:
        species = tuple(symbol_tokens)
        counts_item = cursor.next_nonblank("atom counts")
        assert counts_item is not None
        atom_counts = _integer_tokens(counts_item[0], "VASP atom counts")
        if len(species) != len(atom_counts):
            raise VaspGridError("VASP element symbol and atom-count lengths differ")
    if any(count < 0 for count in atom_counts) or sum(atom_counts) == 0:
        raise VaspGridError("VASP atom counts must be non-negative with a positive total")

    mode_item = cursor.next_nonblank("coordinate mode")
    assert mode_item is not None
    has_selective = mode_item[0].strip().casefold().startswith("s")
    if has_selective:
        mode_item = cursor.next_nonblank("coordinate mode after Selective dynamics")
        assert mode_item is not None
    mode_token = mode_item[0].strip().casefold()
    if mode_token.startswith("d"):
        coordinate_mode: Literal["direct", "cartesian"] = "direct"
    elif mode_token.startswith(("c", "k")):
        coordinate_mode = "cartesian"
    else:
        raise VaspGridError(f"unknown VASP coordinate mode {mode_item[0]!r}")

    inverse_cell = _inverse(typed_cell)
    fractional_positions: list[Vector3] = []
    selective_dynamics: list[SelectiveFlags | None] = []
    for atom_index in range(sum(atom_counts)):
        position_item = cursor.next_nonblank(f"coordinates for atom {atom_index + 1}")
        assert position_item is not None
        fields = position_item[0].split()
        position = _vector(position_item[0], f"atom {atom_index + 1} coordinates")
        if coordinate_mode == "cartesian":
            cartesian: Vector3 = tuple(position[axis] * coordinate_scale[axis] for axis in range(3))  # type: ignore[assignment]
            position = _row_vector_times_matrix(cartesian, inverse_cell)
        fractional_positions.append(position)
        if has_selective:
            if len(fields) < 6 or any(flag.casefold() not in {"t", "f"} for flag in fields[3:6]):
                raise VaspGridError(
                    f"atom {atom_index + 1} must have three T/F selective-dynamics flags"
                )
            selective_dynamics.append(
                tuple(flag.casefold() == "t" for flag in fields[3:6])  # type: ignore[arg-type]
            )
        else:
            selective_dynamics.append(None)

    header_item = cursor.next_nonblank("grid dimensions")
    assert header_item is not None
    grid_shape = _shape(header_item[0], "VASP grid dimensions")
    point_count = math.prod(grid_shape)
    if point_count > max_points:
        raise VaspGridError(
            f"VASP grid has {point_count} points, exceeding max_points={max_points}"
        )

    dataset_count = 1
    selected_values = _read_values(
        cursor,
        point_count=point_count,
        dataset_index=0,
        keep=dataset == 0,
    )
    while True:
        next_item = cursor.next_nonblank("another dataset", required=False)
        if next_item is None:
            break
        line, _line_number = next_item
        if _augmentation_line(line):
            raise VaspGridError(
                "VASP augmentation blocks are not supported; provide a plain volumetric file"
            )
        try:
            next_shape = _shape(line, f"dataset {dataset_count} grid dimensions")
        except VaspGridError as exc:
            raise VaspGridError(
                "unsupported content follows the plain VASP grid; augmentation and "
                "unlabeled tails are not supported"
            ) from exc
        if next_shape != grid_shape:
            raise VaspGridError(
                f"dataset {dataset_count} shape {next_shape!r} differs from first dataset "
                f"shape {grid_shape!r}"
            )
        values = _read_values(
            cursor,
            point_count=point_count,
            dataset_index=dataset_count,
            keep=dataset == dataset_count,
        )
        if values is not None:
            selected_values = values
        dataset_count += 1

    if dataset >= dataset_count or selected_values is None:
        raise VaspGridError(
            f"dataset {dataset} is unavailable; file contains {dataset_count} dataset(s)"
        )

    metadata = VaspGridMetadata(
        source=source,
        comment=comment,
        cell=typed_cell,
        species=species,
        atom_counts=atom_counts,
        fractional_positions=tuple(fractional_positions),
        selective_dynamics=tuple(selective_dynamics),
        coordinate_mode=coordinate_mode,
        symbols_inferred=symbols_inferred,
        shape=grid_shape,
        dataset_index=dataset,
        dataset_count=dataset_count,
    )
    data = np.reshape(selected_values, grid_shape, order="F")
    return VaspGrid(metadata=metadata, data=data)


def read_vasp_grid(
    path: str | Path,
    dataset: int = 0,
    max_points: int = DEFAULT_MAX_POINTS,
) -> VaspGrid:
    """Read one plain scalar dataset from a VASP volumetric file.

    VASP 4 and VASP 5 headers, optional selective dynamics, Direct and
    Cartesian coordinates, blank separator lines, and repeated same-shape
    plain datasets are supported.  Augmentation occupancies and any other tail
    format are rejected explicitly rather than skipped or guessed.  The file
    is streamed so memory use scales with the selected numeric dataset, not
    with the size of its text representation.
    """

    _validate_request(dataset, max_points)
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8") as stream:
            comment_line = stream.readline()
            if not comment_line:
                raise VaspGridError(f"VASP volumetric file is empty: {source}")
            return _parse_vasp_grid(
                source=source,
                comment=comment_line.strip(),
                cursor=_LineCursor(iter(stream), line_number=1),
                dataset=dataset,
                max_points=max_points,
            )
    except VaspGridError:
        raise
    except (OSError, UnicodeError) as exc:
        raise VaspGridError(f"cannot read VASP volumetric file {source}: {exc}") from exc


__all__ = [
    "DEFAULT_MAX_POINTS",
    "VaspGrid",
    "VaspGridError",
    "VaspGridMetadata",
    "read_vasp_grid",
]
