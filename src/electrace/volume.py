"""Streaming reader for plain VASP ELFCAR/CHGCAR-style scalar grids."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, cast

import numpy as np
import numpy.typing as npt

Vector3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vector3, Vector3, Vector3]
GridShape: TypeAlias = tuple[int, int, int]
SelectiveFlags: TypeAlias = tuple[bool, bool, bool]
FloatGrid: TypeAlias = npt.NDArray[np.float64]

DEFAULT_MAX_POINTS = 100_000_000


class VaspGridError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VaspGridMetadata:
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

    @property
    def elements(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, count in zip(self.species, self.atom_counts, strict=True)
            for _ in range(count)
        )

    @property
    def point_count(self) -> int:
        return math.prod(self.shape)

    @property
    def cell_volume(self) -> float:
        return abs(float(np.linalg.det(self.cell)))


@dataclass(frozen=True, slots=True)
class VaspGrid:
    metadata: VaspGridMetadata
    data: FloatGrid

    def __post_init__(self) -> None:
        array = np.asarray(self.data, dtype=np.float64).copy(order="C")
        if array.shape != self.metadata.shape or not np.isfinite(array).all():
            raise ValueError("grid data must match its shape and contain finite values")
        array.setflags(write=False)
        object.__setattr__(self, "data", array)

    @property
    def shape(self) -> GridShape:
        return self.metadata.shape

    def summary(self) -> dict[str, object]:
        return {
            "source": str(self.metadata.source),
            "dataset_index": self.metadata.dataset_index,
            "dataset_count": self.metadata.dataset_count,
            "shape": list(self.shape),
            "point_count": self.metadata.point_count,
            "cell_volume": self.metadata.cell_volume,
            "minimum": float(self.data.min()),
            "maximum": float(self.data.max()),
            "mean": float(self.data.mean()),
        }

    def sample(self, fractional: Sequence[float]) -> float:
        try:
            coordinate = np.asarray(tuple(fractional), dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError("fractional coordinate must contain three finite numbers") from exc
        if coordinate.shape != (3,) or not np.isfinite(coordinate).all():
            raise ValueError("fractional coordinate must contain three finite numbers")
        scaled = (coordinate % 1.0) * np.asarray(self.shape)
        lower = np.floor(scaled).astype(int)
        weight = scaled - lower
        result = 0.0
        for mask in range(8):
            indices = tuple(
                int((lower[axis] + ((mask >> axis) & 1)) % self.shape[axis]) for axis in range(3)
            )
            factor = math.prod(
                weight[axis] if (mask >> axis) & 1 else 1.0 - weight[axis] for axis in range(3)
            )
            result += factor * float(self.data[indices])
        return result


@dataclass(slots=True)
class _Cursor:
    lines: Iterator[str]
    number: int = 0

    def next(self, context: str, required: bool = True) -> str | None:
        for raw in self.lines:
            self.number += 1
            if raw.strip():
                return raw.strip()
        if required:
            raise VaspGridError(f"VASP volumetric file is missing {context}")
        return None


def _float(token: str, context: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise VaspGridError(f"invalid number {token!r} in {context}") from exc
    if not math.isfinite(value):
        raise VaspGridError(f"non-finite number in {context}")
    return value


def _vector(line: str, context: str) -> Vector3:
    fields = line.split()
    if len(fields) < 3:
        raise VaspGridError(f"expected three numbers in {context}")
    return cast(Vector3, tuple(_float(token, context) for token in fields[:3]))


def _integers(line: str, context: str) -> tuple[int, ...]:
    try:
        values = tuple(int(token) for token in line.split())
    except ValueError as exc:
        raise VaspGridError(f"{context} must contain integers") from exc
    if not values:
        raise VaspGridError(f"{context} is empty")
    return values


def _shape(line: str) -> GridShape:
    values = _integers(line, "grid dimensions")
    if len(values) != 3 or any(value <= 0 for value in values):
        raise VaspGridError("grid dimensions must be three positive integers")
    return values


def _values(cursor: _Cursor, count: int, index: int, keep: bool) -> FloatGrid | None:
    result = np.empty(count) if keep else None
    consumed = 0
    while consumed < count:
        line = cursor.next(f"values for dataset {index}")
        assert line is not None
        if line.casefold().startswith("augmentation"):
            raise VaspGridError("VASP augmentation blocks are not supported")
        tokens = line.split()
        if consumed + len(tokens) > count:
            raise VaspGridError(f"dataset {index} has extra values")
        for offset, token in enumerate(tokens):
            value = _float(token, f"dataset {index}")
            if result is not None:
                result[consumed + offset] = value
        consumed += len(tokens)
    return result


def _parse(source: Path, comment: str, cursor: _Cursor, dataset: int, max_points: int) -> VaspGrid:
    scale_line = cursor.next("scale factor")
    assert scale_line is not None
    scales = tuple(_float(token, "scale") for token in scale_line.split())
    if len(scales) not in {1, 3}:
        raise VaspGridError("scale line must contain one or three numbers")
    raw_cell = np.asarray(
        [_vector(cast(str, cursor.next("lattice vector")), "lattice vector") for _ in range(3)]
    )
    raw_volume = abs(float(np.linalg.det(raw_cell)))
    if raw_volume < 1e-14:
        raise VaspGridError("VASP lattice is singular")
    if len(scales) == 1:
        scale = scales[0]
        if scale == 0:
            raise VaspGridError("scale factor cannot be zero")
        scale = (-scale / raw_volume) ** (1 / 3) if scale < 0 else scale
        cell_array = raw_cell * scale
        coordinate_scale = np.full(3, scale)
    else:
        if any(value <= 0 for value in scales):
            raise VaspGridError("three scale factors must be positive")
        coordinate_scale = np.asarray(scales)
        cell_array = raw_cell * coordinate_scale
    cell = cast(Matrix3, tuple(map(tuple, cell_array.tolist())))

    symbol_line = cast(str, cursor.next("element symbols or atom counts"))
    symbols = symbol_line.split()
    inferred = all(token.lstrip("+-").isdigit() for token in symbols)
    if inferred:
        counts = _integers(symbol_line, "atom counts")
        species = tuple(f"X{index + 1}" for index in range(len(counts)))
    else:
        species = tuple(symbols)
        counts = _integers(cast(str, cursor.next("atom counts")), "atom counts")
    if len(species) != len(counts) or any(count < 0 for count in counts) or not sum(counts):
        raise VaspGridError("invalid element symbols or atom counts")

    mode_line = cast(str, cursor.next("coordinate mode"))
    selective = mode_line.casefold().startswith("s")
    if selective:
        mode_line = cast(str, cursor.next("coordinate mode"))
    if mode_line.casefold().startswith("d"):
        mode: Literal["direct", "cartesian"] = "direct"
    elif mode_line.casefold().startswith(("c", "k")):
        mode = "cartesian"
    else:
        raise VaspGridError(f"unknown coordinate mode {mode_line!r}")

    positions: list[Vector3] = []
    flags: list[SelectiveFlags | None] = []
    inverse = np.linalg.inv(cell_array)
    for atom in range(sum(counts)):
        line = cast(str, cursor.next(f"coordinates for atom {atom + 1}"))
        fields = line.split()
        position = np.asarray(_vector(line, "atom coordinates"))
        if mode == "cartesian":
            position = (position * coordinate_scale) @ inverse
        positions.append(cast(Vector3, tuple(position.tolist())))
        if selective:
            if len(fields) < 6 or any(value.casefold() not in {"t", "f"} for value in fields[3:6]):
                raise VaspGridError("invalid selective-dynamics flags")
            flags.append(
                cast(SelectiveFlags, tuple(value.casefold() == "t" for value in fields[3:6]))
            )
        else:
            flags.append(None)

    shape = _shape(cast(str, cursor.next("grid dimensions")))
    point_count = math.prod(shape)
    if point_count > max_points:
        raise VaspGridError(f"grid exceeds max_points={max_points}")
    selected = _values(cursor, point_count, 0, dataset == 0)
    dataset_count = 1
    while (next_dataset := cursor.next("another dataset", required=False)) is not None:
        if next_dataset.casefold().startswith("augmentation"):
            raise VaspGridError("VASP augmentation blocks are not supported")
        if _shape(next_dataset) != shape:
            raise VaspGridError("all datasets must use the same shape")
        values = _values(cursor, point_count, dataset_count, dataset == dataset_count)
        selected = values if values is not None else selected
        dataset_count += 1
    if selected is None:
        raise VaspGridError(f"dataset {dataset} is unavailable")
    metadata = VaspGridMetadata(
        source,
        comment,
        cell,
        species,
        counts,
        tuple(positions),
        tuple(flags),
        mode,
        inferred,
        shape,
        dataset,
        dataset_count,
    )
    return VaspGrid(metadata, np.reshape(selected, shape, order="F"))


def read_vasp_grid(
    path: str | Path, dataset: int = 0, max_points: int = DEFAULT_MAX_POINTS
) -> VaspGrid:
    if isinstance(dataset, bool) or not isinstance(dataset, int) or dataset < 0:
        raise ValueError("dataset must be a non-negative integer")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 1:
        raise ValueError("max_points must be positive")
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8") as stream:
            comment = stream.readline()
            if not comment:
                raise VaspGridError(f"VASP volumetric file is empty: {source}")
            return _parse(source, comment.strip(), _Cursor(iter(stream), 1), dataset, max_points)
    except VaspGridError:
        raise
    except (OSError, UnicodeError) as exc:
        raise VaspGridError(f"cannot read VASP volumetric file {source}: {exc}") from exc


__all__ = ["DEFAULT_MAX_POINTS", "VaspGrid", "VaspGridError", "VaspGridMetadata", "read_vasp_grid"]
