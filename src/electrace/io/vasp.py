"""Dependency-light readers for the small VASP files ElecTrace needs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from electrace.models import Atom, Frame, Matrix3, Vec3


class VaspParseError(ValueError):
    """Raised when a VASP structure or scalar output is malformed."""


@dataclass(frozen=True, slots=True)
class PoscarData:
    """Parsed POSCAR data before it is converted into a core :class:`Frame`."""

    comment: str
    cell: Matrix3
    elements: tuple[str, ...]
    fractional_positions: tuple[Vec3, ...]
    selective_dynamics: tuple[tuple[bool, bool, bool] | None, ...]
    coordinate_mode: str
    symbols_inferred: bool
    species: tuple[str, ...]  # species-line labels, in file order
    counts: tuple[int, ...]  # atoms per species-line label


_NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)"
_TOTEN = re.compile(rf"free\s+energy\s+TOTEN\s*=\s*{_NUMBER}", re.IGNORECASE)
_OSZICAR_E0 = re.compile(rf"\bE0=\s*{_NUMBER}", re.IGNORECASE)
_OSZICAR_F = re.compile(rf"(?:^|\s)F=\s*{_NUMBER}", re.IGNORECASE)
_TITEL = re.compile(r"^\s*TITEL\s*=\s*\S+\s+(\S+)")
_ZVAL = re.compile(rf"\bPOMASS\s*=[^;]*;\s*ZVAL\s*=\s*{_NUMBER}")
_ELEMENT_SYMBOL = re.compile(r"[A-Z][a-z]?")


def _float(token: str, *, context: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise VaspParseError(f"invalid number {token!r} in {context}") from exc
    if not math.isfinite(value):
        raise VaspParseError(f"non-finite number {token!r} in {context}")
    return value


def _three_floats(line: str, *, context: str) -> Vec3:
    fields = line.split()
    if len(fields) < 3:
        raise VaspParseError(f"expected three numbers in {context}, got {line!r}")
    return tuple(_float(field, context=context) for field in fields[:3])  # type: ignore[return-value]


def _cross(u: Vec3, v: Vec3) -> Vec3:
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def _determinant(matrix: Matrix3) -> float:
    a, b, c = matrix
    bc = _cross(b, c)
    return a[0] * bc[0] + a[1] * bc[1] + a[2] * bc[2]


def _inverse(matrix: Matrix3) -> Matrix3:
    a, b, c = matrix
    determinant = _determinant(matrix)
    if abs(determinant) < 1.0e-14:
        raise VaspParseError("POSCAR lattice is singular")
    # The columns of the inverse are the reciprocal lattice vectors.
    u, v, w = _cross(b, c), _cross(c, a), _cross(a, b)
    return (
        (u[0] / determinant, v[0] / determinant, w[0] / determinant),
        (u[1] / determinant, v[1] / determinant, w[1] / determinant),
        (u[2] / determinant, v[2] / determinant, w[2] / determinant),
    )


def _row_vector_times_matrix(vector: Vec3, matrix: Matrix3) -> Vec3:
    x, y, z = (sum(vector[row] * matrix[row][axis] for row in range(3)) for axis in range(3))
    return x, y, z


def parse_poscar(path: str | Path) -> PoscarData:
    """Parse VASP 4/5 POSCAR or CONTCAR without requiring ASE or pymatgen.

    Direct and Cartesian coordinates, selective-dynamics flags, positive and
    negative volume scale factors, and VASP's three-factor lattice scaling are
    supported.  VASP 4 files without element symbols receive stable ``X1``,
    ``X2``, ... placeholders and are marked with ``symbols_inferred=True``.
    """

    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise VaspParseError(f"cannot read VASP structure {source}: {exc}") from exc
    if len(lines) < 8:
        raise VaspParseError(f"{source} is too short to be a POSCAR")

    comment = lines[0].strip()
    scale_tokens = lines[1].split()
    if len(scale_tokens) not in {1, 3}:
        raise VaspParseError("POSCAR scale line must contain one or three numbers")
    scales = tuple(_float(token, context="POSCAR scale") for token in scale_tokens)
    raw_cell: Matrix3 = tuple(
        _three_floats(lines[index], context=f"lattice vector {index - 1}") for index in range(2, 5)
    )  # type: ignore[assignment]
    raw_volume = abs(_determinant(raw_cell))
    if raw_volume < 1.0e-14:
        raise VaspParseError("POSCAR lattice is singular")

    if len(scales) == 1:
        scale = scales[0]
        if scale == 0.0:
            raise VaspParseError("POSCAR scale factor cannot be zero")
        if scale < 0.0:
            scale = (-scale / raw_volume) ** (1.0 / 3.0)
        scales = (scale, scale, scale)
    elif any(scale <= 0.0 for scale in scales):
        raise VaspParseError("three POSCAR scale factors must all be positive")
    cell: Matrix3 = tuple(
        tuple(raw_cell[row][axis] * scales[axis] for axis in range(3)) for row in range(3)
    )  # type: ignore[assignment]

    symbol_or_count = lines[5].split()
    if not symbol_or_count:
        raise VaspParseError("POSCAR symbol/count line is empty")
    symbols_inferred = all(token.lstrip("+-").isdigit() for token in symbol_or_count)
    if symbols_inferred:
        counts_line_index = 5
        symbols = [f"X{index + 1}" for index in range(len(symbol_or_count))]
    else:
        counts_line_index = 6
        symbols = symbol_or_count
    count_tokens = lines[counts_line_index].split()
    if len(count_tokens) != len(symbols):
        raise VaspParseError("POSCAR element symbol and count lengths differ")
    try:
        counts = [int(token) for token in count_tokens]
    except ValueError as exc:
        raise VaspParseError("POSCAR atom counts must be integers") from exc
    if any(count < 0 for count in counts) or sum(counts) == 0:
        raise VaspParseError("POSCAR atom counts must be non-negative with a positive total")
    elements = tuple(
        symbol for symbol, count in zip(symbols, counts, strict=True) for _ in range(count)
    )

    cursor = counts_line_index + 1
    has_selective = lines[cursor].strip().lower().startswith("s")
    if has_selective:
        cursor += 1
        if cursor >= len(lines):
            raise VaspParseError("POSCAR is missing coordinate mode after Selective dynamics")
    mode_token = lines[cursor].strip().lower()
    if mode_token.startswith("d"):
        coordinate_mode = "direct"
    elif mode_token.startswith(("c", "k")):
        coordinate_mode = "cartesian"
    else:
        raise VaspParseError(f"unknown POSCAR coordinate mode {lines[cursor]!r}")
    cursor += 1
    if len(lines) < cursor + len(elements):
        raise VaspParseError(
            f"POSCAR declares {len(elements)} atoms but has only {max(0, len(lines) - cursor)} coordinate lines"
        )

    fractional: list[Vec3] = []
    selective: list[tuple[bool, bool, bool] | None] = []
    inverse_cell = _inverse(cell)
    for atom_index in range(len(elements)):
        line = lines[cursor + atom_index]
        position = _three_floats(line, context=f"atom {atom_index + 1} coordinates")
        fields = line.split()
        if coordinate_mode == "cartesian":
            cartesian = tuple(position[axis] * scales[axis] for axis in range(3))
            position = _row_vector_times_matrix(cartesian, inverse_cell)  # type: ignore[arg-type]
        fractional.append(position)
        if has_selective:
            if len(fields) < 6 or any(flag[:1].lower() not in {"t", "f"} for flag in fields[3:6]):
                raise VaspParseError(f"invalid selective-dynamics flags for atom {atom_index + 1}")
            selective.append(tuple(flag[:1].lower() == "t" for flag in fields[3:6]))  # type: ignore[arg-type]
        else:
            selective.append(None)

    return PoscarData(
        comment=comment,
        cell=cell,
        elements=elements,
        fractional_positions=tuple(fractional),
        selective_dynamics=tuple(selective),
        coordinate_mode=coordinate_mode,
        symbols_inferred=symbols_inferred,
        species=tuple(symbols),
        counts=tuple(counts),
    )


def read_poscar(
    source: Path,
    *,
    index: int = 0,
    label: str | None = None,
    energy: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Frame:
    """Read a POSCAR/CONTCAR directly into an ElecTrace ``Frame``."""

    data = parse_poscar(source)
    frame_metadata: dict[str, Any] = {
        "structure_source": str(source),
        "comment": data.comment,
        "coordinate_mode": data.coordinate_mode,
        "symbols_inferred": data.symbols_inferred,
        # Species blocks as VASP pairs them with POTCAR entries; adjacent blocks of one
        # element (Fe Fe O) stay apart.
        "species": list(data.species),
        "species_counts": list(data.counts),
    }
    if metadata:
        frame_metadata.update(metadata)
    atoms = tuple(
        Atom(
            index=atom_index,
            element=element,
            fractional_position=position,
            attributes={"selective_dynamics": flags} if flags is not None else {},
        )
        for atom_index, (element, position, flags) in enumerate(
            zip(data.elements, data.fractional_positions, data.selective_dynamics, strict=True)
        )
    )
    return Frame(
        index=index,
        label=label or source.parent.name or source.name,
        cell=data.cell,
        atoms=atoms,
        energy=energy,
        metadata=frame_metadata,
    )


def element_symbol(label: str) -> str:
    """Return the element symbol that starts a species label such as ``Fe_pv`` or ``H.75``."""

    match = _ELEMENT_SYMBOL.match(label)
    return match.group(0) if match else label


def read_vasp_zval(source: Path) -> tuple[tuple[str, float], ...]:
    """Return the element and ZVAL of each entry in a POTCAR or OUTCAR, in file order.

    Each entry's ``TITEL`` line precedes its ``POMASS = ...; ZVAL = ...`` line in both
    files. An OUTCAR is read only up to ``ions per type``, which follows its copy of the
    POTCAR headers.
    """

    entries: list[tuple[str, float]] = []
    label: str | None = None
    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "ions per type" in line:
                    break
                titel = _TITEL.match(line)
                if titel is not None:
                    if label is not None:
                        raise VaspParseError(f"{source.name} entry {label} has no ZVAL")
                    label = titel.group(1)
                    continue
                zval = _ZVAL.search(line)
                if zval is not None:
                    if label is None:
                        raise VaspParseError(f"{source.name} has a ZVAL line without a TITEL")
                    value = _float(zval.group(1), context=f"{source.name} ZVAL")
                    entries.append((element_symbol(label), value))
                    label = None
    except OSError as exc:
        raise VaspParseError(f"cannot read {source}: {exc}") from exc
    if label is not None:
        raise VaspParseError(f"{source.name} entry {label} has no ZVAL")
    if not entries:
        raise VaspParseError(f"{source.name} has no TITEL and ZVAL lines")
    return tuple(entries)


def read_vasp_energy(source: Path) -> float | None:
    """Extract a final electronic energy in eV from OUTCAR or OSZICAR.

    For OUTCAR the last ``free energy TOTEN`` value is returned.  For OSZICAR
    the last ionic ``E0=`` value is preferred, falling back to ``F=``.  A
    missing or incomplete energy record returns ``None`` rather than making an
    otherwise usable path unreadable.
    """

    patterns = (_OSZICAR_E0, _OSZICAR_F) if source.name.upper() == "OSZICAR" else (_TOTEN,)
    values: list[float | None] = [None] * len(patterns)
    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                for index, pattern in enumerate(patterns):
                    match = pattern.search(line)
                    if match is not None:
                        values[index] = _float(match.group(1), context="VASP energy")
    except OSError:
        return None
    return next((value for value in values if value is not None), None)
