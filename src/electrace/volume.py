"""Streaming reader for VASP ELFCAR/CHGCAR-style scalar grids."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, TypeAlias, cast

import numpy as np
import numpy.typing as npt

from electrace._files import atomic_output_path

Vector3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vector3, Vector3, Vector3]
GridShape: TypeAlias = tuple[int, int, int]
SelectiveFlags: TypeAlias = tuple[bool, bool, bool]
FloatGrid: TypeAlias = npt.NDArray[np.float64]

DEFAULT_MAX_POINTS = 100_000_000
SPIN_SPLIT_NAMES = ("CHGCAR_tot", "CHGCAR_mag")
DENSITY_SUM_NAME = "CHGCAR_sum"
_INTEGER = re.compile(r"[+-]?\d+\Z")
# Both grids of one run carry the same header text, which VASP writes with six
# decimals; anything larger means the files describe different structures.
_HEADER_TOLERANCE = 1.0e-5


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


def _values(cursor: _Cursor, count: int, context: str, keep: bool) -> FloatGrid | None:
    result = np.empty(count) if keep else None
    consumed = 0
    while consumed < count:
        line = cursor.next(f"values for {context}")
        assert line is not None
        if line.casefold().startswith("augmentation"):
            raise VaspGridError(f"{context} is truncated: it has {consumed} of {count} values")
        tokens = line.split()
        if consumed + len(tokens) > count:
            raise VaspGridError(f"{context} has extra values")
        for offset, token in enumerate(tokens):
            value = _float(token, context)
            if result is not None:
                result[consumed + offset] = value
        consumed += len(tokens)
    return result


def _next_dataset(cursor: _Cursor, shape: GridShape, dataset: int) -> bool:
    """Skip CHGCAR extras after a grid; return whether another grid follows.

    VASP follows each CHGCAR grid with PAW augmentation occupancies and, for
    ISPIN = 2 or non-collinear runs, writes a line of per-atom moments before the
    next grid. As in pymatgen and BaderKit, a grid starts only at an exact repeat
    of the grid dimensions. Moments that are not followed by a grid mean the file
    was truncated, so they are an error rather than a one-dataset file.
    """

    moments = 0
    while (line := cursor.next("another dataset", required=False)) is not None:
        fields = line.split()
        if line.casefold().startswith("augmentation"):
            if len(fields) < 3 or not all(_INTEGER.fullmatch(item) for item in fields[-2:]):
                raise VaspGridError(f"invalid augmentation header after dataset {dataset}")
            context = f"augmentation block {fields[-2]} after dataset {dataset}"
            _values(cursor, int(fields[-1]), context, False)
        elif len(fields) == 3 and all(_INTEGER.fullmatch(item) for item in fields):
            if _shape(line) != shape:
                raise VaspGridError("all datasets must use the same shape")
            return True
        else:
            for token in fields:
                _float(token, f"per-atom values after dataset {dataset}")
            moments += len(fields)
    if moments:
        raise VaspGridError(
            f"{moments} per-atom values after dataset {dataset} are not followed by "
            "another grid; the file may be truncated"
        )
    return False


def _parse(
    source: Path,
    comment: str,
    cursor: _Cursor,
    keep: Callable[[int], bool],
    max_points: int,
) -> dict[int, VaspGrid]:
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
    kept: dict[int, FloatGrid] = {}
    dataset_count = 0
    while True:
        values = _values(cursor, point_count, f"dataset {dataset_count}", keep(dataset_count))
        if values is not None:
            kept[dataset_count] = np.reshape(values, shape, order="F")
        dataset_count += 1
        if not _next_dataset(cursor, shape, dataset_count - 1):
            break
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
        0,
        dataset_count,
    )
    return {
        index: VaspGrid(replace(metadata, dataset_index=index), data)
        for index, data in kept.items()
    }


def _read(path: str | Path, keep: Callable[[int], bool], max_points: int) -> dict[int, VaspGrid]:
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 1:
        raise ValueError("max_points must be positive")
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8") as stream:
            comment = stream.readline()
            if not comment:
                raise VaspGridError(f"VASP volumetric file is empty: {source}")
            return _parse(source, comment.strip(), _Cursor(iter(stream), 1), keep, max_points)
    except VaspGridError:
        raise
    except (OSError, UnicodeError) as exc:
        raise VaspGridError(f"cannot read VASP volumetric file {source}: {exc}") from exc


def read_vasp_grid(
    path: str | Path, dataset: int = 0, max_points: int = DEFAULT_MAX_POINTS
) -> VaspGrid:
    if isinstance(dataset, bool) or not isinstance(dataset, int) or dataset < 0:
        raise ValueError("dataset must be a non-negative integer")
    grids = _read(path, lambda index: index == dataset, max_points)
    if dataset not in grids:
        raise VaspGridError(f"dataset {dataset} is unavailable")
    return grids[dataset]


def _write_grid(grid: VaspGrid, path: Path) -> None:
    # Bader reads the header with fixed-width Fortran formats, so this follows
    # the column layout VASP itself writes for CHGCAR.
    metadata = grid.metadata
    lines = [metadata.comment or "ElecTrace grid", f"{1.0:19.14f}"]
    lines.extend(" " + "".join(f"{value:12.6f}" for value in row) for row in metadata.cell)
    if not metadata.symbols_inferred:
        lines.append("".join(f"{symbol:>5}" for symbol in metadata.species))
    lines.append("".join(f"{count:6d}" for count in metadata.atom_counts))
    lines.append("Direct")
    lines.extend(
        " " + "".join(f"{value:10.6f}" for value in position)
        for position in metadata.fractional_positions
    )
    lines.extend((" ", "".join(f"{size:5d}" for size in metadata.shape)))
    values = grid.data.reshape(-1, order="F")
    full = values.size - values.size % 5
    with atomic_output_path(path) as temporary, temporary.open("w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
        np.savetxt(stream, values[:full].reshape(-1, 5), fmt="%.11E")
        if full < values.size:
            stream.write(" ".join(f"{value:.11E}" for value in values[full:]) + "\n")


def split_spin_density(
    path: str | Path,
    output_directory: str | Path | None = None,
    *,
    overwrite: bool = False,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, object]:
    """Write the total and magnetization grids of a collinear ISPIN = 2 CHGCAR.

    The two files keep VASP's rho*V scaling and the magnetization sign (spin up
    minus spin down) unchanged, so ``bader CHGCAR_mag -ref CHGCAR_tot`` integrates
    the magnetization within the total-density Bader basins. ElecTrace reads only
    the file; it never infers ISPIN from INCAR.
    """

    source = Path(path).expanduser().resolve()
    if "CHG" not in source.name.upper():
        raise VaspGridError(
            f"spin splitting reads a CHGCAR or CHG density, not {source.name}; an ISPIN = 2 "
            "ELFCAR holds spin-up and spin-down ELF rather than total and magnetization"
        )
    grids = _read(source, lambda index: index < 2, max_points)
    count = grids[0].metadata.dataset_count
    if count == 1:
        raise VaspGridError(
            f"{source} has one dataset and no magnetization grid (ISPIN = 1, or the run did "
            "not write it); no spin files were written"
        )
    if count == 4:
        raise VaspGridError(
            f"{source} is non-collinear (total, mx, my, mz); per-atom spin populations "
            "need a collinear ISPIN = 2 run"
        )
    if count != 2:
        raise VaspGridError(f"{source} has {count} datasets; a collinear ISPIN = 2 CHGCAR has 2")
    directory = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else source.parent / "spin"
    )
    targets = tuple(directory / name for name in SPIN_SPLIT_NAMES)
    existing = [str(target) for target in targets if target.exists() or target.is_symlink()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to replace existing spin files: {', '.join(existing)}")
    for index, target in enumerate(targets):
        _write_grid(grids[index], target)
    return {
        "source": str(source),
        "dataset_count": count,
        "shape": list(grids[0].shape),
        "integrated_total": float(grids[0].data.mean()),
        "integrated_magnetization": float(grids[1].data.mean()),
        "written": [str(target) for target in targets],
    }


def _structure_mismatch(first: VaspGridMetadata, second: VaspGridMetadata) -> str | None:
    """Return how two grid headers differ in grid, cell, or atoms, or None."""

    if first.shape != second.shape:
        return f"grid {first.shape} differs from {second.shape}"
    if (first.species, first.atom_counts) != (second.species, second.atom_counts):
        return (
            f"species {' '.join(first.species)} ({' '.join(map(str, first.atom_counts))}) "
            f"differ from {' '.join(second.species)} ({' '.join(map(str, second.atom_counts))})"
        )
    if not np.allclose(first.cell, second.cell, rtol=0.0, atol=_HEADER_TOLERANCE):
        return "lattice vectors differ"
    shift = np.subtract(first.fractional_positions, second.fractional_positions)
    shift -= np.rint(shift)
    if np.abs(shift).max() > _HEADER_TOLERANCE:
        atom = int(np.abs(shift).max(axis=1).argmax())
        return f"atom {atom + 1} positions differ"
    return None


def sum_density_grids(
    path: str | Path,
    other: str | Path,
    output_directory: str | Path | None = None,
    *,
    overwrite: bool = False,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, object]:
    """Write ``CHGCAR_sum``, the sum of two grids of one structure, such as AECCAR0 + AECCAR2.

    With ``LAECHG = .TRUE.``, VASP writes the frozen core density (AECCAR0) and the
    valence density with its one-centre PAW corrections (AECCAR2) on the fine grid.
    Their sum approximates the all-electron density, whose maxima sit on the nuclei,
    so ``bader CHGCAR -ref CHGCAR_sum`` places the Bader basins. The first dataset of
    each file is used, values keep VASP's rho*V scaling, and the header is the first
    file's. Grids, lattices, species, and atom positions must agree, which catches
    files from different runs or images; nothing is written otherwise.
    """

    first = read_vasp_grid(path, max_points=max_points)
    second = read_vasp_grid(other, max_points=max_points)
    mismatch = _structure_mismatch(first.metadata, second.metadata)
    if mismatch is not None:
        raise VaspGridError(
            f"{first.metadata.source} and {second.metadata.source} do not describe one "
            f"structure and grid: {mismatch}; no {DENSITY_SUM_NAME} was written"
        )
    directory = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else first.metadata.source.parent
    )
    target = directory / DENSITY_SUM_NAME
    if target.resolve() in {first.metadata.source, second.metadata.source}:
        raise ValueError(f"{target} would replace one of the summed files")
    if (target.exists() or target.is_symlink()) and not overwrite:
        raise FileExistsError(f"refusing to replace existing {target}")
    total = VaspGrid(first.metadata, first.data + second.data)
    _write_grid(total, target)
    return {
        "sources": [str(first.metadata.source), str(second.metadata.source)],
        "dataset_counts": [first.metadata.dataset_count, second.metadata.dataset_count],
        "shape": list(total.shape),
        "integrated": [float(first.data.mean()), float(second.data.mean())],
        "integrated_sum": float(total.data.mean()),
        "written": str(target),
    }


__all__ = [
    "DEFAULT_MAX_POINTS",
    "DENSITY_SUM_NAME",
    "SPIN_SPLIT_NAMES",
    "VaspGrid",
    "VaspGridError",
    "VaspGridMetadata",
    "read_vasp_grid",
    "split_spin_density",
    "sum_density_grids",
]
